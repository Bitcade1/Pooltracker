from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, date
from sqlalchemy import func, extract
from flask_app import db
from flask_app import CompletedTable, TopRail, CompletedPods, WoodCount, PrintedPartsCount
from flask_app import ProductionSchedule, TableStock, HardwarePart, MDFInventory

# Create a Blueprint for the API routes
api = Blueprint('api', __name__)

# API Authentication (simple token-based auth)
API_TOKENS = ["bitcade_api_key_1", "mobile_app_token_2"]  # Store securely in production

def require_api_token(view_function):
    """Decorator to check for valid API token"""
    def decorated(*args, **kwargs):
        token = request.headers.get('X-API-Token')
        if token and token in API_TOKENS:
            return view_function(*args, **kwargs)
        return jsonify({"error": "Unauthorized access. Valid API token required."}), 401
    
    # Preserve the original function's name and docstring
    decorated.__name__ = view_function.__name__
    decorated.__doc__ = view_function.__doc__
    return decorated

# API Routes
@api.route('/api/status', methods=['GET'])
def api_status():
    """Simple API status check endpoint"""
    return jsonify({
        "status": "online",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    })

@api.route('/api/production/summary', methods=['GET'])
@require_api_token
def production_summary():
    """Get production summary for current month"""
    today = date.today()
    current_year = today.year
    current_month = today.month
    
    # Get production schedule for current month
    schedule = ProductionSchedule.query.filter_by(
        year=current_year, month=current_month
    ).first()
    
    # Completed items this month
    completed_bodies = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == current_year,
        extract('month', CompletedTable.date) == current_month
    ).count()
    
    completed_top_rails = TopRail.query.filter(
        extract('year', TopRail.date) == current_year,
        extract('month', TopRail.date) == current_month
    ).count()
    
    completed_pods = CompletedPods.query.filter(
        extract('year', CompletedPods.date) == current_year,
        extract('month', CompletedPods.date) == current_month
    ).count()

    # Function to count 6ft vs 7ft items
    def count_by_size(items, is_6ft_func):
        count_6ft = sum(1 for item in items if is_6ft_func(item))
        return {
            "6ft": count_6ft,
            "7ft": len(items) - count_6ft
        }
    
    # Get all completed items for this month
    all_bodies = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == current_year,
        extract('month', CompletedTable.date) == current_month
    ).all()
    
    all_rails = TopRail.query.filter(
        extract('year', TopRail.date) == current_year,
        extract('month', TopRail.date) == current_month
    ).all()
    
    all_pods = CompletedPods.query.filter(
        extract('year', CompletedPods.date) == current_year,
        extract('month', CompletedPods.date) == current_month
    ).all()
    
    # Function to check if item is 6ft based on serial number
    def is_6ft(item):
        serial = item.serial_number
        return " - 6" in serial or "-6" in serial or serial.replace(" ", "").endswith("-6")
    
    # Format the response
    response = {
        "production_targets": {
            "target_7ft": schedule.target_7ft if schedule else 60,
            "target_6ft": schedule.target_6ft if schedule else 60,
            "total_target": (schedule.target_7ft + schedule.target_6ft) if schedule else 120
        },
        "current_production": {
            "total": {
                "bodies": completed_bodies,
                "top_rails": completed_top_rails,
                "pods": completed_pods
            },
            "by_size": {
                "bodies": count_by_size(all_bodies, is_6ft),
                "top_rails": count_by_size(all_rails, is_6ft),
                "pods": count_by_size(all_pods, is_6ft)
            }
        },
        "progress_percentage": {
            "bodies": round((completed_bodies / ((schedule.target_7ft + schedule.target_6ft) if schedule else 120)) * 100, 1),
            "top_rails": round((completed_top_rails / ((schedule.target_7ft + schedule.target_6ft) if schedule else 120)) * 100, 1),
            "pods": round((completed_pods / ((schedule.target_7ft + schedule.target_6ft) if schedule else 120)) * 100, 1)
        }
    }
    
    return jsonify(response)

@api.route('/api/inventory/summary', methods=['GET'])
@require_api_token
def inventory_summary():
    """Get summary of current inventory levels"""
    
    # MDF Inventory
    mdf_inventory = MDFInventory.query.first()
    if not mdf_inventory:
        mdf_inventory = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
    
    # Table parts inventory
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
    
    table_parts_counts = {}
    for part in table_parts:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        table_parts_counts[part] = latest_entry[0] if latest_entry else 0
    
    # 3D printed parts inventory
    printed_parts = [
        "Large Ramp", "Paddle", "Laminate", "Spring Mount", "Spring Holder",
        "Small Ramp", "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp"
    ]
    
    printed_parts_counts = {}
    for part in printed_parts:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        printed_parts_counts[part] = latest_entry[0] if latest_entry else 0
    
    # Hardware parts inventory
    hardware_parts = HardwarePart.query.all()
    hardware_counts = {}
    for part in hardware_parts:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part.name)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        hardware_counts[part.name] = latest_entry[0] if latest_entry else part.initial_count
    
    # Table stock (finished components)
    table_stock_entries = TableStock.query.all()
    table_stock = {}
    for entry in table_stock_entries:
        table_stock[entry.type] = entry.count
    
    # Wooden components
    wooden_counts = {}
    for section in ["Body", "Pod Sides", "Bases"]:
        latest_entry = (
            db.session.query(WoodCount.count)
            .filter_by(section=f"7ft - {section}")
            .order_by(WoodCount.date.desc(), WoodCount.time.desc())
            .first()
        )
        wooden_counts[f"7ft_{section.lower()}"] = latest_entry[0] if latest_entry else 0
        
        latest_entry_6ft = (
            db.session.query(WoodCount.count)
            .filter_by(section=f"6ft - {section}")
            .order_by(WoodCount.date.desc(), WoodCount.time.desc())
            .first()
        )
        wooden_counts[f"6ft_{section.lower()}"] = latest_entry_6ft[0] if latest_entry_6ft else 0
    
    # Calculate tables possible based on inventory
    tables_possible_per_part = {}
    for part, count in table_parts_counts.items():
        req_per_table = table_parts.get(part, 1)
        tables_possible_per_part[part] = count // req_per_table if req_per_table > 0 else 0
    
    max_tables_possible = min(tables_possible_per_part.values()) if tables_possible_per_part else 0
    
    response = {
        "mdf_inventory": {
            "plain_mdf": mdf_inventory.plain_mdf,
            "black_mdf": mdf_inventory.black_mdf,
            "plain_mdf_36": mdf_inventory.plain_mdf_36
        },
        "wooden_components": wooden_counts,
        "table_parts": table_parts_counts,
        "printed_parts": printed_parts_counts,
        "hardware_parts": hardware_counts,
        "finished_components": table_stock,
        "production_capacity": {
            "max_tables_possible": max_tables_possible,
            "limiting_parts": [
                part for part, count in tables_possible_per_part.items() 
                if count == max_tables_possible
            ] if max_tables_possible > 0 else []
        }
    }
    
    return jsonify(response)

@api.route('/api/work/daily', methods=['GET'])
@require_api_token
def daily_work_summary():
    """Get summary of work completed today"""
    today = date.today()
    
    # Get all components completed today
    bodies_today = CompletedTable.query.filter_by(date=today).all()
    top_rails_today = TopRail.query.filter_by(date=today).all()
    pods_today = CompletedPods.query.filter_by(date=today).all()
    
    # Format the response
    response = {
        "date": today.isoformat(),
        "completed_components": {
            "bodies": [
                {
                    "worker": body.worker,
                    "start_time": body.start_time,
                    "finish_time": body.finish_time,
                    "serial_number": body.serial_number,
                    "issue": body.issue,
                    "had_lunch": body.lunch == "Yes"
                }
                for body in bodies_today
            ],
            "top_rails": [
                {
                    "worker": rail.worker,
                    "start_time": rail.start_time,
                    "finish_time": rail.finish_time,
                    "serial_number": rail.serial_number,
                    "issue": rail.issue,
                    "had_lunch": rail.lunch == "Yes"
                }
                for rail in top_rails_today
            ],
            "pods": [
                {
                    "worker": pod.worker,
                    "start_time": pod.start_time.strftime('%H:%M') if isinstance(pod.start_time, datetime) else pod.start_time,
                    "finish_time": pod.finish_time.strftime('%H:%M') if isinstance(pod.finish_time, datetime) else pod.finish_time,
                    "serial_number": pod.serial_number,
                    "issue": pod.issue,
                    "had_lunch": pod.lunch == "Yes"
                }
                for pod in pods_today
            ]
        },
        "counts": {
            "bodies": len(bodies_today),
            "top_rails": len(top_rails_today),
            "pods": len(pods_today),
            "total_components": len(bodies_today) + len(top_rails_today) + len(pods_today)
        }
    }
    
    return jsonify(response)

@api.route('/api/wood/counts', methods=['GET'])
@require_api_token
def wood_counts():
    """Get current wood counts for all sections"""
    
    response = {
        "7ft": {},
        "6ft": {}
    }
    
    # Define sections we want to query
    sections = {
        "7ft": ["Body", "Pod Sides", "Bases", "Top Rail Pieces Short", "Top Rail Pieces Long"],
        "6ft": ["Body", "Pod Sides", "Bases", "Top Rail Pieces Short", "Top Rail Pieces Long"]
    }
    
    for size, size_sections in sections.items():
        for section in size_sections:
            full_section = f"{size} - {section}"
            latest_entry = (
                db.session.query(WoodCount.count)
                .filter_by(section=full_section)
                .order_by(WoodCount.date.desc(), WoodCount.time.desc())
                .first()
            )
            response[size][section] = latest_entry[0] if latest_entry else 0
    
    # Add weekly and monthly sheet counts
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    start_of_month = today.replace(day=1)
    
    # Count wood cut since start of week/month
    weekly_sheets = db.session.query(func.count(WoodCount.id)).filter(
        WoodCount.date >= start_of_week,
        WoodCount.date <= today
    ).scalar()
    
    monthly_sheets = db.session.query(func.count(WoodCount.id)).filter(
        WoodCount.date >= start_of_month,
        WoodCount.date <= today
    ).scalar()
    
    response["summary"] = {
        "weekly_sheets_cut": weekly_sheets,
        "monthly_sheets_cut": monthly_sheets
    }
    
    return jsonify(response)

@api.route('/api/tables/<string:serial_number>', methods=['GET'])
@require_api_token
def get_table_by_serial(serial_number):
    """Get details for a table by its serial number"""
    
    # Check in each of the possible tables
    table = CompletedTable.query.filter_by(serial_number=serial_number).first()
    if table:
        return jsonify({
            "type": "body",
            "serial_number": table.serial_number,
            "worker": table.worker,
            "date": table.date.isoformat(),
            "start_time": table.start_time,
            "finish_time": table.finish_time,
            "issue": table.issue,
            "had_lunch": table.lunch == "Yes"
        })
    
    top_rail = TopRail.query.filter_by(serial_number=serial_number).first()
    if top_rail:
        return jsonify({
            "type": "top_rail",
            "serial_number": top_rail.serial_number,
            "worker": top_rail.worker,
            "date": top_rail.date.isoformat(),
            "start_time": top_rail.start_time,
            "finish_time": top_rail.finish_time,
            "issue": top_rail.issue,
            "had_lunch": top_rail.lunch == "Yes"
        })
    
    pod = CompletedPods.query.filter_by(serial_number=serial_number).first()
    if pod:
        return jsonify({
            "type": "pod",
            "serial_number": pod.serial_number,
            "worker": pod.worker,
            "date": pod.date.isoformat(),
            "start_time": pod.start_time.strftime('%H:%M') if isinstance(pod.start_time, datetime) else pod.start_time,
            "finish_time": pod.finish_time.strftime('%H:%M') if isinstance(pod.finish_time, datetime) else pod.finish_time,
            "issue": pod.issue,
            "had_lunch": pod.lunch == "Yes"
        })
    
    return jsonify({"error": "Table not found with this serial number"}), 404

# Register additional API endpoints here as needed