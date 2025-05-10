from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, date
from sqlalchemy import func, extract, desc
from functools import wraps # Import wraps here

# Corrected imports: Import from 'flask_app' which is your main application module
from flask_app import db, CompletedTable, TopRail, CompletedPods, WoodCount, PrintedPartsCount, ProductionSchedule, MDFInventory, HardwarePart, TableStock

api = Blueprint('api', __name__, url_prefix='/api')

# API Authentication
API_TOKENS = ["bitcade_api_key_1", "mobile_app_token_2"] 

def require_api_token(view_function):
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
        "version": "1.1.1", # Incremented version for this fix
        "timestamp": datetime.utcnow().isoformat()
    })

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
                    "start_time": pod.start_time.strftime('%H:%M:%S') if isinstance(pod.start_time, (datetime, date.time)) else str(pod.start_time),
                    "finish_time": pod.finish_time.strftime('%H:%M:%S') if isinstance(pod.finish_time, (datetime, date.time)) else str(pod.finish_time),
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
    today_str = date.today().strftime("%Y-%m-%d")
    return daily_work_summary_historical(today_str)


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
    """Get production summary for a specific year and month"""
    if not (1 <= month <= 12):
        return jsonify({"error": "Invalid month. Must be between 1 and 12."}), 400
    if year < 2000 or year > date.today().year + 5: 
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
    mdf_inventory_db = MDFInventory.query.first()
    if not mdf_inventory_db:
        mdf_inventory_db = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
    
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
    
    hardware_parts_db = HardwarePart.query.all()
    hardware_counts = {}
    for part_hw in hardware_parts_db:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part_hw.name) 
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
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
        "table_parts_current": table_parts_counts,
        "printed_parts_current": printed_parts_counts,
        "hardware_parts_current": hardware_counts,
        "finished_components_stock": table_stock_finished,
        "production_capacity_current": {
            "max_tables_possible_based_on_table_parts": max_tables_possible,
            "limiting_table_parts": [
                part for part, count in tables_possible_per_part.items() 
                if count == max_tables_possible
            ] if max_tables_possible >= 0 else []
        }
    }
    return jsonify(response)

@api.route('/inventory/printed_parts_count/all', methods=['GET'])
@require_api_token
def all_printed_parts_counts():
    """Returns all historical records from PrintedPartsCount."""
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
    """
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
            parts_as_of_date[part_name] = {"count": 0, "last_recorded_date": None, "last_recorded_time": None}
            
    return jsonify(parts_as_of_date)


@api.route('/inventory/wood_counts/all', methods=['GET'])
@require_api_token
def all_wood_counts():
    """Returns all historical records from WoodCount."""
    records = WoodCount.query.order_by(WoodCount.date.desc(), WoodCount.time.desc()).all()
    return jsonify([{
        "id": r.id, "section": r.section, "count": r.count, 
        "date": r.date.isoformat(), "time": r.time.strftime('%H:%M:%S')
    } for r in records])

@api.route('/inventory/wood_counts/as_of/<target_date_str>', methods=['GET'])
@require_api_token
def wood_counts_as_of(target_date_str):
    """Returns the latest count for each wood section on or before target_date."""
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
    schedules = ProductionSchedule.query.order_by(ProductionSchedule.year, ProductionSchedule.month).all()
    return jsonify([{
        "id": s.id, "year": s.year, "month": s.month,
        "target_7ft": s.target_7ft, "target_6ft": s.target_6ft
    } for s in schedules])

@api.route('/definitions/production_schedule/<int:year>/<int:month>', methods=['GET'])
@require_api_token
def get_production_schedule_for_month(year, month):
    """Get production schedule for a specific year and month."""
    schedule = ProductionSchedule.query.filter_by(year=year, month=month).first()
    if schedule:
        return jsonify({
            "id": schedule.id, "year": schedule.year, "month": schedule.month,
            "target_7ft": schedule.target_7ft, "target_6ft": schedule.target_6ft
        })
    return jsonify({"error": "Production schedule not found for this period"}), 404
