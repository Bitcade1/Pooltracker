from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, date
from collections import defaultdict  # Ensure defaultdict is imported
from calendar import monthrange
from sqlalchemy import func, extract
import requests
import os
import re  # Add this import at the top of the file

app = Flask(__name__)
app.secret_key = 'your_secret_key'

basedir = os.path.abspath(os.path.dirname(__file__))

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'pool_table_tracker.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Custom filter for absolute value
@app.template_filter('abs')
def abs_filter(value):
    """Return the absolute value of a number."""
    try:
        return abs(int(value))
    except (TypeError, ValueError):
        return 0

app.jinja_env.filters['abs'] = abs_filter

# Models
class CompletedTable(db.Model):
    __tablename__ = 'completed_table'
    id = db.Column(db.Integer, primary_key=True)
    worker = db.Column(db.String(50), nullable=False)
    start_time = db.Column(db.String(5), nullable=False)  # Store as string "HH:MM"
    finish_time = db.Column(db.String(5), nullable=False)
    serial_number = db.Column(db.String(20), unique=True, nullable=False)
    issue = db.Column(db.String(100))
    lunch = db.Column(db.String(3), default='No')
    date = db.Column(db.Date, default=date.today, nullable=False)

class TableStock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Use a string like 'body_7ft', 'body_6ft', 'top_rail', or 'cushion_set'
    type = db.Column(db.String(50), unique=True, nullable=False)
    count = db.Column(db.Integer, default=0, nullable=False)


class Worker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class Issue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(100), unique=True, nullable=False)

class TopRail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    worker = db.Column(db.String(50), nullable=False)
    start_time = db.Column(db.String(10), nullable=False)
    finish_time = db.Column(db.String(10), nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow, nullable=False)
    serial_number = db.Column(db.String(20), unique=True, nullable=False)
    issue = db.Column(db.String(50), nullable=False)
    lunch = db.Column(db.String(3), default='No')

class WoodCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(50), nullable=False)  
    count = db.Column(db.Integer, default=0, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow, nullable=False)
    time = db.Column(db.Time, default=datetime.utcnow().time, nullable=False)

    def __init__(self, section, count=0, date=None, time=None):
        self.section = section
        self.count = count
        self.date = date if date else datetime.utcnow().date()
        self.time = time if time else datetime.utcnow().time()

class MDFInventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plain_mdf = db.Column(db.Integer, nullable=False, default=0)
    black_mdf = db.Column(db.Integer, nullable=False, default=0)
    plain_mdf_36 = db.Column(db.Integer, nullable=False, default=0)

class PrintedPartsCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_name = db.Column(db.String(50), nullable=False)
    count = db.Column(db.Integer, default=1)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)

class CompletedPods(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    worker = db.Column(db.String(50), nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    finish_time = db.Column(db.Time, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow().date(), nullable=False)
    serial_number = db.Column(db.String(20), unique=True, nullable=False)
    issue = db.Column(db.String(100)) 
    lunch = db.Column(db.String(3), default='No')

class CushionJobLog(db.Model):
    __tablename__ = 'cushion_job_log'
    id = db.Column(db.Integer, primary_key=True)
    job_name = db.Column(db.String(100), nullable=False)
    goal_time_hours = db.Column(db.Float, nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    actual_hours = db.Column(db.Float, nullable=False)
    setup_hours = db.Column(db.Float)
    date = db.Column(db.Date, default=date.today, nullable=False)

class ProductionSchedule(db.Model):
    __tablename__ = 'production_schedule'
    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    target_7ft = db.Column(db.Integer, default=0, nullable=False)
    target_6ft = db.Column(db.Integer, default=0, nullable=False)

    def __repr__(self):
        return f"<ProductionSchedule {self.month}/{self.year} 7ft={self.target_7ft} 6ft={self.target_6ft}>"





class HardwarePart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    initial_count = db.Column(db.Integer, default=0)
    used_per_table = db.Column(db.Float, default=0.0000)

class PartThreshold(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_name = db.Column(db.String(100), unique=True, nullable=False)
    threshold = db.Column(db.Integer, default=0, nullable=False)

def check_and_notify_low_stock(part_name, old_count, new_count):
    threshold_entry = PartThreshold.query.filter_by(part_name=part_name).first()
    if threshold_entry and threshold_entry.threshold > 0:
        # notify on every decrement while at or below threshold
        if new_count <= threshold_entry.threshold:
            try:
                message = f"Stock for {part_name} is low ({new_count} remaining)."
                title   = "Low Stock Warning"
                requests.post(
                    "https://ntfy.sh/PoolTableTracker",
                    data=message,
                    headers={"Title": title, "Priority": "high"}
                )
            except requests.RequestException as e:
                print(f"Ntfy notification failed for low stock: {e}")


@app.route('/logout')
def logout():
    session.pop('worker', None)
    flash("Logged out successfully!", "success")
    return redirect(url_for('login'))

@app.context_processor
def inject_logged_in_worker():
    worker = session.get('worker', None)
    return {'logged_in_worker': worker}



@app.route('/')
def home():
    # Check if the user is logged in
    if 'worker' not in session:
        return redirect(url_for('login'))
    # If logged in, show the home page
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    workers = Worker.query.all()  # Fetch all workers for the dropdown
    if request.method == 'POST':
        worker_name = request.form.get('worker_name')
        password = request.form.get('password')

        worker = Worker.query.filter_by(name=worker_name).first()
        if worker and password == "Bitcade":
            session['worker'] = worker.name
            flash("Logged in successfully!", "success")
            return redirect(url_for('home'))
        else:
            flash("Invalid worker name or password.", "error")

    return render_template('login.html', workers=workers)



@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # Add new worker
    if request.method == 'POST' and 'new_worker' in request.form:
        new_worker = request.form['new_worker']
        if new_worker and not Worker.query.filter_by(name=new_worker).first():
            worker = Worker(name=new_worker)
            db.session.add(worker)
            db.session.commit()
            flash(f"Worker '{new_worker}' added successfully! Password: 'Bitcade'", "success")
        else:
            flash("Worker name is required or already exists.", "error")

    # Remove existing worker
    if request.method == 'POST' and 'remove_worker' in request.form:
        remove_worker = request.form['remove_worker']
        worker = Worker.query.filter_by(name=remove_worker).first()
        if worker:
            db.session.delete(worker)
            db.session.commit()
            flash(f"Worker '{remove_worker}' removed successfully!", "success")
        else:
            flash("Worker not found.", "error")

    # Add new issue
    if request.method == 'POST' and 'new_issue' in request.form:
        new_issue = request.form['new_issue']
        if new_issue and not Issue.query.filter_by(description=new_issue).first():
            issue = Issue(description=new_issue)
            db.session.add(issue)
            db.session.commit()
            flash(f"Issue '{new_issue}' added successfully!", "success")
        else:
            flash("Issue name is required or already exists.", "error")

    # Remove existing issue
    if request.method == 'POST' and 'remove_issue' in request.form:
        remove_issue = request.form['remove_issue']
        issue = Issue.query.filter_by(description=remove_issue).first()
        if issue:
            db.session.delete(issue)
            db.session.commit()
            flash(f"Issue '{remove_issue}' removed successfully!", "success")
        else:
            flash("Issue not found.", "error")

    # --- Part Threshold Management ---
    if request.method == 'POST' and 'update_threshold' in request.form:
        part_name = request.form.get('part_name')
        try:
            threshold = int(request.form.get('threshold', 0))
            if threshold < 0:
                threshold = 0
            
            threshold_entry = PartThreshold.query.filter_by(part_name=part_name).first()
            if not threshold_entry:
                threshold_entry = PartThreshold(part_name=part_name, threshold=threshold)
                db.session.add(threshold_entry)
            else:
                threshold_entry.threshold = threshold
            
            db.session.commit()

            # --- Check for immediate low stock after threshold update ---
            # Get current stock count
            latest_entry = (db.session.query(PrintedPartsCount.count)
                            .filter_by(part_name=part_name)
                            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                            .first())
            
            current_stock = 0
            if latest_entry:
                current_stock = latest_entry[0]
            else:
                # Check if it's a hardware part with an initial count
                hardware_part = HardwarePart.query.filter_by(name=part_name).first()
                if hardware_part:
                    current_stock = hardware_part.initial_count

            # If stock is already below the new threshold, notify
            if threshold > 0 and current_stock <= threshold:
                try:
                    message = f"Stock for {part_name} is low ({current_stock} remaining, threshold is {threshold})."
                    title = "Low Stock Warning"
                    requests.post("https://ntfy.sh/PoolTableTracker",
                                  data=message,
                                  headers={"Title": title, "Priority": "high"})
                    flash(f"Low stock notification sent for {part_name}.", "info")
                except requests.RequestException as e:
                    print(f"Ntfy notification failed for low stock: {e}")
            # --- End check ---

            flash(f"Threshold for {part_name} updated to {threshold}.", "success")
        except ValueError:
            flash("Invalid threshold value.", "error")
        return redirect(url_for('admin'))

 # Fetch all existing hardware parts from the database
    hardware_parts = HardwarePart.query.all()

    # Check if user wants to add a new hardware part
    if request.method == 'POST' and 'new_hardware_part' in request.form:
        new_part_name = request.form['new_hardware_part'].strip()
        initial_count = int(request.form['initial_hardware_count'])

        # Check if part with the same name already exists
        existing_part = HardwarePart.query.filter_by(name=new_part_name).first()
        if existing_part:
            flash("Hardware part already exists.", "error")
        else:
            # Create and save the new hardware part
            new_part = HardwarePart(name=new_part_name, initial_count=initial_count)
            db.session.add(new_part)
            db.session.commit()
            flash(f"Hardware part '{new_part_name}' added successfully!", "success")

        # After adding a new part (or hitting an error), redirect or re-render
        return redirect(url_for('admin'))

    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    workers = Worker.query.all()
    issues = Issue.query.all()
    hardware_parts = HardwarePart.query.all()
    pods = CompletedPods.query.all()
    top_rails = TopRail.query.all()
    bodies = CompletedTable.query.all()

    # Gather all unique part names for threshold management
    all_parts_query1 = db.session.query(HardwarePart.name.label("part_name")).distinct()
    all_parts_query2 = db.session.query(PrintedPartsCount.part_name.label("part_name")).distinct()
    all_parts_union = all_parts_query1.union(all_parts_query2).all()
    all_part_names = sorted([name for (name,) in all_parts_union])

    # Get all current thresholds
    thresholds = PartThreshold.query.all()
    thresholds_map = {t.part_name: t.threshold for t in thresholds}

    if request.method == 'POST' and 'table' in request.form:
        table = request.form.get('table')
        entry_id = request.form.get('id')

        model = None
        if table == 'pods':
            model = CompletedPods
            # When deleting a pod, restore felt, carpet and tee nuts
            if 'delete' in request.form:
                pod = CompletedPods.query.get(entry_id)
                if pod:
                    # Determine if it's a 6ft pod
                    is_6ft = ' - 6' in pod.serial_number or '-6' in pod.serial_number
                    
                    # Determine which felt and carpet to restore
                    felt_part = "6ft Felt" if is_6ft else "7ft Felt"
                    carpet_part = "6ft Carpet" if is_6ft else "7ft Carpet"
                    
                    # Restore felt
                    felt_entry = PrintedPartsCount.query.filter_by(part_name=felt_part).order_by(
                        PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
                    if felt_entry:
                        felt_entry.count += 1
                    else:
                        new_felt = PrintedPartsCount(part_name=felt_part, count=1, 
                                                   date=datetime.utcnow().date(), 
                                                   time=datetime.utcnow().time())
                        db.session.add(new_felt)
                    
                    # Restore carpet
                    carpet_entry = PrintedPartsCount.query.filter_by(part_name=carpet_part).order_by(
                        PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
                    if carpet_entry:
                        carpet_entry.count += 1
                    else:
                        new_carpet = PrintedPartsCount(part_name=carpet_part, count=1,
                                                     date=datetime.utcnow().date(),
                                                     time=datetime.utcnow().time())
                        db.session.add(new_carpet)
                    
                    # Restore tee nuts
                    tee_nuts_entry = PrintedPartsCount.query.filter_by(part_name="M10x13mm Tee Nut").order_by(
                        PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
                    if tee_nuts_entry:
                        tee_nuts_entry.count += 16
                    else:
                        new_tee_nuts = PrintedPartsCount(part_name="M10x13mm Tee Nut", count=16,
                                                       date=datetime.utcnow().date(),
                                                       time=datetime.utcnow().time())
                        db.session.add(new_tee_nuts)
                    
                    flash(f"Stock restored: +1 {felt_part}, +1 {carpet_part}, +16 Tee Nuts", "success")
        elif table == 'top rails':
            model = TopRail
        elif table == 'bodies':
            model = CompletedTable

        if model:
            entry = model.query.get(entry_id)
            if entry:
                if 'update' in request.form:
                    # Note: updating worker here is still possible via admin, 
                    # but in normal usage worker is from session
                    entry.worker = request.form['worker']
                    entry.start_time = request.form['start_time']
                    entry.finish_time = request.form['finish_time']
                    entry.serial_number = request.form['serial_number']
                    entry.issue = request.form['issue']
                    entry.lunch = request.form['lunch']
                    db.session.commit()
                    flash(f"{table.title()} entry updated successfully!", "success")
                elif 'delete' in request.form:
                    db.session.delete(entry)
                    db.session.commit()
                    flash(f"{table.title()} entry deleted successfully!", "success")

        return redirect(url_for('admin'))

    return render_template(
        'admin.html',
        workers=workers,
        issues=issues,
        inventory=inventory,
        pods=pods,
        top_rails=top_rails,
        bodies=bodies,
        hardware_parts=hardware_parts,
        all_part_names=all_part_names,
        thresholds_map=thresholds_map
    )

@app.route('/dashboard')
def dashboard():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    today = datetime.utcnow().date()
    start_of_week = today - timedelta(days=today.weekday())
    start_of_month = today.replace(day=1)
    start_of_year = today.replace(month=1, day=1)

    def get_count(model, start_date=None):
        query = model.query
        if start_date:
            query = query.filter(model.date >= start_date)
        return query.count()

    top_rails_today = get_count(TopRail, today)
    top_rails_week = get_count(TopRail, start_of_week)
    top_rails_month = get_count(TopRail, start_of_month)
    top_rails_year = get_count(TopRail, start_of_year)

    bodies_today = get_count(CompletedTable, today)
    bodies_week = get_count(CompletedTable, start_of_week)
    bodies_month = get_count(CompletedTable, start_of_month)
    bodies_year = get_count(CompletedTable, start_of_year)

    pods_today = get_count(CompletedPods, today)
    pods_week = get_count(CompletedPods, start_of_week)
    pods_month = get_count(CompletedPods, start_of_month)
    pods_year = get_count(CompletedPods, start_of_year)

    def get_wood_count(section, start_date=None):
        query = WoodCount.query.filter_by(section=section)
        if start_date:
            query = query.filter(WoodCount.date >= start_date)
        return query.count()

    wood_counts = {
        "body": {
            "today": get_wood_count('Body', today),
            "week": get_wood_count('Body', start_of_week),
            "month": get_wood_count('Body', start_of_month),
            "year": get_wood_count('Body', start_of_year),
        },
        "pod_sides": {
            "today": get_wood_count('Pod Sides', today),
            "week": get_wood_count('Pod Sides', start_of_week),
            "month": get_wood_count('Pod Sides', start_of_month),
            "year": get_wood_count('Pod Sides', start_of_year),
        },
        "bases": {
            "today": get_wood_count('Bases', today),
            "week": get_wood_count('Bases', start_of_week),
            "month": get_wood_count('Bases', start_of_month),
            "year": get_wood_count('Bases', start_of_year),
        },
    }

    return render_template(
        'dashboard.html',
        top_rails={
            "today": top_rails_today,
            "week": top_rails_week,
            "month": top_rails_month,
            "year": top_rails_year,
        },
        bodies={
            "today": bodies_today,
            "week": bodies_week,
            "month": bodies_month,
            "year": bodies_year,
        },
        pods={
            "today": pods_today,
            "week": pods_week,
            "month": pods_month,
            "year": pods_year,
        },
        wood=wood_counts
    )

@app.route('/admin/mdf_inventory', methods=['GET', 'POST'])
def manage_mdf_inventory():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # Retrieve the inventory record or create one if it doesn't exist.
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
        db.session.add(inventory)
        db.session.commit()

    if request.method == 'POST':
        try:
            additional_plain_mdf = int(request.form['additional_plain_mdf'])
            additional_black_mdf = int(request.form['additional_black_mdf'])
            additional_plain_mdf_36 = int(request.form['additional_plain_mdf_36'])
            inventory.plain_mdf += additional_plain_mdf
            inventory.black_mdf += additional_black_mdf
            inventory.plain_mdf_36 += additional_plain_mdf_36
            db.session.commit()
            flash("MDF inventory updated successfully!", "success")
        except ValueError:
            flash("Please enter valid numbers for MDF quantities.", "error")

    return render_template('manage_mdf_inventory.html', inventory=inventory)

@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # ---------------------------------------------------------------------
    # 1) 3D PRINTED PARTS
    # ---------------------------------------------------------------------
    parts = [
        "Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder",
        "Small Ramp", "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp",
        "6ft Carpet", "7ft Carpet", "6ft Felt", "7ft Felt"  # Added new parts
    ]

    inventory_counts = {}
    for part in parts:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        inventory_counts[part] = latest_entry[0] if latest_entry else 0

    # ---------------------------------------------------------------------
    # 2) WOODEN PARTS
    # ---------------------------------------------------------------------
    total_body_cut = (
        db.session.query(WoodCount.count)
        .filter_by(section="Body")
        .order_by(WoodCount.date.desc(), WoodCount.time.desc())
        .first()
    )
    total_pod_sides_cut = (
        db.session.query(WoodCount.count)
        .filter_by(section="Pod Sides")
        .order_by(WoodCount.date.desc(), WoodCount.time.desc())
        .first()
    )
    total_bases_cut = (
        db.session.query(WoodCount.count)
        .filter_by(section="Bases")
        .order_by(WoodCount.date.desc(), WoodCount.time.desc())
        .first()
    )

    wooden_counts = {
        'body': total_body_cut[0] if total_body_cut else 0,
        'pod_sides': total_pod_sides_cut[0] if total_pod_sides_cut else 0,
        'bases': total_bases_cut[0] if total_bases_cut else 0
    }

    # ---------------------------------------------------------------------
    # 3) MONTHLY PRODUCTION REQUIREMENTS (3D PRINTED)
    # ---------------------------------------------------------------------
    today = datetime.utcnow().date()

    # Retrieve the production schedule for the current month using the new fields.
    schedule = ProductionSchedule.query.filter_by(year=today.year, month=today.month).first()
    if schedule:
        target_7ft = schedule.target_7ft
        target_6ft = schedule.target_6ft
    else:
        # Fallback defaults if no schedule is set.
        target_7ft = 60
        target_6ft = 60

    # Get all completed tables for the current month
    completed_tables = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == today.year,
        extract('month', CompletedTable.date) == today.month
    ).all()

    # Separate completed tables by size based on serial number.
    bodies_built_7ft = sum(1 for table in completed_tables if " - 6" not in table.serial_number)
    bodies_built_6ft = sum(1 for table in completed_tables if " - 6" in table.serial_number)

    # Define usage per table for each part.
    parts_usage_per_body = {
        "Large Ramp": 1,
        "Paddle": 1,
        "Laminate": 4,
        "Spring Mount": 1,
        "Spring Holder": 1,
        "Small Ramp": 1,
        "Cue Ball Separator": 1,
        "Bushing": 2,
        "6ft Cue Ball Separator": 1,
        "6ft Large Ramp": 1,
        "6ft Carpet": 1,    # Added new 6ft parts
        "6ft Felt": 1,
        "7ft Carpet": 1,    # Added new 7ft parts
        "7ft Felt": 2
        
    }

    # Calculate how many of each part have been used this month.
    parts_used_this_month = {}
    for part, usage in parts_usage_per_body.items():
        if part in ["Large Ramp", "Cue Ball Separator"]:
            parts_used_this_month[part] = bodies_built_7ft * usage
        elif part in ["6ft Large Ramp", "6ft Cue Ball Separator"]:
            parts_used_this_month[part] = bodies_built_6ft * usage
        else:
            parts_used_this_month[part] = (bodies_built_7ft + bodies_built_6ft) * usage

    # Determine the required total for each part based on production targets.
    parts_status = {}
    for part, usage in parts_usage_per_body.items():
        if part in ["Large Ramp", "Cue Ball Separator", "7ft Carpet", "7ft Felt"]:  # Added 7ft items
            required_total = target_7ft * usage
            completed_total = bodies_built_7ft * usage

        elif part in ["6ft Large Ramp", "6ft Cue Ball Separator", "6ft Carpet", "6ft Felt"]:  # Added 6ft items
            required_total = target_6ft * usage
            completed_total = bodies_built_6ft * usage

        else:
            required_total = (target_7ft + target_6ft) * usage
            completed_total = (bodies_built_7ft + bodies_built_6ft) * usage

        # IMPORTANT: inventory is exactly what's in stock now
        inventory_total = inventory_counts.get(part, 0)

        # How many parts you have available in total
        available_total = inventory_total + completed_total

        difference = available_total - required_total
        if difference >= 0:
            parts_status[part] = f"{difference} extras"
        else:
            parts_status[part] = f"{abs(difference)} left to make"

    # ---------------------------------------------------------------------
    # 4) TABLE PARTS
    # ---------------------------------------------------------------------
    table_parts = {
        "Table legs": 4, "Ball Gullies 1 (Untouched)": 2, "Ball Gullies 2": 1,
        "Ball Gullies 3": 1, "Ball Gullies 4": 1, "Ball Gullies 5": 1,
        "Feet": 4, "Triangle trim": 1, "White ball return trim": 1,
        "Color ball trim": 1, "Ball window trim": 1, "Aluminum corner": 4,
        "Chrome corner": 4, "Top rail trim short length": 4,
        "Top rail trim long length": 2, "Ramp 170mm": 1, "Ramp 158mm": 1,
        "Ramp 918mm": 1, "Ramp 376mm": 1, "Chrome handles": 1,
        "Center pockets": 2, "Corner pockets": 4, "Sticker Set": 1
    }

    table_parts_counts = {part: 0 for part in table_parts}
    for part in table_parts:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        table_parts_counts[part] = latest_entry[0] if latest_entry else 0

    tables_possible_per_part = {
        part: table_parts_counts[part] // req_per_table
        for part, req_per_table in table_parts.items()
    }
    max_tables_possible = min(tables_possible_per_part.values())

    # ---------------------------------------------------------------------
    # 5) HARDWARE PARTS (FROM DB)
    # ---------------------------------------------------------------------
    hardware_parts_query = HardwarePart.query.all()
    hardware_counts = {}
    for hp in hardware_parts_query:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=hp.name)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        hardware_counts[hp.name] = latest_entry[0] if latest_entry else hp.initial_count

    # ---------------------------------------------------------------------
    # 6) RENDER TEMPLATE
    # ---------------------------------------------------------------------
    return render_template(
        'inventory.html',
        inventory_counts=inventory_counts,
        wooden_counts=wooden_counts,
        parts_used_this_month=parts_used_this_month,
        parts_status=parts_status,
        table_parts_counts=table_parts_counts,
        max_tables_possible=max_tables_possible,
        tables_possible_per_part=tables_possible_per_part,
        hardware_counts=hardware_counts
    )


@app.route('/counting_chinese_parts', methods=['GET', 'POST'])
def counting_chinese_parts():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # List of "Table Parts" items
    table_parts = [
        "Table legs", "Ball Gullies 1 (Untouched)", "Ball Gullies 2", "Ball Gullies 3",
        "Ball Gullies 4", "Ball Gullies 5", "Feet", "Triangle trim",
        "White ball return trim", "Color ball trim", "Ball window trim",
        "Aluminum corner", "Chrome corner", "Top rail trim short length",
        "Top rail trim long length", "Ramp 170mm", "Ramp 158mm", "Ramp 918mm",
        "Chrome handles", "Center pockets", "Corner pockets", "Ramp 376mm", "Sticker Set"
    ]

    def get_table_parts_counts():
        """
        Return a dictionary of { part_name: current_count } for each part.
        We query a single row per part_name; if none exists, assume 0.
        """
        counts = {}
        for part in table_parts:
            existing_entry = (db.session.query(PrintedPartsCount)
                              .filter_by(part_name=part)
                              .first())
            counts[part] = existing_entry.count if existing_entry else 0
        return counts

    # Fetch current counts for all parts
    table_parts_counts = get_table_parts_counts()

    # Determine the currently selected part (default to first in list if none selected)
    selected_part = request.form.get('table_part', table_parts[0])
    action = request.form.get('action')  # e.g. 'increment', 'decrement', 'bulk'

    # Process form submission if we're in POST and have an 'action'
    if request.method == 'POST' and action:
        if selected_part not in table_parts_counts:
            flash("Invalid part selected.", "error")
            return redirect(url_for('counting_chinese_parts'))

        # Fetch the specific part entry to be updated
        existing_entry = (db.session.query(PrintedPartsCount)
                          .filter_by(part_name=selected_part)
                          .first())

        # If no entry exists, create one before proceeding
        if not existing_entry:
            existing_entry = PrintedPartsCount(
                part_name=selected_part,
                count=0,
                date=datetime.utcnow().date(),
                time=datetime.utcnow().time()
            )
            db.session.add(existing_entry)

        # Amount is only used for 'bulk' updates; default to 1 otherwise
        try:
            amount = int(request.form.get('amount', 1))
        except ValueError:
            flash("Amount must be a number.", "error")
            return redirect(url_for('counting_chinese_parts'))

        # Perform the requested action
        if action == 'increment':
            existing_entry.count += 1

        elif action == 'decrement':
            if existing_entry.count > 0:
                old_count = existing_entry.count
                existing_entry.count -= 1
                check_and_notify_low_stock(selected_part, old_count, existing_entry.count)
            else:
                flash("Cannot decrement below zero.", "error")

        elif action == 'bulk':
            # Positive bulk = add stock; negative bulk = remove stock if sufficient
            if amount < 0 and existing_entry.count < abs(amount):
                flash(f"Not enough stock to remove. Current count for '{selected_part}': {existing_entry.count}", "error")
                return redirect(url_for('counting_chinese_parts'))
            
            old_count = existing_entry.count
            existing_entry.count += amount
            if amount < 0:
                check_and_notify_low_stock(selected_part, old_count, existing_entry.count)

        else:
            flash("Invalid operation.", "error")
            return redirect(url_for('counting_chinese_parts'))

        # Update date/time so you can see the latest update
        existing_entry.date = datetime.utcnow().date()
        existing_entry.time = datetime.utcnow().time()

        # Commit changes
        db.session.commit()

        flash(f"{selected_part} updated successfully! New count: {existing_entry.count}", "success")
        
        # Re-fetch updated counts for display
        table_parts_counts = get_table_parts_counts()

    # Render the template with the current data
    return render_template(
        'counting_chinese_parts.html',
        table_parts=table_parts,
        table_parts_counts=table_parts_counts,
        selected_part=selected_part
    )


@app.route('/counting_hardware', methods=['GET', 'POST'])
def counting_hardware():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # 1. Fetch all hardware parts
    hardware_parts = HardwarePart.query.all()

    # 2. Build a dictionary of the latest known counts (or initial_count if none recorded)
    hardware_counts = {}
    for part in hardware_parts:
        latest_entry = (db.session.query(PrintedPartsCount.count)
                        .filter_by(part_name=part.name)
                        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                        .first())
        hardware_counts[part.name] = latest_entry[0] if latest_entry else part.initial_count

    # 3. Handle POST actions
    if request.method == 'POST':
        action = request.form.get('action')

        # ------------------------------------------------------
        # A) UPDATE "USED PER TABLE" for a hardware part (as float)
        # ------------------------------------------------------
        if action == 'update_usage':
            part_name = request.form['hardware_part']
            new_usage_str = request.form.get('usage_per_table', '0')

            try:
                # Convert usage to float to allow decimals like 0.5
                new_usage = float(new_usage_str)
            except ValueError:
                flash("Please provide a valid number (integer or decimal) for 'Used Per Table'.", "error")
                return redirect(url_for('counting_hardware'))

            # Find the HardwarePart in the database
            hardware_part = HardwarePart.query.filter_by(name=part_name).first()
            if hardware_part:
                hardware_part.used_per_table = new_usage
                db.session.commit()
                flash(f"Updated usage for '{part_name}' to {new_usage} per table.", "success")
            else:
                flash(f"Hardware part '{part_name}' not found.", "error")

            return redirect(url_for('counting_hardware'))

        # ------------------------------------------------------
        # B) INCREMENT / DECREMENT / BULK UPDATE the count
        # ------------------------------------------------------
        elif action in ['increment', 'decrement', 'bulk']:
            part_name = request.form['hardware_part']
            # For increment/decrement, default to 1; for bulk, read from 'amount'
            amount_str = request.form.get('amount', '1')
            try:
                amount = int(amount_str)
            except ValueError:
                flash("Amount must be a number.", "error")
                return redirect(url_for('counting_hardware'))

            # Validate the selected part
            if part_name not in hardware_counts:
                flash(f"Invalid hardware part: {part_name}", "error")
                return redirect(url_for('counting_hardware'))

            current_count = hardware_counts[part_name]
            new_count = current_count

            if action == 'increment':
                new_count += amount
            elif action == 'decrement':
                if current_count < amount:
                    flash(f"Not enough stock to remove. Current count for '{part_name}': {current_count}", "error")
                    return redirect(url_for('counting_hardware'))
                new_count -= amount
            elif action == 'bulk':
                if amount < 0 and current_count < abs(amount):
                    flash(f"Not enough stock to remove. Current count for '{part_name}': {current_count}", "error")
                    return redirect(url_for('counting_hardware'))
                new_count += amount

            # Check for low stock if count is decreasing
            if new_count < current_count:
                check_and_notify_low_stock(part_name, current_count, new_count)

            # Record the new count in the PrintedPartsCount table
            new_entry = PrintedPartsCount(
                part_name=part_name,
                count=new_count,
                date=datetime.utcnow().date(),
                time=datetime.utcnow().time()
            )
            db.session.add(new_entry)
            db.session.commit()

            flash(f"{part_name} updated successfully! New count: {new_count}", "success")

    # 4. Render the template
    return render_template(
        'counting_hardware.html',
        hardware_parts=hardware_parts,
        hardware_counts=hardware_counts
    )





    
from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime, date
from sqlalchemy.exc import IntegrityError
from sqlalchemy import extract, func, distinct

@app.route('/pods', methods=['GET', 'POST'])
def pods():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))
    
    issues = [issue.description for issue in Issue.query.all()]
    
    if request.method == 'POST':
        worker = session['worker']
        serial_number = request.form['serial_number']
        issue_text = request.form['issue']
        lunch = request.form['lunch']
        size_selector = request.form.get('size_selector', '7ft')  # Get the size from dropdown
        
        # Ensure serial number has proper format
        if size_selector == '6ft' and ' - 6' not in serial_number and '-6' not in serial_number:
            # Add the 6ft suffix if not already present
            serial_number = serial_number + ' - 6'
        
        try:
            start_time = datetime.strptime(request.form['start_time'], "%H:%M").time()
        except ValueError:
            start_time = datetime.strptime(request.form['start_time'], "%H:%M:%S").time()
        try:
            finish_time = datetime.strptime(request.form['finish_time'], "%H:%M").time()
        except ValueError:
            finish_time = datetime.strptime(request.form['finish_time'], "%H:%M:%S").time()
        
        # Determine if it's a 6ft pod based on serial number or size selector
        is_6ft = size_selector == '6ft' or ' - 6' in serial_number or '-6' in serial_number
        
        # Determine which felt and carpet to deduct
        felt_part = "6ft Felt" if is_6ft else "7ft Felt"
        carpet_part = "6ft Carpet" if is_6ft else "7ft Carpet"
        
        # Check and deduct felt
        felt_entry = PrintedPartsCount.query.filter_by(part_name=felt_part).order_by(
            PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        if not felt_entry or felt_entry.count < 1:
            flash(f"Not enough {felt_part} in stock!", "error")
            return redirect(url_for('pods'))
        
        # Check and deduct carpet
        carpet_entry = PrintedPartsCount.query.filter_by(part_name=carpet_part).order_by(
            PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        if not carpet_entry or carpet_entry.count < 1:
            flash(f"Not enough {carpet_part} in stock!", "error")
            return redirect(url_for('pods'))
        
        # Check and deduct Tee Nuts
        tee_nuts_entry = PrintedPartsCount.query.filter_by(part_name="M10x13mm Tee Nut").order_by(
            PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        if not tee_nuts_entry or tee_nuts_entry.count < 16:
            flash("Not enough M10x13mm Tee Nuts in stock! Need 16 per pod.", "error")
            return redirect(url_for('pods'))
        
        try:
            # Create and save the new pod
            new_pod = CompletedPods(
                worker=session['worker'],
                start_time=start_time,
                finish_time=finish_time,
                serial_number=serial_number,
                issue=issue_text,
                lunch=lunch,
                date=date.today()
            )
            
            # Actually deduct the parts now
            old_felt_count = felt_entry.count
            felt_entry.count -= 1
            check_and_notify_low_stock(felt_part, old_felt_count, felt_entry.count)

            old_carpet_count = carpet_entry.count
            carpet_entry.count -= 1
            check_and_notify_low_stock(carpet_part, old_carpet_count, carpet_entry.count)

            old_tee_nuts_count = tee_nuts_entry.count
            tee_nuts_entry.count -= 16  # Added this line to deduct the Tee Nuts
            check_and_notify_low_stock("M10x13mm Tee Nut", old_tee_nuts_count, tee_nuts_entry.count)

            db.session.add(new_pod)
            db.session.commit()
            flash(f"Pod entry added successfully! Deducted 1 {felt_part}, 1 {carpet_part}, and 16 M10x13mm Tee Nuts", "success")

            start_dt = datetime.combine(date.today(), start_time)
            finish_dt = datetime.combine(date.today(), finish_time)

            # Adjust for lunch break
            if lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            time_taken = finish_dt - start_dt
            time_taken_str = str(time_taken)[:-3]  # Trim seconds if you want HH:MM format

            # --- NTFY Notification ---
            size = "6ft" if is_6ft else "7ft"
            message = f"Serial: {serial_number}\nTime Taken: {time_taken_str}"
            title = f"Pod Completed: {size}"
            try:
                requests.post("https://ntfy.sh/PoolTableTracker",
                              data=message,
                              headers={"Title": title})
            except requests.RequestException as e:
                print(f"Ntfy notification failed: {e}")
            # --- End NTFY Notification ---
        except IntegrityError:
            db.session.rollback()
            flash("Error: Serial number already exists. Please use a unique serial number.", "error")
            return redirect(url_for('pods'))

        return redirect(url_for('pods'))

    today = date.today()
    # Retrieve today's pods.
    completed_pods = CompletedPods.query.filter_by(date=today).all()
    last_entry = CompletedPods.query.order_by(CompletedPods.id.desc()).first()
    current_time = last_entry.finish_time.strftime("%H:%M") if last_entry else datetime.now().strftime("%H:%M")
    
    # Retrieve all pods for the current month.
    all_pods_this_month = CompletedPods.query.filter(
        extract('year', CompletedPods.date) == today.year,
        extract('month', CompletedPods.date) == today.month
    ).all()
    pods_this_month = len(all_pods_this_month)
    
    # Helper function: classify a pod as 6ft if its serial number (with spaces removed) ends with "-6"
    def is_6ft(serial):
        return serial.replace(" ", "").endswith("-6")
    
    current_production_pods_6ft = sum(1 for pod in all_pods_this_month if is_6ft(pod.serial_number))
    current_production_pods_7ft = pods_this_month - current_production_pods_6ft
    
    # Helper: last 5 working days (Monday-Friday)
    def get_last_n_working_days(n, reference_date):
        working_days = []
        d = reference_date
        while len(working_days) < n:
            if d.weekday() < 5:
                working_days.append(d)
            d -= timedelta(days=1)
        return working_days
    
    last_working_days = get_last_n_working_days(5, today)
    daily_history = (
        db.session.query(
            CompletedPods.date,
            func.count(CompletedPods.id).label('count'),
            func.group_concat(CompletedPods.serial_number, ', ').label('serial_numbers')
        )
        .filter(CompletedPods.date.in_(last_working_days))
        .group_by(CompletedPods.date)
        .order_by(CompletedPods.date.desc())
        .all()
    )
    daily_history_formatted = [
        {
            "date": row.date.strftime("%A %d/%m/%y"),
            "count": row.count,
            "serial_numbers": row.serial_numbers
        }
        for row in daily_history
    ]
    
    monthly_totals = (
        db.session.query(
            extract('year', CompletedPods.date).label('year'),
            extract('month', CompletedPods.date).label('month'),
            func.count(CompletedPods.id).label('total')
        )
        .group_by('year', 'month')
        .order_by(desc(extract('year', CompletedPods.date)), desc(extract('month', CompletedPods.date)))
        .all()
    )
    monthly_totals_formatted = []
    for row in monthly_totals:
        yr = int(row.year)
        mo = int(row.month)
        total_pods = row.total
        last_day = today.day if (yr == today.year and mo == today.month) else monthrange(yr, mo)[1]
        work_days = sum(1 for day in range(1, last_day + 1) if date(yr, mo, day).weekday() < 5)
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_pod = (cumulative_working_hours / total_pods if total_pods > 0 else None)
        if avg_hours_per_pod is not None:
            hours = int(avg_hours_per_pod)
            minutes = int((avg_hours_per_pod - hours) * 60)
            seconds = int((((avg_hours_per_pod - hours) * 60) - minutes) * 60)
            avg_hours_per_pod_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            avg_hours_per_pod_formatted = "N/A"
        monthly_totals_formatted.append({
            "month": date(year=yr, month=mo, day=1).strftime("%B %Y"),
            "count": total_pods,
            "average_hours_per_pod": avg_hours_per_pod_formatted
        })
    
    # Retrieve production schedule targets for current month
    schedule = ProductionSchedule.query.filter_by(year=today.year, month=today.month).first()
    if schedule:
        target_7ft = schedule.target_7ft
        target_6ft = schedule.target_6ft
    else:
        target_7ft = 60
        target_6ft = 60
    
    # Next serial number generation logic
    last_pod = CompletedPods.query.order_by(CompletedPods.id.desc()).first()
    next_serial_number = "1000"  # Default value
    default_size = '7ft'  # Default size
    
    if last_pod:
        # Determine the next serial number and the size
        serial = last_pod.serial_number
        
        # Check if it's a 6ft pod
        if is_6ft(serial):
            default_size = '6ft'
            # Extract base serial number without the size suffix
            serial_parts = serial.split('-')
            if len(serial_parts) >= 2:
                try:
                    base_serial = serial_parts[0].strip()
                    next_serial_number = str(int(base_serial) + 1)
                except ValueError:
                    next_serial_number = "1000"
        else:
            # For 7ft, just increment the number
            try:
                if '-' in serial:
                    base_serial = serial.split('-')[0].strip()
                    next_serial_number = str(int(base_serial) + 1)
                else:
                    next_serial_number = str(int(serial) + 1)
            except ValueError:
                next_serial_number = "1000"
    
    return render_template(
        'pods.html',
        issues=issues,
        current_time=current_time,
        completed_pods=completed_pods,
        pods_this_month=pods_this_month,
        current_production_pods_7ft=current_production_pods_7ft,
        current_production_pods_6ft=current_production_pods_6ft,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted,
        next_serial_number=next_serial_number,
        target_7ft=target_7ft,
        target_6ft=target_6ft,
        default_size=default_size
    )

@app.route('/admin/raw_data', methods=['GET', 'POST'])
def manage_raw_data():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    serial_number_query = request.args.get('serial_number')
    if serial_number_query:
        # Normalize the search query by removing spaces
        normalized_query = serial_number_query.replace(" ", "")
        
        # Use LIKE for partial matching with wildcards
        pods = CompletedPods.query.filter(CompletedPods.serial_number.like(f'%{serial_number_query}%')).all()
        top_rails = TopRail.query.filter(TopRail.serial_number.like(f'%{serial_number_query}%')).all()
        bodies = CompletedTable.query.filter(CompletedTable.serial_number.like(f'%{serial_number_query}%')).all()
        
        # If no results found with spaces, try without spaces (normalized search)
        if not (pods or top_rails or bodies):
            pods = CompletedPods.query.all()
            top_rails = TopRail.query.all()
            bodies = CompletedTable.query.all()
            
            # Filter in Python to handle normalized comparison
            pods = [pod for pod in pods if normalized_query in pod.serial_number.replace(" ", "")]
            top_rails = [rail for rail in top_rails if normalized_query in rail.serial_number.replace(" ", "")]
            bodies = [body for body in bodies if normalized_query in body.serial_number.replace(" ", "")]
    else:
        pods = CompletedPods.query.all()
        top_rails = TopRail.query.all()
        bodies = CompletedTable.query.all()

    if request.method == 'POST':
        table = request.form.get('table')
        entry_id = request.form.get('id')
        entry = None
        if table == 'pods':
            entry = CompletedPods.query.get(entry_id)
        elif table == 'top_rails':
            entry = TopRail.query.get(entry_id)
        elif table == 'bodies':
            entry = CompletedTable.query.get(entry_id)

        if entry:
            if 'delete' in request.form:
                # Revert inventory if deleting a table or top rail entry.
                if table == 'bodies':
                    # Parts used for a completed table
                    parts_used = {
                        "Large Ramp": 1,
                        "Paddle": 1,
                        "Laminate": 4,
                        "Spring Mount": 1,
                        "Spring Holder": 1,
                        "Small Ramp": 1,
                        "Cue Ball Separator": 1,
                        "Bushing": 2,
                        "Table legs": 4,
                        "Ball Gullies 1 (Untouched)": 2,
                        "Ball Gullies 2": 1,
                        "Ball Gullies 3": 1,
                        "Ball Gullies 4": 1,
                        "Ball Gullies 5": 1,
                        "Feet": 4,
                        "Triangle trim": 1,
                        "White ball return trim": 1,
                        "Color ball trim": 1,
                        "Ball window trim": 1,
                        "Aluminum corner": 4,
                        "Ramp 170mm": 1,
                        "Ramp 158mm": 1,
                        "Ramp 918mm": 1,
                        "Ramp 376mm": 1,
                        "Chrome handles": 1,
                        "Sticker Set": 1
                    }
                    # If the table was a 6ft table, adjust the parts used.
                    if " - 6" in entry.serial_number:
                        parts_used.pop("Large Ramp", None)
                        parts_used.pop("Cue Ball Separator", None)
                        parts_used["6ft Large Ramp"] = 1
                        parts_used["6ft Cue Ball Separator"] = 1

                    # Revert each part's inventory.
                    for part_name, qty in parts_used.items():
                        inventory_entry = PrintedPartsCount.query.filter_by(
                            part_name=part_name
                        ).order_by(PrintedPartsCount.date.desc(),
                                   PrintedPartsCount.time.desc()).first()
                        if inventory_entry:
                            inventory_entry.count += qty
                        else:
                            new_inv = PrintedPartsCount(
                                part_name=part_name,
                                count=qty,
                                date=datetime.utcnow().date(),
                                time=datetime.utcnow().time()
                            )
                            db.session.add(new_inv)
                    db.session.commit()

                elif table == 'top_rails':
                    # Parts used for a top rail
                    parts_used = {
                        "Top rail trim long length": 2,
                        "Top rail trim short length": 4,
                        "Chrome corner": 4,
                        "Center pockets": 2,
                        "Corner pockets": 4
                    }
                    # Revert each part's inventory for the top rail.
                    for part_name, qty in parts_used.items():
                        inventory_entry = PrintedPartsCount.query.filter_by(
                            part_name=part_name
                        ).order_by(PrintedPartsCount.date.desc(),
                                   PrintedPartsCount.time.desc()).first()
                        if inventory_entry:
                            inventory_entry.count += qty
                        else:
                            new_inv = PrintedPartsCount(
                                part_name=part_name,
                                count=qty,
                                date=datetime.utcnow().date(),
                                time=datetime.utcnow().time()
                            )
                            db.session.add(new_inv)
                    db.session.commit()

                # Now delete the entry.
                # If deleting a body, also update the table stock
                if table == 'bodies':
                    # Determine if it's a 6ft or 7ft body using the normalized approach
                    if entry.serial_number.replace(" ", "").endswith("-6"):
                        stock_type = 'body_6ft'
                    else:
                        stock_type = 'body_7ft'
                    
                    # Update the stock count
                    stock_entry = TableStock.query.filter_by(type=stock_type).first()
                    if stock_entry and stock_entry.count > 0:
                        stock_entry.count -= 1
                        db.session.commit()
                # If deleting a top rail, also update the table stock
                elif table == 'top_rails':
                    # Determine if it's a 6ft or 7ft top rail using the normalized approach
                    if entry.serial_number.replace(" ", "").endswith("-6"):
                        stock_type = 'top_rail_6ft'
                    else:
                        stock_type = 'top_rail_7ft'
                    
                    # Update the stock count
                    stock_entry = TableStock.query.filter_by(type=stock_type).first()
                    if stock_entry and stock_entry.count > 0:
                        stock_entry.count -= 1
                        db.session.commit()
                
                db.session.delete(entry)
                db.session.commit()
                flash(f"{table.title()} entry deleted successfully!", "success")
            else:
                # Update logic for non-deletion operations
                if table == 'pods':
                    try:
                        entry.start_time = datetime.strptime(request.form.get('start_time'), "%H:%M").time()
                        entry.finish_time = datetime.strptime(request.form.get('finish_time'), "%H:%M").time()
                    except ValueError:
                        flash("Invalid time format. Please use HH:MM.", "error")
                        return redirect(url_for('manage_raw_data', serial_number=serial_number_query))
                else:
                    entry.start_time = request.form.get('start_time')
                    entry.finish_time = request.form.get('finish_time')

                entry.worker = request.form.get('worker')
                entry.serial_number = request.form.get('serial_number')
                entry.issue = request.form.get('issue')
                entry.lunch = request.form.get('lunch')

                date_input = request.form.get('date')
                if date_input:
                    try:
                        entry.date = datetime.strptime(date_input, "%Y-%m-%d").date()
                    except ValueError:
                        flash("Invalid date format. Please use YYYY-MM-DD.", "error")
                        return redirect(url_for('manage_raw_data', serial_number=serial_number_query))

                db.session.commit()
                flash(f"{table.capitalize()} entry updated successfully!", "success")

        return redirect(url_for('manage_raw_data', serial_number=serial_number_query))

    return render_template('admin_raw_data.html', pods=pods, top_rails=top_rails, bodies=bodies)

@app.route('/counting_wood', methods=['GET', 'POST'])
def counting_wood():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # Ensure MDF inventory record exists.
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
        db.session.add(inventory)
        db.session.commit()

    # Get DST info for the UK
    def is_dst_active():
        # Simple DST calculation for UK:
        # DST starts last Sunday of March and ends last Sunday of October
        now = datetime.now()
        year = now.year
        
        # Last Sunday of March
        march_end = datetime(year, 3, 31)
        while march_end.weekday() != 6:  # 6 = Sunday
            march_end = march_end - timedelta(days=1)
        
        # Last Sunday of October
        october_end = datetime(year, 10, 31)
        while october_end.weekday() != 6:  # 6 = Sunday
            october_end = october_end - timedelta(days=1)
        
        return march_end <= now < october_end
    
    # Determine the hour offset based on DST
    hour_offset = 1 if is_dst_active() else 0

    today = datetime.now().date()
    previous_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    current_month = today.replace(day=1)
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    available_months = [
        (previous_month.strftime("%Y-%m"), previous_month.strftime("%B %Y")),
        (current_month.strftime("%Y-%m"), current_month.strftime("%B %Y")),
        (next_month.strftime("%Y-%m"), next_month.strftime("%B %Y"))
    ]
    # Selected month is taken from form or query string.
    selected_month = request.form.get('month') or request.args.get('month', current_month.strftime("%Y-%m"))
    selected_year, selected_month_num = map(int, selected_month.split('-'))
    month_start_date = date(selected_year, selected_month_num, 1)
    month_end_date = date(selected_year, selected_month_num, monthrange(selected_year, selected_month_num)[1])

    # Define wood counting sections.
    # For each group ("7ft" and "6ft"), we have these five items.
    sections = {
        "7ft": ["Body", "Pod Sides", "Bases", "Top Rail Pieces Short", "Top Rail Pieces Long"],
        "6ft": ["Body", "Pod Sides", "Bases", "Top Rail Pieces Short", "Top Rail Pieces Long"]
    }

    if request.method == 'POST' and 'section' in request.form:
        # Expect a section value like "7ft - Body" or "6ft - Top Rail Pieces Long"
        section = request.form['section']
        action = request.form.get('action', 'increment')
        current_time = datetime.now().time()

        # Retrieve (or create) the monthly wood count record for this section.
        monthly_entry = WoodCount.query.filter(
            WoodCount.section == section,
            WoodCount.date >= month_start_date,
            WoodCount.date <= month_end_date
        ).first()
        if not monthly_entry:
            monthly_entry = WoodCount(section=section, count=0, date=month_start_date, time=current_time)
            db.session.add(monthly_entry)

        # Create a new wood count log entry for today.
        new_entry = WoodCount(section=section, count=0, date=today, time=current_time)

        # --- Special logic for Top Rail Pieces ---
        if "Top Rail Pieces" in section:
            # For any top rail piece cut, deduct 1 sheet of 36mm Plain MDF per cut (or per unit in bulk)
            if action in ['increment', 'bulk_increment']:
                # Determine the number of cuts (1 for increment; bulk_amount for bulk)
                multiplier = 1
                if action == 'bulk_increment':
                    try:
                        multiplier = int(request.form.get('bulk_amount', 0))
                    except ValueError:
                        flash("Please enter a valid bulk amount.", "error")
                        return redirect(url_for('counting_wood', month=selected_month))
                    if multiplier <= 0:
                        flash("Please enter a valid bulk amount.", "error")
                        return redirect(url_for('counting_wood', month=selected_month))
                if inventory.plain_mdf_36 < multiplier:
                    flash("Not enough 36mm Plain MDF to perform this cut.", "error")
                    return redirect(url_for('counting_wood', month=selected_month))
                inventory.plain_mdf_36 -= multiplier
            elif action == 'decrement':
                # When decrementing, we add back one sheet per cut reversed.
                inventory.plain_mdf_36 += 1

            # Yield logic for Top Rail Pieces:
            if action == 'increment':
                if "Long" in section:
                    # A long cut yields: +8 to Long and +3 to the corresponding Short for 6ft, or +2 for 7ft
                    monthly_entry.count += 8
                    new_entry.count = 8
                    # Update the corresponding Short section.
                    corresponding_section = section.replace("Long", "Short")
                    short_entry = WoodCount.query.filter(
                        WoodCount.section == corresponding_section,
                        WoodCount.date >= month_start_date,
                        WoodCount.date <= month_end_date
                    ).first()
                    if not short_entry:
                        short_entry = WoodCount(section=corresponding_section, count=0, date=month_start_date, time=current_time)
                        db.session.add(short_entry)
                    
                    # Check if it's 6ft or 7ft section
                    if "6ft" in section:
                        short_count = 3
                    else:  # 7ft section
                        short_count = 2
                        
                    short_entry.count += short_count
                    # Also log a separate entry for the short yield.
                    new_short = WoodCount(section=corresponding_section, count=short_count, date=today, time=current_time)
                    db.session.add(new_short)
                elif "Short" in section:
                    # A short cut yields: +16 to Short.
                    monthly_entry.count += 16
                    new_entry.count = 16

            elif action == 'decrement':
                if "Long" in section:
                    if monthly_entry.count >= 8:
                        monthly_entry.count -= 8
                        new_entry.count = -8
                        # Also update the corresponding Short section.
                        corresponding_section = section.replace("Long", "Short")
                        short_entry = WoodCount.query.filter(
                            WoodCount.section == corresponding_section,
                            WoodCount.date >= month_start_date,
                            WoodCount.date <= month_end_date
                        ).first()
                        
                        # Check if it's 6ft or 7ft section
                        if "6ft" in section:
                            short_count = 3
                        else:  # 7ft section
                            short_count = 2
                            
                        if short_entry and short_entry.count >= short_count:
                            short_entry.count -= short_count
                            new_short = WoodCount(section=corresponding_section, count=-short_count, date=today, time=current_time)
                            db.session.add(new_short)
                        else:
                            flash("Not enough count in the corresponding Short section to decrement.", "error")
                            return redirect(url_for('counting_wood', month=selected_month))
                    else:
                        flash("Cannot decrement below zero for Long top rail pieces.", "error")
                        return redirect(url_for('counting_wood', month=selected_month))
                elif "Short" in section:
                    if monthly_entry.count >= 16:
                        monthly_entry.count -= 16
                        new_entry.count = -16
                    else:
                        flash("Cannot decrement below zero for Short top rail pieces.", "error")
                        return redirect(url_for('counting_wood', month=selected_month))
            elif action == 'bulk_increment':
                try:
                    bulk_amount = int(request.form.get('bulk_amount', 0))
                except ValueError:
                    flash("Please enter a valid bulk amount.", "error")
                    return redirect(url_for('counting_wood', month=selected_month))
                if bulk_amount > 0:
                    if "Long" in section:
                        monthly_entry.count += bulk_amount * 8
                        new_entry.count = bulk_amount * 8
                        # Update corresponding Short section.
                        corresponding_section = section.replace("Long", "Short")
                        short_entry = WoodCount.query.filter(
                            WoodCount.section == corresponding_section,
                            WoodCount.date >= month_start_date,
                            WoodCount.date <= month_end_date
                        ).first()
                        if not short_entry:
                            short_entry = WoodCount(section=corresponding_section, count=0, date=month_start_date, time=current_time)
                            db.session.add(short_entry)
                        
                        # Check if it's 6ft or 7ft section
                        if "6ft" in section:
                            short_count = 3
                        else:  # 7ft section
                            short_count = 2
                        
                        short_entry.count += bulk_amount * short_count
                        new_short = WoodCount(section=corresponding_section, count=bulk_amount * short_count, date=today, time=current_time)
                        db.session.add(new_short)
                    elif "Short" in section:
                        monthly_entry.count += bulk_amount * 16
                        new_entry.count = bulk_amount * 16
                    # Deduct one sheet per cut in bulk.
                    if inventory.plain_mdf_36 < bulk_amount:
                        flash("Insufficient 36mm Plain MDF for bulk operation.", "error")
                        return redirect(url_for('counting_wood', month=selected_month))
                    inventory.plain_mdf_36 -= bulk_amount
                else:
                    flash("Please enter a valid bulk amount.", "error")
                    return redirect(url_for('counting_wood', month=selected_month))
            else:
                flash("Invalid action.", "error")
                return redirect(url_for('counting_wood', month=selected_month))

        else:
            # --- Original logic for sections other than Top Rail Pieces ---
            if action == 'increment':
                monthly_entry.count += 1
                new_entry.count = 1
                if section.endswith("Body") and inventory.black_mdf > 0:
                    inventory.black_mdf -= 1
                elif (section.endswith("Pod Sides") or section.endswith("Bases")) and inventory.plain_mdf > 0:
                    inventory.plain_mdf -= 1
                # No additional inventory change for other sections.
            elif action == 'decrement':
                if monthly_entry.count > 0:
                    monthly_entry.count -= 1
                    new_entry.count = -1
                    if section.endswith("Body"):
                        inventory.black_mdf += 1
                    elif (section.endswith("Pod Sides") or section.endswith("Bases")):
                        inventory.plain_mdf += 1
                else:
                    flash("Cannot decrement below zero.", "error")
                    return redirect(url_for('counting_wood', month=selected_month))
            elif action == 'bulk_increment':
                try:
                    bulk_amount = int(request.form.get('bulk_amount', 0))
                except ValueError:
                    flash("Please enter a valid bulk amount.", "error")
                    return redirect(url_for('counting_wood', month=selected_month))
                if bulk_amount > 0:
                    if section.endswith("Body"):
                        if inventory.black_mdf >= bulk_amount:
                            inventory.black_mdf -= bulk_amount
                        else:
                            flash("Insufficient inventory for bulk operation.", "error")
                            return redirect(url_for('counting_wood', month=selected_month))
                    elif section.endswith("Pod Sides") or section.endswith("Bases"):
                        if inventory.plain_mdf >= bulk_amount:
                            inventory.plain_mdf -= bulk_amount
                        else:
                            flash("Insufficient inventory for bulk operation.", "error")
                            return redirect(url_for('counting_wood', month=selected_month))
                    monthly_entry.count += bulk_amount
                    new_entry.count = bulk_amount
                else:
                    flash("Please enter a valid bulk amount.", "error")
                    return redirect(url_for('counting_wood', month=selected_month))
            else:
                flash("Invalid action.", "error")
                return redirect(url_for('counting_wood', month=selected_month))

        db.session.add(new_entry)
        db.session.commit()
        return redirect(url_for('counting_wood', month=selected_month))

    # --- GET request handling ---
    # Build a counts dictionary for display.
    counts = {}
    for group, items in sections.items():
        for item in items:
            sec = f"{group} - {item}"
            entry = WoodCount.query.filter(
                WoodCount.section == sec,
                WoodCount.date >= month_start_date,
                WoodCount.date <= month_end_date
            ).first()
            counts[sec] = entry.count if entry else 0

    # Weekly summary: Sum the counts for each weekday over the current week.
    start_of_week = today - timedelta(days=today.weekday())
    weekly_summary = {day: 0 for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']}
    daily_entries = WoodCount.query.filter(
        WoodCount.date >= start_of_week,
        WoodCount.date <= today
    ).all()
    for entry in daily_entries:
        weekday = entry.date.strftime("%A")
        weekly_summary[weekday] += entry.count

    # Calculate weekly sheets cut - CORRECTED
    weekly_sheets_cut = 0

    # Count Body, Pod Sides, and Bases pieces (each positive entry = 1 sheet)
    weekly_regular_entries = WoodCount.query.filter(
        WoodCount.date >= start_of_week,
        WoodCount.date <= today,
        (WoodCount.section.like("% - Body") | 
         WoodCount.section.like("% - Pod Sides") | 
         WoodCount.section.like("% - Bases")),
        WoodCount.count > 0
    ).all()

    # Each positive entry represents 1 sheet cut
    weekly_sheets_cut += len(weekly_regular_entries)

    # Count Top Rail Pieces sheets - CORRECTED LOGIC
    # For Long pieces: each entry represents 1 sheet (regardless of count value)
    weekly_long_entries = WoodCount.query.filter(
        WoodCount.date >= start_of_week,
        WoodCount.date <= today,
        WoodCount.section.like("% - Top Rail Pieces Long"),
        WoodCount.count > 0
    ).all()

    # Each Long entry represents 1 sheet cut (the count value is pieces produced, not sheets)
    weekly_sheets_cut += len(weekly_long_entries)

    # For Short pieces: only count standalone cuts (not ones generated from Long cuts)
    weekly_short_entries = WoodCount.query.filter(
        WoodCount.date >= start_of_week,
        WoodCount.date <= today,
        WoodCount.section.like("% - Top Rail Pieces Short"),
        WoodCount.count > 0
    ).all()

    # Check each Short entry to see if it was a standalone cut
    for short_entry in weekly_short_entries:
        # Check if there's a corresponding Long entry on the same date
        corresponding_long_section = short_entry.section.replace("Short", "Long")
        long_entry_same_date = WoodCount.query.filter(
            WoodCount.date == short_entry.date,
            WoodCount.section == corresponding_long_section,
            WoodCount.count > 0
        ).first()
        
        if not long_entry_same_date:
            # This was a standalone Short cut, count it as 1 sheet
            weekly_sheets_cut += 1

    # Calculate monthly sheets cut - CORRECTED
    monthly_sheets_cut = 0

    # Count Body, Pod Sides, and Bases pieces (each positive entry = 1 sheet)
    monthly_regular_entries = WoodCount.query.filter(
        WoodCount.date >= month_start_date,
        WoodCount.date <= month_end_date,
        (WoodCount.section.like("% - Body") | 
         WoodCount.section.like("% - Pod Sides") | 
         WoodCount.section.like("% - Bases")),
        WoodCount.count > 0
    ).all()

    monthly_sheets_cut += len(monthly_regular_entries)

    # Count Top Rail Pieces sheets - CORRECTED LOGIC
    monthly_long_entries = WoodCount.query.filter(
        WoodCount.date >= month_start_date,
        WoodCount.date <= month_end_date,
        WoodCount.section.like("% - Top Rail Pieces Long"),
        WoodCount.count > 0
    ).all()

    monthly_sheets_cut += len(monthly_long_entries)

    # For Short pieces: only count standalone cuts
    monthly_short_entries = WoodCount.query.filter(
        WoodCount.date >= month_start_date,
        WoodCount.date <= month_end_date,
        WoodCount.section.like("% - Top Rail Pieces Short"),
        WoodCount.count > 0
    ).all()

    for short_entry in monthly_short_entries:
        corresponding_long_section = short_entry.section.replace("Short", "Long")
        long_entry_same_date = WoodCount.query.filter(
            WoodCount.date == short_entry.date,
            WoodCount.section == corresponding_long_section,
            WoodCount.count > 0
        ).first()
        
        if not long_entry_same_date:
            monthly_sheets_cut += 1
            
    # Break down monthly data by weeks
    weekly_breakdown = {}
    
    # Define a function to count sheets for a specific date range - CORRECTED
    def count_sheets_for_range(start_date, end_date):
        sheet_count = 0
        
        # Count Body, Pod Sides, and Bases pieces (each positive entry = 1 sheet)
        regular_entries = WoodCount.query.filter(
            WoodCount.date >= start_date,
            WoodCount.date <= end_date,
            (WoodCount.section.like("% - Body") | 
             WoodCount.section.like("% - Pod Sides") | 
             WoodCount.section.like("% - Bases")),
            WoodCount.count > 0
        ).all()
        
        sheet_count += len(regular_entries)
        
        # Count Long pieces (each entry = 1 sheet)
        long_entries = WoodCount.query.filter(
            WoodCount.date >= start_date,
            WoodCount.date <= end_date,
            WoodCount.section.like("% - Top Rail Pieces Long"),
            WoodCount.count > 0
        ).all()
        
        sheet_count += len(long_entries)
        
        # Count standalone Short pieces (each entry = 1 sheet)
        short_entries = WoodCount.query.filter(
            WoodCount.date >= start_date,
            WoodCount.date <= end_date,
            WoodCount.section.like("% - Top Rail Pieces Short"),
            WoodCount.count > 0
        ).all()
        
        for short_entry in short_entries:
            corresponding_long_section = short_entry.section.replace("Short", "Long")
            long_entry_same_date = WoodCount.query.filter(
                WoodCount.date == short_entry.date,
                WoodCount.section == corresponding_long_section,
                WoodCount.count > 0
            ).first()
            
            if not long_entry_same_date:
                sheet_count += 1
        
        return sheet_count
    
    # Calculate sheets cut for each week of the month
    current_date = month_start_date
    week_number = 1
    
    while current_date <= month_end_date:
        # Calculate end of week (either Saturday or end of month, whichever comes first)
        days_until_saturday = (5 - current_date.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7
        week_end = min(current_date + timedelta(days=days_until_saturday - 1), month_end_date)
        
        # Count sheets for this week
        sheets_this_week = count_sheets_for_range(current_date, week_end)
        weekly_breakdown[f"Week {week_number}"] = {
            "start_date": current_date.strftime("%d %b"),
            "end_date": week_end.strftime("%d %b"),
            "sheets_cut": sheets_this_week
        }
        
        # Move to next week
        current_date = week_end + timedelta(days=1)
        week_number += 1
        
        # Break if we've reached the end of the month
        if current_date > month_end_date:
            break
    
    # Calculate yearly breakdown (all months this year)
    year_start = date(today.year, 1, 1)
    yearly_breakdown = {}
    
    for month_num in range(1, 13):
        month_start = date(today.year, month_num, 1)
        if month_num == 12:
            month_end = date(today.year, month_num, 31)
        else:
            month_end = date(today.year, month_num + 1, 1) - timedelta(days=1)
        
        # Skip future months
        if month_start > today:
            continue
            
        # Count sheets for this month
        sheets_this_month = count_sheets_for_range(month_start, month_end)
        month_name = month_start.strftime("%B")
        yearly_breakdown[month_name] = sheets_this_month

    # Retrieve wood entries for today and adjust time display
    daily_wood_data = WoodCount.query.filter(WoodCount.date == today).all()
    
    # Adjust times for display
    daily_wood_data_with_local_time = []
    for entry in daily_wood_data:
        # Create a copy of entry with the adjusted time
        adjusted_time = entry.time
        if hour_offset > 0:
            # Add the hour offset while handling overflow (e.g., 23:00 + 1 hour)
            adjusted_hours = (adjusted_time.hour + hour_offset) % 24
            adjusted_time = adjusted_time.replace(hour=adjusted_hours)
        
        entry_copy = {
            'time': adjusted_time,
            'section': entry.section,
            'count': entry.count
        }
       
        daily_wood_data_with_local_time.append(entry_copy)

    return render_template(
        'counting_wood.html',
        inventory=inventory,
        available_months=available_months,
        selected_month=selected_month,
        counts=counts,
        daily_wood_data=daily_wood_data_with_local_time,
        weekly_summary=weekly_summary,
        weekly_sheets_cut=weekly_sheets_cut,
        monthly_sheets_cut=monthly_sheets_cut,
        weekly_breakdown=weekly_breakdown,
        yearly_breakdown=yearly_breakdown,
        today=today,
        current_year=today.year
    )


# New model class definitions first:
class CushionJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    order = db.Column(db.Integer, nullable=False)  # to maintain job sequence
    
    def __repr__(self):
        return f"<CushionJob {self.name}>"

class CushionSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, default=date.today, nullable=False)
    worker = db.Column(db.String(50), nullable=False)
    target_6ft = db.Column(db.Integer, default=0)
    target_7ft = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    completed = db.Column(db.Boolean, default=False)
    
    def __repr__(self):
        return f"<CushionSession {self.date} by {self.worker}>"

class CushionJobRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('cushion_session.id'), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey('cushion_job.id'), nullable=False)
    goal_time_hours = db.Column(db.Float, nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    actual_hours = db.Column(db.Float, nullable=False)
    setup_hours = db.Column(db.Float)
    paused_time = db.Column(db.DateTime)  # New field for tracking when a job is paused
    actual_minutes = db.Column(db.Float, nullable=False)
    setup_minutes = db.Column(db.Float)
    paused_minutes = db.Column(db.Integer, default=0)  # New field to store accumulated pause time
	
    
    # Relationship to parent entities
    session = db.relationship('CushionSession', backref='job_records')
    job = db.relationship('CushionJob', backref='records')
    
    def __repr__(self):
        return f"<CushionJobRecord {self.job.name} for session {self.session_id}>"

@app.route('/predicted_finish', methods=['GET', 'POST'])
def predicted_finish():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            tables_for_month = int(request.form['tables_for_month'])
            if tables_for_month <= 0:
                flash("Please enter a positive number of tables.", "error")
                return redirect(url_for('predicted_finish'))
        except ValueError:
            flash("Please enter a valid number.", "error")
            return redirect(url_for('predicted_finish'))
        
        work_days = [0, 1, 2, 3, 4]
        work_hours_per_day = 8
        work_start_hour = 9
        today = datetime.utcnow().date()
        current_year = today.year
        current_month = today.month
        last_full_day = today - timedelta(days=1)

        def calculate_average(model):
            first_entry_date = db.session.query(func.min(model.date)).filter(
                func.extract('year', model.date) == current_year,
                func.extract('month', model.date) == current_month
            ).scalar()
            
            if not first_entry_date or first_entry_date >= last_full_day:
                return None

            days_worked = sum(1 for i in range((last_full_day - first_entry_date).days + 1)
                              if (first_entry_date + timedelta(days=i)).weekday() in work_days)

            records = db.session.query(func.count(model.id)).filter(
                func.extract('year', model.date) == current_year,
                func.extract('month', model.date) == current_month,
                model.date >= first_entry_date,
                model.date <= last_full_day
            ).scalar()

            return records / days_worked if days_worked > 0 else None

        avg_pods = calculate_average(CompletedPods)
        avg_bodies = calculate_average(CompletedTable)
        avg_top_rails = calculate_average(TopRail)

        def completed_this_month(model):
            return db.session.query(func.count(model.id)).filter(
                func.extract('year', model.date) == current_year,
                func.extract('month', model.date) == current_month
            ).scalar()

        completed_pods = completed_this_month(CompletedPods)
        completed_bodies = completed_this_month(CompletedTable)
        completed_top_rails = completed_this_month(TopRail)

        remaining_pods = max(tables_for_month - completed_pods, 0)
        remaining_bodies = max(tables_for_month - completed_bodies, 0)
        remaining_top_rails = max(tables_for_month - completed_top_rails, 0)

        def format_date_with_suffix(d):
            day = d.day
            suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
            return d.strftime(f'%B {day}{suffix}')

        def project_finish_date_and_time(avg_per_day, remaining_needed):
            if avg_per_day is None or avg_per_day == 0:
                return "N/A", "N/A"

            full_days_needed = int(remaining_needed // avg_per_day)
            partial_day_fraction = remaining_needed % avg_per_day / avg_per_day

            finish_date = today
            workdays_counted = 0

            while workdays_counted < full_days_needed:
                finish_date += timedelta(days=1)
                if finish_date.weekday() in work_days:
                    workdays_counted += 1

            if partial_day_fraction > 0:
                while finish_date.weekday() not in work_days:
                    finish_date += timedelta(days=1)

                hours_needed_on_last_day = partial_day_fraction * work_hours_per_day
                finish_time = (datetime.combine(finish_date, datetime.min.time()) +
                               timedelta(hours=work_start_hour + hours_needed_on_last_day))
                finish_time_formatted = finish_time.strftime('%I:%M %p')
            else:
                finish_time_formatted = f"{work_start_hour + work_hours_per_day}:00 PM"

            finish_date_formatted = format_date_with_suffix(finish_date)
            return finish_date_formatted, finish_time_formatted

        pods_finish_date, pods_finish_time = project_finish_date_and_time(avg_pods, remaining_pods)
        bodies_finish_date, bodies_finish_time = project_finish_date_and_time(avg_bodies, remaining_bodies)
        top_rails_finish_date, top_rails_finish_time = project_finish_date_and_time(avg_top_rails, remaining_top_rails)

        return render_template(
            'predicted_finish.html',
            pods_finish_date=pods_finish_date,
            pods_finish_time=pods_finish_time,
            bodies_finish_date=bodies_finish_date,
            bodies_finish_time=bodies_finish_time,
            top_rails_finish_date=top_rails_finish_date,
            top_rails_finish_time=top_rails_finish_time,
            avg_pods=avg_pods,
            avg_bodies=avg_bodies,
            avg_top_rails=avg_top_rails,
            tables_for_month=tables_for_month,
            completed_pods=completed_pods,
            completed_bodies=completed_bodies,
            completed_top_rails=completed_top_rails
        )

    return render_template('predicted_finish.html')

from flask import render_template, request, redirect, url_for, flash, session
from sqlalchemy import func, extract
from datetime import datetime, date, timedelta
from calendar import monthrange

from flask import render_template, request, redirect, url_for, flash, session
from sqlalchemy import func, extract, desc
from datetime import datetime, date, timedelta
from calendar import monthrange

@app.route('/bodies', methods=['GET', 'POST'])
def bodies():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))
    
    # Retrieve issues and any pods not yet converted
    issues = [issue.description for issue in Issue.query.all()]
    
    # Get the base serial numbers of all completed tables (without color suffixes)
    completed_table_serials = db.session.query(CompletedTable.serial_number).all()
    completed_base_serials = []
    
    for (serial,) in completed_table_serials:
        # Strip any color suffixes to get the base serial
        base_serial = serial
        for suffix in [' - GO', '-GO', ' - O', '-O', ' - C', '-C', ' - B', '-B']:
            if suffix in base_serial:
                base_serial = base_serial.split(suffix, 1)[0].strip()
        completed_base_serials.append(base_serial)
    
    # Find pods that haven't been converted to tables (considering base serial numbers)
    unconverted_pods = []
    for pod in CompletedPods.query.all():
        # Clean the pod serial number if it has the prefix
        pod_serial = pod.serial_number
        if "**Pod Serial Number:" in pod_serial:
            pod_serial = pod_serial.replace("**Pod Serial Number:", "").strip()
            
        # Check if the base serial is not in completed tables
        if pod_serial not in completed_base_serials:
            unconverted_pods.append(pod)

    if request.method == 'POST':
        # Helper function to determine color from serial number
        def get_color(serial):
            norm_serial = serial.replace(" ", "")
            if "-GO" in norm_serial or "-go" in norm_serial:
                return "grey_oak"
            elif "-O" in norm_serial and not "-GO" in norm_serial:
                return "rustic_oak"
            elif "-C" in norm_serial or "-c" in norm_serial:
                return "stone"
            elif "-RB" in norm_serial or "-rb" in norm_serial:
                return "rustic_black"
            else:
                return "black"  # Default if no color suffix or has -B

        # Helper function to determine if it's a 6ft table
        def is_6ft(serial):
            return serial.replace(" ", "").endswith("-6")

        worker = session['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        serial_number = request.form['serial_number']
        issue_text = request.form['issue']
        lunch = request.form['lunch']

        # Get the formatted serial number if it exists, otherwise use the original
        serial_number = request.form.get('formatted_serial_number', serial_number)

        # If formatted_serial_number is empty or not provided, format the serial number manually
        if not serial_number or serial_number.strip() == "":
            # Clean any existing color suffix from the serial number, but preserve size suffix
            clean_serial = serial_number
            
            # Remove "**Pod Serial Number:" prefix if present
            if "**Pod Serial Number:" in clean_serial:
                clean_serial = clean_serial.replace("**Pod Serial Number:", "").strip()
            
            # Check for and remove existing color suffixes
            for suffix in ['-GO', ' - GO', '-O', ' - O', '-C', ' - C', '-B', ' - B']:
                if suffix in clean_serial:
                    # If suffix is in the middle of the string, keep what comes after it
                    parts = re.split(r'(-| - )', clean_serial, 1)
                    base_serial = parts[0]
                    clean_serial = f"{base_serial} - 6"
            
            # Add color suffix based on selection
            if color_selector == 'Grey Oak':
                clean_serial += ' - GO'
            elif color_selector == 'Rustic Oak':
                clean_serial += ' - O'
            elif color_selector == 'Stone':
                clean_serial += ' - C'
            # Black is default, so no suffix needed
            
            serial_number = clean_serial

        issue_text = request.form['issue']
        lunch = request.form['lunch']

        # ---------------------------
        # PARTS DEDUCTION LOGIC
        # ---------------------------
        parts_to_deduct = {
            "Large Ramp": 1,
            "Paddle": 1,
            "Laminate": 4,
            "Spring Mount": 1,
            "Spring Holder": 1,
            "Small Ramp": 1,
            "Cue Ball Separator": 1,
            "Bushing": 2,
            # Additional parts for the table build:
            "Table legs": 4,
            "Ball Gullies 1 (Untouched)": 2,
            "Ball Gullies 2": 1,
            "Ball Gullies 3": 1,
            "Ball Gullies 4": 1,
            "Ball Gullies 5": 1,
            "Feet": 4,
            "Triangle trim": 1,
            "White ball return trim": 1,
            "Color ball trim": 1,
            "Ball window trim": 1,
            "Aluminum corner": 4,
            "Ramp 170mm": 1,
            "Ramp 158mm": 1,
            "Ramp 918mm": 1,
            "Ramp 376mm": 1,
            "Chrome handles": 1,
            "Sticker Set": 1,
            "4.8x16mm Self Tapping Screw": 37,
            "4.0 x 50mm Wood Screw": 4,
            "Plastic Window": 1,
            "4.2 x 16 No2 Self Tapping Screw": 19,
            "Latch": 12
        }

        # Adjust parts for 6ft tables
        if is_6ft(serial_number):
            # For a 6ft table, remove standard parts and add the 6ft-specific ones.
            parts_to_deduct.pop("Large Ramp", None)
            parts_to_deduct.pop("Cue Ball Separator", None)
            parts_to_deduct.pop("Small Ramp", None)  # Remove Small Ramp for 6ft tables
            parts_to_deduct.pop("Ramp 170mm", None)  # Also remove Ramp 170mm for 6ft tables
            parts_to_deduct.pop("Ramp 158mm", None)  # Also remove Ramp 158mm for 6ft tables
            parts_to_deduct["6ft Large Ramp"] = 1
            parts_to_deduct["6ft Cue Ball Separator"] = 1
        print(parts_to_deduct)
        # Deduct each required part from the inventory
        for part_name, quantity_needed in parts_to_deduct.items():
            part_entry = (PrintedPartsCount.query
                            .filter_by(part_name=part_name)
                            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                            .first())
            
            if not part_entry:
                # Create a new entry if none exists
                part_entry = PrintedPartsCount(
                    part_name=part_name,
                    count=0,
                    date=date.today(),
                    time=datetime.utcnow().time()
                )
                db.session.add(part_entry)
                db.session.commit()
            
            old_count = part_entry.count
            if old_count >= quantity_needed:
                part_entry.count -= quantity_needed
                check_and_notify_low_stock(part_name, old_count, part_entry.count)
            else:
                flash(f"Not enough inventory for {part_name} (need {quantity_needed}, have {part_entry.count}) to complete the body!", "error")
                db.session.rollback()
                return redirect(url_for('bodies'))
        db.session.commit()

        # Create new CompletedTable record
        new_table = CompletedTable(
            worker=worker,
            start_time=start_time,
            finish_time=finish_time,
            serial_number=serial_number,
            issue=issue_text,
            lunch=lunch,
            date=date.today()
        )
        try:
            db.session.add(new_table)
            db.session.commit()
            flash("Body entry added successfully and inventory updated!", "success")

            # Calculate time taken
            start_dt = datetime.combine(date.today(), datetime.strptime(start_time, "%H:%M").time())
            finish_dt = datetime.combine(date.today(), datetime.strptime(finish_time, "%H:%M").time())

            # Adjust for lunch break
            if lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            time_taken = finish_dt - start_dt
            time_taken_str = str(time_taken)[:-3]  # Trim seconds if you want HH:MM format


            # --- NTFY Notification ---
            size = "6ft" if is_6ft(serial_number) else "7ft"
            color = get_color(serial_number).replace('_', ' ').title()
            message = f"Serial: {serial_number}\nTime Taken: {time_taken_str}"
            title = f"Body Completed: {size} {color}"
            try:
                requests.post("https://ntfy.sh/PoolTableTracker",
                              data=message,
                              headers={"Title": title})
            except requests.RequestException as e:
                print(f"Ntfy notification failed: {e}")
            # --- End NTFY Notification ---
        except IntegrityError:
            db.session.rollback()
            flash("Error: Serial number already exists. Please use a unique serial number.", "error")
            return redirect(url_for('bodies'))

        # Update table stock based on size and color
        size = "6ft" if is_6ft(serial_number) else "7ft"
        color = get_color(serial_number)
        stock_type = f'body_{size.lower()}_{color}'
        
        stock_entry = TableStock.query.filter_by(type=stock_type).first()
        if not stock_entry:
            stock_entry = TableStock(type=stock_type, count=0)
            db.session.add(stock_entry)
        stock_entry.count += 1
        db.session.commit()

        return redirect(url_for('bodies'))

    # ---------------------------
    # GET request handling
    # ---------------------------
    today = date.today()
    completed_tables = CompletedTable.query.filter_by(date=today).all()
    all_bodies_this_month = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == today.year,
        extract('month', CompletedTable.date) == today.month
    ).all()
    current_month_bodies_count = len(all_bodies_this_month)

    # Helper: determine table color based on serial number
    def get_color(serial):
        norm_serial = serial.replace(" ", "")
        if "-GO" in norm_serial or "-go" in norm_serial:
            return "Grey Oak"
        elif "-O" in norm_serial and not "-GO" in norm_serial:
            return "Rustic Oak"
        elif "-C" in norm_serial or "-c" in norm_serial:
            return "Stone"
        else:
            return "Black"  # Default if no color suffix or has -B

    # Determine default color based on the last completed table
    last_table = CompletedTable.query.order_by(CompletedTable.id.desc()).first()
    default_color = get_color(last_table.serial_number) if last_table else 'Black'

    # Helper: determine table size based on serial number
    def is_6ft(serial):
        return serial.replace(" ", "").endswith("-6") or "-6-" in serial.replace(" ", "") or " - 6 - " in serial

    current_production_6ft = sum(1 for table in all_bodies_this_month if is_6ft(table.serial_number))
    current_production_7ft = sum(1 for table in all_bodies_this_month if not is_6ft(table.serial_number))

    # Daily history (last 5 working days)
    def get_last_n_working_days(n, reference_date):
        working_days = []
        d = reference_date
        while len(working_days) < n:
            if d.weekday() < 5:
                working_days.append(d)
            d -= timedelta(days=1)
        return working_days

    last_working_days = get_last_n_working_days(5, today)
    daily_history = (
        db.session.query(
            CompletedTable.date,
            func.count(CompletedTable.id).label('count'),
            func.group_concat(CompletedTable.serial_number, ', ').label('serial_numbers')
        )
        .filter(CompletedTable.date.in_(last_working_days))
        .group_by(CompletedTable.date)
        .order_by(CompletedTable.date.desc())
        .all()
    )
    daily_history_formatted = [
        {
            "date": row.date.strftime("%A %d/%m/%y"),
            "count": row.count,
            "serial_numbers": row.serial_numbers
        }
        for row in daily_history
    ]

    monthly_totals = (
        db.session.query(
            extract('year', CompletedTable.date).label('year'),
            extract('month', CompletedTable.date).label('month'),
            func.count(CompletedTable.id).label('total')
        )
        .group_by('year', 'month')
        .order_by(desc(extract('year', CompletedTable.date)), desc(extract('month', CompletedTable.date)))
        .all()
    )
    monthly_totals_formatted = []
    for row in monthly_totals:
        yr = int(row.year)
        mo = int(row.month)
        total_bodies = row.total
        last_day = today.day if (yr == today.year and mo == today.month) else monthrange(yr, mo)[1]
        work_days = sum(1 for day in range(1, last_day + 1) if date(yr, mo, day).weekday() < 5)
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_body = (cumulative_working_hours / total_bodies if total_bodies > 0 else None)
        if avg_hours_per_body is not None:
            hours = int(avg_hours_per_body)
            minutes = int((avg_hours_per_body - hours) * 60)
            seconds = int((((avg_hours_per_body - hours) * 60) - minutes) * 60)
            avg_hours_per_body_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            avg_hours_per_body_formatted = "N/A"
        monthly_totals_formatted.append({
            "month": date(year=yr, month=mo, day=1).strftime("%B %Y"),
            "count": total_bodies,
            "average_hours_per_body": avg_hours_per_body_formatted
        })
    
    # Retrieve production targets for the current month
    schedule = ProductionSchedule.query.filter_by(year=today.year, month=today.month).first()
    if schedule:
        target_7ft = schedule.target_7ft
        target_6ft = schedule.target_6ft
    else:
        target_7ft = 60
        target_6ft = 60

    # Get the current finish time to pre-populate the form
    last_entry = CompletedTable.query.order_by(CompletedTable.id.desc()).first()
    current_time = last_entry.finish_time if last_entry else datetime.now().strftime("%H:%M")

    return render_template(
        'bodies.html',
        issues=issues,
        current_time=current_time,
        unconverted_pods=unconverted_pods,
        completed_tables=completed_tables,
        current_month_bodies_count=current_month_bodies_count,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted,
        target_7ft=target_7ft,
        target_6ft=target_6ft,
        current_production_7ft=current_production_7ft,
        current_production_6ft=current_production_6ft,
        default_color=default_color
    )
@app.route('/top_rails', methods=['GET', 'POST'])
def top_rails():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    issues = [issue.description for issue in Issue.query.all()]

    if request.method == 'POST':
        worker = session['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        serial_number = request.form['serial_number']
        issue_text = request.form['issue']
        lunch = request.form['lunch']
        
        # Get the selected size and color
        size_selector = request.form.get('size_selector', '7ft')
        color_selector = request.form.get('color_selector', 'Black')

        # Ensure serial number has proper format with size and color
        # First handle any existing size/color suffixes in the serial number
        clean_serial = serial_number
        
        # Check if size suffix is already in the serial number
        has_size_suffix = ' - 6' in clean_serial or '-6' in clean_serial
        if not has_size_suffix and size_selector == '6ft':
            # Add size suffix if not present
            clean_serial = f"{clean_serial} - 6"
                
        # Set the corrected serial number with appropriate suffixes
        serial_number = clean_serial
        
        # Make sure color suffix is present if needed
        # Colors use the following convention: GO (Grey Oak), O (Rustic Oak), C (Stone), B or none (Black)
        has_color_suffix = any(suffix in serial_number for suffix in ['-GO', ' - GO', '-O', ' - O', '-C', ' - C', '-B', ' - B'])
        
        if not has_color_suffix:
            # Add color suffix based on selection
            color_suffix = ''
            if color_selector == 'Grey Oak':
                color_suffix = ' - GO'
            elif color_selector == 'Rustic Oak':
                color_suffix = ' - O'
            elif color_selector == 'Stone':
                color_suffix = ' - C'
            # Black is default, so no suffix needed
            
            if color_suffix:
                serial_number = serial_number + color_suffix

        # Parts and quantities needed for top rail completion
        parts_to_deduct = {
            "Top rail trim long length": 2,
            "Top rail trim short length": 4,
            "Chrome corner": 4,
            "Center pockets": 2,
            "Corner pockets": 4,
            "Catch Plate": 12,
            "M5 x 20 Socket Cap Screw": 16,
            "M5 x 18 x 1.25 Penny Mudguard Washer": 16,
            "LAMELLO CLAMEX P-14 CONNECTOR": 18,
            "4.8x32mm Self Tapping Screw": 24
        }

        # Add top rail pieces to deduct based on color and size
        color_str = color_selector.lower().replace(' ', '_')
        size_str = size_selector.replace('ft', '')
        
        # Construct the exact part names as they appear in the database
        long_piece_name = f"{color_str}_{size_str}_long"
        short_piece_name = f"{color_str}_{size_str}_short"
        
        parts_to_deduct[long_piece_name] = 2
        parts_to_deduct[short_piece_name] = 2

        # Check inventory and deduct all required parts
        for part_name, quantity_needed in parts_to_deduct.items():
            if part_name in [short_piece_name, long_piece_name]:
                # Get the top rail piece count
                part_entry = TopRailPieceCount.query.filter_by(part_key=part_name).first()
                if not part_entry:
                    flash(f"No inventory set up for {part_name}!", "error")
                    return redirect(url_for('top_rails'))
                
                # Check if we have enough
                if part_entry.count < quantity_needed:
                    flash(f"Not enough inventory for {part_name}! Need {quantity_needed}, have {part_entry.count}", "error")
                    return redirect(url_for('top_rails'))
                
                # Deduct from inventory
                part_entry.count -= quantity_needed
            else:
                # Handle other parts using PrintedPartsCount as before
                entries = PrintedPartsCount.query.filter_by(part_name=part_name).order_by(
                    PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                
                total_available = sum(entry.count for entry in entries)
                
                if total_available < quantity_needed:
                    flash(f"Not enough inventory for {part_name}! Need {quantity_needed}, have {total_available}", "error")
                    return redirect(url_for('top_rails'))
                
                check_and_notify_low_stock(part_name, total_available, total_available - quantity_needed)
                
                # Deduct parts from newest entries first
                remaining = quantity_needed
                for entry in entries:
                    if remaining <= 0:
                        break
                    if entry.count >= remaining:
                        entry.count -= remaining
                        remaining = 0
                    else:
                        remaining -= entry.count
                        entry.count = 0
            
            db.session.commit()

        # Create the new TopRail record
        new_top_rail = TopRail(
            worker=worker,
            start_time=start_time,
            finish_time=finish_time,
            serial_number=serial_number,
            lunch=lunch,
            issue=issue_text,
            date=date.today()
        )
        
        try:
            db.session.add(new_top_rail)
            db.session.commit()

            start_time_obj = datetime.strptime(start_time, "%H:%M").time()
            finish_time_obj = datetime.strptime(finish_time, "%H:%M").time()

            start_dt = datetime.combine(date.today(), start_time_obj)
            finish_dt = datetime.combine(date.today(), finish_time_obj)


            if lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            time_taken = finish_dt - start_dt
            time_taken_str = str(time_taken)[:-3]

            
            # --- Timer Logic: Stop previous timer and start new one ---
            try:
                # 1. Stop any active timer for this worker (this completion ends the previous timer)
                active_timer = TopRailTiming.query.filter_by(
                    worker=worker, 
                    completed=False
                ).first()
                
                if active_timer:
                    # Complete the previous timer
                    end_time = datetime.now()
                    duration = (end_time - active_timer.start_time).total_seconds() / 60
                    
                    active_timer.end_time = end_time
                    active_timer.duration_minutes = round(duration, 2)
                    active_timer.serial_number = serial_number  # Record which top rail completed the timer
                    active_timer.completed = True
                    
                    # Show the build time in a friendly format
                    minutes = int(duration)
                    seconds = int((duration % 1) * 60)
                    flash(f"Build time recorded: {minutes}m {seconds}s for top rail {serial_number}", "info")
                
                # 2. Start a new timer for the next top rail
                new_timer = TopRailTiming(
                    worker=worker,
                    start_time=datetime.now(),
                    date=date.today()
                )
                db.session.add(new_timer)
                db.session.commit()
                
            except Exception as timer_error:
                # Don't let timer errors break the main functionality
                print(f"Timer error: {str(timer_error)}")
                db.session.rollback()
                # Re-commit the main top rail record
                db.session.add(new_top_rail)
                db.session.commit()
                flash("Top rail entry added successfully (timer had an issue but main record saved)!", "warning")
            
            # --- Update Table Stock for Top Rails ---
            # Determine size and color from serial number
            def is_6ft(serial):
                return serial.replace(" ", "").endswith("-6") or "-6-" in serial.replace(" ", "") or " - 6 - " in serial
            
            def get_color(serial):
                norm_serial = serial.replace(" ", "")
                if "-GO" in norm_serial or "-go" in norm_serial:
                    return "grey_oak"
                elif "-O" in norm_serial and not "-GO" in norm_serial:
                    return "rustic_oak"
                elif "-C" in norm_serial or "-c" in norm_serial:
                    return "stone"
                else:
                    return "black"  # Default if no color suffix or has -B
            
            # Determine size and color
            size = "6ft" if is_6ft(serial_number) else "7ft"
            color = get_color(serial_number)
            
            # Create the stock key
            stock_type = f'top_rail_{size.lower()}_{color}'
            
            # Update the stock count
            stock_entry = TableStock.query.filter_by(type=stock_type).first()
            if not stock_entry:
                stock_entry = TableStock(type=stock_type, count=0)
                db.session.add(stock_entry)
            stock_entry.count += 1
            db.session.commit()

            
            # --- NTFY Notification ---
            display_color = color.replace('_', ' ').title()
            message = f"Serial: {serial_number}\nTime Taken: {time_taken_str}"
            title = f"Top Rail Completed: {size} {display_color}"
            try:
                requests.post("https://ntfy.sh/PoolTableTracker",
                              data=message,
                              headers={"Title": title})
            except requests.RequestException as e:
                print(f"Ntfy notification failed: {e}")
            # --- End NTFY Notification ---

            if 'timer had an issue' not in str(flash):  # Only show success if no timer warning
                flash("Top rail entry added successfully and inventory updated!", "success")
            
        except IntegrityError:
            db.session.rollback()
            flash("Error: Serial number already exists. Please use a unique serial number.", "error")
            return redirect(url_for('top_rails'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating top rail entry: {str(e)}", "error")
            return redirect(url_for('top_rails'))
        
        return redirect(url_for('top_rails'))

    # GET request handling
    today = date.today()
    completed_top_rails = TopRail.query.filter_by(date=today).all()

    # Determine the next serial number and default size/color
    last_entry = TopRail.query.order_by(TopRail.id.desc()).first()
    next_serial_number = "1000"  # Default value
    default_size = '7ft'  # Default size
    default_color = 'Black'  # Default color
    
    if last_entry:
        # Helper function to determine size and color from serial
        def is_6ft(serial):
            return serial.replace(" ", "").endswith("-6") or "-6-" in serial.replace(" ", "") or " - 6 - " in serial
            
        def get_color(serial):
            norm_serial = serial.replace(" ", "")
            if "-GO" in norm_serial or "-go" in norm_serial:
                return "Grey Oak"
            elif "-O" in norm_serial and not "-GO" in norm_serial:
                return "Rustic Oak"
            elif "-C" in norm_serial or "-c" in norm_serial:
                return "Stone"
            else:
                return "Black"  # Default if no color suffix or has -B
        
        serial = last_entry.serial_number
        
        # Determine default size based on last entry
        if is_6ft(serial):
            default_size = '6ft'
        
        # Determine default color based on last entry
        default_color = get_color(serial)
            
        # Generate next serial number by incrementing the number part
        try:
            # Extract numeric part at start of serial
            match = re.match(r'(\d+)', serial)
            if match:
                base_num = int(match.group(1))
                next_serial_number = str(base_num + 1)
            else:
                next_serial_number = "1000"  # Fallback
        except ValueError:
            next_serial_number = "1000"  # Fallback on parsing error

    try:
        current_time = datetime.strptime(last_entry.finish_time, "%H:%M:%S").strftime("%H:%M") if last_entry else datetime.now().strftime("%H:%M")
    except (ValueError, TypeError):
        current_time = last_entry.finish_time if last_entry else datetime.now().strftime("%H:%M")

    top_rails_this_month = (
        db.session.query(func.count(TopRail.id))
        .filter(
            extract('year', TopRail.date) == today.year,
            extract('month', TopRail.date) == today.month
        )
        .scalar()
    )

    # Helper: Get last 5 working days (Monday-Friday)
    def get_last_n_working_days(n, reference_date):
        working_days = []
        d = reference_date
        while len(working_days) < n:
            if d.weekday() < 5:
                working_days.append(d)
            d -= timedelta(days=1)
        return working_days

    last_working_days = get_last_n_working_days(5, today)
    daily_history = (
        db.session.query(
            TopRail.date,
            func.count(TopRail.id).label('count'),
            func.group_concat(TopRail.serial_number, ', ').label('serial_numbers')
        )
        .filter(TopRail.date.in_(last_working_days))
        .group_by(TopRail.date)
        .order_by(TopRail.date.desc())
        .all()
    )
    daily_history_formatted = [
        {
            "date": row.date.strftime("%A %d/%m/%y"),
            "count": row.count,
            "serial_numbers": row.serial_numbers
        }
        for row in daily_history
    ]

    monthly_totals = (
        db.session.query(
            extract('year', TopRail.date).label('year'),
            extract('month', TopRail.date).label('month'),
            func.count(TopRail.id).label('total')
        )
        .group_by('year', 'month')
        .order_by(desc(extract('year', TopRail.date)), desc(extract('month', TopRail.date)))
        .all()
    )
    monthly_totals_formatted = []
    for row in monthly_totals:
        yr = int(row.year)
        mo = int(row.month)
        total_top_rails = row.total
        last_day = today.day if (yr == today.year and mo == today.month) else monthrange(yr, mo)[1]
        work_days = sum(1 for day in range(1, last_day + 1) if date(yr, mo, day).weekday() < 5)
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_top_rail = (cumulative_working_hours / total_top_rails if total_top_rails > 0 else None)
        if avg_hours_per_top_rail is not None:
            hours = int(avg_hours_per_top_rail)
            minutes = int((avg_hours_per_top_rail - hours) * 60)
            seconds = int((((avg_hours_per_top_rail - hours) * 60) - minutes) * 60)
            avg_hours_per_top_rail_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            avg_hours_per_top_rail_formatted = "N/A"
        monthly_totals_formatted.append({
            "month": date(year=yr, month=mo, day=1).strftime("%B %Y"),
            "count": total_top_rails,
            "average_hours_per_top_rail": avg_hours_per_top_rail_formatted
        })

    # --- Calculate Current Production for Top Rails by Size ---
    all_top_rails_this_month = TopRail.query.filter(
        extract('year', TopRail.date) == today.year,
        extract('month', TopRail.date) == today.month
    ).all()

    # Helper function for classification:
    def is_6ft(serial):
        return serial.replace(" ", "").endswith("-6") or "-6-" in serial.replace(" ", "") or " - 6 - " in serial
    
    current_top_rails_6ft = sum(1 for rail in all_top_rails_this_month if is_6ft(rail.serial_number))
    current_top_rails_7ft = sum(1 for rail in all_top_rails_this_month if not is_6ft(rail.serial_number))

    # Get production targets
    schedule = ProductionSchedule.query.filter_by(year=today.year, month=today.month).first()
    if schedule:
        target_7ft = schedule.target_7ft
        target_6ft = schedule.target_6ft
    else:
        target_7ft = 60
        target_6ft = 60

    return render_template(
        'top_rails.html',
        issues=issues,
        current_time=current_time,
        completed_tables=completed_top_rails,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted,
        top_rails_this_month=top_rails_this_month,
        next_serial_number=next_serial_number,
        target_7ft=target_7ft,
        target_6ft=target_6ft,
        current_top_rails_7ft=current_top_rails_7ft,
        current_top_rails_6ft=current_top_rails_6ft,
        default_size=default_size,
        default_color=default_color
    )


def fetch_uk_bank_holidays():
    try:
        response = requests.get("https://www.gov.uk/bank-holidays.json")
        response.raise_for_status()
        data = response.json()
        holidays = data["england-and-wales"]["events"]
        bank_holidays = {}
        for holiday in holidays:
            holiday_date = date.fromisoformat(holiday["date"])
            month = holiday_date.month
            if month not in bank_holidays:
                bank_holidays[month] = []
            bank_holidays[month].append(holiday_date)
        return bank_holidays
    except requests.RequestException as e:
        print(f"Error fetching bank holidays: {e}")
        return {}

@app.route('/working_days', methods=['GET'])
def working_days():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    today = date.today()
    bank_holidays = fetch_uk_bank_holidays()
    current_year_holidays = {month: [day for day in days if day.year == today.year] for month, days in bank_holidays.items()}

    working_days_data = []
    for month in range(1, 13):
        _, days_in_month = monthrange(today.year, month)
        month_days = [date(today.year, month, day) for day in range(1, days_in_month + 1)]
        weekdays = [day for day in month_days if day.weekday() < 5]
        holidays = current_year_holidays.get(month, [])
        working_days = len(weekdays) - len([day for day in weekdays if day in holidays])

        working_days_data.append({
            "month": date(today.year, month, 1).strftime("%B"),
            "total_working_days": working_days,
            "bank_holidays": len(holidays)
        })

    return render_template("working_days.html", working_days_data=working_days_data)

from datetime import date
from flask import render_template, request, redirect, url_for, flash, session
from sqlalchemy.exc import IntegrityError

# Assume you have already:
# from .models import db, ProductionSchedule
# app = Flask(__name__)

@app.route('/production_schedule', methods=['GET', 'POST'])
def production_schedule():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    def get_next_12_months():
        """
        Returns a list of dictionaries with the year, month, and display string for the next 12 months.
        """
        months_list = []
        today_date = date.today()
        start_year = today_date.year
        start_month = today_date.month

        for i in range(12):
            y = start_year + (start_month - 1 + i) // 12
            m = (start_month - 1 + i) % 12 + 1
            tmp_date = date(y, m, 1)
            display_str = tmp_date.strftime("%B %Y")
            months_list.append({'year': y, 'month': m, 'display_str': display_str})
        return months_list

    next_12_months = get_next_12_months()

    if request.method == 'POST':
        for i, month_data in enumerate(next_12_months):
            yr = month_data['year']
            mo = month_data['month']
            target_7ft_str = request.form.get(f"target_7ft_{i}", "0")
            target_6ft_str = request.form.get(f"target_6ft_{i}", "0")
            try:
                target_7ft = int(target_7ft_str)
                target_6ft = int(target_6ft_str)
            except ValueError:
                flash(f"Invalid number for {month_data['display_str']}. Please use whole numbers only.", "error")
                return redirect(url_for('production_schedule'))

            schedule = ProductionSchedule.query.filter_by(year=yr, month=mo).first()
            if not schedule:
                schedule = ProductionSchedule(year=yr, month=mo, target_7ft=target_7ft, target_6ft=target_6ft)
                db.session.add(schedule)
            else:
                schedule.target_7ft = target_7ft
                schedule.target_6ft = target_6ft

        try:
            db.session.commit()
            flash("Production schedule updated successfully!", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Failed to update schedule (Integrity Error).", "error")
        return redirect(url_for('production_schedule'))

    schedules = ProductionSchedule.query.all()
    schedules_map = {}
    for sched in schedules:
        schedules_map[(sched.year, sched.month)] = sched

    return render_template(
        'production_schedule.html',
        next_12_months=next_12_months,  # Each item has 'year', 'month', and 'display_str'
        schedules_map=schedules_map
    )

@app.route('/admin/table_stock', methods=['GET', 'POST'])
def table_stock():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # Process POST submissions (stock adjustments)
    if request.method == 'POST':
        stock_type = request.form.get('stock_type')
        action = request.form.get('action')
        try:
            amount = int(request.form.get('amount', 0))
        except ValueError:
            flash("Invalid amount entered.", "error")
            return redirect(url_for('table_stock'))
        if amount <= 0:
            flash("Amount must be a positive number.", "error")
            return redirect(url_for('table_stock'))

        # Debug information
        print(f"Processing {action} for stock_type: {stock_type}, amount: {amount}")

        stock_entry = TableStock.query.filter_by(type=stock_type).first()

        # Debug: Print all current stock entries
        all_stock = TableStock.query.all()
        print("Available stock entries:")
        for entry in all_stock:
            print(f"Type: {entry.type}, Count: {entry.count}")

        if not stock_entry:
            if action == 'remove':
                flash(f"No stock entry found for '{stock_type}'. Cannot remove stock.", "error")
                return redirect(url_for('table_stock'))
            else:
                stock_entry = TableStock(type=stock_type, count=0)
                db.session.add(stock_entry)
                db.session.commit()
                flash(f"Created new stock entry for '{stock_type}'.", "info")

        if action == 'add':
            stock_entry.count += amount
            db.session.commit()
            flash(f"Added {amount} to {stock_type} stock. New count: {stock_entry.count}", "success")
        elif action == 'remove':
            if stock_entry.count < amount:
                flash(f"Not enough stock to remove. Current count for '{stock_type}': {stock_entry.count}", "error")
            else:
                stock_entry.count -= amount
                db.session.commit()
                flash(f"Removed {amount} from {stock_type} stock. New count: {stock_entry.count}", "success")

        return redirect(url_for('table_stock'))

    # For GET requests, set up stock display and cost calculations.

    # Define the dimensions (sizes and colors)
    sizes = ['6ft', '7ft']
    colors = ['Black', 'Rustic Oak', 'Grey Oak', 'Stone', 'Rustic Black']

    # Initialize dictionaries for each stock category.
    table_data = {}
    top_rail_data = {}
    cushion_data = {}
    other_data = {}

    # Fetch all TableStock entries.
    all_stock_entries = TableStock.query.all()

    # Initialize potential keys for tables, top rails, and cushions.
    for size in sizes:
        for color in colors:
            table_key = f"body_{size.lower()}_{color.lower().replace(' ', '_')}"
            table_data[table_key] = 0

            top_rail_key = f"top_rail_{size.lower()}_{color.lower().replace(' ', '_')}"
            top_rail_data[top_rail_key] = 0

        # Cushion sets depend only on size.
        cushion_key = f"cushion_set_{size.lower()}"
        cushion_data[cushion_key] = 0

    # Process existing stock entries to populate the dictionaries.
    for entry in all_stock_entries:
        stock_type = entry.type
        if stock_type.startswith('body_'):
            # If color is already present in the key, assign it.
            if any(col.lower().replace(' ', '_') in stock_type for col in colors):
                table_data[stock_type] = entry.count
            else:
                # Legacy entries default to black.
                size = '6ft' if '6ft' in stock_type else '7ft'
                table_data[f"body_{size.lower()}_black"] = entry.count
        elif stock_type.startswith('top_rail_'):
            if any(col.lower().replace(' ', '_') in stock_type for col in colors):
                top_rail_data[stock_type] = entry.count
            else:
                size = '6ft' if '6ft' in stock_type else '7ft'
                top_rail_data[f"top_rail_{size.lower()}_black"] = entry.count
        elif stock_type.startswith('cushion_set_'):
            cushion_data[stock_type] = entry.count
        else:
            other_data[stock_type] = entry.count

    # Calculate cost for each table body stock.
    # Black bodies cost £993.60 (incl. VAT); colored bodies cost £1089.60.
    stock_costs = {}
    grand_total = 0
    for size in sizes:
        stock_costs[size] = {}
        for color in colors:
            key = f"body_{size.lower()}_{color.lower().replace(' ', '_')}"
            count = table_data.get(key, 0)
            unit_cost = 993.6 if color.lower() == 'black' else 1089.6
            cost = count * unit_cost
            stock_costs[size][color] = cost
            grand_total += cost

    # Format the cost values so they include a pound sign and the note that VAT is included.
    formatted_stock_costs = {}
    for size in sizes:
        formatted_stock_costs[size] = {}
        for color in colors:
            cost = stock_costs[size][color]
            formatted_stock_costs[size][color] = "£{:.2f} (incl. VAT)".format(cost)
    formatted_grand_total = "£{:.2f} (incl. VAT)".format(grand_total)

    # Pass all required variables to the template.
    return render_template(
        'table_stock.html',
        table_data=table_data,
        top_rail_data=top_rail_data,
        cushion_data=cushion_data,
        other_data=other_data,
        sizes=sizes,
        colors=colors,
        stock_costs=formatted_stock_costs,
        grand_total=formatted_grand_total
    )

from math import ceil

@app.route('/material_calculator', methods=['GET', 'POST'])
def material_calculator():
    # Define the laminate colours and corresponding form field names.
    laminate_colours = [
        {"name": "H1330 ST10 SANTA FE", "field": "num_tables_H1330"},
        {"name": "H1313 ST10 GREY BROWN WHITE RIVER OAK", "field": "num_tables_H1313"},
        {"name": "F637 ST10 WHITE CHROMIX", "field": "num_tables_F637"},
        {"name": "F767 ST9 CUBANIT GREY", "field": "num_tables_F767_GREY"},
        {"name": "F767 ST9 BLACK", "field": "num_tables_F767_BLACK"}
    ]
    
    # Prepare a dictionary to hold the laminate results per colour.
    laminate_results = {}
    # 36mm board variables remain unchanged.
    boards_jobA = 0    # Boards processed in Job A (yielding 8 long & 2 short pieces)
    boards_jobB = 0    # Boards processed in Job B (yielding 16 short pieces)
    board_total = 0    # Total boards needed for 36mm jobs
    leftover_long = 0
    leftover_short = 0

    if request.method == 'POST':
        # Process laminate calculations for each colour.
        for colour in laminate_colours:
            try:
                qty = int(request.form.get(colour["field"], 0)) if request.form.get(colour["field"], '').strip() else 0
            except ValueError:
                qty = 0
            # Each table requires:
            # - 1 big piece (so qty big pieces)
            # - 3 strips, and one laminate piece gives 9 strips, so pieces for strips = ceil((3 * qty)/9)
            big_needed = qty
            strips_needed = ceil((3 * qty) / 9) if qty > 0 else 0
            total_needed = big_needed + strips_needed
            laminate_results[colour["name"]] = {
                "tables": qty,
                "big": big_needed,
                "strips": strips_needed,
                "total": total_needed
            }
        
        # 36mm Board Calculation
        try:
            num_top_rails = int(request.form.get('num_top_rails', 0)) if request.form.get('num_top_rails', '').strip() else 0
        except ValueError:
            num_top_rails = 0

        # Each top rail requires 2 long pieces and 2 short pieces.
        long_needed = 2 * num_top_rails
        short_needed = 2 * num_top_rails

        # Job A (CNC Job 1) yields 8 long pieces and 2 short pieces per board.
        boards_jobA = ceil(long_needed / 8) if num_top_rails > 0 else 0
        short_from_jobA = 2 * boards_jobA
        total_short_needed = short_needed

        # Determine if additional short pieces are needed from Job B.
        additional_short_needed = max(0, total_short_needed - short_from_jobA)
        boards_jobB = ceil(additional_short_needed / 16) if additional_short_needed > 0 else 0

        board_total = boards_jobA + boards_jobB

        # Calculate leftovers:
        produced_long = 8 * boards_jobA
        produced_short = (2 * boards_jobA) + (16 * boards_jobB)
        leftover_long = produced_long - long_needed
        leftover_short = produced_short - short_needed
   

    return render_template(
        'material_calculator.html',
        laminate_results=laminate_results,
        boards_jobA=boards_jobA,
        boards_jobB=boards_jobB,
        board_total=board_total,
        leftover_long=leftover_long,
        leftover_short=leftover_short
    )



@app.route('/counting_3d_printing_parts', methods=['GET', 'POST'])
def counting_3d_printing_parts():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    today = datetime.utcnow().date()

    if request.method == 'POST':
        part = request.form['part']

        if 'reject' in request.form:
            reject_amount = int(request.form['reject_amount'])
            current_count = PrintedPartsCount.query.filter_by(part_name=part).order_by(
                PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()

            if current_count and current_count.count >= reject_amount:
                old_count = current_count.count
                current_count.count -= reject_amount
                check_and_notify_low_stock(part, old_count, current_count.count)
                flash(f"Rejected {reject_amount} of {part} from inventory.", "success")
                db.session.commit()
            else:
                flash(f"Not enough inventory to reject {reject_amount} of {part}.", "error")
        else:
            increment_amount = int(request.form['increment_amount'])
            current_count = PrintedPartsCount.query.filter_by(part_name=part).order_by(
                PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()

            if current_count:
                current_count.count += increment_amount
                current_count.date = today
                current_count.time = datetime.utcnow().time()
                flash(f"Incremented {part} count by {increment_amount}!", "success")
            else:
                new_count = PrintedPartsCount(part_name=part, count=increment_amount, date=today, time=datetime.utcnow().time())
                db.session.add(new_count)
                flash(f"Added {increment_amount} to {part} as a new entry!", "success")

            db.session.commit()

        return redirect(url_for('counting_3d_printing_parts'))

    parts = [
        "Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder",
        "Small Ramp", "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp",
        "6ft Carpet", "7ft Carpet", "6ft Felt", "7ft Felt"  # Added new parts
    ]

    parts_counts = {
        part: db.session.query(db.func.sum(PrintedPartsCount.count))
            .filter_by(part_name=part)
            .scalar() or 0
        for part in parts
    }

    # Add this line to create inventory_counts from parts_counts
    inventory_counts = parts_counts

    current_month = datetime.utcnow().month
    current_year = datetime.utcnow().year

    schedule = ProductionSchedule.query.filter_by(year=current_year, month=current_month).first()
    target_7ft = schedule.target_7ft if schedule else 60
    target_6ft = schedule.target_6ft if schedule else 60

    # Fetch tables built this month
    all_bodies_this_month = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == current_year,
        extract('month', CompletedTable.date) == current_month
    ).all()

    # Clearly identify built tables
    def is_6ft(serial):
        return serial.replace(" ", "").endswith("-6")

    bodies_built_6ft = sum(1 for table in all_bodies_this_month if is_6ft(table.serial_number))
    bodies_built_7ft = sum(1 for table in all_bodies_this_month if not is_6ft(table.serial_number))

    # Define usage per table for each part.
    parts_usage_per_body = {
        "Large Ramp": 1,
        "Paddle": 1,
        "Laminate": 4,
        "Spring Mount": 1,
        "Spring Holder": 1,
        "Small Ramp": 1,
        "Cue Ball Separator": 1,
        "Bushing": 2,
        "6ft Cue Ball Separator": 1,
        "6ft Large Ramp": 1,
        "6ft Carpet": 1,
        "6ft Felt": 1,
        "7ft Carpet": 1,
        "7ft Felt": 2
    }

    # FIXED: Calculate parts status more accurately
    parts_status = {}
    for part, usage in parts_usage_per_body.items():
        # Determine required quantities based on table size
        if part in ["Large Ramp", "Cue Ball Separator", "7ft Carpet", "7ft Felt"]:
            total_required = target_7ft * usage
            already_used = bodies_built_7ft * usage
            still_needed = (target_7ft - bodies_built_7ft) * usage
        elif part in ["6ft Large Ramp", "6ft Cue Ball Separator", "6ft Carpet", "6ft Felt"]:
            total_required = target_6ft * usage
            already_used = bodies_built_6ft * usage
            still_needed = (target_6ft - bodies_built_6ft) * usage
        else:
            total_required = (target_7ft + target_6ft) * usage
            already_used = (bodies_built_7ft + bodies_built_6ft) * usage
            still_needed = ((target_7ft + target_6ft) - (bodies_built_7ft + bodies_built_6ft)) * usage

        # Current inventory count
        current_inventory = inventory_counts.get(part, 0)

        still_needed = total_required - already_used
        current_inventory = inventory_counts.get(part, 0)
        surplus = current_inventory - still_needed

        if surplus < 0:
            parts_status[part] = f"{-surplus} left to make"
        else:
            parts_status[part] = f"{surplus} extras"

    return render_template(
        'counting_3d_printing_parts.html',
        parts_counts=parts_counts,
        parts_status=parts_status
    )


def reset_cushion_jobs():
    """
    Complete reset of the cushion job system:
    1. Get all existing job records
    2. Create fresh job entries
    3. Create mapping to reassign job records
    4. Apply mapping to all records
    """
    from sqlalchemy.sql import text
    
    # Step 1: Get all existing job records with their associated job names
    # We need to join to get both the record ID and the job name it was using
    job_records_data = []
    record_query = text("""
        SELECT 
            jr.id as record_id, 
            j.name as job_name,
            j.order as job_order,
            jr.session_id,
            jr.goal_minutes,
            jr.start_time,
            jr.finish_time,
            jr.actual_minutes,
            jr.setup_minutes
        FROM 
            cushion_job_record jr
        JOIN 
            cushion_job j ON jr.job_id = j.id
    """)
    
    try:
        result = db.session.execute(record_query)
        for row in result:
            job_records_data.append({
                'record_id': row.record_id,
                'job_name': row.job_name,
                'job_order': row.job_order,
                'session_id': row.session_id,
                'goal_minutes': row.goal_minutes,
                'start_time': row.start_time,
                'finish_time': row.finish_time,
                'actual_minutes': row.actual_minutes,
                'setup_minutes': row.setup_minutes
            })
    except Exception as e:
        print(f"Error querying job records: {str(e)}")
        return False
    
    # Standard jobs definition - exactly 9 jobs with correct names
    standard_jobs = [
        {"name": "Cut wood to length", "order": 1},
        {"name": "Spindle mold wood", "order": 2},
        {"name": "Cut rubber to length", "order": 3},
        {"name": "Glue wood and rubber", "order": 4},
        {"name": "Shape wood", "order": 5},
        {"name": "Glue rubber ends", "order": 6},
        {"name": "Shape ends", "order": 7},
        {"name": "Sanding top of the cushions", "order": 8},
        {"name": "Bundle", "order": 9}
    ]
    
    # Create a conversion mapping based on similar names or orders
    conversion_map = {
        # Direct mappings
        "Cut wood to length": "Cut wood to length",
        "Spindle mold wood": "Spindle mold wood",
        "Cut rubber to length": "Cut rubber to length",
        "Glue wood and rubber": "Glue wood and rubber",
        "Shape wood": "Shape wood",
        "Glue rubber ends": "Glue rubber ends",
        "Shape ends": "Shape ends",
        "Bundle": "Bundle",
        
        # Special mappings for renamed jobs
        "Sanding": "Sanding top of the cushions",
        "Top coating": "Bundle",  # Map "Top coating" to "Bundle" as a fallback
    }
    
    try:
        # Step 2: Remove all references to job_id in job records
        # This prevents foreign key violations by setting a default/dummy value
        db.session.execute(text("UPDATE cushion_job_record SET job_id = 1"))
        db.session.commit()
        
        # Step 3: Delete all existing jobs
        db.session.execute(text("DELETE FROM cushion_job"))
        db.session.commit()
        
        # Step 4: Create fresh job entries
        for job in standard_jobs:
            db.session.execute(
                text("INSERT INTO cushion_job (name, \"order\") VALUES (:name, :order)"),
                {"name": job["name"], "order": job["order"]}
            )
        db.session.commit()
        
        # Step 5: Build a mapping from old job names to new job IDs
        job_name_to_id = {}
        for job_data in standard_jobs:
            job_name = job_data["name"]
            result = db.session.execute(
                text("SELECT id FROM cushion_job WHERE name = :name"),
                {"name": job_name}
            )
            job_id = result.scalar()
            if job_id:
                job_name_to_id[job_name] = job_id
        
        # Step 6: Update all job records with their new job IDs
        for record_data in job_records_data:
            record_id = record_data['record_id']
            old_job_name = record_data['job_name']
            
            # Use the conversion map to get the new job name
            new_job_name = conversion_map.get(old_job_name)
            
            # If no direct mapping, find closest match by order
            if not new_job_name:
                old_order = record_data['job_order']
                closest_job = min(standard_jobs, key=lambda j: abs(j["order"] - old_order))
                new_job_name = closest_job["name"]
            
            # Get the ID for the new job name
            new_job_id = job_name_to_id.get(new_job_name)
            
            if new_job_id:
                db.session.execute(
                    text("UPDATE cushion_job_record SET job_id = :job_id WHERE id = :record_id"),
                    {"job_id": new_job_id, "record_id": record_id}
                )
        
        db.session.commit()
        return True
    
    except Exception as e:
        db.session.rollback()
        print(f"Error during job system reset: {str(e)}")
        return False

@app.route('/counting_cushions', methods=['GET', 'POST'])
def counting_cushions():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    worker_name = session['worker']
    today = date.today()
    
    # Check if we need to reset the job system
    try:
        # Count existing jobs
        job_count = db.session.query(func.count(CushionJob.id)).scalar()
        
        # Check if we need to run the reset
        if job_count != 9:  # We should have exactly 9 jobs
            # Call the reset function to rebuild the job system
            success = reset_cushion_jobs()
            if success:
                flash("Job system has been updated.", "success")
            else:
                flash("There was an issue updating the job system. Please contact your administrator.", "error")
    except Exception as e:
        flash(f"Error checking job system: {str(e)}", "error")
    
    # Get any active session (not just for this worker)
    active_session = CushionSession.query.filter_by(active=True).first()
    
    # Handle manual time adjustment
    if request.method == 'POST' and 'adjust_time' in request.form:
        record_id = int(request.form['record_id'])
        record = CushionJobRecord.query.get(record_id)
        
        if record:
            # Parse time values from form
            try:
                # Only update if values are provided
                if request.form.get('start_time'):
                    start_time_str = request.form.get('start_time')
                    start_date = record.start_time.date() if record.start_time else today
                    record.start_time = datetime.combine(start_date, datetime.strptime(start_time_str, "%H:%M").time())
                
                if request.form.get('finish_time'):
                    finish_time_str = request.form.get('finish_time')
                    finish_date = record.finish_time.date() if record.finish_time else today
                    record.finish_time = datetime.combine(finish_date, datetime.strptime(finish_time_str, "%H:%M").time())
                
                if request.form.get('setup_minutes'):
                    record.setup_minutes = int(request.form.get('setup_minutes'))
                
                if request.form.get('paused_minutes'):
                    record.paused_minutes = int(request.form.get('paused_minutes'))
                
                # Recalculate actual minutes if both start and finish times exist
                if record.start_time and record.finish_time:
                    # Total duration in minutes
                    duration = (record.finish_time - record.start_time).total_seconds() / 60
                    
                    # Check for lunch break during job
                    lunch_start = datetime.combine(record.start_time.date(), datetime.strptime('13:30', '%H:%M').time())
                    lunch_end = lunch_start + timedelta(minutes=30)
                    
                    if record.start_time < lunch_end and record.finish_time > lunch_start:
                        # Calculate overlap with lunch
                        overlap_start = max(record.start_time, lunch_start)
                        overlap_end = min(record.finish_time, lunch_end)
                        lunch_duration = (overlap_end - overlap_start).total_seconds() / 60
                        duration -= lunch_duration
                    
                    # Subtract any paused time from the duration
                    if record.paused_minutes:
                        duration -= record.paused_minutes
                    
                    record.actual_minutes = int(duration)
                
                db.session.commit()
                flash("Time adjusted successfully!", "success")
            except ValueError as e:
                flash(f"Invalid time format: {str(e)}", "error")
        else:
            flash("Record not found.", "error")
        
        return redirect(url_for('counting_cushions'))
    
    # Handle session creation if needed
    if request.method == 'POST' and 'create_session' in request.form:
        try:
            target_6ft = int(request.form.get('target_6ft', 0))
            target_7ft = int(request.form.get('target_7ft', 0))
            
            # Only close previous active sessions if we're creating a new one
            previous_sessions = CushionSession.query.filter_by(active=True).all()
            for prev in previous_sessions:
                prev.active = False
            
            # Create a new session
            active_session = CushionSession(
                date=today,
                worker=worker_name,
                target_6ft=target_6ft,
                target_7ft=target_7ft,
                active=True
            )
            db.session.add(active_session)
            db.session.commit()
            flash("New cushion session created!", "success")
            
            # Initialize job records with goal times
            jobs = CushionJob.query.order_by(CushionJob.order).all()
            for job in jobs:
                goal_minutes = request.form.get(f'goal_{job.id}', 0)
                try:
                    goal_minutes = int(goal_minutes)
                except ValueError:
                    goal_minutes = 0
                    
                job_record = CushionJobRecord(
                    session_id=active_session.id,
                    job_id=job.id,
                    goal_minutes=goal_minutes,
                    paused_minutes=0
                )
                db.session.add(job_record)
            db.session.commit()
            
            return redirect(url_for('counting_cushions'))
            
        except ValueError:
            flash("Please enter valid numbers for targets and goal times.", "error")
            return redirect(url_for('counting_cushions'))
    
       
    # Handle job start/finish/pause
    if request.method == 'POST' and 'job_action' in request.form:
        action = request.form['job_action']
        job_record_id = int(request.form['job_record_id'])
        job_record = CushionJobRecord.query.get(job_record_id)
        
        if not job_record:
            flash("Job record not found.", "error")
        else:
            now = datetime.now()
            
            if action == 'start':
                # If this job was paused before, calculate total paused time
                if job_record.paused_time:
                    pause_duration = (now - job_record.paused_time).total_seconds() / 60
                    job_record.paused_minutes = (job_record.paused_minutes or 0) + int(pause_duration)
                    job_record.paused_time = None  # Clear paused time as we're resuming
                    flash(f"Resumed: {job_record.job.name}", "success")
                else:
                    # Calculate setup time from previous job if applicable
                    prev_job_record = CushionJobRecord.query.join(CushionJob).filter(
                        CushionJobRecord.session_id == active_session.id,
                        CushionJob.order < job_record.job.order,
                        CushionJobRecord.finish_time.isnot(None)
                    ).order_by(desc(CushionJob.order)).first()
                    
                    if prev_job_record and prev_job_record.finish_time:
                        setup_time = (now - prev_job_record.finish_time).total_seconds() / 60
                        job_record.setup_minutes = int(setup_time)
                    
                    flash(f"Started: {job_record.job.name}", "success")
                
                job_record.start_time = now
                db.session.commit()
                
            elif action == 'finish':
                if not job_record.start_time:
                    flash("Can't finish a job that hasn't started.", "error")
                else:
                    # If the job was paused, we can't finish it directly
                    if job_record.paused_time:
                        flash("Please resume the job before finishing it.", "error")
                        return redirect(url_for('counting_cushions'))
                    
                    job_record.finish_time = now
                    
                    # Calculate actual working minutes excluding lunch break if applicable
                    start = job_record.start_time
                    finish = job_record.finish_time
                    lunch_start = datetime.combine(today, datetime.strptime('13:30', '%H:%M').time())
                    lunch_end = lunch_start + timedelta(minutes=30)
                    
                    # Total duration in minutes
                    duration = (finish - start).total_seconds() / 60
                    
                    # Check if the job spans lunch break
                    if start < lunch_end and finish > lunch_start:
                        # Calculate overlap with lunch
                        overlap_start = max(start, lunch_start)
                        overlap_end = min(finish, lunch_end)
                        lunch_duration = (overlap_end - overlap_start).total_seconds() / 60
                        duration -= lunch_duration
                    
                    # Subtract any paused time from the duration
                    if job_record.paused_minutes:
                        duration -= job_record.paused_minutes
                    
                    job_record.actual_minutes = int(duration)
                    db.session.commit()
                    flash(f"Finished: {job_record.job.name}", "success")
                    
                    # Check if all jobs are completed
                    all_completed = CushionJobRecord.query.filter(
                        CushionJobRecord.session_id == active_session.id,
                        CushionJob.finish_time.is_(None)
                    ).count() == 0
                    
                    if all_completed:
                        active_session.completed = True
                        db.session.commit()
                        flash("All cushion jobs completed for this session!", "success")
            
            elif action == 'pause':
                if not job_record.start_time:
                    flash("Can't pause a job that hasn't started.", "error")
                elif job_record.finish_time:
                    flash("Can't pause a job that's already finished.", "error")
                elif job_record.paused_time:
                    flash("This job is already paused.", "error")
                else:
                    job_record.paused_time = now
                    db.session.commit()
                    flash(f"Paused: {job_record.job.name}", "success")
            
            return redirect(url_for('counting_cushions'))
    
    # Reset session - only mark as inactive, not completed
    if request.method == 'POST' and 'reset_session' in request.form:
        if active_session:
            active_session.active = False
            db.session.commit()
            flash("Session closed. You can start a new one.", "success")
        return redirect(url_for('counting_cushions'))
    
    # Prepare data for the template
    jobs = CushionJob.query.order_by(CushionJob.order).all()
    
    # If we have an active session, get its job records
    job_records = []
    session_summary = None
    if active_session:
        job_records = (CushionJobRecord.query
                      .join(CushionJob)
                      .filter(CushionJobRecord.session_id == active_session.id)
                      .order_by(CushionJob.order)
                      .all())
        
        # Calculate session summary
        total_goal_minutes = sum(jr.goal_minutes for jr in job_records if jr.goal_minutes)
        total_actual_minutes = sum(jr.actual_minutes for jr in job_records if jr.actual_minutes)
        total_setup_minutes = sum(jr.setup_minutes for jr in job_records if jr.setup_minutes)
        total_paused_minutes = sum(jr.paused_minutes for jr in job_records if jr.paused_minutes)
        
        # Add currently paused time to total
        for record in job_records:
            if record.paused_time:
                current_pause_duration = (datetime.now() - record.paused_time).total_seconds() / 60
                total_paused_minutes += int(current_pause_duration)
        
        efficiency = 0
        if total_actual_minutes > 0 and total_goal_minutes > 0:
            efficiency = round((total_goal_minutes / total_actual_minutes * 100), 2)
        
        session_summary = {
            'target_6ft': active_session.target_6ft,
            'target_7ft': active_session.target_7ft,
            'total_cushions': active_session.target_6ft + active_session.target_7ft,
            'total_goal_minutes': total_goal_minutes,
            'total_goal_formatted': f"{total_goal_minutes // 60}h {total_goal_minutes % 60}m",
            'total_actual_minutes': total_actual_minutes,
            'total_actual_formatted': f"{total_actual_minutes // 60}h {total_actual_minutes % 60}m",
            'total_setup_minutes': total_setup_minutes,
            'total_setup_formatted': f"{total_setup_minutes // 60}h {total_setup_minutes % 60}m",
            'total_paused_minutes': total_paused_minutes,
            'total_paused_formatted': f"{total_paused_minutes // 60}h {total_paused_minutes % 60}m",
            'efficiency': efficiency
        }
    
    # Handle session deletion
    if request.method == 'POST' and 'delete_session' in request.form:
        delete_session_id = int(request.form['delete_session_id'])
        session_to_delete = CushionSession.query.get(delete_session_id)
        
        if session_to_delete:
            # First delete all associated job records
            CushionJobRecord.query.filter_by(session_id=delete_session_id).delete()
            # Then delete the session itself
            db.session.delete(session_to_delete)
            db.session.commit()
            flash("Session and all its records deleted successfully!", "success")
        else:
            flash("Session not found.", "error")
        return redirect(url_for('counting_cushions'))
    
    # Get historical session data
    historical_sessions = (CushionSession.query
                         .filter(CushionSession.completed == True)
                         .order_by(desc(CushionSession.date))
                         .limit(10)
                         .all())
    
    historical_data = []
    for hist_session in historical_sessions:
        hist_records = (CushionJobRecord.query
                        .join(CushionJob)
                        .filter(CushionJobRecord.session_id == hist_session.id)
                        .order_by(CushionJob.order)
                        .all())
        
        hist_goal_minutes = sum(hr.goal_minutes for hr in hist_records if hr.goal_minutes)
        hist_actual_minutes = sum(hr.actual_minutes for hr in hist_records if hr.actual_minutes)
        hist_setup_minutes = sum(hr.setup_minutes for hr in hist_records if hr.setup_minutes)
        hist_paused_minutes = sum(hr.paused_minutes for hr in hist_records if hr.paused_minutes)
        
        efficiency = 0
        if hist_actual_minutes > 0 and hist_goal_minutes > 0:
            efficiency = round((hist_goal_minutes / hist_actual_minutes * 100), 2)
        
        # Prepare detailed job information
        job_details = []
        for record in hist_records:
            record_efficiency = None
            if record.actual_minutes and record.goal_minutes:
                record_efficiency = round((record.goal_minutes / record.actual_minutes * 100), 2)
            
            start_time_str = record.start_time.strftime('%H:%M') if record.start_time else None
            finish_time_str = record.finish_time.strftime('%H:%M') if record.finish_time else None
            
            job_details.append({
                'id': record.id,
                'name': record.job.name,
                'goal_minutes': record.goal_minutes,
                'start_time': start_time_str,
                'finish_time': finish_time_str,
                'actual_minutes': record.actual_minutes,
                'setup_minutes': record.setup_minutes,
                'paused_minutes': record.paused_minutes,
                'efficiency': record_efficiency
            })
        
        historical_data.append({
            'id': hist_session.id,
            'date': hist_session.date.strftime('%d/%m/%Y'),
            'worker': hist_session.worker,
            'total_cushions': hist_session.target_6ft + hist_session.target_7ft,
            'goal_minutes': hist_goal_minutes,
            'goal_formatted': f"{hist_goal_minutes // 60}h {hist_goal_minutes % 60}m",
            'actual_minutes': hist_actual_minutes,
            'actual_formatted': f"{hist_actual_minutes // 60}h {hist_actual_minutes % 60}m",
            'setup_minutes': hist_setup_minutes,
            'setup_formatted': f"{hist_setup_minutes // 60}h {hist_setup_minutes % 60}m",
            'paused_minutes': hist_paused_minutes,
            'paused_formatted': f"{hist_paused_minutes // 60}h {hist_paused_minutes % 60}m",
            'efficiency': efficiency,
            'job_details': job_details
        })
    
    return render_template(
        'counting_cushions.html',
        jobs=jobs,
        active_session=active_session,
        job_records=job_records,
        session_summary=session_summary,
        historical_data=historical_data,
        current_time=datetime.now().strftime('%H:%M')
    )

@app.route('/sales_extrapolation', methods=['GET', 'POST'])
def sales_extrapolation():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # Define the product list (in the exact order requested)
    products = [
        "7ft - Black",
        "7ft - Rustic Oak",
        "7ft - Grey Oak",
        "7ft - Stone",
        "6ft - Black", 
        "6ft - Rustic Oak",
        "6ft - Grey Oak",
        "6ft - Stone"
    ]
    
    # Initialize data dictionary for the template
    data = {
        'products': products,
        'current_sales': {product: 0 for product in products},
        'extrapolated_sales': {product: 0 for product in products},
        'total_current': 0,
        'total_extrapolated': 0,
        'current_period': 30,  # Default current period in days
        'target_period': 365,   # Default target period in days
    }
    
    if request.method == 'POST':
        try:
            # Get the current period and target period from form
            current_period = int(request.form.get('current_period', 30))
            target_period = int(request.form.get('target_period', 365))
            
            # Validate input periods
            if current_period <= 0 or target_period <= 0:
                flash("Periods must be positive numbers.", "error")
                return redirect(url_for('sales_extrapolation'))
            
            # Store the current and target periods in data dict
            data['current_period'] = current_period
            data['target_period'] = target_period
            
            # Calculate the extrapolation ratio
            extrapolation_ratio = target_period / current_period
            
            # Process sales data for each product
            total_current = 0
            total_extrapolated = 0
            
            for product in products:
                # Get the current sales from the form
                current_sales = int(request.form.get(f'sales_{product.replace(" ", "_").replace("-", "")}', 0))
                data['current_sales'][product] = current_sales
                total_current += current_sales
                
                # Calculate extrapolated sales
                extrapolated_sales = round(current_sales * extrapolation_ratio)
                data['extrapolated_sales'][product] = extrapolated_sales
                total_extrapolated += extrapolated_sales
            
            # Store totals in data dict
            data['total_current'] = total_current
            data['total_extrapolated'] = total_extrapolated
            
            flash("Sales data extrapolation complete!", "success")
            
        except ValueError:
            flash("Please enter valid numbers for all fields.", "error")
            return redirect(url_for('sales_extrapolation'))
    
    return render_template('sales_extrapolation.html', **data)

import tinytuya
from flask import flash, redirect, url_for

@app.route('/turn_on_dust_extractor', methods=['POST'])
def turn_on_dust_extractor():
    """Turn on or off the dust extractor via Fingerbot"""
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))
    
    try:
        # Determine action from form submission
        action = request.form.get('action', 'on')
        
        # Cloud API configuration
        cloud = tinytuya.Cloud(
            apiRegion="eu",  # Based on your region
            apiKey="5gcttjq87ffjvvk84a54",  # Your API Key
            apiSecret="55bec326c6e3466db6c1a3374c4d88ec",  # Your API Secret
            apiDeviceID="bfcf09124259fcecdd6ied"  # Your Hub/Gateway ID
        )
        
        # Device IDs
        ON_FINGERBOT_ID = "bfdbd2ybbo1zwocd"  # Original Fingerbot (first one)
        OFF_FINGERBOT_ID = "bf8f805498a758d70epago"  # New Fingerbot (second one)
        
        # Select the appropriate device ID based on action
        device_id = ON_FINGERBOT_ID if action == 'on' else OFF_FINGERBOT_ID
        
        # Send command to turn on/off
        commands = {"commands": [{"code": "switch", "value": True}]}
        result = cloud.sendcommand(device_id, commands)
        
        # Flash a success message
        flash(f"Dust extractor turned {action}!", "success")
    except Exception as e:
        # Flash an error message if something goes wrong
        flash(f"Error turning {action} dust extractor: {str(e)}", "error")
    
    # Redirect back to the previous page
    return redirect(request.referrer or url_for('counting_wood'))

# Add this route with your other routes in flask_app.py

@app.route('/api/docs')
def api_documentation():
    """Render the API documentation page"""
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))
    return render_template('api_documentation.html')

from api_routes import api
app.register_blueprint(api)

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify

# Add this new model to your existing models section in flask_app.py

class TopRailTiming(db.Model):
    __tablename__ = 'top_rail_timing'
    id = db.Column(db.Integer, primary_key=True)
    worker = db.Column(db.String(50), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    duration_minutes = db.Column(db.Float, nullable=True)  # Duration in minutes
    serial_number = db.Column(db.String(20), nullable=True)  # Optional serial number
    date = db.Column(db.Date, default=date.today, nullable=False)
    completed = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<TopRailTiming {self.worker} - {self.duration_minutes}min>"

# Add these routes to your flask_app.py

@app.route('/api/top_rail/start_timer', methods=['POST'])
def start_top_rail_timer():
    """Start timing for a new top rail build."""
    if 'worker' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    worker = session['worker']
    
    # Check if there's already an active timer for this worker
    active_timer = TopRailTiming.query.filter_by(
        worker=worker, 
        completed=False
    ).first()
    
    if active_timer:
        return jsonify({"error": "Timer already active"}), 400
    
    # Create new timing record
    new_timing = TopRailTiming(
        worker=worker,
        start_time=datetime.now(),
        date=date.today()
    )
    
    try:
        db.session.add(new_timing)
        db.session.commit()
        
        return jsonify({
            "message": "Top rail timer started",
            "timer_id": new_timing.id,
            "start_time": new_timing.start_time.isoformat()
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to start timer: {str(e)}"}), 500
@app.route('/api/top_rail/stop_timer', methods=['POST'])
def stop_top_rail_timer():
    """Stop timing for the current top rail build."""
    if 'worker' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    worker = session['worker']
    data = request.get_json() or {}
    serial_number = data.get('serial_number', '')
    
    # Find the active timer for this worker
    active_timer = TopRailTiming.query.filter_by(
        worker=worker, 
        completed=False
    ).first()
    
    if not active_timer:
        return jsonify({"error": "No active timer found"}), 404
    
    # Complete the timing record
    end_time = datetime.now()
    duration = (end_time - active_timer.start_time).total_seconds() / 60  # Convert to minutes
    
    active_timer.end_time = end_time
    active_timer.duration_minutes = round(duration, 2)
    active_timer.serial_number = serial_number
    active_timer.completed = True
    
    try:
        db.session.commit()
        
        return jsonify({
            "message": "Top rail timer stopped",
            "duration_minutes": active_timer.duration_minutes,
            "start_time": active_timer.start_time.isoformat(),
            "end_time": active_timer.end_time.isoformat()
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to stop timer: {str(e)}"}), 500

@app.route('/api/top_rail/current_timer', methods=['GET'])
def get_current_timer():
    """Get the current timer status for the logged-in worker."""
    if 'worker' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    worker = session['worker']
    
    # Find active timer
    active_timer = TopRailTiming.query.filter_by(
        worker=worker, 
        completed=False
    ).first()
    
    if not active_timer:
        return jsonify({"active": False}), 200
    
    # Calculate current elapsed time
    current_time = datetime.now()
    elapsed_minutes = (current_time - active_timer.start_time).total_seconds() / 60
    
    return jsonify({
        "active": True,
        "timer_id": active_timer.id,
        "start_time": active_timer.start_time.isoformat(),
        "elapsed_minutes": round(elapsed_minutes, 2)
    }), 200

@app.route('/api/top_rail/timing_stats', methods=['GET'])
def get_timing_stats():
    """Get timing statistics for top rails."""
    if 'worker' not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    worker = session.get('worker')
    
    # Get completed timings for this worker
    completed_timings = TopRailTiming.query.filter_by(
        worker=worker, 
        completed=True
    ).order_by(TopRailTiming.date.desc()).limit(10).all()
    
    if not completed_timings:
        return jsonify({
            "average_time": None,
            "recent_times": [],
            "total_completed": 0
        }), 200
    
    # Calculate average
    durations = [t.duration_minutes for t in completed_timings if t.duration_minutes]
    average_time = sum(durations) / len(durations) if durations else None
    
    # Format recent times
    recent_times = [{
        "date": timing.date.isoformat(),
        "duration_minutes": timing.duration_minutes,
        "serial_number": timing.serial_number,
        "start_time": timing.start_time.strftime("%H:%M"),
        "end_time": timing.end_time.strftime("%H:%M") if timing.end_time else None
    } for timing in completed_timings]
    
    return jsonify({
        "average_time": round(average_time, 2) if average_time else None,
        "recent_times": recent_times,
        "total_completed": len(completed_timings)
    }), 200

@app.route('/api/top_rail/production_stats', methods=['GET'])
def get_top_rail_production_stats():
    today = date.today()
    
    # Get today's count
    daily_count = TopRail.query.filter(TopRail.date == today).count()
    
    # Get this week's count (starting from Monday)
    start_of_week = today - timedelta(days=today.weekday())
    week_count = TopRail.query.filter(
        TopRail.date >= start_of_week,
        TopRail.date <= today
    ).count()
    
    # Get month count
    month_count = TopRail.query.filter(
        extract('year', TopRail.date) == today.year,
        extract('month', TopRail.date) == today.month
    ).count()
    
    # Get year count
    year_count = TopRail.query.filter(
        extract('year', TopRail.date) == today.year
    ).count()
    
    return jsonify({
        'daily': daily_count,
        'weekly': week_count,
        'monthly': month_count,
        'yearly': year_count
    })



class TopRailPieceCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_key = db.Column(db.String(50), unique=True, nullable=False)  # e.g., 'black_6_short'
    count = db.Column(db.Integer, default=0, nullable=False)

@app.route('/top_rail_pieces', methods=['GET', 'POST'])
def top_rail_pieces():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    key_map = {
        'a': 'black_6_short',
        'b': 'black_6_long',
        'c': 'rustic_oak_6_short',
        'd': 'rustic_oak_6_long',
        'e': 'grey_oak_6_short',
        'f': 'grey_oak_6_long',
        'g': 'stone_6_short',
        'h': 'stone_6_long',
        'i': 'rustic_black_6_short',
        'j': 'rustic_black_6_long',
        'k': 'black_7_short',
        'l': 'black_7_long',
        'm': 'rustic_oak_7_short',
        'n': 'rustic_oak_7_long',
        'o': 'grey_oak_7_short',
        'p': 'grey_oak_7_long',
        'q': 'stone_7_short',
        'r': 'stone_7_long',
        's': 'rustic_black_7_short',
        't': 'rustic_black_7_long',
    }



    if request.method == 'POST':
        key_code = request.form.get('key_code')
        if key_code and key_code in key_map:
            part_key = key_map[key_code]
            part = TopRailPieceCount.query.filter_by(part_key=part_key).first()
            if not part:
                part = TopRailPieceCount(part_key=part_key, count=1)
                db.session.add(part)
            else:
                part.count += 1
            db.session.commit()
            return jsonify({"success": True, "message": f"Added 1 to {part_key}", "part_key": part_key}), 200

        # Standard form submission
        for key in [f"{color}_{size}_{length}" for color in ['black', 'rustic_oak', 'grey_oak', 'stone','rustic_black'] for size in ['6', '7'] for length in ['short', 'long']]:
            input_value = request.form.get(f"piece_{key}")
            if input_value is not None:
                try:
                    count = int(input_value)
                    part = TopRailPieceCount.query.filter_by(part_key=key).first()
                    if not part:
                        part = TopRailPieceCount(part_key=key, count=count)
                        db.session.add(part)
                    else:
                        part.count = count
                except ValueError:
                    flash(f"Invalid number for {key}", "error")
        db.session.commit()
        flash("Top rail piece counts updated successfully.", "success")
        return redirect(url_for('top_rail_pieces'))

    # Prepare data for display
    counts = {}
    all_parts = TopRailPieceCount.query.all()
    for part in all_parts:
        counts[f"piece_{part.part_key}"] = part.count

    return render_template('top_rail_pieces.html', counts=counts)


@app.route('/fastest_leaderboard')
def fastest_leaderboard():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    min_duration = timedelta(minutes=40)
    min_body_duration = timedelta(minutes=55)

    top_rail_entries = []
    pod_entries = []
    body_entries = []

    # Top Rails
    for tr in TopRail.query.all():
        try:
            start_time = datetime.strptime(tr.start_time, "%H:%M").time()
            finish_time = datetime.strptime(tr.finish_time, "%H:%M").time()
            start_dt = datetime.combine(tr.date, start_time)

            if finish_time < start_time:
                finish_dt = datetime.combine(tr.date + timedelta(days=1), finish_time)
            else:
                finish_dt = datetime.combine(tr.date, finish_time)

            if tr.lunch and tr.lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            time_taken = finish_dt - start_dt
            if time_taken >= min_duration:
                top_rail_entries.append({
                    "worker": tr.worker,
                    "serial_number": tr.serial_number,
                    "time_taken": time_taken,
                    "date": tr.date.strftime("%d/%m/%Y")
                })

        except Exception as e:
            print(f"Skipping top rail: {e}")

    # Pods
    for pod in CompletedPods.query.all():
        try:
            start_dt = datetime.combine(pod.date, pod.start_time)
            finish_dt = datetime.combine(pod.date, pod.finish_time)

            if pod.finish_time < pod.start_time:
                finish_dt = datetime.combine(pod.date + timedelta(days=1), pod.finish_time)

            if pod.lunch and pod.lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            time_taken = finish_dt - start_dt
            if time_taken >= min_duration:
                pod_entries.append({
                    "worker": pod.worker,
                    "serial_number": pod.serial_number,
                    "time_taken": time_taken,
                    "date": pod.date.strftime("%d/%m/%Y")
                })

        except Exception as e:
            print(f"Skipping pod: {e}")

    # Bodies
    for body in CompletedTable.query.all():
        try:
            start_time = datetime.strptime(body.start_time, "%H:%M").time()
            finish_time = datetime.strptime(body.finish_time, "%H:%M").time()
            start_dt = datetime.combine(body.date, start_time)

            if finish_time < start_time:
                finish_dt = datetime.combine(body.date + timedelta(days=1), finish_time)
            else:
                finish_dt = datetime.combine(body.date, finish_time)

            if body.lunch and body.lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            time_taken = finish_dt - start_dt
            if time_taken >= min_body_duration:
                body_entries.append({
                    "worker": body.worker,
                    "serial_number": body.serial_number,
                    "time_taken": time_taken,
                    "date": body.date.strftime("%d/%m/%Y")
                })

        except Exception as e:
            print(f"Skipping body: {e}")

    # Sort and keep top 5
    top_rails = sorted(top_rail_entries, key=lambda x: x['time_taken'])[:5]
    top_pods = sorted(pod_entries, key=lambda x: x['time_taken'])[:5]
    top_bodies = sorted(body_entries, key=lambda x: x['time_taken'])[:5]

    return render_template("fastest_leaderboard.html",
                           top_rails=top_rails,
                           pods=top_pods,
                           bodies=top_bodies)






