from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, date

app = Flask(__name__)
app.secret_key = 'your_secret_key'

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////var/www/pooltabletracker.com/pool_table_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


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
    section = db.Column(db.String(50), nullable=False)  # Body, Pod Sides, or Bases
    count = db.Column(db.Integer, default=0, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow, nullable=False)
    time = db.Column(db.Time, default=datetime.utcnow, nullable=False)

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




# Home and Bodies Routes
@app.route('/')
def home():
    return render_template('home.html')

from datetime import datetime, date

@app.route('/bodies', methods=['GET', 'POST'])
def bodies():
    # Fetch workers and issues from the database
    workers = [worker.name for worker in Worker.query.all()]
    issues = [issue.description for issue in Issue.query.all()]

    if request.method == 'POST':
        worker = request.form['worker']

        # Convert start_time and finish_time to "HH:MM" string format
        start_time = datetime.strptime(request.form['start_time'], "%H:%M").strftime("%H:%M")
        finish_time = datetime.strptime(request.form['finish_time'], "%H:%M").strftime("%H:%M")

        serial_number = request.form['serial_number']
        issue = request.form['issue']
        lunch = request.form['lunch']

        # Define the 3D printed parts and quantities to deduct for a completed body
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

        # Check availability and deduct each part
        for part_name, quantity_needed in parts_to_deduct.items():
            part_entry = PrintedPartsCount.query.filter_by(part_name=part_name).order_by(PrintedPartsCount.date.desc()).first()

            if part_entry and part_entry.count >= quantity_needed:
                part_entry.count -= quantity_needed
            else:
                flash(f"Not enough inventory for {part_name} to complete the body!", "error")
                return redirect(url_for('bodies'))

        # Commit inventory changes if all parts are available
        db.session.commit()

        # Create a new entry for the completed body
        new_table = CompletedTable(
            worker=worker,
            start_time=start_time,  # Now a string
            finish_time=finish_time,  # Now a string
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

    # Fetch only today's completed bodies
    today = date.today()
    completed_tables = CompletedTable.query.filter_by(date=today).all()

    # Handle last entry's finish_time as a string
    last_entry = CompletedTable.query.order_by(CompletedTable.id.desc()).first()
    current_time = last_entry.finish_time if last_entry else datetime.now().strftime("%H:%M")

    return render_template('bodies.html', workers=workers, issues=issues, current_time=current_time, completed_tables=completed_tables)

# Admin Area Route
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

    # Retrieve or initialize MDF inventory
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    # Fetch workers, issues, and inventory for other sections
    workers = Worker.query.all()
    issues = Issue.query.all()

    # Fetch raw data for CompletedPods, TopRail, and CompletedTable
    pods = CompletedPods.query.all()
    top_rails = TopRail.query.all()
    bodies = CompletedTable.query.all()

    # Check for raw data management form submission
    if request.method == 'POST':
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
        bodies=bodies
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
            issue=issue
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

    # Fetch only today's completed bodies
    today = date.today()
    completed_top_rails = TopRail.query.filter_by(date=today).all()

    last_entry = TopRail.query.order_by(TopRail.id.desc()).first()
    current_time = last_entry.finish_time if last_entry else datetime.now().strftime("%H:%M")

    return render_template('top_rails.html', workers=workers, issues=issues, current_time=current_time, completed_tables=completed_top_rails)

@app.route('/counting_wood', methods=['GET', 'POST'])
def counting_wood():
    # Retrieve MDF inventory data
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0)
        db.session.add(inventory)
        db.session.commit()

    if request.method == 'POST':
        section = request.form['section']
        action = request.form.get('action', 'increment')  # Default to increment if action is not provided

        # Get the latest entry for the section, regardless of the date
        current_count_entry = WoodCount.query.filter_by(section=section).order_by(WoodCount.id.desc()).first()
        current_count_value = current_count_entry.count if current_count_entry else 0

        if action == 'increment':
            # Increment the count
            new_count = WoodCount(
                section=section,
                count=current_count_value + 1,
                date=datetime.utcnow().date(),
                time=datetime.utcnow().time()
            )

            # Adjust MDF inventory based on the section
            if section == 'Body':
                if inventory.black_mdf > 0:
                    inventory.black_mdf -= 1
                else:
                    flash("Not enough Black MDF available to cut a body!", "error")
                    return redirect(url_for('counting_wood'))
            elif section in ['Pod Sides', 'Bases']:
                if inventory.plain_mdf > 0:
                    inventory.plain_mdf -= 1
                else:
                    flash(f"Not enough Plain MDF available to cut {section.lower()}!", "error")
                    return redirect(url_for('counting_wood'))

        elif action == 'decrement' and current_count_value > 0:
            # Decrement the count, ensuring it doesn't go below zero
            new_count = WoodCount(
                section=section,
                count=current_count_value - 1,
                date=datetime.utcnow().date(),
                time=datetime.utcnow().time()
            )

            # Add back to MDF inventory based on the section
            if section == 'Body':
                inventory.black_mdf += 1
            elif section in ['Pod Sides', 'Bases']:
                inventory.plain_mdf += 1

        else:
            flash(f"No {section} cuts to decrement.", "error")
            return redirect(url_for('counting_wood'))

        # Save the new count and updated inventory to the database
        db.session.add(new_count)
        db.session.commit()
        flash(f"{section} count {'incremented' if action == 'increment' else 'decremented'} successfully!", "success")
        return redirect(url_for('counting_wood'))

    # Fetch the latest counts for each section without resetting daily
    body_count = WoodCount.query.filter_by(section='Body').order_by(WoodCount.id.desc()).first()
    pod_sides_count = WoodCount.query.filter_by(section='Pod Sides').order_by(WoodCount.id.desc()).first()
    bases_count = WoodCount.query.filter_by(section='Bases').order_by(WoodCount.id.desc()).first()

    # Render the template with inventory and continuous counts
    return render_template(
        'counting_wood.html',
        inventory=inventory,
        body_count=body_count.count if body_count else 0,
        pod_sides_count=pod_sides_count.count if pod_sides_count else 0,
        bases_count=bases_count.count if bases_count else 0
    )

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
            # Fetch today's entry for the selected part
            current_count = PrintedPartsCount.query.filter_by(part_name=part, date=today).first()

            if current_count and current_count.count >= reject_amount:
                current_count.count -= reject_amount
                flash(f"Rejected {reject_amount} of {part} from inventory.", "success")
                db.session.commit()
            else:
                flash(f"Not enough inventory to reject {reject_amount} of {part}.", "error")
        else:
            # Handle normal increment
            increment_amount = int(request.form['increment_amount'])
            current_count = PrintedPartsCount.query.filter_by(part_name=part, date=today).first()

            if current_count:
                current_count.count += increment_amount
                flash(f"Incremented {part} count by {increment_amount}!", "success")
            else:
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

    parts = ["Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder", "Small Ramp", "Cue Ball Separator", "Bushing"]
    parts_counts = {part: PrintedPartsCount.query.filter_by(part_name=part, date=today).first() for part in parts}
    parts_counts = {part: count.count if count else 0 for part, count in parts_counts.items()}

    return render_template('counting_3d_printing_parts.html', parts_counts=parts_counts)

@app.route('/inventory')
def inventory():
    # List of parts to track in 3D printed inventory
    parts = ["Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder", "Small Ramp", "Cue Ball Separator", "Bushing"]

    # Calculate the latest stock for each 3D printed part based on the most recent entry
    inventory_counts = {
        part: db.session.query(PrintedPartsCount.count).filter_by(part_name=part).order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()[0] or 0
        for part in parts
    }

    # Retrieve the current total count for each wooden part using the correct model, WoodCount
    total_body_cut = db.session.query(WoodCount.count).filter_by(section="Body").order_by(WoodCount.date.desc(), WoodCount.time.desc()).first()
    total_body_cut = total_body_cut[0] if total_body_cut else 0

    total_pod_sides_cut = db.session.query(WoodCount.count).filter_by(section="Pod Sides").order_by(WoodCount.date.desc(), WoodCount.time.desc()).first()
    total_pod_sides_cut = total_pod_sides_cut[0] if total_pod_sides_cut else 0

    total_bases_cut = db.session.query(WoodCount.count).filter_by(section="Bases").order_by(WoodCount.date.desc(), WoodCount.time.desc()).first()
    total_bases_cut = total_bases_cut[0] if total_bases_cut else 0

    # Dictionary for wooden parts counts
    wooden_counts = {
        'body': total_body_cut,
        'pod_sides': total_pod_sides_cut,
        'bases': total_bases_cut
    }

    return render_template('inventory.html', inventory_counts=inventory_counts, wooden_counts=wooden_counts)



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

        # Handle start_time and finish_time parsing to account for both HH:MM and HH:MM:SS formats
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
            date=date.today()  # Ensure the date is set to today
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

    return render_template('pods.html', workers=workers, issues=issues, current_time=current_time, completed_tables=completed_pods)

from datetime import datetime

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
                # Convert form input to time objects for `start_time` and `finish_time`
                try:
                    entry.start_time = datetime.strptime(request.form.get('start_time'), "%H:%M").time()
                    entry.finish_time = datetime.strptime(request.form.get('finish_time'), "%H:%M").time()
                except ValueError:
                    flash("Invalid time format. Please use HH:MM.", "error")
                    return redirect(url_for('manage_raw_data', serial_number=serial_number_query))

                # Update other fields
                entry.worker = request.form.get('worker')
                entry.serial_number = request.form.get('serial_number')
                entry.issue = request.form.get('issue')
                entry.lunch = request.form.get('lunch')
                db.session.commit()
                flash(f"{table.capitalize()} entry updated successfully!", "success")

        return redirect(url_for('manage_raw_data', serial_number=serial_number_query))

    return render_template('admin_raw_data.html', pods=pods, top_rails=top_rails, bodies=bodies)





if __name__ == '__main__':
    app.run(debug=True)