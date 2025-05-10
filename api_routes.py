from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, date
from sqlalchemy import func, extract, desc # Added desc
# Assuming your models are in flask_app.py or accessible via flask_app.db
# If your models are in the same file as your app (app.py), you'd import them like:
# from . import db, CompletedTable, TopRail, CompletedPods, WoodCount, PrintedPartsCount, ProductionSchedule, MDFInventory, HardwarePart, TableStock
# For this example, I'll assume they are accessible via a 'models' module or similar structure
# For a single file app structure, you might need to adjust imports based on how 'db' and models are defined.
# If app.py has "from flask_app import db, CompletedTable, ...", then this file should too.
# For this example, I'll use placeholder imports. Replace with your actual model imports.

# Placeholder: Replace with your actual db and model imports
# from your_app_module import db, CompletedTable, TopRail, CompletedPods, WoodCount, PrintedPartsCount, ProductionSchedule, MDFInventory, HardwarePart, TableStock
# For the provided app.py structure, it seems models are directly available after db is initialized.
# So, we'll need to import them from the main app instance if this is a separate file.
# If this code is to be integrated directly into app.py, these imports might not be needed at the top here.

# Assuming db and models are imported from the main app file (e.g. app.py)
# This is a common pattern for blueprints.
# In your main app.py, you'd have:
# from .api_routes import api_blueprint # or whatever you name this file
# app.register_blueprint(api_blueprint)

# For the provided full app.py, the models are defined globally after 'db = SQLAlchemy(app)'.
# If this api_routes.py is separate, it needs access to them.
# A common way is to pass 'db' and models or import them from where they are defined.
# For now, I will write it as if 'db' and model classes are in scope.
# You will need to ensure they are correctly imported in your project structure.
# Example: from .models import db, CompletedTable, TopRail, ... if you move models to models.py

# --- Start of API Blueprint ---
# If this code is to be part of your main app.py, you would define 'api' like this:
# api = Blueprint('api', __name__)
# And then use @api.route(...)
# The models like CompletedTable, etc., would already be in scope.

# If this is a separate api_routes.py file:
# from . import db, CompletedTable, TopRail, CompletedPods, WoodCount, PrintedPartsCount, ProductionSchedule, MDFInventory, HardwarePart, TableStock
# (Assuming your main app file is in the same directory and initializes db and models)

# Let's assume for now this code will be integrated into the main app.py or models are accessible.
# If you are creating a separate api_routes.py, you'll need to adjust imports for db and models.
# For example, if your main file is `app.py`:
# from .app import db, CompletedTable, TopRail, ... (if models are in app.py)
# or from .models import db, CompletedTable, ... (if models are in models.py)

# For the purpose of this snippet, I'll define the blueprint and assume models are accessible.
# You'll need to integrate this into your Flask app structure.

api = Blueprint('api', __name__, url_prefix='/api') # Added url_prefix

# API Authentication (simple token-based auth) - Copied from your app
# Ensure API_TOKENS is defined or imported if this is a separate file
API_TOKENS = ["bitcade_api_key_1", "mobile_app_token_2"] 

def require_api_token(view_function):
    """Decorator to check for valid API token"""
    # Import wraps if this is a separate file
    from functools import wraps
    @wraps(view_function)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-API-Token')
        if token and token in API_TOKENS:
            return view_function(*args, **kwargs)
        return jsonify({"error": "Unauthorized access. Valid API token required."}), 401
    return decorated

# Helper function to parse date string
def parse_date_str(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None

# API Routes
@api.route('/status', methods=['GET'])
def api_status():
    """Simple API status check endpoint"""
    return jsonify({
        "status": "online",
        "version": "1.1.0", # Updated version
        "timestamp": datetime.utcnow().isoformat()
    })

@api.route('/work/daily/<target_date_str>', methods=['GET'])
@require_api_token
def daily_work_summary_historical(target_date_str):
    """Get summary of work completed for a specific date (YYYY-MM-DD)"""
    # Import models here if they are not globally available in this blueprint context
    from __main__ import db, CompletedTable, TopRail, CompletedPods # Assuming app.py is run as __main__

    target_d = parse_date_str(target_date_str)
    if not target_d:
        return jsonify({"error": "Invalid date format. Please use YYYY-MM-DD."}), 400
    
    bodies_done = CompletedTable.query.filter_by(date=target_d).all()
    top_rails_done = TopRail.query.filter_by(date=target_d).all()
    pods_done = CompletedPods.query.filter_by(date=target_d).all()
    
    response = {
        "date": target_d.isoformat(),
        "completed_components": {
            "bodies": [
                {
                    "worker": body.worker, "start_time": body.start_time, "finish_time": body.finish_time,
                    "serial_number": body.serial_number, "issue": body.issue, "had_lunch": body.lunch == "Yes"
                } for body in bodies_done
            ],
            "top_rails": [
                {
                    "worker": rail.worker, "start_time": rail.start_time, "finish_time": rail.finish_time,
                    "serial_number": rail.serial_number, "issue": rail.issue, "had_lunch": rail.lunch == "Yes"
                } for rail in top_rails_done
            ],
            "pods": [
                {
                    "worker": pod.worker, 
                    "start_time": pod.start_time.strftime('%H:%M:%S') if isinstance(pod.start_time, datetime) else str(pod.start_time), # Ensure time is string
                    "finish_time": pod.finish_time.strftime('%H:%M:%S') if isinstance(pod.finish_time, datetime) else str(pod.finish_time), # Ensure time is string
                    "serial_number": pod.serial_number, "issue": pod.issue, "had_lunch": pod.lunch == "Yes"
                } for pod in pods_done
            ]
        },
        "counts": {
            "bodies": len(bodies_done),
            "top_rails": len(top_rails_done),
            "pods": len(pods_done),
            "total_components": len(bodies_done) + len(top_rails_done) + len(pods_done)
        }
    }
    return jsonify(response)

@api.route('/work/daily', methods=['GET'])
@require_api_token
def daily_work_summary_today():
    """Get summary of work completed today (default)"""
    # Redirects or calls the historical one with today's date
    today_str = date.today().strftime("%Y-%m-%d")
    return daily_work_summary_historical(today_str)


def get_production_summary_for_period(year, month):
    """Helper function to get production summary for a given year and month"""
    from __main__ import db, CompletedTable, TopRail, CompletedPods, ProductionSchedule # Assuming app.py is run as __main__

    schedule = ProductionSchedule.query.filter_by(year=year, month=month).first()
    
    completed_bodies = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == year,
        extract('month', CompletedTable.date) == month
    ).count()
    
    completed_top_rails = TopRail.query.filter(
        extract('year', TopRail.date) == year,
        extract('month', TopRail.date) == month
    ).count()
    
    completed_pods = CompletedPods.query.filter(
        extract('year', CompletedPods.date) == year,
        extract('month', CompletedPods.date) == month
    ).count()

    def is_6ft_serial(serial_str): # Helper for serial check
        if not serial_str: return False
        return " - 6" in serial_str or "-6" in serial_str or serial_str.replace(" ", "").endswith("-6")

    all_bodies = CompletedTable.query.filter(extract('year', CompletedTable.date) == year, extract('month', CompletedTable.date) == month).all()
    all_rails = TopRail.query.filter(extract('year', TopRail.date) == year, extract('month', TopRail.date) == month).all()
    all_pods = CompletedPods.query.filter(extract('year', CompletedPods.date) == year, extract('month', CompletedPods.date) == month).all()

    def count_by_size(items):
        count_6ft = sum(1 for item in items if is_6ft_serial(item.serial_number))
        return {"6ft": count_6ft, "7ft": len(items) - count_6ft}

    target_7ft_val = schedule.target_7ft if schedule else 60
    target_6ft_val = schedule.target_6ft if schedule else 60
    total_target_val = (target_7ft_val + target_6ft_val) if schedule else 120
    if total_target_val == 0: total_target_val = 1 # Avoid division by zero

    response = {
        "period": {"year": year, "month": month},
        "production_targets": {
            "target_7ft": target_7ft_val,
            "target_6ft": target_6ft_val,
            "total_target": total_target_val
        },
        "current_production": {
            "total": {
                "bodies": completed_bodies, "top_rails": completed_top_rails, "pods": completed_pods
            },
            "by_size": {
                "bodies": count_by_size(all_bodies),
                "top_rails": count_by_size(all_rails),
                "pods": count_by_size(all_pods)
            }
        },
        "progress_percentage": {
            "bodies": round((completed_bodies / total_target_val) * 100, 1) if total_target_val else 0,
            "top_rails": round((completed_top_rails / total_target_val) * 100, 1) if total_target_val else 0,
            "pods": round((completed_pods / total_target_val) * 100, 1) if total_target_val else 0
        }
    }
    return response

@api.route('/production/summary/<int:year>/<int:month>', methods=['GET'])
@require_api_token
def production_summary_historical(year, month):
    """Get production summary for a specific year and month"""
    if not (1 <= month <= 12):
        return jsonify({"error": "Invalid month. Must be between 1 and 12."}), 400
    if year < 2000 or year > date.today().year + 5: # Basic year validation
        return jsonify({"error": "Invalid year."}), 400
        
    summary_data = get_production_summary_for_period(year, month)
    return jsonify(summary_data)

@api.route('/production/summary', methods=['GET'])
@require_api_token
def production_summary_current():
    """Get production summary for the current month"""
    today = date.today()
    summary_data = get_production_summary_for_period(today.year, today.month)
    return jsonify(summary_data)


@api.route('/inventory/summary', methods=['GET'])
@require_api_token
def inventory_summary():
    """Get summary of current inventory levels (existing endpoint, kept for current state)"""
    from __main__ import db, MDFInventory, PrintedPartsCount, HardwarePart, TableStock, WoodCount # Assuming app.py is run as __main__
    
    # MDF Inventory
    mdf_inventory_db = MDFInventory.query.first()
    if not mdf_inventory_db:
        mdf_inventory_db = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
    
    # Table parts (Chinese parts) - these names are specific
    table_parts_definitions = {
        "Table legs": 4, "Ball Gullies 1 (Untouched)": 2, "Ball Gullies 2": 1,
        "Ball Gullies 3": 1, "Ball Gullies 4": 1, "Ball Gullies 5": 1,
        "Feet": 4, "Triangle trim": 1, "White ball return trim": 1,
        "Color ball trim": 1, "Ball window trim": 1, "Aluminum corner": 4,
        "Chrome corner": 4, "Top rail trim short length": 4,
        "Top rail trim long length": 2, "Ramp 170mm": 1, "Ramp 158mm": 1,
        "Ramp 918mm": 1, "Ramp 376mm": 1, "Chrome handles": 1,
        "Center pockets": 2, "Corner pockets": 4, "Sticker Set": 1
    }
    table_parts_counts = {}
    for part_name_def in table_parts_definitions:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part_name_def)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        table_parts_counts[part_name_def] = latest_entry[0] if latest_entry else 0
    
    # 3D printed parts inventory
    printed_parts_definitions = [
        "Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder",
        "Small Ramp", "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp"
    ]
    printed_parts_counts = {}
    for part_name_def in printed_parts_definitions:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part_name_def)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        printed_parts_counts[part_name_def] = latest_entry[0] if latest_entry else 0
    
    # Hardware parts inventory
    hardware_parts_db = HardwarePart.query.all()
    hardware_counts = {}
    for part_hw in hardware_parts_db:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part_hw.name) # Hardware parts also use PrintedPartsCount table
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        hardware_counts[part_hw.name] = latest_entry[0] if latest_entry else part_hw.initial_count
    
    # Table stock (finished components)
    table_stock_entries_db = TableStock.query.all()
    table_stock_finished = {entry.type: entry.count for entry in table_stock_entries_db}
    
    # Wooden components (latest counts)
    wooden_counts = {}
    for section_wc in ["Body", "Pod Sides", "Bases", "Top Rail Pieces Short", "Top Rail Pieces Long"]: # Added Top Rail Pieces
        for size_wc in ["7ft", "6ft"]:
            full_section_name = f"{size_wc} - {section_wc}"
            latest_entry = (
                db.session.query(WoodCount.count)
                .filter_by(section=full_section_name)
                .order_by(WoodCount.date.desc(), WoodCount.time.desc())
                .first()
            )
            wooden_counts[full_section_name.replace(" ", "_").lower()] = latest_entry[0] if latest_entry else 0
            
    # Calculate tables possible based on inventory (simplified from your app code)
    tables_possible_per_part = {
        part: table_parts_counts[part] // req
        for part, req in table_parts_definitions.items() if req > 0
    }
    max_tables_possible = min(tables_possible_per_part.values()) if tables_possible_per_part else 0
    
    response = {
        "mdf_inventory": {
            "plain_mdf": mdf_inventory_db.plain_mdf,
            "black_mdf": mdf_inventory_db.black_mdf,
            "plain_mdf_36": mdf_inventory_db.plain_mdf_36
        },
        "wooden_components_current": wooden_counts,
        "table_parts_current": table_parts_counts,      # Chinese parts
        "printed_parts_current": printed_parts_counts,  # 3D Printed parts
        "hardware_parts_current": hardware_counts,
        "finished_components_stock": table_stock_finished,
        "production_capacity_current": {
            "max_tables_possible_based_on_table_parts": max_tables_possible,
            "limiting_table_parts": [
                part for part, count in tables_possible_per_part.items() 
                if count == max_tables_possible
            ] if max_tables_possible >= 0 else [] # Ensure positive or zero
        }
    }
    return jsonify(response)

@api.route('/inventory/printed_parts_count/all', methods=['GET'])
@require_api_token
def all_printed_parts_counts():
    """Returns all historical records from PrintedPartsCount."""
    from __main__ import PrintedPartsCount # Assuming app.py is run as __main__
    records = PrintedPartsCount.query.order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).all()
    return jsonify([{
        "id": r.id, "part_name": r.part_name, "count": r.count, 
        "date": r.date.isoformat(), "time": r.time.strftime('%H:%M:%S')
    } for r in records])

@api.route('/inventory/printed_parts_count/as_of/<target_date_str>', methods=['GET'])
@require_api_token
def printed_parts_counts_as_of(target_date_str):
    """
    Returns the latest count for each part in PrintedPartsCount on or before target_date.
    This covers 3D printed parts, table parts (Chinese), and hardware parts.
    """
    from __main__ import db, PrintedPartsCount # Assuming app.py is run as __main__
    target_d = parse_date_str(target_date_str)
    if not target_d:
        return jsonify({"error": "Invalid date format. Please use YYYY-MM-DD."}), 400

    # Get all distinct part names that have records on or before the target date
    distinct_parts_query = db.session.query(PrintedPartsCount.part_name.distinct()).filter(
        PrintedPartsCount.date <= target_d
    ).all()
    distinct_part_names = [name for (name,) in distinct_parts_query]

    parts_as_of_date = {}
    for part_name in distinct_part_names:
        latest_entry = (
            PrintedPartsCount.query
            .filter(
                PrintedPartsCount.part_name == part_name,
                PrintedPartsCount.date <= target_d
            )
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        if latest_entry:
            parts_as_of_date[part_name] = {
                "count": latest_entry.count,
                "last_recorded_date": latest_entry.date.isoformat(),
                "last_recorded_time": latest_entry.time.strftime('%H:%M:%S')
            }
        else:
            # This case should ideally not happen if distinct_part_names is derived correctly
            parts_as_of_date[part_name] = {"count": 0, "last_recorded_date": None, "last_recorded_time": None}
            
    return jsonify(parts_as_of_date)


@api.route('/inventory/wood_counts/all', methods=['GET'])
@require_api_token
def all_wood_counts():
    """Returns all historical records from WoodCount."""
    from __main__ import WoodCount # Assuming app.py is run as __main__
    records = WoodCount.query.order_by(WoodCount.date.desc(), WoodCount.time.desc()).all()
    return jsonify([{
        "id": r.id, "section": r.section, "count": r.count, 
        "date": r.date.isoformat(), "time": r.time.strftime('%H:%M:%S')
    } for r in records])

@api.route('/inventory/wood_counts/as_of/<target_date_str>', methods=['GET'])
@require_api_token
def wood_counts_as_of(target_date_str):
    """Returns the latest count for each wood section on or before target_date."""
    from __main__ import db, WoodCount # Assuming app.py is run as __main__
    target_d = parse_date_str(target_date_str)
    if not target_d:
        return jsonify({"error": "Invalid date format. Please use YYYY-MM-DD."}), 400

    distinct_sections_query = db.session.query(WoodCount.section.distinct()).filter(
        WoodCount.date <= target_d
    ).all()
    distinct_sections = [name for (name,) in distinct_sections_query]
    
    sections_as_of_date = {}
    for section_name in distinct_sections:
        latest_entry = (
            WoodCount.query
            .filter(
                WoodCount.section == section_name,
                WoodCount.date <= target_d
            )
            .order_by(WoodCount.date.desc(), WoodCount.time.desc())
            .first()
        )
        if latest_entry:
            sections_as_of_date[section_name] = {
                "count": latest_entry.count,
                "last_recorded_date": latest_entry.date.isoformat(),
                "last_recorded_time": latest_entry.time.strftime('%H:%M:%S')
            }
    return jsonify(sections_as_of_date)


@api.route('/tables/<string:serial_number>', methods=['GET'])
@require_api_token
def get_table_by_serial(serial_number):
    """Get details for a completed item (body, top_rail, pod) by its serial number"""
    from __main__ import CompletedTable, TopRail, CompletedPods # Assuming app.py is run as __main__
    # This function seems fine as is from your provided code.
    table = CompletedTable.query.filter_by(serial_number=serial_number).first()
    if table:
        return jsonify({
            "type": "body", "serial_number": table.serial_number, "worker": table.worker,
            "date": table.date.isoformat(), "start_time": str(table.start_time), 
            "finish_time": str(table.finish_time), "issue": table.issue, "had_lunch": table.lunch == "Yes"
        })
    
    top_rail = TopRail.query.filter_by(serial_number=serial_number).first()
    if top_rail:
        return jsonify({
            "type": "top_rail", "serial_number": top_rail.serial_number, "worker": top_rail.worker,
            "date": top_rail.date.isoformat(), "start_time": str(top_rail.start_time), 
            "finish_time": str(top_rail.finish_time), "issue": top_rail.issue, "had_lunch": top_rail.lunch == "Yes"
        })
        
    pod = CompletedPods.query.filter_by(serial_number=serial_number).first()
    if pod:
        return jsonify({
            "type": "pod", "serial_number": pod.serial_number, "worker": pod.worker,
            "date": pod.date.isoformat(), 
            "start_time": pod.start_time.strftime('%H:%M:%S') if isinstance(pod.start_time, (datetime, date.time)) else str(pod.start_time),
            "finish_time": pod.finish_time.strftime('%H:%M:%S') if isinstance(pod.finish_time, (datetime, date.time)) else str(pod.finish_time),
            "issue": pod.issue, "had_lunch": pod.lunch == "Yes"
        })
        
    return jsonify({"error": "Item not found with this serial number"}), 404

@api.route('/definitions/hardware_parts', methods=['GET'])
@require_api_token
def get_all_hardware_parts_definitions():
    """Get definitions of all hardware parts."""
    from __main__ import HardwarePart # Assuming app.py is run as __main__
    parts = HardwarePart.query.all()
    return jsonify([{
        "id": p.id, "name": p.name, 
        "initial_count": p.initial_count, 
        "used_per_table": p.used_per_table
    } for p in parts])

@api.route('/definitions/production_schedule/all', methods=['GET'])
@require_api_token
def get_all_production_schedules():
    """Get all production schedule entries."""
    from __main__ import ProductionSchedule # Assuming app.py is run as __main__
    schedules = ProductionSchedule.query.order_by(ProductionSchedule.year, ProductionSchedule.month).all()
    return jsonify([{
        "id": s.id, "year": s.year, "month": s.month,
        "target_7ft": s.target_7ft, "target_6ft": s.target_6ft
    } for s in schedules])

@api.route('/definitions/production_schedule/<int:year>/<int:month>', methods=['GET'])
@require_api_token
def get_production_schedule_for_month(year, month):
    """Get production schedule for a specific year and month."""
    from __main__ import ProductionSchedule # Assuming app.py is run as __main__
    schedule = ProductionSchedule.query.filter_by(year=year, month=month).first()
    if schedule:
        return jsonify({
            "id": schedule.id, "year": schedule.year, "month": schedule.month,
            "target_7ft": schedule.target_7ft, "target_6ft": schedule.target_6ft
        })
    return jsonify({"error": "Production schedule not found for this period"}), 404

# You would register this blueprint in your main app.py:
# from .api_routes import api as api_blueprint # Assuming this file is api_routes.py
# app.register_blueprint(api_blueprint)
```

**How to Integrate This:**

1.  **Save the code:** Save the code above into a new file named `api_routes.py` in the same directory as your main `app.py` file.
2.  **Import Models and `db`**:
    * At the top of `api_routes.py`, you'll see comments about importing `db` and your models. The most straightforward way if your models and `db` are defined in `app.py` and you run `app.py` directly is to use:
        ```python
        from __main__ import db, CompletedTable, TopRail, CompletedPods, WoodCount, PrintedPartsCount, ProductionSchedule, MDFInventory, HardwarePart, TableStock 
        ```
        Place this import inside each route function that needs them, or once at the top if your project structure ensures `__main__` is consistently your main app module. A cleaner long-term solution is to move your models into their own `models.py` file.
3.  **Register the Blueprint in `app.py`**:
    * In your main `app.py` file, *after* your Flask `app` instance is created and *before* `if __name__ == '__main__':`, add:
        ```python
        from api_routes import api as api_blueprint 
        app.register_blueprint(api_blueprint)
        ```
    * Make sure to remove the old `api = Blueprint('api', __name__)` and all the old `@api.route` definitions from your main `app.py` file, as they are now in `api_routes.py`.

**Important Notes on the API Code:**

* **Model Imports**: The provided `api_routes.py` assumes that the database `db` object and your SQLAlchemy models (`CompletedTable`, `TopRail`, etc.) are accessible. The `from __main__ import ...` lines are a common way to do this when running `app.py` as the main script. If you have a different project structure (e.g., models in `models.py`), you'll need to adjust these imports accordingly (e.g., `from .models import db, CompletedTable`).
* **Error Handling**: Basic error handling (e.g., for invalid dates) is included. You might want to expand this.
* **Data Conversion**: Times and dates are converted to ISO format strings for JSON compatibility.
* **`is_6ft_serial` helper**: A helper function `is_6ft_serial` is included within `get_production_summary_for_period` as it was used in your original API.
* **Efficiency**: For endpoints that query "as of" a certain date by looking for the latest record, performance might degrade on very large tables if not indexed properly on `part_name`/`section` and `date`.
* **Inventory Summary**: The main `/api/inventory/summary` endpoint still provides the *current* state. The new endpoints for `printed_parts_count` and `wood_counts` allow you to get historical data for those specific tables. A fully historical version of the combined inventory summary would be much more complex.

This updated API should give you much more granular access to your application's data. Remember to test it thoroughly after integrati