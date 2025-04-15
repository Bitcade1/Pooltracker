from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, date
from collections import defaultdict  # Ensure defaultdict is imported
import os
import requests
from calendar import monthrange
from sqlalchemy import func, extract
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

    if request.method == 'POST' and 'table' in request.form:
        table = request.form.get('table')
        entry_id = request.form.get('id')

        model = None
        if table == 'pods':
            model = CompletedPods
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
        hardware_parts=hardware_parts
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
        "6ft Cue Ball Separator", "6ft Large Ramp"
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
        "6ft Large Ramp": 1
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
        if part in ["Large Ramp", "Cue Ball Separator"]:
            required_total = target_7ft * usage
        elif part in ["6ft Large Ramp", "6ft Cue Ball Separator"]:
            required_total = target_6ft * usage
        else:
            required_total = (target_7ft + target_6ft) * usage

        available_total = inventory_counts.get(part, 0) + parts_used_this_month.get(part, 0)
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
        "Chrome corner": 4, "Top rail trim short length": 1,
        "Top rail trim long length": 1, "Ramp 170mm": 1, "Ramp 158mm": 1,
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

        current_count = table_parts_counts[selected_part]

        # Amount is only used for 'bulk' updates; default to 1 otherwise
        try:
            amount = int(request.form.get('amount', 1))
        except ValueError:
            flash("Amount must be a number.", "error")
            return redirect(url_for('counting_chinese_parts'))

        # Retrieve or create a single row for this part
        existing_entry = (db.session.query(PrintedPartsCount)
                          .filter_by(part_name=selected_part)
                          .first())
        if not existing_entry:
            # Create a new entry if none exists yet
            existing_entry = PrintedPartsCount(
                part_name=selected_part,
                count=current_count,  # Likely 0, but we'll match logic
                date=datetime.utcnow().date(),
                time=datetime.utcnow().time()
            )
            db.session.add(existing_entry)
            db.session.commit()  # Commit to get an ID if needed

        # Perform the requested action
        if action == 'increment':
            existing_entry.count += 1

        elif action == 'decrement':
            if existing_entry.count > 0:
                existing_entry.count -= 1
            else:
                flash("Cannot decrement below zero.", "error")
                return redirect(url_for('counting_chinese_parts'))

        elif action == 'bulk':
            # Positive bulk = add stock; negative bulk = remove stock if sufficient
            if amount < 0 and existing_entry.count < abs(amount):
                flash("Insufficient stock to perform this bulk decrement.", "error")
                return redirect(url_for('counting_chinese_parts'))
            existing_entry.count += amount

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
                flash("Please provide a valid integer for bulk amount.", "error")
                return redirect(url_for('counting_hardware'))

            # Validate the selected part
            if part_name not in hardware_counts:
                flash("Invalid hardware part selected.", "error")
                return redirect(url_for('counting_hardware'))

            current_count = hardware_counts[part_name]

            if action == 'increment':
                new_count = current_count + 1
            elif action == 'decrement':
                if current_count > 0:
                    new_count = current_count - 1
                else:
                    flash("Cannot decrement; no stock left.", "error")
                    return redirect(url_for('counting_hardware'))
            elif action == 'bulk':
                # Positive for adding, negative for subtracting
                potential_new_count = current_count + amount
                if potential_new_count < 0:
                    flash("Insufficient stock for that bulk reduction.", "error")
                    return redirect(url_for('counting_hardware'))
                new_count = potential_new_count

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

            # Update local dict so it's accurate for this page load
            hardware_counts[part_name] = new_count

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
        
        new_pod = CompletedPods(
            worker=worker,
            start_time=start_time,
            finish_time=finish_time,
            serial_number=serial_number,
            lunch=lunch,
            issue=issue_text,
            date=date.today()
        )
        
        try:
            db.session.add(new_pod)
            db.session.commit()
            flash("Pods entry added successfully!", "success")
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
        if total_pods > 0:
            avg_hours_per_pod = cumulative_working_hours / total_pods
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
                        "Chrome corner": 4,
                        "Top rail trim short length": 1,
                        "Top rail trim long length": 1,
                        "Ramp 170mm": 1,
                        "Ramp 158mm": 1,
                        "Ramp 918mm": 1,
                        "Ramp 376mm": 1,
                        "Chrome handles": 1,
                        "Center pockets": 2,
                        "Corner pockets": 4,
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
                flash(f"{table.capitalize()} entry deleted successfully!", "success")
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
    # Get the current month or the selected month
    current_date = datetime.now()
    selected_month = request.form.get('month', f"{current_date.year}-{current_date.month:02d}")
    
    if request.method == 'POST':
        section = request.form.get('section')
        action = request.form.get('action')
        
        # Initialize database connection
        conn = get_db_connection()
        
        # Handle the different actions
        if action == 'increment':
            # Check if it's a Coloured Body section
            if "Coloured Body" in section:
                # Decrement plain MDF from inventory when adding Coloured Body
                inventory = get_inventory(conn)
                if inventory['plain_mdf'] > 0:  # Make sure there's inventory to take from
                    conn.execute('UPDATE inventory SET plain_mdf = plain_mdf - 1')
                    # Now increment the counter
                    increment_wood_count(conn, section, selected_month)
                    conn.commit()
                    flash('Count updated and inventory adjusted', 'success')
                else:
                    flash('Cannot add Coloured Body - No Plain MDF available in inventory', 'error')
            else:
                # Regular increment for other sections
                increment_wood_count(conn, section, selected_month)
                conn.commit()
                flash('Count incremented', 'success')
                
        elif action == 'decrement':
            # Handle decrement (no need to adjust inventory when decrementing)
            decrement_wood_count(conn, section, selected_month)
            conn.commit()
            flash('Count decremented', 'success')
            
        elif action == 'bulk_increment':
            bulk_amount = int(request.form.get('bulk_amount', 0))
            
            # Check if it's a Coloured Body section
            if "Coloured Body" in section and bulk_amount > 0:
                # Make sure there's enough inventory
                inventory = get_inventory(conn)
                if inventory['plain_mdf'] >= bulk_amount:
                    # Decrease plain MDF by the bulk amount
                    conn.execute('UPDATE inventory SET plain_mdf = plain_mdf - ?', (bulk_amount,))
                    # Add the bulk amount to the counter
                    bulk_increment_wood_count(conn, section, selected_month, bulk_amount)
                    conn.commit()
                    flash(f'Added {bulk_amount} and adjusted inventory', 'success')
                else:
                    flash(f'Cannot add {bulk_amount} Coloured Body - Only {inventory["plain_mdf"]} Plain MDF available', 'error')
            else:
                # Regular bulk increment for other sections
                bulk_increment_wood_count(conn, section, selected_month, bulk_amount)
                conn.commit()
                flash(f'Added {bulk_amount}', 'success')
        
        conn.close()
        return redirect(url_for('counting_wood', month=selected_month))
    
    # Handle GET request
    conn = get_db_connection()
    
    # Make sure your sections include the new "Coloured Body" options
    sections = [
        "7ft Rails", "7ft Diamond", "7ft K66", "7ft Standard Black", "7ft Coloured Body",
        "6ft Rails", "6ft Diamond", "6ft K66", "6ft Standard Black", "6ft Coloured Body"
    ]
    
    # Initialize counts if they don't exist in database
    for section in sections:
        if not get_section_count(conn, section, selected_month):
            initialize_section_count(conn, section, selected_month)
    
    # Get current counts for all sections
    counts = {}
    for section in sections:
        counts[section] = get_section_count(conn, section, selected_month)
    
    # Get inventory
    inventory = get_inventory(conn)
    
    # Get daily wood data
    daily_wood_data = get_daily_wood_data(conn)
    
    # Calculate weekly summary
    weekly_summary = calculate_weekly_summary(conn)
    
    # Calculate weekly sheets cut
    weekly_sheets_cut = calculate_weekly_sheets_cut(conn)
    
    # Calculate monthly sheets cut for the selected month
    monthly_sheets_cut = calculate_monthly_sheets_cut(conn, selected_month)
    
    # Get weekly breakdown for the selected month
    weekly_breakdown = get_weekly_breakdown(conn, selected_month)
    
    # Get yearly breakdown
    current_year = datetime.now().year
    yearly_breakdown = get_yearly_breakdown(conn, current_year)
    
    # Create available months for the dropdown
    available_months = get_available_months(conn)
    
    conn.close()
    
    return render_template('counting_wood.html', 
                          counts=counts,
                          inventory=inventory,
                          daily_wood_data=daily_wood_data,
                          weekly_summary=weekly_summary,
                          weekly_sheets_cut=weekly_sheets_cut,
                          monthly_sheets_cut=monthly_sheets_cut,
                          weekly_breakdown=weekly_breakdown,
                          yearly_breakdown=yearly_breakdown,
                          current_year=current_year,
                          selected_month=selected_month,
                          available_months=available_months)

# Helper functions
def get_db_connection():
    conn = sqlite3.connect('pool_table_tracker.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_section_count(conn, section, month):
    result = conn.execute('SELECT count FROM wood_counts WHERE section = ? AND month = ?', 
                         (section, month)).fetchone()
    return result['count'] if result else None

def initialize_section_count(conn, section, month):
    conn.execute('INSERT INTO wood_counts (section, month, count) VALUES (?, ?, 0)', 
                (section, month))
    conn.commit()

def increment_wood_count(conn, section, month):
    # First check if section exists for the month
    if get_section_count(conn, section, month) is None:
        initialize_section_count(conn, section, month)
    
    # Increment the count
    conn.execute('''
        UPDATE wood_counts 
        SET count = count + 1 
        WHERE section = ? AND month = ?
    ''', (section, month))
    
    # Log this entry
    log_wood_entry(conn, section)

def decrement_wood_count(conn, section, month):
    # Check if section exists and has a count > 0
    current_count = get_section_count(conn, section, month)
    if current_count is None:
        initialize_section_count(conn, section, month)
        return
        
    if current_count > 0:
        conn.execute('''
            UPDATE wood_counts 
            SET count = count - 1 
            WHERE section = ? AND month = ?
        ''', (section, month))

def bulk_increment_wood_count(conn, section, month, amount):
    # First check if section exists for the month
    if get_section_count(conn, section, month) is None:
        initialize_section_count(conn, section, month)
    
    # Increment the count by the bulk amount
    conn.execute('''
        UPDATE wood_counts 
        SET count = count + ? 
        WHERE section = ? AND month = ?
    ''', (amount, section, month))
    
    # Log this entry (as bulk)
    log_wood_entry(conn, section, amount)

def log_wood_entry(conn, section, count=1):
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    
    conn.execute('''
        INSERT INTO wood_log (date, time, section, count) 
        VALUES (?, ?, ?, ?)
    ''', (date_str, now, section, count))

def get_inventory(conn):
    result = conn.execute('SELECT plain_mdf, black_mdf, plain_mdf_36 FROM inventory').fetchone()
    if result:
        return {
            'plain_mdf': result['plain_mdf'],
            'black_mdf': result['black_mdf'],
            'plain_mdf_36': result['plain_mdf_36']
        }
    else:
        # Initialize inventory if it doesn't exist
        conn.execute('INSERT INTO inventory (plain_mdf, black_mdf, plain_mdf_36) VALUES (0, 0, 0)')
        conn.commit()
        return {'plain_mdf': 0, 'black_mdf': 0, 'plain_mdf_36': 0}

def get_daily_wood_data(conn):
    today = datetime.now().strftime('%Y-%m-%d')
    return conn.execute('''
        SELECT * FROM wood_log 
        WHERE date = ? 
        ORDER BY time DESC
    ''', (today,)).fetchall()

def calculate_weekly_summary(conn):
    # Get start of the week (Monday)
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())
    start_date = start_of_week.strftime('%Y-%m-%d')
    
    # Query for data grouped by day
    results = conn.execute('''
        SELECT 
            date, 
            SUM(count) as total 
        FROM wood_log 
        WHERE date >= ? 
        GROUP BY date 
        ORDER BY date
    ''', (start_date,)).fetchall()
    
    # Format into a dictionary
    summary = {}
    for row in results:
        day = datetime.strptime(row['date'], '%Y-%m-%d').strftime('%A')
        summary[day] = row['total']
    
    return summary

def calculate_weekly_sheets_cut(conn):
    # Get start of the week (Monday)
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())
    start_date = start_of_week.strftime('%Y-%m-%d')
    
    # Query for total sum
    result = conn.execute('''
        SELECT SUM(count) as total 
        FROM wood_log 
        WHERE date >= ?
    ''', (start_date,)).fetchone()
    
    return result['total'] if result and result['total'] else 0

def calculate_monthly_sheets_cut(conn, month):
    # Extract year and month from the selected_month format (YYYY-MM)
    if '-' in month:
        year, month_num = month.split('-')
        
        # Create date range for the month
        start_date = f"{year}-{month_num}-01"
        
        # Calculate end date
        if month_num == '12':
            end_year = int(year) + 1
            end_month = '01'
        else:
            end_year = year
            end_month = f"{int(month_num) + 1:02d}"
        
        end_date = f"{end_year}-{end_month}-01"
        
        # Query for total sum for the month
        result = conn.execute('''
            SELECT SUM(count) as total 
            FROM wood_log 
            WHERE date >= ? AND date < ?
        ''', (start_date, end_date)).fetchone()
        
        return result['total'] if result and result['total'] else 0
    
    return 0

def get_weekly_breakdown(conn, month):
    # Extract year and month
    if '-' in month:
        year, month_num = month.split('-')
        
        # Create date range for the month
        start_date = f"{year}-{month_num}-01"
        start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
        
        # Calculate end date
        if month_num == '12':
            end_year = int(year) + 1
            end_month = '01'
        else:
            end_year = year
            end_month = f"{int(month_num) + 1:02d}"
        
        end_date = f"{end_year}-{end_month}-01"
        end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
        
        # Initialize result
        breakdown = {}
        
        # Calculate weeks in the month
        current_week_start = start_date_obj
        week_num = 1
        
        while current_week_start < end_date_obj:
            # Calculate week end (Sunday)
            days_to_sunday = 6 - current_week_start.weekday()
            current_week_end = current_week_start + timedelta(days=days_to_sunday)
            
            # Ensure week end doesn't exceed month end
            if current_week_end > end_date_obj:
                current_week_end = end_date_obj - timedelta(days=1)
            
            # Format dates for query
            query_start = current_week_start.strftime('%Y-%m-%d')
            query_end = (current_week_end + timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Query for total sum for this week
            result = conn.execute('''
                SELECT SUM(count) as total 
                FROM wood_log 
                WHERE date >= ? AND date < ?
            ''', (query_start, query_end)).fetchone()
            
            total = result['total'] if result and result['total'] else 0
            
            # Add to breakdown
            breakdown[f"Week {week_num}"] = {
                "start_date": current_week_start.strftime('%d %b'),
                "end_date": current_week_end.strftime('%d %b'),
                "sheets_cut": total
            }
            
            # Move to next week
            current_week_start = current_week_end + timedelta(days=1)
            week_num += 1
        
        return breakdown
    
    return {}

def get_yearly_breakdown(conn, year):
    breakdown = {}
    
    # For each month in the year
    for month in range(1, 13):
        month_str = f"{month:02d}"
        
        # Create date range for the month
        start_date = f"{year}-{month_str}-01"
        
        # Calculate end date
        if month == 12:
            end_year = year + 1
            end_month = '01'
        else:
            end_year = year
            end_month = f"{month + 1:02d}"
        
        end_date = f"{end_year}-{end_month}-01"
        
        # Query for total sum for the month
        result = conn.execute('''
            SELECT SUM(count) as total 
            FROM wood_log 
            WHERE date >= ? AND date < ?
        ''', (start_date, end_date)).fetchone()
        
        total = result['total'] if result and result['total'] else 0
        
        # Add to breakdown with month name
        month_name = datetime(year, month, 1).strftime('%B')
        breakdown[month_name] = total
    
    return breakdown

def get_available_months(conn):
    # Get unique months from wood_counts
    results = conn.execute('''
        SELECT DISTINCT month FROM wood_counts
        ORDER BY month DESC
    ''').fetchall()
    
    # Create a list of (value, label) tuples for the dropdown
    months = []
    for row in results:
        month_val = row['month']
        if '-' in month_val:
            year, month_num = month_val.split('-')
            month_name = datetime(int(year), int(month_num), 1).strftime('%B %Y')
            months.append((month_val, month_name))
    
    # Add current month if not already in the list
    current_date = datetime.now()
    current_month = f"{current_date.year}-{current_date.month:02d}"
    current_month_name = current_date.strftime('%B %Y')
    
    if not any(month[0] == current_month for month in months):
        months.insert(0, (current_month, current_month_name))
    
    return months


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
    goal_minutes = db.Column(db.Integer, default=0)  # Target time in minutes
    start_time = db.Column(db.DateTime)
    finish_time = db.Column(db.DateTime)
    paused_time = db.Column(db.DateTime)  # New field for tracking when a job is paused
    actual_minutes = db.Column(db.Integer)  # Actual working time in minutes
    setup_minutes = db.Column(db.Integer)  # Setup time in minutes
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

from flask import Flask, render_template, request, redirect, url_for, flash, session
from sqlalchemy import func, extract
from sqlalchemy.exc import IntegrityError
from calendar import monthrange
from datetime import date, datetime


from flask import render_template, request, redirect, url_for, flash, session
from sqlalchemy import func, extract
from datetime import datetime, date, timedelta
from calendar import monthrange

from flask import render_template, request, redirect, url_for, flash, session
from sqlalchemy import func, extract, desc
from datetime import datetime, date, timedelta
from calendar import monthrange

import re  # Make sure this is at the top of the file

@app.route('/bodies', methods=['GET', 'POST'])
def bodies():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))
    
    # Retrieve issues and any pods not yet converted
    issues = [issue.description for issue in Issue.query.all()]
    unconverted_pods = CompletedPods.query.filter(
        ~CompletedPods.serial_number.in_(db.session.query(CompletedTable.serial_number))
    ).all()

    if request.method == 'POST':
        worker = session['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        original_serial_number = request.form['serial_number']
        color_selector = request.form.get('color_selector', 'Black')
        
        # Get the formatted serial number if it exists, otherwise use the original
        serial_number = request.form.get('formatted_serial_number', original_serial_number)
        
        # If formatted_serial_number is empty or not provided, format the serial number manually
        if not serial_number or serial_number.strip() == "":
            # Clean any existing color suffix from the serial number, but preserve size suffix
            clean_serial = original_serial_number
            
            # Check for and remove existing color suffixes
            for suffix in ['-GO', ' - GO', '-O', ' - O', '-C', ' - C', '-B', ' - B']:
                if suffix in clean_serial:
                    # If suffix is in the middle of the string, keep what comes after it
                    parts = clean_serial.split(suffix, 1)
                    if len(parts) > 1 and parts[1]:
                        clean_serial = parts[0] + parts[1]
                    else:
                        clean_serial = parts[0]
            
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
            "Chrome corner": 4,
            "Top rail trim short length": 1,
            "Top rail trim long length": 1,
            "Ramp 170mm": 1,
            "Ramp 158mm": 1,
            "Ramp 918mm": 1,
            "Ramp 376mm": 1,
            "Chrome handles": 1,
            "Center pockets": 2,
            "Corner pockets": 4,
            "Sticker Set": 1
        }

        # Helper function to determine if it's a 6ft table
        def is_6ft(serial):
            return serial.replace(" ", "").endswith("-6") or "-6-" in serial.replace(" ", "") or " - 6 - " in serial

        # Adjust parts for 6ft tables
        if is_6ft(serial_number):
            # For a 6ft table, remove standard parts and add the 6ft-specific ones.
            parts_to_deduct.pop("Large Ramp", None)
            parts_to_deduct.pop("Cue Ball Separator", None)
            parts_to_deduct.pop("Small Ramp", None)  # Also remove Small Ramp for 6ft tables
            parts_to_deduct["6ft Large Ramp"] = 1
            parts_to_deduct["6ft Cue Ball Separator"] = 1

        # Deduct each required part from the inventory
        for part_name, quantity_needed in parts_to_deduct.items():
            part_entry = PrintedPartsCount.query.filter_by(part_name=part_name) \
                                                 .order_by(PrintedPartsCount.date.desc()).first()
            if part_entry and part_entry.count >= quantity_needed:
                part_entry.count -= quantity_needed
            else:
                flash(f"Not enough inventory for {part_name} to complete the body!", "error")
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
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "error")
            return redirect(url_for('bodies'))

        # Helper function to determine color from serial number
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

    # Monthly totals summary
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
        if total_bodies > 0:
            avg_hours = cumulative_working_hours / total_bodies
            hours = int(avg_hours)
            minutes = int((avg_hours - hours) * 60)
            seconds = int((((avg_hours - hours) * 60) - minutes) * 60)
            avg_hours_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            avg_hours_formatted = "N/A"
        monthly_totals_formatted.append({
            "month": date(year=yr, month=mo, day=1).strftime("%B %Y"),
            "count": total_bodies,
            "average_hours_per_table": avg_hours_formatted
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
            if '-' in clean_serial or ' - ' in clean_serial:
                parts = re.split(r'(-| - )', clean_serial, 1)
                base_serial = parts[0]
                clean_serial = f"{base_serial} - 6"
            else:
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
            "Corner pockets": 4
        }

        # Deduct inventory for each part needed to complete the top rail
        for part_name, quantity_needed in parts_to_deduct.items():
            part_entries = db.session.query(PrintedPartsCount).filter_by(part_name=part_name).all()
            total_stock = sum(entry.count for entry in part_entries)
            if total_stock < quantity_needed:
                flash(f"Not enough inventory for {part_name} to complete the top rail! (Available: {total_stock})", "error")
                return redirect(url_for('top_rails'))
            remaining_to_deduct = quantity_needed
            for entry in part_entries:
                if remaining_to_deduct <= 0:
                    break
                if entry.count >= remaining_to_deduct:
                    entry.count -= remaining_to_deduct
                    remaining_to_deduct = 0
                else:
                    remaining_to_deduct -= entry.count
                    entry.count = 0

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
            flash("Top rail entry added successfully and inventory updated!", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Error: Serial number already exists. Please use a unique serial number.", "error")
            return redirect(url_for('top_rails'))
        
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
        stock_type = f"top_rail_{size.lower()}_{color}"
        
        # Update the stock count
        stock_entry = TableStock.query.filter_by(type=stock_type).first()
        if not stock_entry:
            stock_entry = TableStock(type=stock_type, count=0)
            db.session.add(stock_entry)
        stock_entry.count += 1
        db.session.commit()
        # -----------------------------------------
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
    colors = ['Black', 'Rustic Oak', 'Grey Oak', 'Stone']

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
                current_count.count -= reject_amount
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
        "6ft Cue Ball Separator", "6ft Large Ramp"
    ]

    parts_counts = {
        part: db.session.query(db.func.sum(PrintedPartsCount.count))
            .filter_by(part_name=part)
            .scalar() or 0
        for part in parts
    }

    current_month = datetime.utcnow().month
    current_year = datetime.utcnow().year

    schedule = ProductionSchedule.query.filter_by(year=current_year, month=current_month).first()
    target_7ft = schedule.target_7ft if schedule else 60
    target_6ft = schedule.target_6ft if schedule else 60

    # Fetch tables built this month (exactly as you did in bodies route)
    all_bodies_this_month = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == current_year,
        extract('month', CompletedTable.date) == current_month
    ).all()

    # Clearly identify built tables
    def is_6ft(serial):
        return serial.replace(" ", "").endswith("-6")

    bodies_built_6ft = sum(1 for table in all_bodies_this_month if is_6ft(table.serial_number))
    bodies_built_7ft = sum(1 for table in all_bodies_this_month if not is_6ft(table.serial_number))

    # Now accurately calculate required totals per table type
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
        "6ft Large Ramp": 1
    }

    parts_status = {}
    for part, usage in parts_usage_per_body.items():
        if part in ["Large Ramp", "Cue Ball Separator"]:
            required_total = target_7ft * usage
            completed_total = bodies_built_7ft * usage

        elif part in ["6ft Large Ramp", "6ft Cue Ball Separator"]:
            required_total = target_6ft * usage
            completed_total = bodies_built_6ft * usage

        else:
            required_total = (target_7ft + target_6ft) * usage
            completed_total = (bodies_built_7ft + bodies_built_6ft) * usage

        # IMPORTANT: inventory is exactly what's in stock now
        inventory_total = parts_counts.get(part, 0)

        # How many parts you have available in total
        available_total = inventory_total + completed_total

        difference = available_total - required_total
        if difference >= 0:
            parts_status[part] = f"{difference} extras"
        else:
            parts_status[part] = f"{abs(difference)} left to make"

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
                        CushionJobRecord.finish_time.is_(None)
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
        'target_period': 365,  # Default target period in days
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


if __name__ == '__main__':
    app.run(debug=True)