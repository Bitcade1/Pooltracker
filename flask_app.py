from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, date
from collections import defaultdict  # Ensure defaultdict is imported
import os
import requests
from calendar import monthrange
from sqlalchemy import func, extract

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

class CushionCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cushion_type = db.Column(db.String(10), nullable=False)
    count = db.Column(db.Integer, default=1, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow, nullable=False)
    time = db.Column(db.Time, default=datetime.utcnow().time, nullable=False)

    def __init__(self, cushion_type):
        self.cushion_type = cushion_type
        self.count = 1
        self.date = datetime.utcnow().date()
        self.time = datetime.utcnow().time()

class ProductionSchedule(db.Model):
    __tablename__ = 'production_schedule'
    id = db.Column(db.Integer, primary_key=True)

    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)

    black_7ft = db.Column(db.Integer, default=0, nullable=False)
    black_6ft = db.Column(db.Integer, default=0, nullable=False)

    grey_7ft = db.Column(db.Integer, default=0, nullable=False)
    grey_6ft = db.Column(db.Integer, default=0, nullable=False)

    oak_7ft = db.Column(db.Integer, default=0, nullable=False)
    oak_6ft = db.Column(db.Integer, default=0, nullable=False)

    grey_oak_7ft = db.Column(db.Integer, default=0, nullable=False)
    grey_oak_6ft = db.Column(db.Integer, default=0, nullable=False)

    concrete_7ft = db.Column(db.Integer, default=0, nullable=False)
    concrete_6ft = db.Column(db.Integer, default=0, nullable=False)

    def __repr__(self):
        return (
            f"<ProductionSchedule {self.month}/{self.year} "
            f"Black7={self.black_7ft} Black6={self.black_6ft} "
            f"Grey7={self.grey_7ft} Grey6={self.grey_6ft} "
            f"Oak7={self.oak_7ft} Oak6={self.oak_6ft} "
            f"GreyOak7={self.grey_oak_7ft} GreyOak6={self.grey_oak_6ft} "
            f"Concrete7={self.concrete_7ft} Concrete6={self.concrete_6ft}>"
        )




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

    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    if request.method == 'POST':
        try:
            additional_plain_mdf = int(request.form['additional_plain_mdf'])
            additional_black_mdf = int(request.form['additional_black_mdf'])
            inventory.plain_mdf += additional_plain_mdf
            inventory.black_mdf += additional_black_mdf
            db.session.commit()
            flash("MDF inventory updated successfully!", "success")
        except ValueError:
            flash("Please enter valid numbers for MDF quantities.", "error")

    return render_template('manage_mdf_inventory.html', inventory=inventory)

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
            current_count = PrintedPartsCount.query.filter_by(part_name=part).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()

            if current_count and current_count.count >= reject_amount:
                current_count.count -= reject_amount
                flash(f"Rejected {reject_amount} of {part} from inventory.", "success")
                db.session.commit()
            else:
                flash(f"Not enough inventory to reject {reject_amount} of {part}.", "error")
        else:
            increment_amount = int(request.form['increment_amount'])
            current_count = PrintedPartsCount.query.filter_by(part_name=part).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()

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

    parts = ["Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder", "Small Ramp", "Cue Ball Separator", "Bushing"]
    parts_counts = {part: PrintedPartsCount.query.filter_by(part_name=part).order_by(PrintedPartsCount.date.desc()).first() for part in parts}
    parts_counts = {part: count.count if count else 0 for part, count in parts_counts.items()}

    return render_template('counting_3d_printing_parts.html', parts_counts=parts_counts)

from flask import render_template, request, redirect, url_for, session, flash
from datetime import datetime
from sqlalchemy import func, extract
from your_app import app, db
from your_app.models import (
    PrintedPartsCount,
    WoodCount,
    CompletedTable,
    HardwarePart
)

@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # ---------------------------------------------------------------------
    # 1) 3D PRINTED PARTS (still using your existing hard-coded list)
    # ---------------------------------------------------------------------
    parts = ["Large Ramp", "Paddle", "Laminate", "Spring Mount",
             "Spring Holder", "Small Ramp", "Cue Ball Separator", "Bushing"]

    # Calculate current stock for each 3D printed part
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
    bodies_built_this_month = (
        db.session.query(func.count(CompletedTable.id))
        .filter(
            extract('year', CompletedTable.date) == today.year,
            extract('month', CompletedTable.date) == today.month
        )
        .scalar()
    )

    parts_usage_per_body = {
        "Large Ramp": 1,
        "Paddle": 1,
        "Laminate": 4,
        "Spring Mount": 1,
        "Spring Holder": 1,
        "Small Ramp": 1,
        "Cue Ball Separator": 1,
        "Bushing": 2
    }
    parts_used_this_month = {
        part: bodies_built_this_month * usage
        for part, usage in parts_usage_per_body.items()
    }
    target_tables_per_month = 60
    parts_status = {}

    for part, usage in parts_usage_per_body.items():
        required_total = target_tables_per_month * usage
        available_total = inventory_counts.get(part, 0) + parts_used_this_month.get(part, 0)
        difference = available_total - required_total
        if difference >= 0:
            parts_status[part] = f"{difference} extras"
        else:
            parts_status[part] = f"{abs(difference)} left to make"

    # ---------------------------------------------------------------------
    # 4) TABLE PARTS (still using your original dict)
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

    # Calculate how many tables can be built from table parts
    tables_possible_per_part = {
        part: table_parts_counts[part] // req_per_table
        for part, req_per_table in table_parts.items()
    }
    max_tables_possible = min(tables_possible_per_part.values())

    # ---------------------------------------------------------------------
    # 5) HARDWARE PARTS (NOW FROM DB INSTEAD OF LIST)
    # ---------------------------------------------------------------------
    # Retrieve all hardware parts from your HardwarePart table:
    hardware_parts_query = HardwarePart.query.all()  # or filter(...) if needed

    hardware_counts = {}
    for hp in hardware_parts_query:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=hp.name)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        # If no PrintedPartsCount record, default to hp.initial_count
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
                count=current_count,  # Likely 0, but we’ll match logic
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
        issue = request.form['issue']
        lunch = request.form['lunch']

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
            issue=issue,
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
    completed_pods = CompletedPods.query.filter_by(date=today).all()
    last_entry = CompletedPods.query.order_by(CompletedPods.id.desc()).first()
    current_time = last_entry.finish_time.strftime("%H:%M") if last_entry else datetime.now().strftime("%H:%M")

    pods_this_month = CompletedPods.query.filter(
        extract('year', CompletedPods.date) == today.year,
        extract('month', CompletedPods.date) == today.month
    ).count()

    daily_history = (
        db.session.query(
            CompletedPods.date,
            func.count(CompletedPods.id).label('count'),
            func.group_concat(CompletedPods.serial_number, ', ').label('serial_numbers')
        )
        .filter(
            extract('year', CompletedPods.date) == today.year,
            extract('month', CompletedPods.date) == today.month
        )
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

    # Monthly totals for the entire year
    monthly_totals = (
        db.session.query(
            extract('year', CompletedPods.date).label('year'),
            extract('month', CompletedPods.date).label('month'),
            func.count(CompletedPods.id).label('total'),
            func.max(CompletedPods.finish_time).label('last_completion_time')
        )
        .filter(
            extract('year', CompletedPods.date) == today.year
        )
        .group_by('year', 'month')
        .order_by('year', 'month')
        .all()
    )

    monthly_totals_formatted = []
    for row in monthly_totals:
        year = int(row.year)
        month = int(row.month)
        total_pods = row.total
        last_completion_time = row.last_completion_time

        if last_completion_time:
            last_completion_datetime = datetime.combine(today, last_completion_time)
        else:
            last_completion_datetime = datetime.now()

        last_day = last_completion_datetime.day
        # Calculate workdays (Mon-Fri)
        work_days = sum(1 for day_i in range(1, last_day + 1) if date(year, month, day_i).weekday() < 5)
        cumulative_working_hours = work_days * 7.5  # 7.5 hours per workday

        if total_pods > 0:
            avg_hours_per_pod = cumulative_working_hours / total_pods
            # Convert decimal hours to HH:MM:SS
            hours = int(avg_hours_per_pod)
            minutes = int((avg_hours_per_pod - hours) * 60)
            seconds = int((((avg_hours_per_pod - hours) * 60) - minutes) * 60)
            avg_hours_per_pod_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            avg_hours_per_pod_formatted = "N/A"

        monthly_totals_formatted.append({
            "month": date(year=year, month=month, day=1).strftime("%B %Y"),
            "count": total_pods,
            "average_hours_per_pod": avg_hours_per_pod_formatted
        })

    last_pod = CompletedPods.query.order_by(CompletedPods.id.desc()).first()
    if last_pod:
        try:
            next_serial_number = str(int(last_pod.serial_number) + 1)
        except ValueError:
            next_serial_number = "1000"
    else:
        next_serial_number = "1000"

    return render_template(
        'pods.html',
        issues=issues,
        current_time=current_time,
        completed_tables=completed_pods,
        pods_this_month=pods_this_month,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted,
        next_serial_number=next_serial_number
    )




@app.route('/admin/raw_data', methods=['GET', 'POST'])
def manage_raw_data():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    serial_number_query = request.args.get('serial_number')
    if serial_number_query:
        pods = CompletedPods.query.filter_by(serial_number=serial_number_query).all()
        top_rails = TopRail.query.filter_by(serial_number=serial_number_query).all()
        bodies = CompletedTable.query.filter_by(serial_number=serial_number_query).all()
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
                db.session.delete(entry)
                db.session.commit()
                flash(f"{table.capitalize()} entry deleted successfully!", "success")
            else:
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

    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    today = datetime.now().date()
    previous_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    current_month = today.replace(day=1)
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)

    available_months = [
        (previous_month.strftime("%Y-%m"), previous_month.strftime("%B %Y")),
        (current_month.strftime("%Y-%m"), current_month.strftime("%B %Y")),
        (next_month.strftime("%Y-%m"), next_month.strftime("%B %Y"))
    ]

    selected_month = request.form.get('month') or request.args.get('month', current_month.strftime("%Y-%m"))
    selected_year, selected_month_num = map(int, selected_month.split('-'))
    month_start_date = date(selected_year, selected_month_num, 1)
    month_end_date = date(selected_year, selected_month_num, monthrange(selected_year, selected_month_num)[1])

    if request.method == 'POST' and 'section' in request.form:
        section = request.form['section']
        action = request.form.get('action', 'increment')
        current_time = datetime.now().time()

        monthly_entry = WoodCount.query.filter(
            WoodCount.section == section,
            WoodCount.date >= month_start_date,
            WoodCount.date <= month_end_date
        ).first()

        if not monthly_entry:
            monthly_entry = WoodCount(section=section, count=0, date=month_start_date, time=current_time)
            db.session.add(monthly_entry)

        new_entry = WoodCount(section=section, count=0, date=today, time=current_time)

        if action == 'increment':
            monthly_entry.count += 1
            new_entry.count = 1
            if section == "Body" and inventory.black_mdf > 0:
                inventory.black_mdf -= 1
            elif section in ["Pod Sides", "Bases"] and inventory.plain_mdf > 0:
                inventory.plain_mdf -= 1

        elif action == 'decrement':
            if monthly_entry.count > 0:
                monthly_entry.count -= 1
                new_entry.count = -1
                if section == "Body":
                    inventory.black_mdf += 1
                elif section in ["Pod Sides", "Bases"]:
                    inventory.plain_mdf += 1

        elif action == 'bulk_increment':
            bulk_amount = int(request.form.get('bulk_amount', 0))
            if bulk_amount > 0:
                monthly_entry.count += bulk_amount
                new_entry.count = bulk_amount
                if section == "Body" and inventory.black_mdf >= bulk_amount:
                    inventory.black_mdf -= bulk_amount
                elif section in ["Pod Sides", "Bases"] and inventory.plain_mdf >= bulk_amount:
                    inventory.plain_mdf -= bulk_amount
                else:
                    flash("Insufficient inventory for bulk operation.", "error")
                    return redirect(url_for('counting_wood', month=selected_month))
            else:
                flash("Please enter a valid bulk amount.", "error")
                return redirect(url_for('counting_wood', month=selected_month))

        db.session.add(new_entry)
        db.session.commit()
        return redirect(url_for('counting_wood', month=selected_month))

    sections = ['Body', 'Pod Sides', 'Bases']
    counts = {
        section: WoodCount.query.filter(
            WoodCount.section == section,
            WoodCount.date >= month_start_date,
            WoodCount.date <= month_end_date
        ).first().count if WoodCount.query.filter(
            WoodCount.section == section,
            WoodCount.date >= month_start_date,
            WoodCount.date <= month_end_date
        ).first() else 0
        for section in sections
    }

    start_of_week = today - timedelta(days=today.weekday())
    weekly_summary = {day: 0 for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']}
    daily_wood_data = WoodCount.query.filter(
        WoodCount.date >= start_of_week,
        WoodCount.date <= today
    ).all()

    for entry in daily_wood_data:
        weekday = entry.date.strftime("%A")
        weekly_summary[weekday] += entry.count

    daily_wood_data = WoodCount.query.filter(
        WoodCount.date == today
    ).all()

    return render_template(
        'counting_wood.html',
        inventory=inventory,
        available_months=available_months,
        selected_month=selected_month,
        counts=counts,
        daily_wood_data=daily_wood_data,
        weekly_summary=weekly_summary
    )

@app.route('/counting_cushions', methods=['GET', 'POST'])
def counting_cushions():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    today = datetime.utcnow().date()

    if request.method == 'POST':
        if 'reset' in request.form:
            db.session.query(CushionCount).filter(CushionCount.date == today).delete()
            db.session.commit()
            flash("All counts reset successfully!", "success")
            return redirect(url_for('counting_cushions'))

        cushion_type = request.form.get('cushion_type')
        if cushion_type:
            new_cushion_count = CushionCount(cushion_type=cushion_type)
            db.session.add(new_cushion_count)
            db.session.commit()
            flash(f"Cushion {cushion_type} count incremented!", "success")
        else:
            flash("Error: Cushion type not specified.", "error")

        return redirect(url_for('counting_cushions'))

    daily_counts = db.session.query(
        CushionCount.cushion_type,
        func.count(CushionCount.id).label('total')
    ).filter(CushionCount.date == today).group_by(CushionCount.cushion_type).all()

    weekly_counts = db.session.query(
        func.strftime('%Y', CushionCount.date).label('year'),
        func.strftime('%W', CushionCount.date).label('week_number'),
        CushionCount.cushion_type,
        func.count(CushionCount.id).label('total')
    ).group_by('year', 'week_number', 'cushion_type').order_by('year', 'week_number').all()

    grouped_weekly_counts = {}
    for year, week_number, cushion_type, total in weekly_counts:
        key = f"Week {week_number}, {year}"
        if key not in grouped_weekly_counts:
            grouped_weekly_counts[key] = {}
        grouped_weekly_counts[key][cushion_type] = total

    avg_times = {}
    for c_type in ['1', '2', '3', '4', '5', '6']:
        times = db.session.query(CushionCount.time).filter(
            CushionCount.cushion_type == c_type,
            CushionCount.date == today
        ).order_by(CushionCount.time).all()

        if len(times) > 1:
            total_time_diff = sum(
                (datetime.combine(today, times[i][0]) - datetime.combine(today, times[i - 1][0])).total_seconds()
                for i in range(1, len(times))
            )
            avg_time_diff_seconds = total_time_diff / (len(times) - 1)
            avg_hours, remainder = divmod(int(avg_time_diff_seconds), 3600)
            avg_minutes, avg_seconds = divmod(remainder, 60)
            avg_times[c_type] = f"{avg_hours:02}:{avg_minutes:02}:{avg_seconds:02}"
        else:
            avg_times[c_type] = "N/A"

    return render_template(
        'counting_cushions.html',
        daily_counts=daily_counts,
        grouped_weekly_counts=grouped_weekly_counts,
        avg_times=avg_times
    )

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

@app.route('/bodies', methods=['GET', 'POST'])
def bodies():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    issues = [issue.description for issue in Issue.query.all()]

    unconverted_pods = CompletedPods.query.filter(
        ~CompletedPods.serial_number.in_(
            db.session.query(CompletedTable.serial_number)
        )
    ).all()

    if request.method == 'POST':
        worker = session['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        serial_number = request.form['serial_number']
        issue = request.form['issue']
        lunch = request.form['lunch']

        # Deduct parts logic remains as in original code
        # ...

        new_table = CompletedTable(
            worker=worker,
            start_time=start_time,
            finish_time=finish_time,
            serial_number=serial_number,
            issue=issue,
            lunch=lunch,
            date=date.today()
        )

        try:
            db.session.add(new_table)
            db.session.commit()
            flash("Body entry added successfully and inventory updated!", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Error: Serial number already exists. Please use a unique serial number.", "error")
            return redirect(url_for('bodies'))

        return redirect(url_for('bodies'))

    today = date.today()
    completed_tables = CompletedTable.query.filter_by(date=today).all()
    last_entry = CompletedTable.query.order_by(CompletedTable.id.desc()).first()
    current_time = last_entry.finish_time if last_entry else datetime.now().strftime("%H:%M")

    current_month_bodies_count = (
        db.session.query(func.count(CompletedTable.id))
        .filter(
            extract('year', CompletedTable.date) == today.year,
            extract('month', CompletedTable.date) == today.month
        )
        .scalar()
    )

    daily_history = (
        db.session.query(
            CompletedTable.date,
            func.count(CompletedTable.id).label('count'),
            func.group_concat(CompletedTable.serial_number, ', ').label('serial_numbers')
        )
        .filter(
            extract('year', CompletedTable.date) == today.year,
            extract('month', CompletedTable.date) == today.month
        )
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
        .order_by('year', 'month')
        .all()
    )

    monthly_totals_formatted = []
    for row in monthly_totals:
        year = int(row.year)
        month = int(row.month)
        total_bodies = row.total

        last_day = today.day if year == today.year and month == today.month else monthrange(year, month)[1]
        work_days = sum(1 for day_i in range(1, last_day + 1) if date(year, month, day_i).weekday() < 5)
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_table = cumulative_working_hours / total_bodies if total_bodies > 0 else None

        if avg_hours_per_table is not None:
            hours = int(avg_hours_per_table)
            minutes = int((avg_hours_per_table - hours) * 60)
            seconds = int((((avg_hours_per_table - hours) * 60) - minutes) * 60)
            avg_hours_per_table_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            avg_hours_per_table_formatted = "N/A"

        monthly_totals_formatted.append({
            "month": date(year=year, month=month, day=1).strftime("%B %Y"),
            "count": total_bodies,
            "average_hours_per_table": avg_hours_per_table_formatted
        })

    return render_template(
        'bodies.html',
        issues=issues,
        current_time=current_time,
        completed_tables=completed_tables,
        current_month_bodies_count=current_month_bodies_count,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted,
        unconverted_pods=unconverted_pods
    )

@app.route('/top_rails', methods=['GET', 'POST'])
def top_rails():
    """View for creating or viewing top rails and deducting inventory."""
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # Collect existing issues for the form dropdown
    issues = [issue.description for issue in Issue.query.all()]

    if request.method == 'POST':
        worker = session['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        serial_number = request.form['serial_number']
        issue = request.form['issue']
        lunch = request.form['lunch']

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

            # Check if we have enough total stock across all entries for this part
            if total_stock < quantity_needed:
                flash(
                    f"Not enough inventory for {part_name} to complete the top rail! "
                    f"(Available: {total_stock})", 
                    "error"
                )
                return redirect(url_for('top_rails'))

            # Deduct the required quantity from one or more rows
            remaining_to_deduct = quantity_needed
            for entry in part_entries:
                if remaining_to_deduct <= 0:
                    break  # We have deducted everything we need

                if entry.count >= remaining_to_deduct:
                    entry.count -= remaining_to_deduct
                    remaining_to_deduct = 0
                else:
                    remaining_to_deduct -= entry.count
                    entry.count = 0

        # Only commit *after* all parts have been successfully deducted
        # Create the new top rail entry
        new_top_rail = TopRail(
            worker=worker,
            start_time=start_time,
            finish_time=finish_time,
            serial_number=serial_number,
            lunch=lunch,
            issue=issue,
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

        return redirect(url_for('top_rails'))

    # ---- GET request logic below ----
    today = date.today()
    completed_top_rails = TopRail.query.filter_by(date=today).all()

    last_entry = TopRail.query.order_by(TopRail.id.desc()).first()
    if last_entry:
        try:
            current_time = datetime.strptime(last_entry.finish_time, "%H:%M:%S").strftime("%H:%M")
        except ValueError:
            current_time = last_entry.finish_time
    else:
        current_time = datetime.now().strftime("%H:%M")

    top_rails_this_month = (
        db.session.query(func.count(TopRail.id))
        .filter(
            extract('year', TopRail.date) == today.year,
            extract('month', TopRail.date) == today.month
        )
        .scalar()
    )

    daily_history = (
        db.session.query(
            TopRail.date,
            func.count(TopRail.id).label('count'),
            func.group_concat(TopRail.serial_number, ', ').label('serial_numbers')
        )
        .filter(
            extract('year', TopRail.date) == today.year,
            extract('month', TopRail.date) == today.month
        )
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
        .order_by('year', 'month')
        .all()
    )

    monthly_totals_formatted = []
    for row in monthly_totals:
        year = int(row.year)
        month = int(row.month)
        total_top_rails = row.total

        last_day = (
            today.day if (year == today.year and month == today.month)
            else monthrange(year, month)[1]
        )
        work_days = sum(
            1
            for day_num in range(1, last_day + 1)
            if date(year, month, day_num).weekday() < 5
        )
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_top_rail = (
            cumulative_working_hours / total_top_rails if total_top_rails > 0 else None
        )

        if avg_hours_per_top_rail is not None:
            hours = int(avg_hours_per_top_rail)
            minutes = int((avg_hours_per_top_rail - hours) * 60)
            seconds = int((((avg_hours_per_top_rail - hours) * 60) - minutes) * 60)
            avg_hours_per_top_rail_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            avg_hours_per_top_rail_formatted = "N/A"

        monthly_totals_formatted.append({
            "month": date(year=year, month=month, day=1).strftime("%B %Y"),
            "count": total_top_rails,
            "average_hours_per_top_rail": avg_hours_per_top_rail_formatted
        })

    last_top_rail = TopRail.query.order_by(TopRail.id.desc()).first()
    if last_top_rail:
        try:
            next_serial_number = str(int(last_top_rail.serial_number) + 1)
        except ValueError:
            next_serial_number = "1000"
    else:
        next_serial_number = "1000"

    return render_template(
        'top_rails.html',
        issues=issues,
        current_time=current_time,
        completed_tables=completed_top_rails,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted,
        top_rails_this_month=top_rails_this_month,
        next_serial_number=next_serial_number
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
    """Manage a 12-month schedule of tables in various colors & sizes, 
       displaying 'December 2024' etc. without using Jinja strftime."""
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    def get_next_12_months():
        """
        Return a list of dictionaries, each containing:
          {
            'year': 2024,
            'month': 12,
            'display_str': 'December 2024'
          }
        so we can show this in the template without calling Jinja's strftime.
        """
        months_list = []
        today_date = date.today()
        start_year = today_date.year
        start_month = today_date.month

        for i in range(12):
            y = start_year + (start_month - 1 + i) // 12
            m = (start_month - 1 + i) % 12 + 1
            # Create a temporary date to format nicely (e.g., "December 2024")
            tmp_date = date(y, m, 1)
            display_str = tmp_date.strftime("%B %Y")  # e.g. "December 2024"

            months_list.append({
                'year': y,
                'month': m,
                'display_str': display_str
            })
        return months_list

    next_12_months = get_next_12_months()

    # Handle POST => save or update production data
    if request.method == 'POST':
        for i in range(len(next_12_months)):
            yr = next_12_months[i]['year']
            mo = next_12_months[i]['month']

            # For each color + size, read the form keys: black_7ft_i, black_6ft_i, etc.
            black_7ft_str = request.form.get(f"black_7ft_{i}", "0")
            black_6ft_str = request.form.get(f"black_6ft_{i}", "0")

            grey_7ft_str = request.form.get(f"grey_7ft_{i}", "0")
            grey_6ft_str = request.form.get(f"grey_6ft_{i}", "0")

            oak_7ft_str = request.form.get(f"oak_7ft_{i}", "0")
            oak_6ft_str = request.form.get(f"oak_6ft_{i}", "0")

            grey_oak_7ft_str = request.form.get(f"grey_oak_7ft_{i}", "0")
            grey_oak_6ft_str = request.form.get(f"grey_oak_6ft_{i}", "0")

            concrete_7ft_str = request.form.get(f"concrete_7ft_{i}", "0")
            concrete_6ft_str = request.form.get(f"concrete_6ft_{i}", "0")

            try:
                black_7ft = int(black_7ft_str)
                black_6ft = int(black_6ft_str)

                grey_7ft = int(grey_7ft_str)
                grey_6ft = int(grey_6ft_str)

                oak_7ft = int(oak_7ft_str)
                oak_6ft = int(oak_6ft_str)

                grey_oak_7ft = int(grey_oak_7ft_str)
                grey_oak_6ft = int(grey_oak_6ft_str)

                concrete_7ft = int(concrete_7ft_str)
                concrete_6ft = int(concrete_6ft_str)
            except ValueError:
                flash(f"Invalid number for {mo}/{yr}. Please use whole numbers only.", "error")
                return redirect(url_for('production_schedule'))

            # Look up or create a schedule row for (yr, mo)
            schedule = ProductionSchedule.query.filter_by(year=yr, month=mo).first()
            if not schedule:
                schedule = ProductionSchedule(year=yr, month=mo)
                db.session.add(schedule)

            # Update columns
            schedule.black_7ft = black_7ft
            schedule.black_6ft = black_6ft
            schedule.grey_7ft = grey_7ft
            schedule.grey_6ft = grey_6ft
            schedule.oak_7ft = oak_7ft
            schedule.oak_6ft = oak_6ft
            schedule.grey_oak_7ft = grey_oak_7ft
            schedule.grey_oak_6ft = grey_oak_6ft
            schedule.concrete_7ft = concrete_7ft
            schedule.concrete_6ft = concrete_6ft

        # Commit once after processing all months
        try:
            db.session.commit()
            flash("Production schedule updated successfully!", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Failed to update schedule (Integrity Error).", "error")

        return redirect(url_for('production_schedule'))

    # Handle GET => load existing data
    schedules = ProductionSchedule.query.all()
    schedules_map = {}
    for sched in schedules:
        schedules_map[(sched.year, sched.month)] = sched

    return render_template(
        'production_schedule.html',
        next_12_months=next_12_months,  # This has year, month, and display_str
        schedules_map=schedules_map
    )







if __name__ == '__main__':
    app.run(debug=True)
