from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, date
from collections import defaultdict  # Ensure defaultdict is imported
import os

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

# Register the filter explicitly
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
    section = db.Column(db.String(50), nullable=False)  # E.g., 'Body', 'Pod Sides', or 'Bases'
    count = db.Column(db.Integer, default=0, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow, nullable=False)  # Tracks the day
    time = db.Column(db.Time, default=datetime.utcnow().time, nullable=False)  # Tracks the time

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
    issue = db.Column(db.String(100))  # Add this line to include the 'issue' field
    lunch = db.Column(db.String(3), default='No')

class CushionCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cushion_type = db.Column(db.String(10), nullable=False)  # Types: '1', '2', ..., '6'
    count = db.Column(db.Integer, default=1, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow, nullable=False)
    time = db.Column(db.Time, default=datetime.utcnow().time, nullable=False)

    def __init__(self, cushion_type):
        self.cushion_type = cushion_type
        self.count = 1
        self.date = datetime.utcnow().date()
        self.time = datetime.utcnow().time()

class HardwarePart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    initial_count = db.Column(db.Integer, default=0)



# Home and Bodies Routes
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/bodies', methods=['GET', 'POST'])
def bodies():
    # Fetch workers and issues from the database
    workers = [worker.name for worker in Worker.query.all()]
    issues = [issue.description for issue in Issue.query.all()]

    if request.method == 'POST':
        worker = request.form['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        serial_number = request.form['serial_number']
        issue = request.form['issue']
        lunch = request.form['lunch']

        # Deduct inventory for each part needed to complete the body
        parts_to_deduct = {
            "Large Ramp": 1,
            "Paddle": 1,
            "Laminate": 4,
            "Spring Mount": 1,
            "Spring Holder": 1,
            "Small Ramp": 1,
            "Cue Ball Separator": 1,
            "Bushing": 2
        }

        for part_name, quantity_needed in parts_to_deduct.items():
            part_entry = PrintedPartsCount.query.filter_by(part_name=part_name).order_by(PrintedPartsCount.date.desc()).first()
            if part_entry and part_entry.count >= quantity_needed:
                part_entry.count -= quantity_needed
            else:
                flash(f"Not enough inventory for {part_name} to complete the body!", "error")
                return redirect(url_for('bodies'))

        db.session.commit()

        # Create a new entry for CompletedTable
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

    # Daily History Calculation - Filtered by current month
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

    # Monthly Totals Calculation
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

        # Calculate workdays up to today in the current month
        last_day = today.day if year == today.year and month == today.month else monthrange(year, month)[1]
        work_days = sum(1 for day in range(1, last_day + 1) if date(year, month, day).weekday() < 5)

        # Calculate cumulative working hours and average hours per table
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_table = cumulative_working_hours / total_bodies if total_bodies > 0 else None

        # Convert decimal hours to HH:MM:SS format
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
        workers=workers,
        issues=issues,
        current_time=current_time,
        completed_tables=completed_tables,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted
    )


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    # Add new worker
    if request.method == 'POST' and 'new_worker' in request.form:
        new_worker = request.form['new_worker']
        if new_worker and not Worker.query.filter_by(name=new_worker).first():
            worker = Worker(name=new_worker)
            db.session.add(worker)
            db.session.commit()
            flash(f"Worker '{new_worker}' added successfully!", "success")
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

    # Add new hardware part
    if request.method == 'POST' and 'new_hardware_part' in request.form:
        new_part_name = request.form['new_hardware_part'].strip()
        initial_count = int(request.form['initial_hardware_count'])
        
        # Check if the part already exists
        if HardwarePart.query.filter_by(name=new_part_name).first():
            flash("Hardware part already exists.", "error")
        else:
            new_part = HardwarePart(name=new_part_name, initial_count=initial_count)
            db.session.add(new_part)
            db.session.commit()
            flash(f"Hardware part '{new_part_name}' added successfully!", "success")

    # Retrieve or initialize MDF inventory
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    # Fetch workers, issues, and inventory for other sections
    workers = Worker.query.all()
    issues = Issue.query.all()
    
    # Fetch hardware parts to pass to template
    hardware_parts = HardwarePart.query.all()

    # Fetch raw data for CompletedPods, TopRail, and CompletedTable
    pods = CompletedPods.query.all()
    top_rails = TopRail.query.all()
    bodies = CompletedTable.query.all()

    # Check for raw data management form submission
    if request.method == 'POST' and 'table' in request.form:
        table = request.form.get('table')
        entry_id = request.form.get('id')

        # Identify which table to manage based on the form submission
        model = None
        if table == 'pods':
            model = CompletedPods
        elif table == 'top rails':
            model = TopRail
        elif table == 'bodies':
            model = CompletedTable

        # Perform update or delete action based on form inputs
        if model:
            entry = model.query.get(entry_id)
            if entry:
                if 'update' in request.form:
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

    # Render template with all data
    return render_template(
        'admin.html',
        workers=workers,
        issues=issues,
        inventory=inventory,
        pods=pods,
        top_rails=top_rails,
        bodies=bodies,
        hardware_parts=hardware_parts  # Pass hardware parts to the template
    )


@app.route('/top_rails', methods=['GET', 'POST'])
def top_rails():
    # Fetch workers and issues from the database
    workers = [worker.name for worker in Worker.query.all()]
    issues = [issue.description for issue in Issue.query.all()]

    if request.method == 'POST':
        worker = request.form['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        serial_number = request.form['serial_number']
        issue = request.form['issue']
        lunch = request.form['lunch']

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
            flash("Top rail entry added successfully!", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Error: Serial number already exists. Please use a unique serial number.", "error")
            return redirect(url_for('top_rails'))

        return redirect(url_for('top_rails'))

    # Fetch only today's completed top rails
    today = date.today()
    completed_top_rails = TopRail.query.filter_by(date=today).all()

    # Set current_time based on last entry's finish time or default to current time
    last_entry = TopRail.query.order_by(TopRail.id.desc()).first()
    current_time = last_entry.finish_time if last_entry else datetime.now().strftime("%H:%M")

    # Daily History Calculation - Filtered by current month
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

    # Monthly Totals Calculation
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

        # Calculate workdays up to today in the current month
        last_day = today.day if year == today.year and month == today.month else monthrange(year, month)[1]
        work_days = sum(1 for day in range(1, last_day + 1) if date(year, month, day).weekday() < 5)

        # Calculate cumulative working hours and average hours per top rail
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_top_rail = cumulative_working_hours / total_top_rails if total_top_rails > 0 else None

        # Convert decimal hours to HH:MM:SS format
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

    return render_template(
        'top_rails.html',
        workers=workers,
        issues=issues,
        current_time=current_time,
        completed_tables=completed_top_rails,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted
    )


from datetime import datetime, timedelta, date
from flask import flash, redirect, render_template, request, url_for

from datetime import datetime, timedelta, date


@app.route('/dashboard')
def dashboard():
    # Get today's date and timeframes
    today = datetime.utcnow().date()
    start_of_week = today - timedelta(days=today.weekday())  # Monday as the start of the week
    start_of_month = today.replace(day=1)
    start_of_year = today.replace(month=1, day=1)

    # Helper function to get count by date range
    def get_count(model, start_date=None):
        query = model.query
        if start_date:
            query = query.filter(model.date >= start_date)
        return query.count()

    # Top Rails counts
    top_rails_today = get_count(TopRail, today)
    top_rails_week = get_count(TopRail, start_of_week)
    top_rails_month = get_count(TopRail, start_of_month)
    top_rails_year = get_count(TopRail, start_of_year)

    # Bodies counts
    bodies_today = get_count(CompletedTable, today)
    bodies_week = get_count(CompletedTable, start_of_week)
    bodies_month = get_count(CompletedTable, start_of_month)
    bodies_year = get_count(CompletedTable, start_of_year)

    # Pods counts
    pods_today = get_count(CompletedPods, today)
    pods_week = get_count(CompletedPods, start_of_week)
    pods_month = get_count(CompletedPods, start_of_month)
    pods_year = get_count(CompletedPods, start_of_year)

    # Wood counts for each section
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
    # Retrieve or initialize inventory
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    if request.method == 'POST':
        try:
            # Retrieve the values to add from the form
            additional_plain_mdf = int(request.form['additional_plain_mdf'])
            additional_black_mdf = int(request.form['additional_black_mdf'])

            # Update inventory values
            inventory.plain_mdf += additional_plain_mdf
            inventory.black_mdf += additional_black_mdf

            # Save changes
            db.session.commit()
            flash("MDF inventory updated successfully!", "success")
        except ValueError:
            flash("Please enter valid numbers for MDF quantities.", "error")

    return render_template('manage_mdf_inventory.html', inventory=inventory)

@app.route('/counting_3d_printing_parts', methods=['GET', 'POST'])
def counting_3d_printing_parts():
    today = datetime.utcnow().date()

    if request.method == 'POST':
        part = request.form['part']

        if 'reject' in request.form:  # Check if rejection request
            reject_amount = int(request.form['reject_amount'])
            # Fetch the most recent entry for the selected part
            current_count = PrintedPartsCount.query.filter_by(part_name=part).order_by(PrintedPartsCount.date.desc()).first()

            if current_count and current_count.count >= reject_amount:
                current_count.count -= reject_amount
                flash(f"Rejected {reject_amount} of {part} from inventory.", "success")
                db.session.commit()
            else:
                flash(f"Not enough inventory to reject {reject_amount} of {part}.", "error")
        else:
            # Handle normal increment
            increment_amount = int(request.form['increment_amount'])
            current_count = PrintedPartsCount.query.filter_by(part_name=part).order_by(PrintedPartsCount.date.desc()).first()

            if current_count:
                # Update the existing count
                current_count.count += increment_amount
                current_count.date = today  # Update date to today
                flash(f"Incremented {part} count by {increment_amount}!", "success")
            else:
                # Add as a new entry if no existing count found
                new_count = PrintedPartsCount(
                    part_name=part,
                    count=increment_amount,
                    date=today,
                    time=datetime.utcnow().time()
                )
                db.session.add(new_count)
                flash(f"Added {increment_amount} to {part} as a new entry!", "success")

            db.session.commit()

        return redirect(url_for('counting_3d_printing_parts'))

    # Retrieve the most recent count for each part to display
    parts = ["Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder", "Small Ramp", "Cue Ball Separator", "Bushing"]
    parts_counts = {part: PrintedPartsCount.query.filter_by(part_name=part).order_by(PrintedPartsCount.date.desc()).first() for part in parts}
    parts_counts = {part: count.count if count else 0 for part, count in parts_counts.items()}

    return render_template('counting_3d_printing_parts.html', parts_counts=parts_counts)
@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    # List of 3D printed parts
    parts = ["Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder", "Small Ramp", "Cue Ball Separator", "Bushing"]

    # Calculate current stock for each 3D printed part
    inventory_counts = {}
    for part in parts:
        latest_entry = db.session.query(PrintedPartsCount.count).filter_by(part_name=part).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        inventory_counts[part] = latest_entry[0] if latest_entry else 0

    # Calculate total counts for each wooden part section
    total_body_cut = db.session.query(WoodCount.count).filter_by(section="Body").order_by(WoodCount.date.desc(), WoodCount.time.desc()).first()
    total_pod_sides_cut = db.session.query(WoodCount.count).filter_by(section="Pod Sides").order_by(WoodCount.date.desc(), WoodCount.time.desc()).first()
    total_bases_cut = db.session.query(WoodCount.count).filter_by(section="Bases").order_by(WoodCount.date.desc(), WoodCount.time.desc()).first()

    wooden_counts = {
        'body': total_body_cut[0] if total_body_cut else 0,
        'pod_sides': total_pod_sides_cut[0] if total_pod_sides_cut else 0,
        'bases': total_bases_cut[0] if total_bases_cut else 0
    }

    # Calculate monthly production requirements for 3D printed parts
    today = datetime.utcnow().date()
    bodies_built_this_month = db.session.query(func.count(CompletedTable.id)).filter(
        extract('year', CompletedTable.date) == today.year,
        extract('month', CompletedTable.date) == today.month
    ).scalar()

    # Define 3D printed part usage per body and calculate monthly requirements
    parts_usage_per_body = {
        "Large Ramp": 1, "Paddle": 1, "Laminate": 4, "Spring Mount": 1,
        "Spring Holder": 1, "Small Ramp": 1, "Cue Ball Separator": 1, "Bushing": 2
    }
    parts_used_this_month = {part: bodies_built_this_month * usage for part, usage in parts_usage_per_body.items()}
    target_tables_per_month = 60
    parts_status = {}
    for part, usage in parts_usage_per_body.items():
        required_total = target_tables_per_month * usage
        available_total = inventory_counts.get(part, 0) + parts_used_this_month.get(part, 0)
        difference = available_total - required_total
        parts_status[part] = f"{difference} extras" if difference >= 0 else f"{abs(difference)} left to make"

    # Table Parts Section
    table_parts = {
        "Table legs": 4, "Ball Gullies 1": 1, "Ball Gullies 2": 1, "Ball Gullies 3": 1,
        "Ball Gullies 4": 1, "Ball Gullies 5 (Untouched)": 2, "Feet": 4, "Triangle trim": 1,
        "White ball return trim": 1, "Color ball trim": 1, "Ball window trim": 1,
        "Aluminum corner": 4, "Chrome corner": 4, "Top rail trim short length": 1,
        "Top rail trim long length": 1, "Ramp 170mm": 1, "Ramp 158mm": 1, "Ramp 918mm": 1,
        "Chrome handles": 1, "Center pockets": 2, "Corner pockets": 4
    }

    # Retrieve or calculate counts for each item in Table Parts
    table_parts_counts = {part: 0 for part in table_parts}
    for part in table_parts:
        latest_entry = db.session.query(PrintedPartsCount.count).filter_by(part_name=part).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        table_parts_counts[part] = latest_entry[0] if latest_entry else 0

    # Calculate how many tables can be built from table parts
    tables_possible_per_part = {part: table_parts_counts[part] // req_per_table for part, req_per_table in table_parts.items()}
    max_tables_possible = min(tables_possible_per_part.values())

    # Hardware Parts Section
    hardware_parts = [
        "M10x13mm Tee Nut", "M10 x 40 Socket Cap Screw", "4.2 x 16 No2 Self Tapping Screw",
        "4.0 x 50mm Wood Screw", "4.0 x 25mm Wood Screw", "M5 x 18 x 1.25 Penny Mudguard Washer",
        "M10 Washer", "M5 x 20 Socket Cap Screw", "4.8x32mm Self Tapping Screw", "4.8x16mm Self Tapping Screw"
    ]

    # Initialize or retrieve counts for each hardware part
    hardware_counts = {part: 0 for part in hardware_parts}
    for part in hardware_parts:
        latest_entry = db.session.query(PrintedPartsCount.count).filter_by(part_name=part).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        hardware_counts[part] = latest_entry[0] if latest_entry else 0

    # Handle table parts bulk input form
    if request.method == 'POST' and 'table_part' in request.form:
        part = request.form['table_part']
        action = request.form['action']
        amount = int(request.form['amount'])
        if part in table_parts_counts:
            current_count = table_parts_counts[part]
            if action == 'increment':
                new_count = current_count + amount
            elif action == 'decrement' and current_count >= amount:
                new_count = current_count - amount
            else:
                flash(f"Cannot decrement {part} by {amount} as it would result in a negative count.", "error")
                return redirect(url_for('inventory'))

            # Update count in the database
            new_entry = PrintedPartsCount(part_name=part, count=new_count, date=today, time=datetime.utcnow().time())
            db.session.add(new_entry)
            db.session.commit()
            table_parts_counts[part] = new_count
            flash(f"{part} updated successfully!", "success")

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
    # List of "Table Parts" items
    table_parts = [
        "Table legs", "Ball Gullies 1", "Ball Gullies 2", "Ball Gullies 3", 
        "Ball Gullies 4", "Ball Gullies 5 (Untouched)", "Feet", "Triangle trim", 
        "White ball return trim", "Color ball trim", "Ball window trim", 
        "Aluminum corner", "Chrome corner", "Top rail trim short length", 
        "Top rail trim long length", "Ramp 170mm", "Ramp 158mm", "Ramp 918mm", 
        "Chrome handles", "Center pockets", "Corner pockets"
    ]

    # Retrieve or initialize the count for each part
    table_parts_counts = {part: 0 for part in table_parts}
    for part in table_parts:
        latest_entry = db.session.query(PrintedPartsCount.count).filter_by(part_name=part).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        table_parts_counts[part] = latest_entry[0] if latest_entry else 0

    if request.method == 'POST':
        part = request.form['table_part']
        action = request.form['action']
        amount = int(request.form['amount']) if 'amount' in request.form else 1

        if part in table_parts_counts:
            current_count = table_parts_counts[part]
            if action == 'increment':
                new_count = current_count + 1
            elif action == 'decrement' and current_count > 0:
                new_count = current_count - 1
            elif action == 'bulk' and amount > 0:
                new_count = current_count + amount
            elif action == 'bulk' and amount < 0 and current_count >= abs(amount):
                new_count = current_count + amount
            else:
                flash("Invalid operation or insufficient stock.", "error")
                return redirect(url_for('counting_chinese_parts'))

            # Update database with the new count
            new_entry = PrintedPartsCount(part_name=part, count=new_count, date=datetime.utcnow().date(), time=datetime.utcnow().time())
            db.session.add(new_entry)
            db.session.commit()

            flash(f"{part} updated successfully! New count: {new_count}", "success")
            table_parts_counts[part] = new_count

    return render_template('counting_chinese_parts.html', table_parts=table_parts, table_parts_counts=table_parts_counts)

@app.route('/counting_hardware', methods=['GET', 'POST'])
def counting_hardware():
    # Fetch all hardware parts from the database instead of using a static list
    hardware_parts = HardwarePart.query.all()

    # Initialize or retrieve the count for each part
    hardware_counts = {part.name: part.initial_count for part in hardware_parts}
    for part in hardware_parts:
        latest_entry = db.session.query(PrintedPartsCount.count).filter_by(part_name=part.name).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        hardware_counts[part.name] = latest_entry[0] if latest_entry else part.initial_count

    if request.method == 'POST':
        part = request.form['hardware_part']
        action = request.form['action']
        amount = int(request.form['amount']) if 'amount' in request.form else 1

        if part in hardware_counts:
            current_count = hardware_counts[part]
            if action == 'increment':
                new_count = current_count + 1
            elif action == 'decrement' and current_count > 0:
                new_count = current_count - 1
            elif action == 'bulk' and amount > 0:
                new_count = current_count + amount
            elif action == 'bulk' and amount < 0 and current_count >= abs(amount):
                new_count = current_count + amount
            else:
                flash("Invalid operation or insufficient stock.", "error")
                return redirect(url_for('counting_hardware'))

            # Update database with the new count
            new_entry = PrintedPartsCount(part_name=part, count=new_count, date=datetime.utcnow().date(), time=datetime.utcnow().time())
            db.session.add(new_entry)
            db.session.commit()

            flash(f"{part} updated successfully! New count: {new_count}", "success")
            hardware_counts[part] = new_count

    return render_template('counting_hardware.html', hardware_parts=hardware_parts, hardware_counts=hardware_counts)






from datetime import datetime, date
from calendar import monthrange
from sqlalchemy import func, extract
from flask import flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

@app.route('/pods', methods=['GET', 'POST'])
def pods():
    # Fetch workers and issues from the database
    workers = [worker.name for worker in Worker.query.all()]
    issues = [issue.description for issue in Issue.query.all()]

    if request.method == 'POST':
        worker = request.form['worker']
        serial_number = request.form['serial_number']
        issue = request.form['issue']
        lunch = request.form['lunch']

        # Handle start_time and finish_time parsing
        try:
            start_time = datetime.strptime(request.form['start_time'], "%H:%M").time()
        except ValueError:
            start_time = datetime.strptime(request.form['start_time'], "%H:%M:%S").time()

        try:
            finish_time = datetime.strptime(request.form['finish_time'], "%H:%M").time()
        except ValueError:
            finish_time = datetime.strptime(request.form['finish_time'], "%H:%M:%S").time()

        # Create a new entry for CompletedPods
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

    # Fetch only today's CompletedPods entries
    today = date.today()
    completed_pods = CompletedPods.query.filter_by(date=today).all()

    # Set current_time based on last entry's finish time or default to current time
    last_entry = CompletedPods.query.order_by(CompletedPods.id.desc()).first()
    current_time = last_entry.finish_time.strftime("%H:%M") if last_entry else datetime.now().strftime("%H:%M")

    # Daily History Calculation - Filtered by current month
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

    # Monthly Totals Calculation
    monthly_totals = (
        db.session.query(
            extract('year', CompletedPods.date).label('year'),
            extract('month', CompletedPods.date).label('month'),
            func.count(CompletedPods.id).label('total')
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

        # Calculate workdays up to today in the current month
        last_day = today.day if year == today.year and month == today.month else monthrange(year, month)[1]
        work_days = sum(1 for day in range(1, last_day + 1) if date(year, month, day).weekday() < 5)

        # Calculate cumulative working hours and average hours per pod
        cumulative_working_hours = work_days * 7.5
        avg_hours_per_pod = cumulative_working_hours / total_pods if total_pods > 0 else None

        # Convert decimal hours to HH:MM:SS format
        if avg_hours_per_pod is not None:
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

    return render_template(
        'pods.html',
        workers=workers,
        issues=issues,
        current_time=current_time,
        completed_tables=completed_pods,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted
    )


@app.route('/admin/raw_data', methods=['GET', 'POST'])
def manage_raw_data():
    serial_number_query = request.args.get('serial_number')

    # Filter data by serial number if query provided, else fetch all records
    if serial_number_query:
        pods = CompletedPods.query.filter_by(serial_number=serial_number_query).all()
        top_rails = TopRail.query.filter_by(serial_number=serial_number_query).all()
        bodies = CompletedTable.query.filter_by(serial_number=serial_number_query).all()
    else:
        pods = CompletedPods.query.all()
        top_rails = TopRail.query.all()
        bodies = CompletedTable.query.all()

    if request.method == 'POST':
        # Check which table and ID is being edited
        table = request.form.get('table')
        entry_id = request.form.get('id')

        # Retrieve the relevant entry based on the table name
        entry = None
        if table == 'pods':
            entry = CompletedPods.query.get(entry_id)
        elif table == 'top_rails':
            entry = TopRail.query.get(entry_id)
        elif table == 'bodies':
            entry = CompletedTable.query.get(entry_id)

        if entry:
            if 'delete' in request.form:
                # Handle deletion
                db.session.delete(entry)
                db.session.commit()
                flash(f"{table.capitalize()} entry deleted successfully!", "success")
            else:
                # Convert form input to `time` objects for `start_time` and `finish_time` for pods section
                if table == 'pods':
                    try:
                        entry.start_time = datetime.strptime(request.form.get('start_time'), "%H:%M").time()
                        entry.finish_time = datetime.strptime(request.form.get('finish_time'), "%H:%M").time()
                    except ValueError:
                        flash("Invalid time format. Please use HH:MM.", "error")
                        return redirect(url_for('manage_raw_data', serial_number=serial_number_query))
                else:
                    # Store start and finish times as strings for other sections
                    entry.start_time = request.form.get('start_time')
                    entry.finish_time = request.form.get('finish_time')

                # Update other fields
                entry.worker = request.form.get('worker')
                entry.serial_number = request.form.get('serial_number')
                entry.issue = request.form.get('issue')
                entry.lunch = request.form.get('lunch')

                # Update date if provided
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

from flask import current_app

@app.route('/counting_wood', methods=['GET', 'POST'])
def counting_wood():
    # Retrieve MDF inventory data
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    # Generate dates for month selection
    today = datetime.now().date()  # Use local time
    previous_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    current_month = today.replace(day=1)
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)

    # Available months for dropdown
    available_months = [
        (previous_month.strftime("%Y-%m"), previous_month.strftime("%B %Y")),
        (current_month.strftime("%Y-%m"), current_month.strftime("%B %Y")),
        (next_month.strftime("%Y-%m"), next_month.strftime("%B %Y"))
    ]

    # Get selected month or default to current month
    selected_month = request.form.get('month') or request.args.get('month', current_month.strftime("%Y-%m"))
    selected_year, selected_month_num = map(int, selected_month.split('-'))
    month_start_date = date(selected_year, selected_month_num, 1)
    month_end_date = date(selected_year, selected_month_num, monthrange(selected_year, selected_month_num)[1])

    if request.method == 'POST' and 'section' in request.form:
        section = request.form['section']
        action = request.form.get('action', 'increment')
        current_time = datetime.now().time()

        # Fetch or create a WoodCount entry for the selected month and section
        current_count_entry = WoodCount.query.filter(
            WoodCount.section == section,
            WoodCount.date >= month_start_date,
            WoodCount.date <= month_end_date
        ).first()

        if not current_count_entry:
            current_count_entry = WoodCount(section=section, count=0, date=month_start_date, time=current_time)
            db.session.add(current_count_entry)

        # Adjust the count based on action
        if action == 'increment':
            current_count_entry.count += 1

            # MDF Inventory adjustment logic
            if section == "Body" and inventory.black_mdf > 0:
                inventory.black_mdf -= 1
            elif section in ["Pod Sides", "Bases"] and inventory.plain_mdf > 0:
                inventory.plain_mdf -= 1

        elif action == 'decrement' and current_count_entry.count > 0:
            current_count_entry.count -= 1
            if current_count_entry.count == 0:
                db.session.delete(current_count_entry)  # Delete if count reaches zero

        elif action == 'bulk_increment':
            bulk_amount = int(request.form.get('bulk_amount', 0))
            if bulk_amount > 0:
                current_count_entry.count += bulk_amount
            else:
                flash("Please enter a valid bulk amount.", "error")
                return redirect(url_for('counting_wood', month=selected_month))

        db.session.commit()

        # Redirect back to the updated route with the selected month
        return redirect(url_for('counting_wood', month=selected_month))

    # Fetch counts for each section based on the selected month
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

    # Fetch data for the selected month (Daily and Weekly Views)
    daily_wood_data = WoodCount.query.filter(
        WoodCount.date >= month_start_date,
        WoodCount.date <= month_end_date
    ).all()

    # Weekly Summary Calculation
    weekly_summary = defaultdict(int)
    for entry in daily_wood_data:
        weekday = entry.date.strftime("%A")
        weekly_summary[weekday] += entry.count

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
    today = datetime.utcnow().date()

    if request.method == 'POST':
        if 'reset' in request.form:
            # Reset action: delete all entries for today
            db.session.query(CushionCount).filter(CushionCount.date == today).delete()
            db.session.commit()
            flash("All counts reset successfully!", "success")
            return redirect(url_for('counting_cushions'))

        cushion_type = request.form.get('cushion_type')
        
        # Check if cushion_type is valid before adding a new count entry
        if cushion_type:
            new_cushion_count = CushionCount(cushion_type=cushion_type)
            db.session.add(new_cushion_count)
            db.session.commit()
            flash(f"Cushion {cushion_type} count incremented!", "success")
        else:
            flash("Error: Cushion type not specified.", "error")
        
        return redirect(url_for('counting_cushions'))

    # Calculate daily totals for each cushion type
    daily_counts = db.session.query(
        CushionCount.cushion_type,
        func.count(CushionCount.id).label('total')
    ).filter(CushionCount.date == today).group_by(CushionCount.cushion_type).all()

    # Calculate weekly totals for each cushion type
    start_of_week = today - timedelta(days=today.weekday())  # Monday as start of the week
    weekly_counts = db.session.query(
        CushionCount.cushion_type,
        func.count(CushionCount.id).label('total')
    ).filter(
        CushionCount.date >= start_of_week,
        CushionCount.date <= today
    ).group_by(CushionCount.cushion_type).all()

    # Calculate average time between presses for each cushion type
    avg_times = {}
    for cushion_type in ['1', '2', '3', '4', '5', '6']:
        times = db.session.query(CushionCount.time).filter(
            CushionCount.cushion_type == cushion_type,
            CushionCount.date == today
        ).order_by(CushionCount.time).all()

        if len(times) > 1:
            # Calculate time differences between consecutive presses
            total_time_diff = sum(
                (datetime.combine(today, times[i][0]) - datetime.combine(today, times[i - 1][0])).total_seconds()
                for i in range(1, len(times))
            )
            avg_time_diff_seconds = total_time_diff / (len(times) - 1)
            avg_hours, remainder = divmod(int(avg_time_diff_seconds), 3600)
            avg_minutes, avg_seconds = divmod(remainder, 60)
            avg_times[cushion_type] = f"{avg_hours:02}:{avg_minutes:02}:{avg_seconds:02}"
        else:
            avg_times[cushion_type] = "N/A"

    return render_template(
        'counting_cushions.html',
        daily_counts=daily_counts,
        weekly_counts=weekly_counts,
        avg_times=avg_times
    )


@app.route('/predicted_finish', methods=['GET', 'POST'])
def predicted_finish():
    if request.method == 'POST':
        try:
            tables_for_month = int(request.form['tables_for_month'])
            if tables_for_month <= 0:
                flash("Please enter a positive number of tables.", "error")
                return redirect(url_for('predicted_finish'))
        except ValueError:
            flash("Please enter a valid number.", "error")
            return redirect(url_for('predicted_finish'))
        
        # Define workdays (Monday to Friday) and work hours per day
        work_days = [0, 1, 2, 3, 4]  # 0 = Monday, 4 = Friday
        work_hours_per_day = 8
        work_start_hour = 9  # Assume starting work at 9:00 AM
        
        # Fetch current month and year
        today = datetime.utcnow().date()
        current_year = today.year
        current_month = today.month
        last_full_day = today - timedelta(days=1)

        # Helper function to calculate average daily production
        def calculate_average(model):
            first_entry_date = db.session.query(func.min(model.date)).filter(
                func.extract('year', model.date) == current_year,
                func.extract('month', model.date) == current_month
            ).scalar()
            
            if not first_entry_date or first_entry_date >= last_full_day:
                return None  # No data or only partial data for today, so average is undefined

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

        total_pods_needed = tables_for_month
        total_bodies_needed = tables_for_month
        total_top_rails_needed = tables_for_month

        def completed_this_month(model):
            return db.session.query(func.count(model.id)).filter(
                func.extract('year', model.date) == current_year,
                func.extract('month', model.date) == current_month
            ).scalar()

        completed_pods = completed_this_month(CompletedPods)
        completed_bodies = completed_this_month(CompletedTable)
        completed_top_rails = completed_this_month(TopRail)

        remaining_pods = max(total_pods_needed - completed_pods, 0)
        remaining_bodies = max(total_bodies_needed - completed_bodies, 0)
        remaining_top_rails = max(total_top_rails_needed - completed_top_rails, 0)

        def format_date_with_suffix(date_obj):
            day = date_obj.day
            suffix = 'th' if 11 <= day <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
            return date_obj.strftime(f'%B {day}{suffix}')

        def project_finish_date_and_time(avg_per_day, remaining_needed):
            if avg_per_day is None or avg_per_day == 0:
                return "N/A", "N/A"

            # Calculate the number of full days required to meet the target
            full_days_needed = int(remaining_needed // avg_per_day)
            partial_day_fraction = remaining_needed % avg_per_day / avg_per_day

            finish_date = today
            workdays_counted = 0

            # Add full days to reach the projected finish
            while workdays_counted < full_days_needed:
                finish_date += timedelta(days=1)
                if finish_date.weekday() in work_days:
                    workdays_counted += 1

            # If there's a partial day needed, calculate the finish time
            if partial_day_fraction > 0:
                while finish_date.weekday() not in work_days:
                    finish_date += timedelta(days=1)

                hours_needed_on_last_day = partial_day_fraction * work_hours_per_day
                finish_time = (datetime.combine(finish_date, datetime.min.time()) +
                               timedelta(hours=work_start_hour + hours_needed_on_last_day))
                finish_time_formatted = finish_time.strftime('%I:%M %p')
            else:
                # Finish at the end of the last full workday if no partial day is required
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














if __name__ == '__main__':
    app.run(debug=True)
