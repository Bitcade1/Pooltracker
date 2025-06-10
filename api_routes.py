from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, date, timezone # Added timezone
import calendar # For monthrange
from sqlalchemy import func, extract, desc
from functools import wraps

# Corrected imports: Import from 'flask_app' which is your main application module
# Ensure 'db' is your SQLAlchemy instance, and other models are correctly defined in flask_app
from flask_app import db, CompletedTable, TopRail, CompletedPods, WoodCount, PrintedPartsCount, ProductionSchedule, MDFInventory, HardwarePart, TableStock
# Import datetime module itself to access datetime.time if needed for other parts (dt alias)
import datetime as dt # dt alias is used in existing code

# --- Define the API Blueprint ---
api = Blueprint('api', __name__, url_prefix='/api')

# --- API Authentication ---
API_TOKENS = ["bitcade_api_key_1", "mobile_app_token_2"] # Example tokens

def require_api_token(view_function):
    @wraps(view_function)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-API-Token')
        if token and token in API_TOKENS:
            return view_function(*args, **kwargs)
        return jsonify({"error": "Unauthorized access. Valid API token required."}), 401
    return decorated

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_token = request.headers.get('Authorization')
        api_token = request.headers.get('X-API-Token')
        
        if not auth_token and not api_token:
            return jsonify({"error": "No auth token provided"}), 401
            
        expected_token = "bitcade_api_key_1"  # Should match Desktop App config
        
        # Check either Authorization or X-API-Token
        if auth_token:
            token = auth_token.split("Bearer ")[-1]
        else:
            token = api_token
            
        if token != expected_token:
            return jsonify({"error": "Invalid auth token"}), 401
            
        return f(*args, **kwargs)
    return decorated

# --- SQLAlchemy Model for System State (e.g., last task completion time) ---
# Ideally, this model should be in your flask_app.models (or equivalent) and imported.
# Defining it here for completeness, assuming 'db' is the SQLAlchemy instance from flask_app.
class SystemState(db.Model):
    __tablename__ = 'system_state'
    key = db.Column(db.String(50), primary_key=True, doc="The key for the state variable, e.g., 'last_task_completion_utc'")
    value = db.Column(db.String(100), doc="The value of the state variable, e.g., an ISO timestamp string")
    # Use dt.datetime and dt.timezone for consistency with 'import datetime as dt'
    updated_at = db.Column(db.DateTime, 
                           default=lambda: dt.datetime.now(dt.timezone.utc), 
                           onupdate=lambda: dt.datetime.now(dt.timezone.utc),
                           doc="Timestamp of the last update in UTC")

    def __repr__(self):
        return f"<SystemState {self.key}='{self.value}' @ {self.updated_at}>"

# --- Helper function to parse date string ---
def parse_date_str(date_str):
    """Converts YYYY-MM-DD string to a date object."""
    try:
        return dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None

# --- API Routes ---
@api.route('/status', methods=['GET'])
def api_status():
    """Simple API status check endpoint"""
    return jsonify({
        "status": "online",
        "version": "1.3.0", # Version updated for new performance endpoints
        # Using timezone-aware UTC timestamp
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat() 
    })

@api.route('/top_rail/next_serial', methods=['GET'])
@require_api_token
def get_next_top_rail_serial():
    """Get the next serial number for top rails"""
    try:
        # Get the most recent top rail
        last_rail = TopRail.query.order_by(TopRail.id.desc()).first()
        
        if not last_rail or not last_rail.serial_number:
            return jsonify({"next_serial": "1000"})  # Default starting number
            
        # Extract the base number and any suffixes
        current_serial = last_rail.serial_number
        
        # Check for color suffixes first
        color_suffix = ""
        if " - GO" in current_serial or "-GO" in current_serial:
            color_suffix = " - GO"
            current_serial = current_serial.replace(" - GO", "").replace("-GO", "")
        elif " - O" in current_serial or "-O" in current_serial:
            color_suffix = " - O"
            current_serial = current_serial.replace(" - O", "").replace("-O", "")
        elif " - C" in current_serial or "-C" in current_serial:
            color_suffix = " - C"
            current_serial = current_serial.replace(" - C", "").replace("-C", "")
        elif " - B" in current_serial or "-B" in current_serial:
            color_suffix = " - B"
            current_serial = current_serial.replace(" - B", "").replace("-B", "")
            
        # Now check for size suffix
        size_suffix = ""
        if " - 6" in current_serial or "-6" in current_serial:
            size_suffix = " - 6"
            current_serial = current_serial.replace(" - 6", "").replace("-6", "")

        try:
            # Extract the base number and return exactly one more than current
            base_number = int(''.join(filter(str.isdigit, current_serial)))
            next_serial = str(base_number + 1) + size_suffix + color_suffix
            
        except ValueError:
            next_serial = "1000"  # Fallback if conversion fails

        return jsonify({"next_serial": next_serial})
        
    except Exception as e:
        return jsonify({"error": f"Failed to generate next serial number: {str(e)}"}), 500

# --- New Endpoints for Performance Timer ---
@api.route('/performance/task_completed', methods=['POST'])
@require_api_token
def mark_task_completed_performance():
    """
    Endpoint to be called when a task is completed for performance tracking.
    It records the current UTC timestamp as the last task completion time.
    """
    key_name = 'last_task_completion_utc'
    current_ts_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    state_entry = SystemState.query.get(key_name)
    if state_entry:
        state_entry.value = current_ts_iso
        # 'updated_at' will be handled by onupdate in the model
    else:
        state_entry = SystemState(key=key_name, value=current_ts_iso)
        db.session.add(state_entry)
    
    try:
        db.session.commit()
        # Log server-side for monitoring
        print(f"Performance task completed. New last_completion_time_utc: {current_ts_iso}")
        return jsonify({
            "message": "Task completion time for performance tracking recorded successfully.",
            "last_completion_time_utc": current_ts_iso
        }, 200)
    except Exception as e:
        db.session.rollback()
        # Log the error server-side
        print(f"Error updating SystemState for {key_name}: {str(e)}")
        return jsonify({"error": f"Failed to record task completion time: {str(e)}"}), 500

@api.route('/performance/last_completion_time', methods=['GET'])
@require_api_token
def get_last_completion_time_performance():
    """
    Endpoint to retrieve the timestamp of the last completed task for performance tracking.
    The frontend can use this to calculate elapsed time.
    """
    key_name = 'last_task_completion_utc'
    state_entry = SystemState.query.get(key_name)

    if state_entry and state_entry.value:
        return jsonify({
            "last_completion_time_utc": state_entry.value,
            "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()
        }), 200
    else:
        # If no task has been completed yet, or key not found
        return jsonify({
            "message": "No task completion time recorded yet for performance tracking.",
            "last_completion_time_utc": None,
            "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()
        }), 404


# --- Existing API Routes (from user's provided code) ---

@api.route('/work/daily/<target_date_str>', methods=['GET'])
@require_api_token
def daily_work_summary_historical(target_date_str):
    """Get summary of work completed for a specific date (YYYY-MM-DD)"""
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
                    "worker": body.worker, "start_time": str(body.start_time), "finish_time": str(body.finish_time),
                    "serial_number": body.serial_number, "issue": body.issue, "had_lunch": body.lunch == "Yes"
                } for body in bodies_done
            ],
            "top_rails": [
                {
                    "worker": rail.worker, "start_time": str(rail.start_time), "finish_time": str(rail.finish_time),
                    "serial_number": rail.serial_number, "issue": rail.issue, "had_lunch": rail.lunch == "Yes"
                } for rail in top_rails_done
            ],
            "pods": [
                {
                    "worker": pod.worker, 
                    "start_time": pod.start_time.strftime('%H:%M:%S') if isinstance(pod.start_time, (dt.datetime, dt.time)) else str(pod.start_time),
                    "finish_time": pod.finish_time.strftime('%H:%M:%S') if isinstance(pod.finish_time, (dt.datetime, dt.time)) else str(pod.finish_time),
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
    today_str = date.today().strftime("%Y-%m-%d") # Uses 'date' from 'from datetime import ... date'
    return daily_work_summary_historical(today_str)

@api.route('/work/monthly/<int:year>/<int:month>', methods=['GET'])
@require_api_token
def monthly_work_summary(year, month):
    """
    Get a summary of work completed for each day in a specific month.
    Returns a list of daily production counts.
    """
    if not (1 <= month <= 12):
        return jsonify({"error": "Invalid month. Must be between 1 and 12."}), 400
    # Uses 'date' from 'from datetime import ... date'
    if year < 2000 or year > date.today().year + 10: 
        return jsonify({"error": "Invalid year."}), 400

    try:
        num_days_in_month = calendar.monthrange(year, month)[1]
    except calendar.IllegalMonthError: 
        return jsonify({"error": "Invalid month number provided."}), 400
    except Exception as e: 
        return jsonify({"error": f"Error determining days in month: {str(e)}"}), 500

    results_for_month = []

    # Uses 'date' from 'from datetime import ... date'
    month_start_date = date(year, month, 1)
    month_end_date = date(year, month, num_days_in_month)

    bodies_in_month = CompletedTable.query.filter(CompletedTable.date.between(month_start_date, month_end_date)).all()
    pods_in_month = CompletedPods.query.filter(CompletedPods.date.between(month_start_date, month_end_date)).all()
    top_rails_in_month = TopRail.query.filter(TopRail.date.between(month_start_date, month_end_date)).all()

    daily_counts = {} 

    for b in bodies_in_month:
        date_str = b.date.isoformat()
        if date_str not in daily_counts: daily_counts[date_str] = {"bodies": 0, "pods": 0, "top_rails": 0}
        daily_counts[date_str]["bodies"] += 1
    
    for p in pods_in_month:
        date_str = p.date.isoformat()
        if date_str not in daily_counts: daily_counts[date_str] = {"bodies": 0, "pods": 0, "top_rails": 0}
        daily_counts[date_str]["pods"] += 1

    for tr in top_rails_in_month:
        date_str = tr.date.isoformat()
        if date_str not in daily_counts: daily_counts[date_str] = {"bodies": 0, "pods": 0, "top_rails": 0}
        daily_counts[date_str]["top_rails"] += 1

    for day_num in range(1, num_days_in_month + 1):
        # Uses 'date' from 'from datetime import ... date'
        current_date_obj = date(year, month, day_num)
        current_date_str = current_date_obj.isoformat()
        
        if current_date_str in daily_counts:
            results_for_month.append({
                "date": current_date_str,
                "bodies": daily_counts[current_date_str]["bodies"],
                "pods": daily_counts[current_date_str]["pods"],
                "top_rails": daily_counts[current_date_str]["top_rails"],
                "error_info": None 
            })
        else:
            results_for_month.append({
                "date": current_date_str,
                "bodies": 0,
                "pods": 0,
                "top_rails": 0,
                "error_info": None 
            })
    
    return jsonify(results_for_month)


# --- Placeholder for other existing API routes from flask_api_routes_v3 ---
# (get_production_summary_for_period, production_summary_historical, production_summary_current,
# inventory_summary, all_printed_parts_counts, printed_parts_counts_as_of,
# all_wood_counts, wood_counts_as_of, get_table_by_serial,
# get_all_hardware_parts_definitions, get_all_production_schedules,
# get_production_schedule_for_month)
# ... User should ensure these are correctly placed and integrated ...

# Example of how one of the further functions might start (based on user's snippet)
def get_production_summary_for_period(year, month):
    """Helper function to get production summary for a given year and month"""
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

    def is_6ft_serial(serial_str): 
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
    if total_target_val == 0: total_target_val = 1 

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
    if not (1 <= month <= 12):
        return jsonify({"error": "Invalid month. Must be between 1 and 12."}), 400
    # Uses 'date' from 'from datetime import ... date'
    if year < 2000 or year > date.today().year + 5: 
        return jsonify({"error": "Invalid year."}), 400
    summary_data = get_production_summary_for_period(year, month)
    return jsonify(summary_data)

@api.route('/production/summary', methods=['GET'])
@require_api_token
def production_summary_current():
    # Uses 'date' from 'from datetime import ... date'
    today = date.today()
    summary_data = get_production_summary_for_period(today.year, today.month)
    return jsonify(summary_data)

@api.route('/inventory/summary', methods=['GET'])
@require_api_token
def inventory_summary():
    mdf_inventory_db = MDFInventory.query.first()
    if not mdf_inventory_db:
        # Provide default values if MDFInventory is not yet populated
        mdf_inventory_db = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
        # If your model doesn't allow instantiation without adding to session,
        # you might need to create a dictionary or a simple object instead:
        # mdf_inventory_data = {"plain_mdf":0, "black_mdf":0, "plain_mdf_36":0}
        # And then use mdf_inventory_data['plain_mdf'] etc.
    
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
    
    printed_parts_definitions = [
        "Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder",
        "Small Ramp", "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp",
        "6ft Carpet", "7ft Carpet", "6ft Felt", "7ft Felt",  # Added new carpet and felt parts
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
    
    hardware_parts_db = HardwarePart.query.all()
    hardware_counts = {}
    for part_hw in hardware_parts_db:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part_hw.name) 
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        # Fallback to initial_count if no PrintedPartsCount entry exists for this hardware part
        hardware_counts[part_hw.name] = latest_entry[0] if latest_entry else part_hw.initial_count
    
    table_stock_entries_db = TableStock.query.all()
    table_stock_finished = {entry.type: entry.count for entry in table_stock_entries_db}
    
    wooden_counts = {}
    for section_wc in ["Body", "Pod Sides", "Bases", "Top Rail Pieces Short", "Top Rail Pieces Long"]:
        for size_wc in ["7ft", "6ft"]:
            full_section_name = f"{size_wc} - {section_wc}"
            latest_entry = (
                db.session.query(WoodCount.count)
                .filter_by(section=full_section_name)
                .order_by(WoodCount.date.desc(), WoodCount.time.desc())
                .first()
            )
            wooden_counts[full_section_name.replace(" ", "_").lower()] = latest_entry[0] if latest_entry else 0
            
    tables_possible_per_part = {
        part: table_parts_counts[part] // req
        for part, req in table_parts_definitions.items() if req > 0 and table_parts_counts.get(part, 0) is not None
    }
    max_tables_possible = min(tables_possible_per_part.values()) if tables_possible_per_part else 0
    
    response = {
        "mdf_inventory": {
            "plain_mdf": mdf_inventory_db.plain_mdf,
            "black_mdf": mdf_inventory_db.black_mdf,
            "plain_mdf_36": mdf_inventory_db.plain_mdf_36
        },
        "wooden_components_current": wooden_counts,
        "table_parts_current": table_parts_counts,
        "printed_parts_current": printed_parts_counts,
        "hardware_parts_current": hardware_counts,
        "finished_components_stock": table_stock_finished,
        "production_capacity_current": {
            "max_tables_possible_based_on_table_parts": max_tables_possible,
            "limiting_table_parts": [
                part for part, count in tables_possible_per_part.items() 
                if count == max_tables_possible
            ] if max_tables_possible >= 0 else [] # Ensure positive or zero, handle empty case
        }
    }
    return jsonify(response)

@api.route('/inventory/printed_parts_count/all', methods=['GET'])
@require_api_token
def all_printed_parts_counts():
    records = PrintedPartsCount.query.order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).all()
    return jsonify([{
        "id": r.id, "part_name": r.part_name, "count": r.count, 
        "date": r.date.isoformat(), 
        "time": r.time.strftime('%H:%M:%S') if r.time else None # Handle null time
    } for r in records])

@api.route('/inventory/printed_parts_count/as_of/<target_date_str>', methods=['GET'])
@require_api_token
def printed_parts_counts_as_of(target_date_str):
    target_d = parse_date_str(target_date_str)
    if not target_d:
        return jsonify({"error": "Invalid date format. Please use YYYY-MM-DD."}), 400
    
    distinct_parts_query = db.session.query(PrintedPartsCount.part_name.distinct()).filter(
        PrintedPartsCount.date <= target_d
    ).all()
    distinct_part_names = [name for (name,) in distinct_parts_query]
    parts_as_of_date = {}
    for part_name in distinct_part_names:
        latest_entry = (
            PrintedPartsCount.query
            .filter(PrintedPartsCount.part_name == part_name, PrintedPartsCount.date <= target_d)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first())
        if latest_entry:
            parts_as_of_date[part_name] = {
                "count": latest_entry.count,
                "last_recorded_date": latest_entry.date.isoformat(),
                "last_recorded_time": latest_entry.time.strftime('%H:%M:%S') if latest_entry.time else None
            }
        else:
            # This case should ideally not be hit if part_name comes from distinct_parts_query
            # based on records up to target_d. But as a fallback:
            parts_as_of_date[part_name] = {"count": 0, "last_recorded_date": None, "last_recorded_time": None}
    return jsonify(parts_as_of_date)

@api.route('/inventory/wood_counts/all', methods=['GET'])
@require_api_token
def all_wood_counts():
    records = WoodCount.query.order_by(WoodCount.date.desc(), WoodCount.time.desc()).all()
    return jsonify([{
        "id": r.id, "section": r.section, "count": r.count, 
        "date": r.date.isoformat(), 
        "time": r.time.strftime('%H:%M:%S') if r.time else None # Handle null time
    } for r in records])

@api.route('/inventory/wood_counts/as_of/<target_date_str>', methods=['GET'])
@require_api_token
def wood_counts_as_of(target_date_str):
    target_d = parse_date_str(target_date_str)
    if not target_d:
        return jsonify({"error": "Invalid date format. Please use YYYY-MM-DD."}), 400
    
    distinct_sections_query = db.session.query(WoodCount.section.distinct()).filter(WoodCount.date <= target_d).all()
    distinct_sections = [name for (name,) in distinct_sections_query]
    sections_as_of_date = {}
    for section_name in distinct_sections:
        latest_entry = (
            WoodCount.query
            .filter(WoodCount.section == section_name, WoodCount.date <= target_d)
            .order_by(WoodCount.date.desc(), WoodCount.time.desc()).first())
        if latest_entry:
            sections_as_of_date[section_name] = {
                "count": latest_entry.count,
                "last_recorded_date": latest_entry.date.isoformat(),
                "last_recorded_time": latest_entry.time.strftime('%H:%M:%S') if latest_entry.time else None
            }
        # No else needed, as section_name is guaranteed to have at least one entry by this logic
    return jsonify(sections_as_of_date)

@api.route('/tables/<string:serial_number>', methods=['GET'])
@require_api_token
def get_table_by_serial(serial_number):
    table = CompletedTable.query.filter_by(serial_number=serial_number).first()
    if table:
        return jsonify({
            "type": "body", "serial_number": table.serial_number, "worker": table.worker,
            "date": table.date.isoformat(), "start_time": str(table.start_time), 
            "finish_time": str(table.finish_time), "issue": table.issue, "had_lunch": table.lunch == "Yes"})
    
    top_rail = TopRail.query.filter_by(serial_number=serial_number).first()
    if top_rail:
        return jsonify({
            "type": "top_rail", "serial_number": top_rail.serial_number, "worker": top_rail.worker,
            "date": top_rail.date.isoformat(), "start_time": str(top_rail.start_time), 
            "finish_time": str(top_rail.finish_time), "issue": top_rail.issue, "had_lunch": top_rail.lunch == "Yes"})
            
    pod = CompletedPods.query.filter_by(serial_number=serial_number).first()
    if pod:
        return jsonify({
            "type": "pod", "serial_number": pod.serial_number, "worker": pod.worker,
            "date": pod.date.isoformat(), 
            "start_time": pod.start_time.strftime('%H:%M:%S') if isinstance(pod.start_time, (dt.datetime, dt.time)) else str(pod.start_time),
            "finish_time": pod.finish_time.strftime('%H:%M:%S') if isinstance(pod.finish_time, (dt.datetime, dt.time)) else str(pod.finish_time),
            "issue": pod.issue, "had_lunch": pod.lunch == "Yes"})
            
    return jsonify({"error": "Item not found with this serial number"}), 404

@api.route('/definitions/hardware_parts', methods=['GET'])
@require_api_token
def get_all_hardware_parts_definitions():
    parts = HardwarePart.query.all()
    return jsonify([{"id": p.id, "name": p.name, "initial_count": p.initial_count, "used_per_table": p.used_per_table} for p in parts])

@api.route('/definitions/production_schedule/all', methods=['GET'])
@require_api_token
def get_all_production_schedules():
    schedules = ProductionSchedule.query.order_by(ProductionSchedule.year, ProductionSchedule.month).all()
    return jsonify([{"id": s.id, "year": s.year, "month": s.month, "target_7ft": s.target_7ft, "target_6ft": s.target_6ft} for s in schedules])

@api.route('/definitions/production_schedule/<int:year>/<int:month>', methods=['GET'])
@require_api_token
def get_production_schedule_for_month(year, month):
    schedule = ProductionSchedule.query.filter_by(year=year, month=month).first()
    if schedule:
        return jsonify({"id": schedule.id, "year": schedule.year, "month": schedule.month, "target_7ft": schedule.target_7ft, "target_6ft": schedule.target_6ft})
    return jsonify({"error": "Production schedule not found for this period"}), 404

# --- Valid parts list for inventory tracking ---
VALID_PARTS = [
    "Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder",
    "Small Ramp", "Cue Ball Separator", "Bushing",
    "6ft Cue Ball Separator", "6ft Large Ramp", 
    "6ft Carpet", "7ft Carpet", "6ft Felt", "7ft Felt",
    "Table legs", "Ball Gullies 1 (Untouched)", "Ball Gullies 2",
    "Ball Gullies 3", "Ball Gullies 4", "Ball Gullies 5",
    "Feet", "Triangle trim", "White ball return trim",
    "Color ball trim", "Ball window trim", "Aluminum corner",
    "Chrome corner", "Top rail trim short length",
    "Top rail trim long length", "Ramp 170mm", "Ramp 158mm",
    "Ramp 918mm", "Ramp 376mm", "Chrome handles",
    "Center pockets", "Corner pockets", "Sticker Set",
    "M5 x 18 x 1.25 Penny Mudguard Washer",  # Added new hardware
    "M5 x 20 Socket Cap Screw",              # Added new hardware
    "Catch Plate",                           # Added new hardware
    "4.8x32mm Self Tapping Screw"            # Added new hardware
]



@api.route('/api/top_rail/start_timer', methods=['POST'])
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

@api.route('/api/top_rail/stop_timer', methods=['POST'])
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

@api.route('/api/top_rail/current_timer', methods=['GET'])
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

@api.route('/api/top_rail/timing_stats', methods=['GET'])
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