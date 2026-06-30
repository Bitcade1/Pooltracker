from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError, OperationalError
from datetime import datetime, timedelta, date, time, timezone
from collections import defaultdict  # Ensure defaultdict is imported
from calendar import monthrange
from sqlalchemy import func, extract, and_, or_, text
from sqlalchemy.orm import joinedload
import requests
import threading
import os
import re  # Add this import at the top of the file
import csv
import json
import uuid
from math import ceil, floor
from io import StringIO

app = Flask(__name__)
app.secret_key = 'your_secret_key'

basedir = os.path.abspath(os.path.dirname(__file__))
STOCK_SNAPSHOT_INDEX_FILE = os.path.join(basedir, "stock_costs_snapshots.json")
STOCK_SNAPSHOT_DELETED_WEEKS_FILE = os.path.join(basedir, "stock_costs_deleted_snapshot_weeks.json")
STOCK_SNAPSHOT_DIR = os.path.join(basedir, "stock_costs_snapshots")

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

# Make sure the format_number filter is registered correctly
@app.template_filter('format_number')
def format_number_filter(value):
    """Format a number with commas as thousands separators."""
    try:
        return "{:,.2f}".format(float(value))
    except (TypeError, ValueError):
        return "0.00"


@app.template_filter('duration')
def duration_filter(value):
    """Format a duration in seconds without rounding it to minutes."""
    try:
        seconds = max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return "N/A"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"

# Register filters explicitly
app.jinja_env.filters['abs'] = abs_filter
app.jinja_env.filters['format_number'] = format_number_filter


def slugify_key(value):
    if not value:
        return "item"
    slug = re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')
    return slug or "item"


def load_stock_snapshots():
    if not os.path.exists(STOCK_SNAPSHOT_INDEX_FILE):
        return []
    try:
        with open(STOCK_SNAPSHOT_INDEX_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_stock_snapshots(snapshots):
    try:
        with open(STOCK_SNAPSHOT_INDEX_FILE, "w") as f:
            json.dump(snapshots, f)
        return True
    except OSError:
        return False


def load_deleted_stock_snapshot_weeks():
    if not os.path.exists(STOCK_SNAPSHOT_DELETED_WEEKS_FILE):
        return set()
    try:
        with open(STOCK_SNAPSHOT_DELETED_WEEKS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {week for week in data if week}
    except (json.JSONDecodeError, OSError):
        pass
    return set()


def save_deleted_stock_snapshot_weeks(week_keys):
    try:
        with open(STOCK_SNAPSHOT_DELETED_WEEKS_FILE, "w") as f:
            json.dump(sorted(week_keys), f)
        return True
    except OSError:
        return False


def safe_stock_snapshot_file_path(filename):
    if not filename or os.path.basename(filename) != filename:
        return None
    snapshot_dir = os.path.abspath(STOCK_SNAPSHOT_DIR)
    candidate = os.path.abspath(os.path.join(snapshot_dir, filename))
    try:
        if os.path.commonpath([snapshot_dir, candidate]) != snapshot_dir:
            return None
    except ValueError:
        return None
    return candidate


def last_sunday(year, month):
    last_day = monthrange(year, month)[1]
    target = date(year, month, last_day)
    return target - timedelta(days=(target.weekday() + 1) % 7)


def is_bst_utc(utc_value):
    bst_start = datetime.combine(last_sunday(utc_value.year, 3), time(1, 0))
    bst_end = datetime.combine(last_sunday(utc_value.year, 10), time(1, 0))
    return bst_start <= utc_value < bst_end


def is_bst_london_local(local_value):
    bst_start = datetime.combine(last_sunday(local_value.year, 3), time(2, 0))
    bst_end = datetime.combine(last_sunday(local_value.year, 10), time(2, 0))
    return bst_start <= local_value < bst_end


def london_now():
    utc_now = datetime.utcnow()
    return utc_now + (timedelta(hours=1) if is_bst_utc(utc_now) else timedelta())


def utc_to_london(value):
    if not value:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value + (timedelta(hours=1) if is_bst_utc(value) else timedelta())


@app.template_filter('london_time')
def london_time_filter(value, fmt="%H:%M"):
    local_value = utc_to_london(value)
    return local_value.strftime(fmt) if local_value else "-"


def london_period_utc_bounds(year, month=None, day=None):
    if day is not None:
        start_date = date(int(year), int(month), int(day))
        end_date = start_date + timedelta(days=1)
    elif month is not None:
        start_date = date(int(year), int(month), 1)
        if int(month) == 12:
            end_date = date(int(year) + 1, 1, 1)
        else:
            end_date = date(int(year), int(month) + 1, 1)
    else:
        start_date = date(int(year), 1, 1)
        end_date = date(int(year) + 1, 1, 1)

    start_local = datetime.combine(start_date, time.min)
    end_local = datetime.combine(end_date, time.min)
    start_offset = timedelta(hours=1) if is_bst_london_local(start_local) else timedelta()
    end_offset = timedelta(hours=1) if is_bst_london_local(end_local) else timedelta()
    return (
        start_local - start_offset,
        end_local - end_offset,
    )

# Shared serial parsing helper (works with formats like "1059 - 6 - RB").
def serial_is_6ft(serial):
    if not serial:
        return False
    normalized = serial.replace(" ", "").upper()
    return normalized.endswith("-6") or "-6-" in normalized


def serial_is_lite(serial):
    if not serial:
        return False
    normalized = serial.replace(" ", "").upper()
    return normalized.endswith("-L")


TABLE_TYPE_CHAMPION = "champion"
TABLE_TYPE_LITE = "lite"
BODY_TABLE_TYPE_CODES = {
    TABLE_TYPE_CHAMPION: 1,
    TABLE_TYPE_LITE: 2,
}
BODY_TABLE_TYPE_FROM_CODE = {code: key for key, code in BODY_TABLE_TYPE_CODES.items()}
BODY_COLOR_CODES = {
    "black": 1,
    "rustic_oak": 2,
    "grey_oak": 3,
    "stone": 4,
    "rustic_black": 5,
}
BODY_COLOR_FROM_CODE = {code: key for key, code in BODY_COLOR_CODES.items()}
COLOR_SELECTOR_TO_KEY = {
    "Black": "black",
    "Rustic Oak": "rustic_oak",
    "Grey Oak": "grey_oak",
    "Stone": "stone",
    "Rustic Black": "rustic_black",
}


def table_type_from_serial(serial):
    return TABLE_TYPE_LITE if serial_is_lite(serial) else TABLE_TYPE_CHAMPION


def table_type_display_label(table_type):
    return "Lite" if table_type == TABLE_TYPE_LITE else "Champion"


def serial_size_display_label(serial):
    return "6ft" if serial_is_6ft(serial) else "7ft"


def clean_pod_serial_value(serial):
    cleaned = (serial or "").strip()
    if "**Pod Serial Number:" in cleaned:
        cleaned = cleaned.replace("**Pod Serial Number:", "").strip()
    return cleaned


def color_key_from_selector(color_name):
    return COLOR_SELECTOR_TO_KEY.get(color_name, "black")


def color_key_from_serial(serial):
    norm = (serial or "").replace(" ", "").upper()
    if "-GO" in norm:
        return "grey_oak"
    if "-O" in norm and "-GO" not in norm:
        return "rustic_oak"
    if "-C" in norm:
        return "stone"
    if "-RB" in norm:
        return "rustic_black"
    return "black"


def strip_table_serial_suffixes(serial, remove_color=True, remove_lite=True):
    cleaned = (serial or "").strip()
    if not cleaned:
        return cleaned

    if remove_color:
        # Remove one trailing color suffix (if present).
        cleaned = re.sub(r"\s*-\s*(GO|RB|O|C|B)\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    if remove_lite:
        cleaned = re.sub(r"\s*-\s*L\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def base_serial_for_pod_matching(serial):
    cleaned = strip_table_serial_suffixes(serial, remove_color=True, remove_lite=True)
    # Lite 7ft serials are stored like "<num> - 7 - L"; pods remain "<num>".
    cleaned = re.sub(r"\s*-\s*7\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def gully_parts_for_completion(serial_number):
    if serial_is_6ft(serial_number):
        return {"6ft Gully Set": 1}
    return dict(SEVEN_FOOT_GULLY_PARTS)


def _body_meta_type_key(body_id):
    return f"meta_body_type_{body_id}"


def _body_meta_color_key(body_id):
    return f"meta_body_color_{body_id}"


def save_body_build_metadata(body_id, table_type, color_key):
    type_code = BODY_TABLE_TYPE_CODES.get(table_type, BODY_TABLE_TYPE_CODES[TABLE_TYPE_CHAMPION])
    color_code = BODY_COLOR_CODES.get(color_key, BODY_COLOR_CODES["black"])
    payload = (
        (_body_meta_type_key(body_id), type_code),
        (_body_meta_color_key(body_id), color_code),
    )
    for meta_key, meta_count in payload:
        entry = TableStock.query.filter_by(type=meta_key).first()
        if not entry:
            entry = TableStock(type=meta_key, count=meta_count)
            db.session.add(entry)
        else:
            entry.count = meta_count


def get_body_build_metadata(body_entry):
    table_type = table_type_from_serial(body_entry.serial_number)
    color_key = color_key_from_serial(body_entry.serial_number)

    type_entry = TableStock.query.filter_by(type=_body_meta_type_key(body_entry.id)).first()
    color_entry = TableStock.query.filter_by(type=_body_meta_color_key(body_entry.id)).first()

    if type_entry:
        table_type = BODY_TABLE_TYPE_FROM_CODE.get(type_entry.count, table_type)
    if color_entry:
        color_key = BODY_COLOR_FROM_CODE.get(color_entry.count, color_key)

    return table_type, color_key


def delete_body_build_metadata(body_id):
    for meta_key in (_body_meta_type_key(body_id), _body_meta_color_key(body_id)):
        entry = TableStock.query.filter_by(type=meta_key).first()
        if entry:
            db.session.delete(entry)


def body_parts_for_completion(serial_number, table_type, laminate_color_key):
    laminate_label = LAMINATE_COLOR_KEY_TO_LABEL.get(laminate_color_key, "Black")
    laminate_part_name = f"Laminate - {laminate_label}"
    gully_parts = gully_parts_for_completion(serial_number)

    if table_type == TABLE_TYPE_LITE:
        lite_parts = {
            laminate_part_name: 4,
            "Table legs": 4,
            "Feet": 4,
            "Color ball trim": 1,
            "Aluminum corner": 4,
            "4.2 x 16 No2 Self Tapping Screw": 19,
            "Latch": 12,
        }
        lite_parts.update(gully_parts)
        if serial_is_6ft(serial_number):
            lite_parts["6ft Bag of Bolts"] = 1
        else:
            lite_parts["7ft Bag of Bolts"] = 1
            lite_parts["7ft Ply Supports"] = 2
        return lite_parts

    parts_to_deduct = {
        "Large Ramp": 1,
        "Paddle": 1,
        laminate_part_name: 4,
        "Spring Mount": 1,
        "Spring Holder": 1,
        "Small Ramp": 1,
        "Cue Ball Separator": 1,
        "Bushing": 2,
        "Table legs": 4,
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
        "Spring": 1,
        "Handle Tube": 1,
        "Latch": 12,
        "7ft Bag of Bolts": 1,
        "7ft Ply Supports": 2,
        BRAD_NAILS_PART_NAME: 0.25
    }

    if serial_is_6ft(serial_number):
        parts_to_deduct.pop("Large Ramp", None)
        parts_to_deduct.pop("Cue Ball Separator", None)
        parts_to_deduct.pop("Small Ramp", None)
        parts_to_deduct.pop("Ramp 170mm", None)
        parts_to_deduct.pop("Ramp 158mm", None)
        parts_to_deduct["6ft Large Ramp"] = 1
        parts_to_deduct["6ft Cue Ball Separator"] = 1
        parts_to_deduct.pop("7ft Bag of Bolts", None)
        parts_to_deduct.pop("7ft Ply Supports", None)
        parts_to_deduct["6ft Bag of Bolts"] = 1

    parts_to_deduct.update(gully_parts)
    return parts_to_deduct


def body_stock_type_key(size_label, table_type, color_key):
    normalized_size = (size_label or "7ft").lower()
    normalized_color = (color_key or "black").lower().replace(" ", "_")
    if table_type == TABLE_TYPE_LITE:
        return f"body_{normalized_size}_lite"
    return f"body_{normalized_size}_{normalized_color}"

# Expose slugify_key to Jinja templates for form field names
app.jinja_env.filters['slugify_key'] = slugify_key

LAMINATE_COLOR_LABELS = ["Black", "Rustic Oak", "Grey Oak", "Stone", "Rustic Black"]
LAMINATE_PART_NAMES = [f"Laminate - {label}" for label in LAMINATE_COLOR_LABELS]
LAMINATE_COLOR_KEY_TO_LABEL = {
    "black": "Black",
    "rustic_oak": "Rustic Oak",
    "grey_oak": "Grey Oak",
    "stone": "Stone",
    "rustic_black": "Rustic Black",
}
BODIES_QUICK_ADD_PARTS = [
    {"label": "Black Laminate", "part_name": "Laminate - Black", "hardware": False},
    {"label": "7ft Bolt Bag", "part_name": "7ft Bag of Bolts", "hardware": True},
    {"label": "6ft Bolt Bag", "part_name": "6ft Bag of Bolts", "hardware": True},
]
FELT_PART_NAME = "Felt"
LEGACY_FELT_PART_NAMES = ("7ft Felt", "6ft Felt")
PACKAGING_PART_NAMES = [
    "Straps",
    "Metal Poles",
    "Body Pallets",
    "Top Rail Pallets 7ft",
    "Top Rail Pallets 6ft",
    "Blue Pallets",
]
LEGACY_PRINTED_PART_RENAMES = {
    "Ball Gullies 1 (Untouched)": "Ball Gullies 1",
    "Top rail trim long length": "Top Rail Trim - 822mm",
    "Top Rail Trim 822mm": "Top Rail Trim - 822mm",
    "Top Rail Trim 814mm Left": "Top Rail Trim - 814mm (Left)",
    "Top Rail Trim 814mm Right": "Top Rail Trim - 814mm (Right)",
}
LEGACY_PRINTED_PART_SPLITS = {
    "Top rail trim short length": (
        "Top Rail Trim - 814mm (Left)",
        "Top Rail Trim - 814mm (Right)",
    ),
}
SEVEN_FOOT_GULLY_PARTS = {
    "Ball Gullies 1": 2,
    "Ball Gullies 2": 1,
    "Ball Gullies 3": 1,
    "Ball Gullies 4": 1,
    "Ball Gullies 5": 1,
}
TOP_RAIL_TRIM_PARTS = {
    "Top Rail Trim - 814mm (Left)": 2,
    "Top Rail Trim - 814mm (Right)": 2,
    "Top Rail Trim - 822mm": 2,
}
MANUAL_ONLY_CHINESE_PARTS = [
    "Gullies Untouched",
]
SIX_FOOT_ONLY_CHINESE_PARTS = [
    "6ft Gully Set",
]
ALL_CHINESE_PARTS = [
    "Table legs",
    *SEVEN_FOOT_GULLY_PARTS.keys(),
    *MANUAL_ONLY_CHINESE_PARTS,
    *SIX_FOOT_ONLY_CHINESE_PARTS,
    "Feet",
    "Triangle trim",
    "White ball return trim",
    "Color ball trim",
    "Ball window trim",
    "Aluminum corner",
    "Chrome corner",
    *TOP_RAIL_TRIM_PARTS.keys(),
    "Ramp 170mm",
    "Ramp 158mm",
    "Ramp 918mm",
    "Ramp 376mm",
    "Chrome handles",
    "Center pockets",
    "Corner pockets",
    "Sticker Set",
]
CHINESE_PARTS_CAPACITY = {
    "Table legs": 4,
    **SEVEN_FOOT_GULLY_PARTS,
    "Feet": 4,
    "Triangle trim": 1,
    "White ball return trim": 1,
    "Color ball trim": 1,
    "Ball window trim": 1,
    "Aluminum corner": 4,
    "Chrome corner": 4,
    **TOP_RAIL_TRIM_PARTS,
    "Ramp 170mm": 1,
    "Ramp 158mm": 1,
    "Ramp 918mm": 1,
    "Ramp 376mm": 1,
    "Chrome handles": 1,
    "Center pockets": 2,
    "Corner pockets": 4,
    "Sticker Set": 1,
}
CHINESE_PARTS_ALLOW_NEGATIVE = set(ALL_CHINESE_PARTS)
CHINESE_PARTS_ORDER_MORE_PART = "Sticker Set"
CHINESE_PARTS_ORDER_MORE_THRESHOLD = 300
CHINESE_PARTS_ON_ORDER_FILE = os.path.join(basedir, "on_order_chinese_parts.json")
HIDDEN_BODY_PICKER_PODS_FILE = os.path.join(basedir, "hidden_body_picker_pods.json")
BODY_PICKER_HIDE_MIN_AGE_DAYS = 60
BRAD_NAILS_PART_NAME = "18G 10mm Brad Nails"
BRAD_NAILS_UNITS_PER_STRIP = 4  # Track quarter-strip usage (0.25 = 1 unit, 0.5 = 2 units)


def allows_negative_inventory(part_name):
    return part_name in CHINESE_PARTS_ALLOW_NEGATIVE

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


class TableStockLog(db.Model):
    __tablename__ = 'table_stock_log'

    id = db.Column(db.Integer, primary_key=True)
    stock_type = db.Column(db.String(50), nullable=False, index=True)
    action_type = db.Column(db.String(30), nullable=False)
    worker = db.Column(db.String(50), nullable=False, default="Unknown")
    delta = db.Column(db.Integer, nullable=False, default=0)
    count_before = db.Column(db.Integer, nullable=False, default=0)
    count_after = db.Column(db.Integer, nullable=False, default=0)
    note = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=london_now)


TABLE_STOCK_ACTION_LABELS = {
    "add": "Added",
    "remove": "Removed",
    "set": "Set count",
    "complete_body": "Body completed",
    "delete_body": "Body deleted",
    "complete_top_rail": "Top rail completed",
    "delete_top_rail": "Top rail deleted",
    "complete_cushion_set": "Cushion set completed",
    "correction": "Correction",
}


def ensure_table_stock_log_table():
    if app.config.get("_table_stock_log_table_ready"):
        return
    TableStockLog.__table__.create(db.engine, checkfirst=True)
    app.config["_table_stock_log_table_ready"] = True


def table_stock_type_label(stock_type):
    stock_type = stock_type or ""
    if stock_type.startswith("body_"):
        parts = stock_type.split("_")
        size_label = parts[1] if len(parts) > 1 else ""
        if len(parts) > 2 and parts[2] == "lite":
            return f"{size_label} Lite Body"
        color_key = "_".join(parts[2:]) if len(parts) > 2 else "black"
        color_label = LAMINATE_COLOR_KEY_TO_LABEL.get(color_key, color_key.replace("_", " ").title())
        return f"{size_label} {color_label} Body"

    if stock_type.startswith("top_rail_"):
        parts = stock_type.split("_")
        size_label = parts[2] if len(parts) > 2 else ""
        color_key = "_".join(parts[3:]) if len(parts) > 3 else "black"
        color_label = LAMINATE_COLOR_KEY_TO_LABEL.get(color_key, color_key.replace("_", " ").title())
        return f"{size_label} {color_label} Top Rail"

    if stock_type.startswith("cushion_set_"):
        size_label = stock_type.replace("cushion_set_", "")
        return f"{size_label} Cushion Set"

    return stock_type.replace("_", " ").title()


def table_stock_action_label(action_type):
    return TABLE_STOCK_ACTION_LABELS.get(
        action_type,
        (action_type or "").replace("_", " ").title() or "Stock change"
    )


def record_table_stock_log(stock_type, action_type, worker, delta, count_before, count_after, note=None):
    try:
        delta = int(delta)
        count_before = int(count_before)
        count_after = int(count_after)
    except (TypeError, ValueError):
        return None

    if delta == 0 and count_before == count_after:
        return None

    ensure_table_stock_log_table()
    log_entry = TableStockLog(
        stock_type=stock_type,
        action_type=action_type,
        worker=(worker or "Unknown").strip() or "Unknown",
        delta=delta,
        count_before=count_before,
        count_after=count_after,
        note=(note or "").strip() or None,
        created_at=london_now()
    )
    db.session.add(log_entry)
    return log_entry


class StockItemCost(db.Model):
    __tablename__ = 'stock_item_cost'
    id = db.Column(db.Integer, primary_key=True)
    item_key = db.Column(db.String(120), unique=True, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False, default=0.0)
    shipping_cost = db.Column(db.Float, nullable=False, default=0.0)
    labour_cost = db.Column(db.Float, nullable=False, default=0.0)

    def combined_cost(self):
        return (self.unit_cost or 0.0) + (self.shipping_cost or 0.0) + (self.labour_cost or 0.0)


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


class BonusGoal(db.Model):
    __tablename__ = 'bonus_goal'
    __table_args__ = (
        db.UniqueConstraint('area', 'worker_name', 'year', 'month', name='uq_bonus_goal_area_worker_month'),
    )

    id = db.Column(db.Integer, primary_key=True)
    area = db.Column(db.String(30), nullable=False)
    worker_name = db.Column(db.String(50), nullable=False)
    target_count = db.Column(db.Integer, default=0, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)


BONUS_GOAL_AREAS = [
    {"key": "pods", "label": "Pods"},
    {"key": "bodies", "label": "Bodies"},
    {"key": "top_rails", "label": "Top Rails"},
    {"key": "cnc", "label": "CNC"},
]
BONUS_GOAL_AREA_LABELS = {area["key"]: area["label"] for area in BONUS_GOAL_AREAS}


def ensure_bonus_goal_tables():
    BonusGoal.__table__.create(db.engine, checkfirst=True)


def cnc_completed_quantity_total(year=None, month=None, day=None):
    filters = [
        CncQueueItem.status == CNC_STATUS_COMPLETED,
        CncQueueItem.completed_at.isnot(None),
    ]
    if year is not None or month is not None or day is not None:
        now = london_now()
        target_year = int(year or now.year)
        target_month = int(month or now.month)
        if day is not None:
            start_utc, end_utc = london_period_utc_bounds(target_year, target_month, int(day))
        elif month is not None:
            start_utc, end_utc = london_period_utc_bounds(target_year, target_month)
        else:
            start_utc, end_utc = london_period_utc_bounds(target_year)
        filters.extend([
            CncQueueItem.completed_at >= start_utc,
            CncQueueItem.completed_at < end_utc,
        ])

    total = (
        db.session.query(func.coalesce(func.sum(CncJob.quantity), 0))
        .select_from(CncQueueItem)
        .join(CncJob, CncQueueItem.job_id == CncJob.id)
        .filter(*filters)
        .scalar() or 0
    )
    return int(total or 0)


def cnc_monthly_cut_file_history():
    completed_rows = (
        db.session.query(CncQueueItem.completed_at, CncJob.name, CncJob.quantity)
        .select_from(CncQueueItem)
        .join(CncJob, CncQueueItem.job_id == CncJob.id)
        .filter(
            CncQueueItem.status == CNC_STATUS_COMPLETED,
            CncQueueItem.completed_at.isnot(None),
        )
        .order_by(CncQueueItem.completed_at.desc(), CncQueueItem.id.desc())
        .all()
    )

    months = {}
    for completed_at, job_name, quantity in completed_rows:
        completed_local = utc_to_london(completed_at)
        if not completed_local:
            continue

        month_start = date(completed_local.year, completed_local.month, 1)
        month_key = month_start.strftime("%Y-%m")
        month_data = months.setdefault(month_key, {
            "key": month_key,
            "label": month_start.strftime("%B %Y"),
            "sort_date": month_start,
            "total_quantity": 0,
            "total_runs": 0,
            "files_map": {},
        })

        try:
            cut_quantity = int(quantity or 1)
        except (TypeError, ValueError):
            cut_quantity = 1
        cut_quantity = max(cut_quantity, 1)

        file_name = (job_name or "").strip() or "Unknown file"
        file_data = month_data["files_map"].setdefault(file_name, {
            "name": file_name,
            "quantity": 0,
            "runs": 0,
        })
        file_data["quantity"] += cut_quantity
        file_data["runs"] += 1
        month_data["total_quantity"] += cut_quantity
        month_data["total_runs"] += 1

    history = sorted(months.values(), key=lambda month: month["sort_date"], reverse=True)
    for month_data in history:
        files = list(month_data["files_map"].values())
        files.sort(key=lambda file_data: (-file_data["quantity"], file_data["name"].lower()))
        month_data["files"] = files
        month_data["file_count"] = len(files)
        del month_data["files_map"]
        del month_data["sort_date"]

    return history


def elapsed_weekdays_in_month(target_date):
    month_start = target_date.replace(day=1)
    return sum(
        1
        for offset in range((target_date - month_start).days + 1)
        if (month_start + timedelta(days=offset)).weekday() < 5
    )


def remaining_weekdays_in_month(target_date):
    month_end = date(target_date.year, target_date.month, monthrange(target_date.year, target_date.month)[1])
    return sum(
        1
        for offset in range((month_end - target_date).days + 1)
        if (target_date + timedelta(days=offset)).weekday() < 5
    )


def weekdays_in_month(year, month):
    return sum(
        1
        for day in range(1, monthrange(int(year), int(month))[1] + 1)
        if date(int(year), int(month), day).weekday() < 5
    )


def next_bonus_goal_month(year, month):
    if int(month) == 12:
        return int(year) + 1, 1
    return int(year), int(month) + 1


def make_bonus_goal_progress_row(area, worker_name, current_count, target_count, year, month, next_bonus=False):
    target_count = int(target_count or 0)
    current_count = int(current_count or 0)
    percentage = round((current_count / target_count) * 100) if target_count else 0
    remaining = max(target_count - current_count, 0)
    period_label = bonus_goal_month_label(year, month)
    return {
        "area": area,
        "area_label": BONUS_GOAL_AREA_LABELS.get(area, area.replace("_", " ").title()),
        "worker": worker_name,
        "current": current_count,
        "target": target_count,
        "percentage": percentage,
        "percentage_capped": min(percentage, 100),
        "remaining": remaining,
        "target_hit": current_count >= target_count,
        "period_year": int(year),
        "period_month": int(month),
        "period_label": period_label,
        "next_bonus": bool(next_bonus),
        "goal_key": f"{area}|{normalize_bonus_worker_name(worker_name)}|{int(year)}-{int(month):02d}|{target_count}",
    }


def bonus_goal_progress(area, year=None, month=None):
    ensure_bonus_goal_tables()
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)

    goals = (
        BonusGoal.query
        .filter_by(area=area, year=year, month=month, active=True)
        .filter(BonusGoal.target_count > 0)
        .order_by(BonusGoal.worker_name.asc())
        .all()
    )
    if not goals:
        return []

    counts = {}
    if area == "bodies":
        rows = (
            db.session.query(CompletedTable.worker, func.count(CompletedTable.id))
            .filter(
                extract('year', CompletedTable.date) == year,
                extract('month', CompletedTable.date) == month
            )
            .group_by(CompletedTable.worker)
            .all()
        )
        counts = {(worker or "Unknown"): count for worker, count in rows}
    elif area == "pods":
        rows = (
            db.session.query(CompletedPods.worker, func.count(CompletedPods.id))
            .filter(
                extract('year', CompletedPods.date) == year,
                extract('month', CompletedPods.date) == month
            )
            .group_by(CompletedPods.worker)
            .all()
        )
        counts = {(worker or "Unknown"): count for worker, count in rows}
    elif area == "top_rails":
        rows = (
            db.session.query(TopRail.worker, func.count(TopRail.id))
            .filter(
                extract('year', TopRail.date) == year,
                extract('month', TopRail.date) == month
            )
            .group_by(TopRail.worker)
            .all()
        )
        counts = {(worker or "Unknown"): count for worker, count in rows}
    elif area == "cnc":
        monthly_total = cnc_completed_quantity_total(year=year, month=month)
        counts = {goal.worker_name: monthly_total for goal in goals}

    progress_rows = []
    for goal in goals:
        current_count = counts.get(goal.worker_name, 0)
        progress_rows.append(make_bonus_goal_progress_row(
            area,
            goal.worker_name,
            current_count,
            goal.target_count,
            year,
            month
        ))

    return sorted(progress_rows, key=lambda row: (-row["percentage"], row["worker"].lower()))


def normalize_bonus_worker_name(name):
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def dashboard_bonus_progress(
    area,
    year=None,
    month=None,
    include_workers=None,
    exclude_workers=None,
    label_overrides=None
):
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)
    ensure_bonus_goal_tables()
    include_worker_keys = {
        normalize_bonus_worker_name(worker_name)
        for worker_name in (include_workers or [])
    }
    exclude_worker_keys = {
        normalize_bonus_worker_name(worker_name)
        for worker_name in (exclude_workers or [])
    }
    label_overrides_by_key = {
        normalize_bonus_worker_name(worker_name): label
        for worker_name, label in (label_overrides or {}).items()
    }
    next_year, next_month = next_bonus_goal_month(year, month)
    next_goals = {
        normalize_bonus_worker_name(goal.worker_name): goal
        for goal in BonusGoal.query
        .filter_by(area=area, year=next_year, month=next_month, active=True)
        .filter(BonusGoal.target_count > 0)
        .all()
    }

    def worker_is_visible(worker_name):
        worker_key = normalize_bonus_worker_name(worker_name)
        if include_worker_keys and worker_key not in include_worker_keys:
            return False
        return worker_key not in exclude_worker_keys

    def display_name_for(row):
        worker_key = normalize_bonus_worker_name(row.get("worker"))
        base_name = label_overrides_by_key.get(worker_key, row.get("worker"))
        if row.get("next_bonus"):
            if base_name.endswith(" Goal"):
                base_name = base_name[:-5]
            return f"{base_name} - {row['period_label']} Goal"
        return base_name

    rows = []
    for row in bonus_goal_progress(area, year, month):
        if not worker_is_visible(row.get("worker")):
            continue

        next_goal = next_goals.get(normalize_bonus_worker_name(row.get("worker")))
        if row.get("target_hit") and next_goal:
            early_count = max(row.get("current", 0) - row.get("target", 0), 0)
            next_row = make_bonus_goal_progress_row(
                area,
                next_goal.worker_name,
                early_count,
                next_goal.target_count,
                next_year,
                next_month,
                next_bonus=True
            )
            if worker_is_visible(next_row.get("worker")):
                next_row["display_worker"] = display_name_for(next_row)
                rows.append(next_row)
            continue

        updated = dict(row)
        updated["display_worker"] = display_name_for(updated)
        rows.append(updated)

    return sorted(
        rows,
        key=lambda row: (
            1 if row.get("next_bonus") else 0,
            -row.get("percentage", 0),
            row.get("display_worker", row.get("worker", "")).lower()
        )
    )


def bonus_goal_month_label(year=None, month=None):
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)
    return date(year=year, month=month, day=1).strftime("%B %Y")




class HardwarePart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    initial_count = db.Column(db.Integer, default=0)
    used_per_table = db.Column(db.Float, default=0.0000)

class PartThreshold(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_name = db.Column(db.String(100), unique=True, nullable=False)
    threshold = db.Column(db.Integer, default=0, nullable=False)
    alerts_enabled = db.Column(db.Boolean, default=True, nullable=False)


def ensure_part_threshold_schema():
    if app.config.get("_part_threshold_schema_checked"):
        return

    PartThreshold.__table__.create(db.engine, checkfirst=True)
    columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(part_threshold)")).fetchall()
    }
    if "alerts_enabled" not in columns:
        db.session.execute(
            text("ALTER TABLE part_threshold ADD COLUMN alerts_enabled BOOLEAN NOT NULL DEFAULT 1")
        )
        db.session.commit()

    app.config["_part_threshold_schema_checked"] = True


def _coerce_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _split_legacy_quantity(value):
    total = _coerce_int(value, 0)
    left_qty = (total + 1) // 2
    right_qty = total // 2
    return left_qty, right_qty


def default_chinese_parts_on_order():
    return {
        "parts": {},
        "gullies_units": 0,
        "payments": {},
        "manual_suppliers": {},
        "last_target_tables": None,
        "arrivals": [],
    }


def load_chinese_parts_on_order():
    if not os.path.exists(CHINESE_PARTS_ON_ORDER_FILE):
        return default_chinese_parts_on_order()
    try:
        with open(CHINESE_PARTS_ON_ORDER_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_chinese_parts_on_order()
    return data if isinstance(data, dict) else default_chinese_parts_on_order()


def load_hidden_body_picker_pod_ids():
    if not os.path.exists(HIDDEN_BODY_PICKER_PODS_FILE):
        return set()
    try:
        with open(HIDDEN_BODY_PICKER_PODS_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()

    raw_ids = data.get("pod_ids", []) if isinstance(data, dict) else data
    hidden_ids = set()
    if not isinstance(raw_ids, list):
        return hidden_ids
    for raw_id in raw_ids:
        try:
            hidden_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    return hidden_ids


def save_hidden_body_picker_pod_ids(hidden_ids):
    payload = {"pod_ids": sorted(int(pod_id) for pod_id in hidden_ids)}
    with open(HIDDEN_BODY_PICKER_PODS_FILE, "w") as f:
        json.dump(payload, f, indent=2)


def ensure_legacy_inventory_names_migrated():
    if app.config.get("_legacy_inventory_names_migrated"):
        return

    try:
        ensure_part_threshold_schema()
        migration_changed = False

        for old_name, new_name in LEGACY_PRINTED_PART_RENAMES.items():
            printed_entries = PrintedPartsCount.query.filter_by(part_name=old_name).all()
            for entry in printed_entries:
                entry.part_name = new_name
                migration_changed = True

            old_threshold = PartThreshold.query.filter_by(part_name=old_name).first()
            if old_threshold:
                new_threshold = PartThreshold.query.filter_by(part_name=new_name).first()
                if new_threshold:
                    new_threshold.threshold = max(new_threshold.threshold or 0, old_threshold.threshold or 0)
                    if not old_threshold.alerts_enabled:
                        new_threshold.alerts_enabled = False
                    db.session.delete(old_threshold)
                else:
                    old_threshold.part_name = new_name
                migration_changed = True

            old_cost_key = f"parts_inventory__{slugify_key(old_name)}"
            new_cost_key = f"parts_inventory__{slugify_key(new_name)}"
            old_cost = StockItemCost.query.filter_by(item_key=old_cost_key).first()
            if old_cost:
                new_cost = StockItemCost.query.filter_by(item_key=new_cost_key).first()
                if new_cost:
                    if not (new_cost.unit_cost or 0.0):
                        new_cost.unit_cost = old_cost.unit_cost
                    if not (new_cost.shipping_cost or 0.0):
                        new_cost.shipping_cost = old_cost.shipping_cost
                    if not (new_cost.labour_cost or 0.0):
                        new_cost.labour_cost = old_cost.labour_cost
                    db.session.delete(old_cost)
                else:
                    old_cost.item_key = new_cost_key
                migration_changed = True

        for old_name, new_names in LEGACY_PRINTED_PART_SPLITS.items():
            split_entries = (
                PrintedPartsCount.query
                .filter_by(part_name=old_name)
                .order_by(PrintedPartsCount.date.asc(), PrintedPartsCount.time.asc(), PrintedPartsCount.id.asc())
                .all()
            )
            if split_entries:
                left_name, right_name = new_names
                for entry in split_entries:
                    left_qty, right_qty = _split_legacy_quantity(entry.count)
                    entry.part_name = left_name
                    entry.count = left_qty
                    cloned_entry = PrintedPartsCount(
                        part_name=right_name,
                        count=right_qty,
                        date=entry.date,
                        time=entry.time
                    )
                    db.session.add(cloned_entry)
                migration_changed = True

            old_threshold = PartThreshold.query.filter_by(part_name=old_name).first()
            if old_threshold:
                for new_name in new_names:
                    threshold_entry = PartThreshold.query.filter_by(part_name=new_name).first()
                    if threshold_entry:
                        threshold_entry.threshold = max(threshold_entry.threshold or 0, old_threshold.threshold or 0)
                        if not old_threshold.alerts_enabled:
                            threshold_entry.alerts_enabled = False
                    else:
                        db.session.add(PartThreshold(
                            part_name=new_name,
                            threshold=old_threshold.threshold or 0,
                            alerts_enabled=bool(old_threshold.alerts_enabled)
                        ))
                db.session.delete(old_threshold)
                migration_changed = True

            old_cost_key = f"parts_inventory__{slugify_key(old_name)}"
            old_cost = StockItemCost.query.filter_by(item_key=old_cost_key).first()
            if old_cost:
                for new_name in new_names:
                    new_cost_key = f"parts_inventory__{slugify_key(new_name)}"
                    new_cost = StockItemCost.query.filter_by(item_key=new_cost_key).first()
                    if new_cost:
                        if not (new_cost.unit_cost or 0.0):
                            new_cost.unit_cost = old_cost.unit_cost
                        if not (new_cost.shipping_cost or 0.0):
                            new_cost.shipping_cost = old_cost.shipping_cost
                        if not (new_cost.labour_cost or 0.0):
                            new_cost.labour_cost = old_cost.labour_cost
                    else:
                        db.session.add(StockItemCost(
                            item_key=new_cost_key,
                            unit_cost=old_cost.unit_cost,
                            shipping_cost=old_cost.shipping_cost,
                            labour_cost=old_cost.labour_cost
                        ))
                db.session.delete(old_cost)
                migration_changed = True

        if os.path.exists(CHINESE_PARTS_ON_ORDER_FILE):
            try:
                with open(CHINESE_PARTS_ON_ORDER_FILE, "r") as f:
                    on_order_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                on_order_data = None

            if isinstance(on_order_data, dict):
                parts_data = on_order_data.get("parts")
                if isinstance(parts_data, dict):
                    file_changed = False
                    for old_name, new_name in LEGACY_PRINTED_PART_RENAMES.items():
                        if old_name not in parts_data:
                            continue
                        old_value = parts_data.pop(old_name, 0)
                        parts_data[new_name] = _coerce_int(parts_data.get(new_name), 0) + _coerce_int(old_value, 0)
                        file_changed = True
                    for old_name, new_names in LEGACY_PRINTED_PART_SPLITS.items():
                        if old_name not in parts_data:
                            continue
                        old_value = parts_data.pop(old_name, 0)
                        left_name, right_name = new_names
                        left_qty, right_qty = _split_legacy_quantity(old_value)
                        parts_data[left_name] = _coerce_int(parts_data.get(left_name), 0) + left_qty
                        parts_data[right_name] = _coerce_int(parts_data.get(right_name), 0) + right_qty
                        file_changed = True
                    if file_changed:
                        try:
                            with open(CHINESE_PARTS_ON_ORDER_FILE, "w") as f:
                                json.dump(on_order_data, f)
                        except OSError:
                            pass

        if migration_changed:
            db.session.commit()
        app.config["_legacy_inventory_names_migrated"] = True
    except OperationalError:
        db.session.rollback()
    except Exception:
        db.session.rollback()
        raise


@app.before_request
def run_legacy_inventory_name_migrations():
    ensure_legacy_inventory_names_migrated()
    ensure_table_stock_log_table()


@app.after_request
def prevent_stale_count_pages(response):
    if request.method == "POST" and response.status_code in (301, 302):
        response.status_code = 303

    content_type = response.headers.get("Content-Type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


class CncJob(db.Model):
    __tablename__ = 'cnc_job'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    queue_items = db.relationship('CncQueueItem', backref='job', lazy=True, cascade='all, delete-orphan')


class CncQueueItem(db.Model):
    __tablename__ = 'cnc_queue_item'
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('cnc_job.id'), nullable=False)
    machine_number = db.Column(db.Integer, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default='queued')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    completed_by = db.Column(db.String(50), nullable=True)


CNC_MACHINE_NUMBERS = [1, 2, 3, 4]
CNC_STATUS_QUEUED = "queued"
CNC_STATUS_COMPLETED = "completed"
CNC_QUEUE_LOW_NOTIFY_THRESHOLD = 3


def ensure_cnc_tables():
    CncJob.__table__.create(db.engine, checkfirst=True)
    CncQueueItem.__table__.create(db.engine, checkfirst=True)


def _coerce_positive_int_list(values):
    parsed = []
    for value in values or []:
        try:
            as_int = int(value)
        except (TypeError, ValueError):
            continue
        if as_int > 0:
            parsed.append(as_int)
    return sorted(set(parsed))


def _cnc_reindex_machine(machine_number):
    queued_items = (
        CncQueueItem.query
        .filter_by(machine_number=machine_number, status=CNC_STATUS_QUEUED)
        .order_by(CncQueueItem.position.asc(), CncQueueItem.id.asc())
        .all()
    )
    for index, item in enumerate(queued_items, start=1):
        item.position = index


def _cnc_queue_snapshot():
    queues = {machine: [] for machine in CNC_MACHINE_NUMBERS}
    queued_items = (
        CncQueueItem.query
        .options(joinedload(CncQueueItem.job))
        .filter(CncQueueItem.status == CNC_STATUS_QUEUED)
        .order_by(CncQueueItem.machine_number.asc(), CncQueueItem.position.asc(), CncQueueItem.id.asc())
        .all()
    )
    for item in queued_items:
        if item.machine_number in queues:
            queues[item.machine_number].append(item)
    return queues


def _cnc_queue_count(machine_number):
    return (
        db.session.query(func.count(CncQueueItem.id))
        .filter_by(machine_number=machine_number, status=CNC_STATUS_QUEUED)
        .scalar() or 0
    )


def _cnc_capture_queue_counts(machine_numbers=None):
    target_machines = machine_numbers or CNC_MACHINE_NUMBERS
    return {
        machine_number: _cnc_queue_count(machine_number)
        for machine_number in target_machines
        if machine_number in CNC_MACHINE_NUMBERS
    }


def _send_cnc_low_queue_notification(machine_number, new_count):
    message = f"CNC {machine_number} queue is low ({new_count} queued jobs)."
    try:
        requests.post(
            "https://ntfy.sh/PoolTableTracker",
            data=message,
            headers={"Title": "CNC Queue Warning", "Priority": "high"}
        )
    except requests.RequestException as error:
        print(f"Ntfy notification failed for CNC queue warning: {error}")


def _cnc_notify_low_queue_transitions(previous_counts):
    for machine_number, old_count in (previous_counts or {}).items():
        if machine_number not in CNC_MACHINE_NUMBERS:
            continue
        new_count = _cnc_queue_count(machine_number)
        if old_count >= CNC_QUEUE_LOW_NOTIFY_THRESHOLD and new_count < CNC_QUEUE_LOW_NOTIFY_THRESHOLD:
            _send_cnc_low_queue_notification(machine_number, new_count)


CNC_WOOD_LOG_SESSION_KEY = "cnc_wood_logged_items"
CNC_WOOD_COMPONENT_LABELS = {
    "body": "Body",
    "pod_sides": "Pod Sides",
    "bases": "Bases",
    "top_rail_short": "Top Rail Pieces Short",
    "top_rail_long": "Top Rail Pieces Long",
}


def _get_or_create_mdf_inventory():
    inventory = MDFInventory.query.first()
    if not inventory:
        inventory = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
        db.session.add(inventory)
    return inventory


def _cnc_wood_job_details(job_name):
    normalized = re.sub(r"[^a-z0-9]+", " ", (job_name or "").lower()).strip()
    if not normalized:
        return None
    tokens = set(normalized.split())

    size = None
    if "7ft" in tokens or ("7" in tokens and tokens.intersection({"ft", "foot", "feet"})):
        size = "7ft"
    elif "6ft" in tokens or ("6" in tokens and tokens.intersection({"ft", "foot", "feet"})):
        size = "6ft"
    if not size:
        return None

    has_top_rail = (
        ("top" in tokens and tokens.intersection({"rail", "rails"}))
        or tokens.intersection({"toprail", "toprails"})
    )
    component = None
    if has_top_rail and "long" in tokens:
        component = "top_rail_long"
    elif has_top_rail and "short" in tokens:
        component = "top_rail_short"
    elif "pod" in tokens and tokens.intersection({"side", "sides"}):
        component = "pod_sides"
    elif tokens.intersection({"base", "bases"}):
        component = "bases"
    elif "black" in tokens and tokens.intersection({"side", "sides"}):
        component = "body"
    elif tokens.intersection({"body", "bodies"}):
        component = "body"

    if not component:
        return None

    label = CNC_WOOD_COMPONENT_LABELS[component]
    return {
        "size": size,
        "component": component,
        "section": f"{size} - {label}",
    }


def _wood_month_bounds(target_date):
    month_start = date(target_date.year, target_date.month, 1)
    month_end = date(target_date.year, target_date.month, monthrange(target_date.year, target_date.month)[1])
    return month_start, month_end


def _get_or_create_monthly_wood_entry(section, target_date, current_time):
    month_start, month_end = _wood_month_bounds(target_date)
    entry = (
        WoodCount.query
        .filter(
            WoodCount.section == section,
            WoodCount.date >= month_start,
            WoodCount.date <= month_end
        )
        .order_by(WoodCount.date.asc(), WoodCount.id.asc())
        .first()
    )
    if not entry:
        entry = WoodCount(section=section, count=0, date=month_start, time=current_time)
        db.session.add(entry)
    return entry


def _combine_wood_entries(entries):
    combined = defaultdict(int)
    for entry in entries:
        section = entry.get("section")
        try:
            count = int(entry.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        if section and count:
            combined[section] += count
    return [
        {"section": section, "count": count}
        for section, count in combined.items()
        if count
    ]


def _apply_wood_count_entries(entries, inventory_deltas=None, inventory=None, log_date=None, log_time=None):
    now = london_now()
    log_date = log_date or now.date()
    log_time = log_time or now.time()
    inventory = inventory or _get_or_create_mdf_inventory()
    entries = _combine_wood_entries(entries)
    inventory_deltas = {
        field: int(delta)
        for field, delta in (inventory_deltas or {}).items()
        if int(delta) != 0
    }

    monthly_entries = {}
    for entry in entries:
        monthly_entry = _get_or_create_monthly_wood_entry(entry["section"], log_date, log_time)
        next_count = monthly_entry.count + entry["count"]
        if next_count < 0:
            raise ValueError(f"Cannot reduce {entry['section']} below zero.")
        monthly_entries[entry["section"]] = monthly_entry

    for field, delta in inventory_deltas.items():
        if not hasattr(inventory, field):
            raise ValueError("Invalid MDF inventory field.")
        next_value = getattr(inventory, field) + delta
        if next_value < 0:
            raise ValueError("Not enough MDF inventory to complete this CNC job.")

    for field, delta in inventory_deltas.items():
        setattr(inventory, field, getattr(inventory, field) + delta)

    for entry in entries:
        monthly_entries[entry["section"]].count += entry["count"]
        db.session.add(WoodCount(
            section=entry["section"],
            count=entry["count"],
            date=log_date,
            time=log_time
        ))

    return {
        "entries": entries,
        "inventory_deltas": inventory_deltas,
    }


def _build_cnc_wood_count_change(job):
    details = _cnc_wood_job_details(job.name if job else "")
    if not details:
        return None

    try:
        quantity = int(job.quantity or 1)
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(quantity, 1)

    component = details["component"]
    section = details["section"]
    entries = []
    inventory_deltas = {}

    if component == "top_rail_long":
        short_count = 3 if details["size"] == "6ft" else 2
        entries.append({"section": section, "count": quantity * 8})
        entries.append({"section": section.replace("Long", "Short"), "count": quantity * short_count})
        inventory_deltas["plain_mdf_36"] = -quantity
    elif component == "top_rail_short":
        entries.append({"section": section, "count": quantity * 16})
        inventory_deltas["plain_mdf_36"] = -quantity
    else:
        entries.append({"section": section, "count": quantity})
        inventory_field = "black_mdf" if component == "body" else "plain_mdf"
        inventory = _get_or_create_mdf_inventory()
        current_stock = getattr(inventory, inventory_field)
        if quantity == 1:
            if current_stock > 0:
                inventory_deltas[inventory_field] = -1
        else:
            inventory_deltas[inventory_field] = -quantity

    return {
        "details": details,
        "quantity": quantity,
        "entries": entries,
        "inventory_deltas": inventory_deltas,
    }


def _record_cnc_job_wood_count(job):
    change = _build_cnc_wood_count_change(job)
    if not change:
        return {
            "logged": False,
            "message": "Wood count not updated - CNC job name was not recognised."
        }

    inventory = _get_or_create_mdf_inventory()
    applied = _apply_wood_count_entries(
        change["entries"],
        change["inventory_deltas"],
        inventory=inventory
    )
    return {
        "logged": True,
        "section": change["details"]["section"],
        "quantity": change["quantity"],
        "entries": applied["entries"],
        "inventory_deltas": applied["inventory_deltas"],
        "message": "Wood count updated."
    }


def _remember_cnc_wood_log(item_id, wood_result):
    if not wood_result or not wood_result.get("logged"):
        return
    logs = session.get(CNC_WOOD_LOG_SESSION_KEY) or {}
    logs[str(item_id)] = {
        "entries": wood_result.get("entries", []),
        "inventory_deltas": wood_result.get("inventory_deltas", {}),
    }
    session[CNC_WOOD_LOG_SESSION_KEY] = logs
    session.modified = True


def _get_remembered_cnc_wood_log(item_id):
    logs = session.get(CNC_WOOD_LOG_SESSION_KEY) or {}
    return logs.get(str(item_id))


def _forget_cnc_wood_log(item_id):
    logs = session.get(CNC_WOOD_LOG_SESSION_KEY) or {}
    if str(item_id) in logs:
        logs.pop(str(item_id), None)
        session[CNC_WOOD_LOG_SESSION_KEY] = logs
        session.modified = True


def _reverse_remembered_cnc_wood_log(item_id):
    remembered = _get_remembered_cnc_wood_log(item_id)
    if not remembered:
        return {
            "logged": False,
            "message": "Wood count was not changed for this undo."
        }

    reverse_entries = [
        {"section": entry.get("section"), "count": -int(entry.get("count", 0))}
        for entry in remembered.get("entries", [])
    ]
    reverse_inventory = {
        field: -int(delta)
        for field, delta in (remembered.get("inventory_deltas") or {}).items()
    }
    _apply_wood_count_entries(reverse_entries, reverse_inventory)
    return {
        "logged": True,
        "message": "Wood count reversed."
    }


def _payload_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False

# Shared helper to read felt counts (uses legacy felt names if needed).
def get_latest_part_entry(part_name):
    return (PrintedPartsCount.query
            .filter_by(part_name=part_name)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first())


def get_felt_count():
    entry = get_latest_part_entry(FELT_PART_NAME)
    if entry:
        return entry.count
    total = 0
    for legacy_name in LEGACY_FELT_PART_NAMES:
        legacy_entry = get_latest_part_entry(legacy_name)
        if legacy_entry:
            total += legacy_entry.count
    return total


def saved_chinese_parts_on_order_counts(data=None):
    saved_on_order = data if isinstance(data, dict) else load_chinese_parts_on_order()
    parts_on_order = saved_on_order.get("parts", {})
    if not isinstance(parts_on_order, dict):
        parts_on_order = {}
    return {
        part: _coerce_int(parts_on_order.get(part), 0)
        for part in CHINESE_PARTS_CAPACITY
    }


def saved_chinese_part_on_order(part_name, data=None):
    return saved_chinese_parts_on_order_counts(data).get(part_name, 0)


def calculate_chinese_parts_build_capacity(counts, on_order_counts=None):
    on_order_counts = on_order_counts or {}
    tables_possible_per_part = {}
    for part, req_per_table in CHINESE_PARTS_CAPACITY.items():
        if req_per_table <= 0:
            continue
        available_count = (counts.get(part, 0) or 0) + (on_order_counts.get(part, 0) or 0)
        tables_possible_per_part[part] = int(max(available_count, 0) // req_per_table)
    max_tables_possible = min(tables_possible_per_part.values()) if tables_possible_per_part else 0
    return max_tables_possible, tables_possible_per_part


def check_and_notify_chinese_parts_order_more(
    part_name,
    old_count,
    new_count,
    collected_warnings=None,
    old_on_order_count=None,
    new_on_order_count=None
):
    if part_name != CHINESE_PARTS_ORDER_MORE_PART:
        return None

    req_per_table = CHINESE_PARTS_CAPACITY.get(part_name, 0)
    if req_per_table <= 0:
        return None

    if old_on_order_count is None:
        old_on_order_count = saved_chinese_part_on_order(part_name)
    if new_on_order_count is None:
        new_on_order_count = old_on_order_count

    old_available_count = (old_count or 0) + (old_on_order_count or 0)
    new_available_count = (new_count or 0) + (new_on_order_count or 0)
    old_can_build = int(max(old_available_count, 0) // req_per_table)
    new_can_build = int(max(new_available_count, 0) // req_per_table)
    if old_can_build < CHINESE_PARTS_ORDER_MORE_THRESHOLD or new_can_build >= CHINESE_PARTS_ORDER_MORE_THRESHOLD:
        return None

    message = (
        f"Order more Chinese parts - {part_name} can build is "
        f"{new_can_build} tables including on-order."
    )
    if collected_warnings is not None:
        collected_warnings.append(message)
        return message

    try:
        requests.post(
            "https://ntfy.sh/PoolTableTracker",
            data=message,
            headers={"Title": "Order More Chinese Parts", "Priority": "high"}
        )
    except requests.RequestException as e:
        print(f"Ntfy notification failed for Chinese parts order warning: {e}")
    return message

# Track last time we alerted for a given part to throttle repeats
LOW_STOCK_LAST_ALERT = {}

def check_and_notify_low_stock(part_name, old_count, new_count, collected_warnings=None):
    ensure_part_threshold_schema()
    threshold_entry = PartThreshold.query.filter_by(part_name=part_name).first()
    if threshold_entry and threshold_entry.alerts_enabled and threshold_entry.threshold > 0:
        # Reset alert state if stock recovered above threshold
        if new_count > threshold_entry.threshold:
            LOW_STOCK_LAST_ALERT.pop(part_name, None)
            return None

        # Only notify while at/below threshold, throttled to once per hour
        message = f"Stock for {part_name} is low ({new_count} remaining)."
        if collected_warnings is not None:
            collected_warnings.append(message)
            return message

        now = datetime.utcnow()
        last_alert = LOW_STOCK_LAST_ALERT.get(part_name)
        should_alert = last_alert is None or (now - last_alert) >= timedelta(hours=1)

        if should_alert:
            # Only alert during business hours (9am-5pm server local time)
            current_time = datetime.now().time()
            business_start = time(9, 0)
            business_end = time(17, 0)
            within_business_hours = business_start <= current_time < business_end

            if within_business_hours:
                try:
                    title = "Low Stock Warning"
                    requests.post(
                        "https://ntfy.sh/PoolTableTracker",
                        data=message,
                        headers={"Title": title, "Priority": "high"}
                    )
                    LOW_STOCK_LAST_ALERT[part_name] = now
                except requests.RequestException as e:
                    print(f"Ntfy notification failed for low stock: {e}")
        return message
    LOW_STOCK_LAST_ALERT.pop(part_name, None)
    return None


def adjust_fractional_strip_inventory(part_name, strip_delta, units_per_strip=4, collected_warnings=None):
    target_units = strip_delta * units_per_strip
    units_delta = int(round(target_units))
    if abs(target_units - units_delta) > 1e-6:
        return False, part_name, 0.0

    hardware_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == part_name.lower()).first()
    canonical_name = hardware_part.name if hardware_part else part_name
    latest_entry = (PrintedPartsCount.query
                    .filter(func.lower(PrintedPartsCount.part_name) == canonical_name.lower())
                    .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                    .first())
    current_strips = latest_entry.count if latest_entry else (hardware_part.initial_count if hardware_part else 0)

    remainder_key = f"{slugify_key(canonical_name)}_remainder"
    remainder_entry = TableStock.query.filter_by(type=remainder_key).first()
    used_units = remainder_entry.count if remainder_entry else 0
    used_units = int(used_units or 0)

    available_units = (current_strips * units_per_strip) - used_units
    available_strips = available_units / units_per_strip if units_per_strip else 0.0
    new_available_units = available_units + units_delta
    if new_available_units < 0:
        return False, canonical_name, available_strips

    new_available_strips = new_available_units / units_per_strip if units_per_strip else 0.0
    new_strips = ceil(new_available_units / units_per_strip) if new_available_units > 0 else 0
    new_used_units = 0 if new_available_units <= 0 else (
        (units_per_strip - (new_available_units % units_per_strip)) % units_per_strip
    )

    now = london_now()
    new_entry = PrintedPartsCount(
        part_name=canonical_name,
        count=new_strips,
        date=now.date(),
        time=now.time()
    )
    db.session.add(new_entry)
    if remainder_entry:
        remainder_entry.count = new_used_units
    else:
        db.session.add(TableStock(type=remainder_key, count=new_used_units))

    check_and_notify_low_stock(
        canonical_name,
        available_strips,
        new_available_strips,
        collected_warnings=collected_warnings
    )

    return True, canonical_name, available_strips


def fractional_strip_display_count(part_name, units_per_strip=4):
    hardware_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == part_name.lower()).first()
    canonical_name = hardware_part.name if hardware_part else part_name
    latest_entry = (PrintedPartsCount.query
                    .filter(func.lower(PrintedPartsCount.part_name) == canonical_name.lower())
                    .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                    .first())
    strips = latest_entry.count if latest_entry else (hardware_part.initial_count if hardware_part else 0)
    remainder_entry = TableStock.query.filter_by(type=f"{slugify_key(canonical_name)}_remainder").first()
    used_units = remainder_entry.count if remainder_entry else 0
    used_units = int(used_units or 0)

    total_units = (strips * units_per_strip) - used_units
    display_count = max(0.0, total_units / units_per_strip) if units_per_strip else 0.0
    return round(display_count, 2), canonical_name


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

    ensure_part_threshold_schema()
    BodyPieceCount.__table__.create(db.engine, checkfirst=True)
    ensure_cushion_consumables()

    threshold_section_open = False

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
            alerts_enabled = request.form.get('alerts_enabled') == '1'
            
            threshold_entry = PartThreshold.query.filter_by(part_name=part_name).first()
            if not threshold_entry:
                threshold_entry = PartThreshold(
                    part_name=part_name,
                    threshold=threshold,
                    alerts_enabled=alerts_enabled
                )
                db.session.add(threshold_entry)
            else:
                threshold_entry.threshold = threshold
                threshold_entry.alerts_enabled = alerts_enabled
            
            db.session.commit()
            if not alerts_enabled or threshold <= 0:
                LOW_STOCK_LAST_ALERT.pop(part_name, None)

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
            if part_name and part_name.lower() == BRAD_NAILS_PART_NAME.lower():
                current_stock, _ = fractional_strip_display_count(
                    BRAD_NAILS_PART_NAME,
                    BRAD_NAILS_UNITS_PER_STRIP
                )

            # If stock is already below the new threshold, notify
            if alerts_enabled and threshold > 0 and current_stock <= threshold:
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

            alert_state = "on" if alerts_enabled else "off"
            flash(f"Threshold for {part_name} updated to {threshold}. Low stock alerts are {alert_state}.", "success")
        except ValueError:
            flash("Invalid threshold value.", "error")
        # Keep the threshold section open after updating
        threshold_section_open = True

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
    all_parts_query3 = db.session.query(TopRailPieceCount.part_key.label("part_name")).distinct()
    all_parts_query4 = db.session.query(BodyPieceCount.part_key.label("part_name")).distinct()
    all_parts_union = all_parts_query1.union(all_parts_query2, all_parts_query3, all_parts_query4).all()
    all_part_names = {name for (name,) in all_parts_union if name}
    all_part_names.update(LAMINATE_PART_NAMES)
    all_part_names.difference_update(LEGACY_FELT_PART_NAMES)
    all_part_names.add(FELT_PART_NAME)
    all_part_names.update(PACKAGING_PART_NAMES)
    all_part_names = sorted(all_part_names, key=lambda n: n.lower())

    # Get all current thresholds
    thresholds = PartThreshold.query.all()
    thresholds_map = {t.part_name: t.threshold for t in thresholds}
    threshold_alerts_enabled_map = {t.part_name: bool(t.alerts_enabled) for t in thresholds}

    if request.method == 'POST' and 'table' in request.form:
        table = request.form.get('table')
        entry_id = request.form.get('id')

        model = None
        if table == 'pods':
            model = CompletedPods
            # When deleting a pod, restore only the parts deducted for that pod type
            if 'delete' in request.form:
                pod = CompletedPods.query.get(entry_id)
                if pod:
                    # Determine if it's a 6ft pod
                    is_6ft = serial_is_6ft(pod.serial_number)
                    pod_table_type = table_type_from_serial(pod.serial_number)
                    restored_parts = []

                    def restore_part(part_name, quantity):
                        inventory_entry = (
                            PrintedPartsCount.query
                            .filter_by(part_name=part_name)
                            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                            .first()
                        )
                        if inventory_entry:
                            inventory_entry.count += quantity
                        else:
                            db.session.add(
                                PrintedPartsCount(
                                    part_name=part_name,
                                    count=quantity,
                                    date=datetime.utcnow().date(),
                                    time=datetime.utcnow().time()
                                )
                            )
                        restored_parts.append(f"+{quantity} {part_name}")

                    if pod_table_type == TABLE_TYPE_CHAMPION:
                        felt_part = FELT_PART_NAME
                        carpet_part = "6ft Carpet" if is_6ft else "7ft Carpet"

                        felt_entry = get_latest_part_entry(felt_part)
                        if felt_entry:
                            felt_entry.count += 2
                        else:
                            fallback_count = get_felt_count()
                            db.session.add(
                                PrintedPartsCount(
                                    part_name=felt_part,
                                    count=fallback_count + 2,
                                    date=datetime.utcnow().date(),
                                    time=datetime.utcnow().time()
                                )
                            )
                        restored_parts.append(f"+2 {felt_part}")
                        restore_part(carpet_part, 1)
                        restore_part("Rows of Black Staples", 2)

                    restore_part("M10x13mm Tee Nut", 16)

                    type_label = "Lite" if pod_table_type == TABLE_TYPE_LITE else "Champion"
                    flash(f"{type_label} pod stock restored: {', '.join(restored_parts)}", "success")
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
        thresholds_map=thresholds_map,
        threshold_alerts_enabled_map=threshold_alerts_enabled_map,
        threshold_section_open=threshold_section_open
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

    def shift_month(dt, months_delta):
        """Return a date at the first of the month, shifted by months_delta months."""
        month_index = dt.month - 1 + months_delta
        year = dt.year + month_index // 12
        month = month_index % 12 + 1
        return date(year, month, 1)

    # Range controls
    def clamp(val, lo, hi):
        try:
            num = int(val)
        except (TypeError, ValueError):
            return lo
        return max(lo, min(hi, num))

    def parse_month_arg(value):
        if not value:
            return None
        try:
            year, month = value.split("-", 1)
            year = int(year)
            month = int(month)
            return date(year, month, 1)
        except Exception:
            return None

    months_back = clamp(request.args.get("months_back", 11), 0, 36)
    months_forward = clamp(request.args.get("months_forward", 0), 0, 12)
    start_month_arg = parse_month_arg(request.args.get("start_month"))
    end_month_arg = parse_month_arg(request.args.get("end_month"))

    current_month_start = today.replace(day=1)

    if start_month_arg:
        first_month = start_month_arg
        if end_month_arg:
            last_month = end_month_arg
        else:
            last_month = shift_month(current_month_start, months_forward)
        if last_month < first_month:
            last_month = first_month
        month_starts = []
        cursor = first_month
        while cursor <= last_month:
            month_starts.append(cursor)
            cursor = shift_month(cursor, 1)
    else:
        total_months = months_back + months_forward + 1
        first_month = shift_month(current_month_start, -months_back)
        month_starts = [shift_month(first_month, i) for i in range(total_months)]

    def monthly_counts(model):
        counts = []
        for idx, start_date in enumerate(month_starts):
            end_date = month_starts[idx + 1] if idx + 1 < len(month_starts) else shift_month(start_date, 1)
            total = model.query.filter(model.date >= start_date, model.date < end_date).count()
            counts.append(total)
        return counts

    chart_labels = [dt.strftime("%b %Y") for dt in month_starts]
    chart_data_pods = monthly_counts(CompletedPods)
    chart_data_bodies = monthly_counts(CompletedTable)
    chart_data_top_rails = monthly_counts(TopRail)

    return render_template(
        'dashboard.html',
        stats={
            "top_rails": {
                "today": top_rails_today,
                "week": top_rails_week,
                "month": top_rails_month,
                "year": top_rails_year,
            },
            "bodies": {
                "today": bodies_today,
                "week": bodies_week,
                "month": bodies_month,
                "year": bodies_year,
            },
            "pods": {
                "today": pods_today,
                "week": pods_week,
                "month": pods_month,
                "year": pods_year,
            },
        },
        chart_labels=chart_labels,
        chart_pods=chart_data_pods,
        chart_bodies=chart_data_bodies,
        chart_top_rails=chart_data_top_rails,
        months_back=months_back,
        months_forward=months_forward,
        start_month=start_month_arg.strftime("%Y-%m") if start_month_arg else "",
        end_month=end_month_arg.strftime("%Y-%m") if end_month_arg else "",
        wood_counts=wood_counts
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
        "Large Ramp", "Paddle", *LAMINATE_PART_NAMES, "Spring Mount", "Spring Holder",
        "Small Ramp", "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp",
        "6ft Carpet", "7ft Carpet", FELT_PART_NAME
    ]

    inventory_counts = {}
    for part in parts:
        if part == FELT_PART_NAME:
            inventory_counts[part] = get_felt_count()
            continue
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
    bodies_built_7ft = sum(1 for table in completed_tables if not serial_is_6ft(table.serial_number))
    bodies_built_6ft = sum(1 for table in completed_tables if serial_is_6ft(table.serial_number))

    # Define usage per table for each part.
    parts_usage_per_body = {
        "Large Ramp": 1,
        "Paddle": 1,
        **{name: 4 for name in LAMINATE_PART_NAMES},
        "Spring Mount": 1,
        "Spring Holder": 1,
        "Small Ramp": 1,
        "Cue Ball Separator": 1,
        "Bushing": 2,
        "6ft Cue Ball Separator": 1,
        "6ft Large Ramp": 1,
        "6ft Carpet": 1,
        FELT_PART_NAME: 2,
        "7ft Carpet": 1
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
        if part in ["Large Ramp", "Cue Ball Separator", "7ft Carpet"]:  # Added 7ft items
            required_total = target_7ft * usage
            completed_total = bodies_built_7ft * usage

        elif part in ["6ft Large Ramp", "6ft Cue Ball Separator", "6ft Carpet"]:  # Added 6ft items
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
    table_parts = {part: 0 for part in ALL_CHINESE_PARTS}

    table_parts_counts = {part: 0 for part in table_parts}
    for part in table_parts_counts:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        table_parts_counts[part] = latest_entry[0] if latest_entry else 0

    table_parts_on_order_counts = saved_chinese_parts_on_order_counts()
    max_tables_possible_stock, tables_possible_per_part_stock = calculate_chinese_parts_build_capacity(
        table_parts_counts
    )
    max_tables_possible, tables_possible_per_part = calculate_chinese_parts_build_capacity(
        table_parts_counts,
        table_parts_on_order_counts
    )

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
        table_parts_on_order_counts=table_parts_on_order_counts,
        max_tables_possible_stock=max_tables_possible_stock,
        tables_possible_per_part_stock=tables_possible_per_part_stock,
        max_tables_possible=max_tables_possible,
        tables_possible_per_part=tables_possible_per_part,
        chinese_parts_order_more_part=CHINESE_PARTS_ORDER_MORE_PART,
        chinese_parts_order_more_threshold=CHINESE_PARTS_ORDER_MORE_THRESHOLD,
        hardware_counts=hardware_counts
    )


def build_stock_snapshot():
    stock_items = []

    BodyPieceCount.__table__.create(db.engine, checkfirst=True)

    def add_item(category, identifier, label, count, key_category=None, **extra_fields):
        key_source = identifier or label
        storage_category = key_category or category
        key = f"{slugify_key(storage_category)}__{slugify_key(key_source)}"
        try:
            numeric_count = float(count)
        except (TypeError, ValueError):
            numeric_count = 0.0
        item_data = {
            "category": category,
            "storage_category": storage_category,
            "identifier": identifier,
            "label": label,
            "count": numeric_count,
            "key": key
        }
        if extra_fields:
            item_data.update(extra_fields)
        stock_items.append(item_data)

    def fetch_part_count(part_name):
        if part_name == FELT_PART_NAME:
            felt_entry = get_latest_part_entry(FELT_PART_NAME)
            if felt_entry:
                return felt_entry.count, True
            legacy_total = 0
            has_legacy = False
            for legacy_name in LEGACY_FELT_PART_NAMES:
                legacy_entry = get_latest_part_entry(legacy_name)
                if legacy_entry:
                    legacy_total += legacy_entry.count
                    has_legacy = True
            return legacy_total, has_legacy

        entry = (
            PrintedPartsCount.query
            .filter_by(part_name=part_name)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        if entry:
            return entry.count, True
        return 0, False

    core_parts = [
        "Large Ramp", "Paddle", *LAMINATE_PART_NAMES,
        "Spring Mount", "Spring Holder", "Small Ramp",
        "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp",
        "6ft Carpet", "7ft Carpet", FELT_PART_NAME
    ]

    table_parts = {part: 0 for part in ALL_CHINESE_PARTS}

    packaging_parts = list(PACKAGING_PART_NAMES)

    hardware_parts = HardwarePart.query.all()
    hardware_defaults = {hp.name: hp.initial_count for hp in hardware_parts}

    part_names = set()
    distinct_parts = db.session.query(PrintedPartsCount.part_name).distinct().all()
    legacy_felt_parts = set(LEGACY_FELT_PART_NAMES)
    for part_tuple in distinct_parts:
        part_name = part_tuple[0]
        if part_name and part_name not in legacy_felt_parts:
            part_names.add(part_name)

    part_names.update(core_parts)
    part_names.update(table_parts.keys())
    part_names.update(packaging_parts)
    part_names.update(hardware_defaults.keys())

    for part_name in sorted(part_names, key=lambda x: x.lower()):
        count, has_record = fetch_part_count(part_name)
        if not has_record and part_name in hardware_defaults:
            count = hardware_defaults[part_name]

        if part_name in packaging_parts:
            display_category = "Packaging"
        elif part_name in hardware_defaults:
            display_category = "Hardware Parts"
        elif part_name in table_parts:
            display_category = "Chinese Parts"
        else:
            display_category = "3D Printed Parts"

        count_editable = part_name in packaging_parts
        add_item(
            display_category,
            part_name,
            part_name,
            count,
            key_category="Parts Inventory",
            count_editable=count_editable
        )

    parts_on_water_total = 0.0
    if os.path.exists(CHINESE_PARTS_ON_ORDER_FILE):
        try:
            with open(CHINESE_PARTS_ON_ORDER_FILE, "r") as f:
                on_order_data = json.load(f)
            payments = on_order_data.get("payments", {})
            for entry in payments.values():
                try:
                    parts_on_water_total += float(entry.get("paid_so_far", 0) or 0)
                except (TypeError, ValueError):
                    continue
        except (json.JSONDecodeError, OSError):
            pass

    add_item(
        "Chinese Parts",
        "parts_on_water",
        "Parts on the water",
        1,
        key_category="Parts Inventory",
        unit_cost=parts_on_water_total,
        shipping_cost=0.0,
        labour_cost=0.0,
        cost_locked=False,
        count_display="-",
    )
    add_item(
        "Chinese Parts",
        "parts_on_water_2",
        "Parts on the water 2",
        1,
        key_category="Parts Inventory",
        unit_cost=0.0,
        shipping_cost=0.0,
        labour_cost=0.0,
        cost_locked=False,
        count_display="-",
    )

    wood_sections = [
        ("Body Wood Sets", "Body"),
        ("Pod Sides", "Pod Sides"),
        ("Base Panels", "Bases")
    ]

    for label, section in wood_sections:
        entry = (
            WoodCount.query
            .filter_by(section=section)
            .order_by(WoodCount.date.desc(), WoodCount.time.desc())
            .first()
        )
        count = entry.count if entry else 0
        add_item("Wood Shop", section, label, count)

    inventory_record = MDFInventory.query.first()
    if not inventory_record:
        inventory_record = MDFInventory(plain_mdf=0, black_mdf=0, plain_mdf_36=0)
        db.session.add(inventory_record)
        db.session.commit()

    add_item("MDF Boards", "plain_mdf", "Plain MDF", inventory_record.plain_mdf)
    add_item("MDF Boards", "black_mdf", "Black MDF", inventory_record.black_mdf)
    add_item("MDF Boards", "plain_mdf_36", "36mm Plain MDF", inventory_record.plain_mdf_36)

    for entry in TableStock.query.all():
        # Skip legacy generic body/top_rail keys without color so they don't pollute totals
        if entry.type in {"body_6ft", "body_7ft", "top_rail_6ft", "top_rail_7ft"}:
            continue
        # Skip synthetic body metadata rows used to reconstruct Lite bodies.
        if entry.type.startswith(("meta_body_type_", "meta_body_color_")):
            continue
        if entry.type.endswith("_remainder"):
            continue

        if entry.type.startswith('body_'):
            category = "Finished Tables"
        elif entry.type.startswith('top_rail_'):
            category = "Top Rails"
        elif entry.type.startswith('cushion_set_'):
            category = "Cushion Sets"
        else:
            category = "Table Stock"
        label = entry.type.replace('_', ' ').title()
        add_item(category, entry.type, label, entry.count)

    laminate_counts = {part.part_key: part.count for part in LaminatePieceCount.query.all()}
    laminate_colors = ['black', 'rustic_oak', 'grey_oak', 'stone', 'rustic_black']
    laminate_sizes = ['6', '7']
    laminate_lengths = ['short', 'long']

    for color_index, color in enumerate(laminate_colors):
        pretty_color = color.replace('_', ' ').title()
        laminate_meta_base = {
            'laminate_color': color,
            'laminate_color_label': pretty_color,
            'laminate_color_index': color_index,
            'laminate_zone': True
        }
        uncut_key = f"{color}_uncut"
        uncut_label = f"{pretty_color} Uncut Laminate"
        count = laminate_counts.get(uncut_key, 0)
        add_item(
            "Cut Laminate",
            uncut_key,
            uncut_label,
            count,
            laminate_is_uncut=True,
            laminate_size=None,
            laminate_length=None,
            **laminate_meta_base
        )
        for size in laminate_sizes:
            for length in laminate_lengths:
                part_key = f"{color}_{size}_{length}"
                label = f"{pretty_color} {size}ft {length.title()} Laminate"
                count = laminate_counts.get(part_key, 0)
                add_item(
                    "Cut Laminate",
                    part_key,
                    label,
                    count,
                    laminate_is_uncut=False,
                    laminate_size=size,
                    laminate_length=length,
                    **laminate_meta_base
                )

    body_piece_counts = {part.part_key: part.count for part in BodyPieceCount.query.all()}
    body_piece_colors = ['black', 'rustic_oak', 'grey_oak', 'stone', 'rustic_black']
    body_piece_sizes = ['6', '7']
    body_piece_types = [
        ('window_side', 'Window Side'),
        ('blank_side', 'Blank Side'),
        ('triangle_end', 'Colour End'),
        ('color_ball_end', 'White Ball End'),
    ]
    for color_index, color in enumerate(body_piece_colors):
        pretty_color = color.replace('_', ' ').title()
        body_piece_meta_base = {
            'body_piece_color': color,
            'body_piece_color_label': pretty_color,
            'body_piece_color_index': color_index,
            'body_piece_zone': True
        }
        for size in body_piece_sizes:
            for piece_key, piece_label in body_piece_types:
                part_key = f"{color}_{size}_{piece_key}"
                label = f"{pretty_color} {size}ft {piece_label}"
                count = body_piece_counts.get(part_key, 0)
                add_item(
                    "Body Pieces",
                    part_key,
                    label,
                    count,
                    key_category="Parts Inventory",
                    body_piece_size=size,
                    body_piece_type=piece_key,
                    **body_piece_meta_base
                )

    return stock_items


@app.route('/stock_costs', methods=['GET', 'POST'])
def stock_costs():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    stock_items = build_stock_snapshot()
    item_keys = [item['key'] for item in stock_items]
    vat_rate = 0.20
    manual_snapshot = request.method == 'POST' and request.form.get('snapshot_action') == 'create_snapshot'

    def parse_currency(value):
        if value is None or value == '':
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def parse_count(value):
        if value is None or value == '':
            return None, None
        try:
            count_value = float(value)
        except (TypeError, ValueError):
            return None, "Count must be a whole number."
        if count_value < 0:
            return None, "Count cannot be negative."
        if not count_value.is_integer():
            return None, "Count must be a whole number."
        return int(count_value), None

    if request.method == 'POST' and not manual_snapshot:
        count_updates = {}
        for item in stock_items:
            if not item.get('count_editable'):
                continue
            raw_value = request.form.get(f"count_{item['key']}")
            if raw_value is None:
                continue
            new_count, error = parse_count(raw_value)
            if error:
                flash(f"Invalid stock count for {item.get('label', 'item')}: {error}", "error")
                return redirect(url_for('stock_costs'))
            if new_count is not None:
                count_updates[item['key']] = new_count

        for item in stock_items:
            if not item.get('count_editable'):
                continue
            if item['key'] not in count_updates:
                continue
            new_count = count_updates[item['key']]
            old_count = int(item.get('count') or 0)
            if new_count == old_count:
                continue
            part_name = item.get('identifier') or item.get('label')
            if new_count < old_count:
                check_and_notify_low_stock(part_name, old_count, new_count)
            new_entry = PrintedPartsCount(
                part_name=part_name,
                count=new_count,
                date=datetime.utcnow().date(),
                time=datetime.utcnow().time()
            )
            db.session.add(new_entry)

        for item in stock_items:
            if item.get('cost_locked'):
                continue
            unit_value = parse_currency(request.form.get(f"unit_cost_{item['key']}", 0))
            shipping_value = parse_currency(request.form.get(f"shipping_cost_{item['key']}", 0))
            labour_value = parse_currency(request.form.get(f"labour_cost_{item['key']}", 0))
            cost_entry = StockItemCost.query.filter_by(item_key=item['key']).first()
            if not cost_entry:
                cost_entry = StockItemCost(item_key=item['key'])
                db.session.add(cost_entry)
            cost_entry.unit_cost = unit_value
            cost_entry.shipping_cost = shipping_value
            cost_entry.labour_cost = labour_value

        db.session.commit()
        flash("Stock costs updated successfully!", "success")
        return redirect(url_for('stock_costs'))

    cost_entries = {}
    if item_keys:
        for entry in StockItemCost.query.filter(StockItemCost.item_key.in_(item_keys)).all():
            cost_entries[entry.item_key] = entry

    category_totals = defaultdict(lambda: {'ex_vat': 0.0, 'inc_vat': 0.0})
    grand_total_ex_vat = 0.0
    grand_total_inc_vat = 0.0
    category_blocks = []
    category_lookup = {}

    for item in stock_items:
        entry = cost_entries.get(item['key'])
        if item.get('cost_locked'):
            unit_cost = item.get('unit_cost', 0.0)
            shipping_cost = item.get('shipping_cost', 0.0)
            labour_cost = item.get('labour_cost', 0.0)
        else:
            unit_cost = entry.unit_cost if entry else item.get('unit_cost', 0.0)
            shipping_cost = entry.shipping_cost if entry else item.get('shipping_cost', 0.0)
            labour_cost = entry.labour_cost if entry else item.get('labour_cost', 0.0)
        material_cost = unit_cost + shipping_cost
        per_item_total = material_cost + labour_cost  # Ex VAT total (labour is VAT exempt)
        per_item_with_vat = (material_cost * (1 + vat_rate)) + labour_cost
        stock_value_ex_vat = per_item_total * item['count']
        stock_value_inc_vat = per_item_with_vat * item['count']
        has_cost = any([unit_cost, shipping_cost, labour_cost]) or item.get('cost_locked')

        item['unit_cost'] = unit_cost
        item['shipping_cost'] = shipping_cost
        item['labour_cost'] = labour_cost
        item['per_item_total'] = per_item_total
        item['per_item_with_vat'] = per_item_with_vat
        item['stock_value_ex_vat'] = stock_value_ex_vat
        item['stock_value_inc_vat'] = stock_value_inc_vat
        item['has_cost'] = has_cost

        category_totals[item['category']]['ex_vat'] += stock_value_ex_vat
        category_totals[item['category']]['inc_vat'] += stock_value_inc_vat
        grand_total_ex_vat += stock_value_ex_vat
        grand_total_inc_vat += stock_value_inc_vat

        if item['category'] not in category_lookup:
            category_lookup[item['category']] = {
                'name': item['category'],
                'entries': []
            }
            category_blocks.append(category_lookup[item['category']])
        category_lookup[item['category']]['entries'].append(item)

    # Reorganize laminate entries to group by color with headers and uncut at top
    for category in category_blocks:
        entries = category['entries']
        if not entries:
            continue
        if not any(entry.get('laminate_zone') for entry in entries):
            continue
        sorted_entries = sorted(
            [entry for entry in entries if not entry.get('is_laminate_header')],
            key=lambda e: (
                e.get('laminate_color_index', 0),
                0 if e.get('laminate_is_uncut') else 1,
                e.get('laminate_size') or '',
                e.get('laminate_length') or ''
            )
        )
        zoned_entries = []
        current_color = None
        for entry in sorted_entries:
            color_key = entry.get('laminate_color')
            if color_key != current_color:
                zoned_entries.append({
                    'is_laminate_header': True,
                    'laminate_color_label': entry.get('laminate_color_label', '').title()
                })
                current_color = color_key
            zoned_entries.append(entry)
        category['entries'] = zoned_entries

    for category in category_blocks:
        entries = category['entries']
        if not entries:
            continue
        if not any(entry.get('body_piece_zone') for entry in entries):
            continue
        piece_order = {
            'window_side': 0,
            'blank_side': 1,
            'triangle_end': 2,
            'color_ball_end': 3
        }
        sorted_entries = sorted(
            [entry for entry in entries if not entry.get('is_body_piece_header')],
            key=lambda e: (
                e.get('body_piece_color_index', 0),
                e.get('body_piece_size') or '',
                piece_order.get(e.get('body_piece_type', ''), 99)
            )
        )
        zoned_entries = []
        current_color = None
        for entry in sorted_entries:
            color_key = entry.get('body_piece_color')
            if color_key != current_color:
                zoned_entries.append({
                    'is_body_piece_header': True,
                    'body_piece_color_label': entry.get('body_piece_color_label', '').title()
                })
                current_color = color_key
            zoned_entries.append(entry)
        category['entries'] = zoned_entries

    ordered_snapshot_items = [
        entry
        for category in category_blocks
        for entry in category.get('entries', [])
        if not entry.get('is_laminate_header')
    ]

    category_totals = {k: v for k, v in category_totals.items()}

    part_categories = {
        "Chinese Parts",
        "Hardware Parts",
        "3D Printed Parts",
        "Cut Laminate",
        "Body Pieces",
        "Wood Shop",
        "MDF Boards",
        "Packaging",
    }
    finished_categories = {
        "Finished Tables",
        "Top Rails",
        "Cushion Sets",
        "Table Stock",
    }

    parts_total_ex_vat = sum(category_totals.get(cat, {}).get('ex_vat', 0.0) for cat in part_categories)
    parts_total_inc_vat = sum(category_totals.get(cat, {}).get('inc_vat', 0.0) for cat in part_categories)
    finished_total_ex_vat = sum(category_totals.get(cat, {}).get('ex_vat', 0.0) for cat in finished_categories)
    finished_total_inc_vat = sum(category_totals.get(cat, {}).get('inc_vat', 0.0) for cat in finished_categories)

    def write_stock_snapshot_file(items, filename, include_category_headers=False):
        try:
            os.makedirs(STOCK_SNAPSHOT_DIR, exist_ok=True)
            filepath = safe_stock_snapshot_file_path(filename)
            if not filepath:
                return False
            with open(filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Category",
                    "Item",
                    "Count",
                    "Unit Cost",
                    "Shipping Cost",
                    "Labour Cost",
                    "Cost / Item (Ex VAT)",
                    "Cost / Item (Incl VAT)",
                    "Stock Value (Ex VAT)",
                    "Stock Value (Incl VAT)",
                ])
                last_category = None
                for item in items:
                    if include_category_headers:
                        category_label = item.get("category", "")
                        if category_label and category_label != last_category:
                            writer.writerow([category_label] + [""] * 9)
                            last_category = category_label
                    count_display = item.get("count_display", item.get("count", 0))
                    writer.writerow([
                        item.get("category", ""),
                        item.get("label", ""),
                        count_display,
                        item.get("unit_cost", 0),
                        item.get("shipping_cost", 0),
                        item.get("labour_cost", 0),
                        item.get("per_item_total", 0),
                        item.get("per_item_with_vat", 0),
                        item.get("stock_value_ex_vat", 0),
                        item.get("stock_value_inc_vat", 0),
                    ])
            return True
        except OSError:
            return False

    stock_snapshots = load_stock_snapshots()
    stock_snapshots.sort(key=lambda s: s.get("timestamp", ""))
    deleted_snapshot_weeks = load_deleted_stock_snapshot_weeks()
    now = datetime.now()
    week_start = now - timedelta(days=now.weekday())
    week_key = week_start.strftime("%Y-%m-%d")
    is_after_trigger = now.weekday() > 0 or now.time() >= time(9, 0)
    if manual_snapshot:
        snapshot_label = now.strftime("%Y-%m-%d %H:%M")
        snapshot_filename = f"stock_snapshot_{now.strftime('%Y-%m-%d_%H%M')}.csv"
        snapshot_payload = {
            "timestamp": now.isoformat(),
            "week_key": week_key,
            "snapshot_label": snapshot_label,
            "total_ex_vat": grand_total_ex_vat,
            "total_inc_vat": grand_total_inc_vat,
            "parts_ex_vat": parts_total_ex_vat,
            "parts_inc_vat": parts_total_inc_vat,
            "finished_ex_vat": finished_total_ex_vat,
            "finished_inc_vat": finished_total_inc_vat
        }
        if write_stock_snapshot_file(ordered_snapshot_items, snapshot_filename, include_category_headers=True):
            snapshot_payload["snapshot_file"] = snapshot_filename
        stock_snapshots.append(snapshot_payload)
        if week_key in deleted_snapshot_weeks:
            deleted_snapshot_weeks.remove(week_key)
            save_deleted_stock_snapshot_weeks(deleted_snapshot_weeks)
        save_stock_snapshots(stock_snapshots)
        flash("Snapshot created successfully.", "success")
        return redirect(url_for('stock_costs'))

    if is_after_trigger and week_key not in deleted_snapshot_weeks:
        existing_snapshot = next((s for s in stock_snapshots if s.get("week_key") == week_key), None)
        snapshot_filename = f"stock_snapshot_{week_key}.csv"
        snapshot_payload = {
            "timestamp": now.isoformat(),
            "week_key": week_key,
            "total_ex_vat": grand_total_ex_vat,
            "total_inc_vat": grand_total_inc_vat,
            "parts_ex_vat": parts_total_ex_vat,
            "parts_inc_vat": parts_total_inc_vat,
            "finished_ex_vat": finished_total_ex_vat,
            "finished_inc_vat": finished_total_inc_vat
        }
        if not existing_snapshot:
            if write_stock_snapshot_file(ordered_snapshot_items, snapshot_filename, include_category_headers=True):
                snapshot_payload["snapshot_file"] = snapshot_filename
            stock_snapshots.append(snapshot_payload)
            save_stock_snapshots(stock_snapshots)
        else:
            missing_fields = ("parts_ex_vat", "parts_inc_vat", "finished_ex_vat", "finished_inc_vat")
            if any(key not in existing_snapshot for key in missing_fields):
                if "parts_ex_vat" not in existing_snapshot:
                    existing_snapshot["parts_ex_vat"] = parts_total_ex_vat
                if "parts_inc_vat" not in existing_snapshot:
                    existing_snapshot["parts_inc_vat"] = parts_total_inc_vat
                if "finished_ex_vat" not in existing_snapshot:
                    existing_snapshot["finished_ex_vat"] = finished_total_ex_vat
                if "finished_inc_vat" not in existing_snapshot:
                    existing_snapshot["finished_inc_vat"] = finished_total_inc_vat
                save_stock_snapshots(stock_snapshots)
            if "snapshot_file" not in existing_snapshot:
                if write_stock_snapshot_file(ordered_snapshot_items, snapshot_filename, include_category_headers=True):
                    existing_snapshot["snapshot_file"] = snapshot_filename
                save_stock_snapshots(stock_snapshots)

    return render_template(
        'stock_costs.html',
        category_blocks=category_blocks,
        category_totals=category_totals,
        grand_total_ex_vat=grand_total_ex_vat,
        grand_total_inc_vat=grand_total_inc_vat,
        parts_total_ex_vat=parts_total_ex_vat,
        parts_total_inc_vat=parts_total_inc_vat,
        finished_total_ex_vat=finished_total_ex_vat,
        finished_total_inc_vat=finished_total_inc_vat,
        stock_snapshots=stock_snapshots
    )


@app.route('/stock_costs_snapshot/<path:filename>')
def download_stock_snapshot(filename):
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))
    return send_from_directory(STOCK_SNAPSHOT_DIR, filename, as_attachment=True)


@app.route('/stock_costs_snapshot/delete', methods=['POST'])
def delete_stock_snapshot():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    snapshot_timestamp = request.form.get('snapshot_timestamp', '').strip()
    snapshot_file = request.form.get('snapshot_file', '').strip()
    week_key = request.form.get('week_key', '').strip()

    stock_snapshots = load_stock_snapshots()
    deleted_snapshot = None
    remaining_snapshots = []

    for snapshot in stock_snapshots:
        timestamp_matches = snapshot_timestamp and snapshot.get("timestamp") == snapshot_timestamp
        file_matches = snapshot_file and snapshot.get("snapshot_file") == snapshot_file
        week_matches = week_key and snapshot.get("week_key") == week_key
        if deleted_snapshot is None and (
            timestamp_matches or
            (file_matches and (not week_key or week_matches))
        ):
            deleted_snapshot = snapshot
            continue
        remaining_snapshots.append(snapshot)

    if not deleted_snapshot:
        flash("Snapshot not found.", "error")
        return redirect(url_for('stock_costs'))

    if not save_stock_snapshots(remaining_snapshots):
        flash("Could not update the snapshot list.", "error")
        return redirect(url_for('stock_costs'))

    file_delete_failed = False
    deleted_file = deleted_snapshot.get("snapshot_file")
    if deleted_file and not any(s.get("snapshot_file") == deleted_file for s in remaining_snapshots):
        filepath = safe_stock_snapshot_file_path(deleted_file)
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                file_delete_failed = True

    deleted_week = deleted_snapshot.get("week_key")
    if deleted_week:
        deleted_weeks = load_deleted_stock_snapshot_weeks()
        deleted_weeks.add(deleted_week)
        save_deleted_stock_snapshot_weeks(deleted_weeks)

    if file_delete_failed:
        flash("Snapshot removed from the dashboard, but the CSV file could not be deleted.", "warning")
    else:
        flash("Snapshot deleted.", "success")
    return redirect(url_for('stock_costs'))


@app.route('/counting_chinese_parts', methods=['GET', 'POST'])
def counting_chinese_parts():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    table_parts = list(ALL_CHINESE_PARTS)

    def get_table_parts_counts():
        """
        Return a dictionary of { part_name: current_count } for each part.
        We query a single row per part_name; if none exists, assume 0.
        """
        counts = {}
        for part in table_parts:
            existing_entry = (db.session.query(PrintedPartsCount)
                              .filter_by(part_name=part)
                              .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                              .first())
            counts[part] = existing_entry.count if existing_entry else 0
        return counts

    # Fetch current counts for all parts
    table_parts_counts = get_table_parts_counts()
    table_parts_on_order_counts = saved_chinese_parts_on_order_counts()
    _, tables_possible_per_part = calculate_chinese_parts_build_capacity(
        table_parts_counts,
        table_parts_on_order_counts
    )

    # Determine the currently selected part (default to first in list if none selected)
    selected_part = request.form.get('table_part') or request.args.get('selected') or table_parts[0]
    action = request.form.get('action')  # e.g. 'increment', 'decrement', 'bulk'

    # Process form submission if we're in POST and have an 'action'
    if request.method == 'POST' and action:
        if selected_part not in table_parts_counts:
            flash("Invalid part selected.", "error")
            return redirect(url_for('counting_chinese_parts'))

        # Fetch the specific part entry to be updated
        existing_entry = (db.session.query(PrintedPartsCount)
                          .filter_by(part_name=selected_part)
                          .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                          .first())

        current_count = existing_entry.count if existing_entry else 0

        # Perform the requested action
        if action == 'increment':
            new_count = current_count + 1

        elif action == 'quick_add':
            try:
                amount = int(request.form.get('quick_amount', 1))
            except ValueError:
                flash("Amount must be a number.", "error")
                return redirect(url_for('counting_chinese_parts', selected=selected_part))
            new_count = current_count + abs(amount)

        elif action == 'decrement':
            new_count = current_count - 1
            check_and_notify_low_stock(selected_part, current_count, new_count)

        elif action == 'bulk':
            try:
                amount = int(request.form.get('amount', 1))
            except ValueError:
                flash("Amount must be a number.", "error")
                return redirect(url_for('counting_chinese_parts', selected=selected_part))

            new_count = current_count + amount
            if amount < 0:
                check_and_notify_low_stock(selected_part, current_count, new_count)

        else:
            flash("Invalid operation.", "error")
            return redirect(url_for('counting_chinese_parts', selected=selected_part))

        new_entry = PrintedPartsCount(
            part_name=selected_part,
            count=new_count,
            date=datetime.utcnow().date(),
            time=datetime.utcnow().time()
        )
        db.session.add(new_entry)

        # Commit changes
        db.session.commit()

        order_more_message = check_and_notify_chinese_parts_order_more(
            selected_part,
            current_count,
            new_count
        )
        if order_more_message:
            flash(order_more_message, "warning")
        flash(f"{selected_part} updated successfully! New count: {new_count}", "success")
        return redirect(url_for('counting_chinese_parts', selected=selected_part))

    chinese_parts_log = []
    if table_parts:
        recent_entries = (
            PrintedPartsCount.query
            .filter(PrintedPartsCount.part_name.in_(table_parts))
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .limit(50)
            .all()
        )

        for entry in recent_entries:
            previous = (
                PrintedPartsCount.query
                .filter(PrintedPartsCount.part_name == entry.part_name)
                .filter(or_(
                    PrintedPartsCount.date < entry.date,
                    and_(PrintedPartsCount.date == entry.date, PrintedPartsCount.time < entry.time),
                ))
                .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                .first()
            )
            previous_count = previous.count if previous else 0
            chinese_parts_log.append({
                "part_name": entry.part_name,
                "date": entry.date,
                "time": entry.time,
                "new_count": entry.count,
                "delta": (entry.count or 0) - (previous_count or 0),
            })

    # Render the template with the current data
    return render_template(
        'counting_chinese_parts.html',
        table_parts=table_parts,
        table_parts_counts=table_parts_counts,
        table_parts_on_order_counts=table_parts_on_order_counts,
        tables_possible_per_part=tables_possible_per_part,
        selected_part=selected_part,
        selected_part_on_order=table_parts_on_order_counts.get(selected_part, 0),
        selected_part_can_build=tables_possible_per_part.get(selected_part),
        chinese_parts_order_more_part=CHINESE_PARTS_ORDER_MORE_PART,
        chinese_parts_order_more_threshold=CHINESE_PARTS_ORDER_MORE_THRESHOLD,
        chinese_parts_log=chinese_parts_log
    )


@app.route('/counting_hardware', methods=['GET', 'POST'])
def counting_hardware():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    # Ensure new hardware parts exist so they show in the dropdown
    required_hardware = [
        "7ft Bag of Bolts",
        "6ft Bag of Bolts",
        "7ft Ply Supports"
    ]
    created_any = False
    for name in required_hardware:
        if not HardwarePart.query.filter(func.lower(HardwarePart.name) == name.lower()).first():
            db.session.add(HardwarePart(name=name, initial_count=0))
            created_any = True
    if created_any:
        db.session.commit()

    # 1. Fetch all hardware parts
    hardware_parts = HardwarePart.query.all()

    # Default selected part comes from query param if present; otherwise first in list
    selected_part = request.args.get('selected') if request.args.get('selected') else (hardware_parts[0].name if hardware_parts else None)

    # 2. Build a dictionary of the latest known counts (or initial_count if none recorded)
    hardware_counts = {}
    for part in hardware_parts:
        latest_entry = (db.session.query(PrintedPartsCount.count)
                        .filter_by(part_name=part.name)
                        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                        .first())
        hardware_counts[part.name] = latest_entry[0] if latest_entry else part.initial_count
    hardware_counts_raw = dict(hardware_counts)

    def pallet_wrap_display_count():
        target_name = "Pallet Wrap"
        wrap_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == target_name.lower()).first()
        part_name = wrap_part.name if wrap_part else target_name
        latest_entry = (PrintedPartsCount.query
                        .filter(func.lower(PrintedPartsCount.part_name) == part_name.lower())
                        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                        .first())
        roll_count = latest_entry.count if latest_entry else (wrap_part.initial_count if wrap_part else 0)
        remainder_entry = TableStock.query.filter_by(type="pallet_wrap_remainder").first()
        used_in_current_roll = remainder_entry.count if remainder_entry else 0
        bodies_per_wrap_roll = 7
        if used_in_current_roll <= 0 or used_in_current_roll >= bodies_per_wrap_roll:
            fraction_remaining = 0
        else:
            fraction_remaining = (bodies_per_wrap_roll - used_in_current_roll) / bodies_per_wrap_roll
        display_count = max(0.0, roll_count + fraction_remaining)
        return round(display_count, 2), part_name

    def pallet_wrap_state():
        target_name = "Pallet Wrap"
        wrap_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == target_name.lower()).first()
        part_name = wrap_part.name if wrap_part else target_name
        latest_entry = (PrintedPartsCount.query
                        .filter(func.lower(PrintedPartsCount.part_name) == part_name.lower())
                        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                        .first())
        roll_count = latest_entry.count if latest_entry else (wrap_part.initial_count if wrap_part else 0)
        remainder_entry = TableStock.query.filter_by(type="pallet_wrap_remainder").first()
        used_in_current_roll = remainder_entry.count if remainder_entry else 0
        return part_name, roll_count, used_in_current_roll, remainder_entry

    # Align pallet wrap display with fractional rolls remaining
    wrap_count, wrap_name = pallet_wrap_display_count()
    if wrap_name in hardware_counts:
        hardware_counts[wrap_name] = wrap_count
    brad_display, brad_name = fractional_strip_display_count(
        BRAD_NAILS_PART_NAME,
        BRAD_NAILS_UNITS_PER_STRIP
    )
    if brad_name in hardware_counts:
        hardware_counts[brad_name] = brad_display

    # 3. Handle POST actions
    if request.method == 'POST':
        selected_part = request.form.get('hardware_part', selected_part)
        action = request.form.get('action')
        paid_all_supplier = None
        if action and action.startswith('paid_all:'):
            paid_all_supplier = action.split(':', 1)[1]
            action = 'paid_all'

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

        # ------------------------------------------------------
        # B) INCREMENT / DECREMENT / BULK UPDATE the count
        # ------------------------------------------------------
        elif action in ['increment', 'decrement', 'bulk', 'quick_add']:
            part_name = request.form['hardware_part']
            # For increment/decrement, default to 1; for bulk, read from 'amount';
            # for quick add, use 'quick_amount'
            if action == 'quick_add':
                amount_str = request.form.get('quick_amount', '1')
            elif action == 'bulk':
                amount_str = request.form.get('amount', '').strip()
                if not amount_str:
                    flash("Please enter a bulk amount to adjust stock.", "error")
                    return redirect(url_for('counting_hardware', selected=selected_part))
            else:
                amount_str = request.form.get('amount', '1')
            wrap_part_name, roll_count, used_in_current_roll, remainder_entry = pallet_wrap_state()
            is_pallet_wrap = part_name and wrap_part_name and part_name.lower() == wrap_part_name.lower()
            is_brad_nails = part_name and part_name.lower() == BRAD_NAILS_PART_NAME.lower()
            if is_pallet_wrap:
                try:
                    amount = float(amount_str)
                except ValueError:
                    flash("Amount must be a number.", "error")
                    return redirect(url_for('counting_hardware'))
                if action in ['increment', 'quick_add']:
                    amount = abs(amount)
                elif action == 'decrement':
                    amount = -abs(amount)
                if amount == 0:
                    flash("Pallet Wrap adjustment must be non-zero.", "error")
                    return redirect(url_for('counting_hardware'))
            elif is_brad_nails:
                try:
                    amount = float(amount_str)
                except ValueError:
                    flash("Amount must be a number.", "error")
                    return redirect(url_for('counting_hardware'))
                if action in ['increment', 'quick_add']:
                    amount = abs(amount)
                elif action == 'decrement':
                    amount = -abs(amount)
                if amount == 0:
                    flash("Brad Nails adjustment must be non-zero.", "error")
                    return redirect(url_for('counting_hardware'))
                units_target = amount * BRAD_NAILS_UNITS_PER_STRIP
                if abs(units_target - round(units_target)) > 1e-6:
                    flash("Brad Nails adjustments must be in 0.25 strip increments.", "error")
                    return redirect(url_for('counting_hardware'))
            else:
                try:
                    amount = int(amount_str)
                except ValueError:
                    flash("Amount must be a number.", "error")
                    return redirect(url_for('counting_hardware'))

            # Validate the selected part
            if part_name not in hardware_counts_raw:
                flash(f"Invalid hardware part: {part_name}", "error")
                return redirect(url_for('counting_hardware'))

            current_count = hardware_counts_raw.get(part_name, hardware_counts.get(part_name, 0))
            new_count = current_count

            if is_pallet_wrap:
                bodies_per_wrap_roll = 7
                bodies_delta = int(round(amount * bodies_per_wrap_roll))
                if bodies_delta == 0:
                    flash("Pallet Wrap adjustments must be at least 1/7 of a roll.", "error")
                    return redirect(url_for('counting_hardware'))

                bodies_available = (roll_count * bodies_per_wrap_roll) - used_in_current_roll
                new_bodies_available = bodies_available + bodies_delta
                if new_bodies_available < 0:
                    flash(f"Not enough stock to remove. Current count for '{part_name}': {current_count}", "error")
                    return redirect(url_for('counting_hardware'))

                new_count = ceil(new_bodies_available / bodies_per_wrap_roll) if new_bodies_available > 0 else 0
                new_used_in_current_roll = 0 if new_bodies_available <= 0 else (
                    (bodies_per_wrap_roll - (new_bodies_available % bodies_per_wrap_roll)) % bodies_per_wrap_roll
                )
                if new_count < current_count:
                    check_and_notify_low_stock(part_name, current_count, new_count)

                now = london_now()
                new_entry = PrintedPartsCount(
                    part_name=part_name,
                    count=new_count,
                    date=now.date(),
                    time=now.time()
                )
                db.session.add(new_entry)
                if remainder_entry:
                    remainder_entry.count = new_used_in_current_roll
                else:
                    db.session.add(TableStock(type="pallet_wrap_remainder", count=new_used_in_current_roll))
                db.session.commit()

                hardware_counts[part_name] = new_count
                hardware_counts_raw[part_name] = new_count

                applied_rolls = round(bodies_delta / bodies_per_wrap_roll, 2)
                if round(amount, 2) != applied_rolls:
                    flash(
                        f"{part_name} adjusted by {applied_rolls} rolls (rounded to 1/7 roll increments). New count: {new_count}",
                        "success"
                    )
                else:
                    flash(f"{part_name} updated successfully! New count: {new_count}", "success")
                return redirect(url_for('counting_hardware', selected=selected_part))
            elif is_brad_nails:
                ok, canonical_name, available_strips = adjust_fractional_strip_inventory(
                    part_name,
                    amount,
                    units_per_strip=BRAD_NAILS_UNITS_PER_STRIP
                )
                if not ok:
                    if amount < 0:
                        flash(
                            f"Not enough stock to remove. Current count for '{canonical_name}': {available_strips:.2f}",
                            "error"
                        )
                    else:
                        flash("Unable to update Brad Nails stock.", "error")
                    return redirect(url_for('counting_hardware', selected=selected_part))

                db.session.commit()
                brad_display, brad_name = fractional_strip_display_count(
                    canonical_name,
                    BRAD_NAILS_UNITS_PER_STRIP
                )
                hardware_counts[brad_name] = brad_display
                hardware_counts_raw[brad_name] = brad_display
                flash(f"{brad_name} updated successfully! New count: {brad_display}", "success")
                return redirect(url_for('counting_hardware', selected=selected_part))
            else:
                if action in ['increment', 'quick_add']:
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
            now = london_now()
            new_entry = PrintedPartsCount(
                part_name=part_name,
                count=new_count,
                date=now.date(),
                time=now.time()
            )
            db.session.add(new_entry)
            db.session.commit()

            # Ensure the rendered page reflects the updated count immediately
            hardware_counts[part_name] = new_count
            hardware_counts_raw[part_name] = new_count

            flash(f"{part_name} updated successfully! New count: {new_count}", "success")

        # Redirect after handling POST to prevent duplicate submissions on refresh
        return redirect(url_for('counting_hardware', selected=selected_part))

    # 4. Build recent change log (adds/removals) for hardware parts
    hardware_log = []
    hardware_part_names = [p.name for p in hardware_parts]
    initial_by_name = {p.name: (p.initial_count or 0) for p in hardware_parts}

    if hardware_part_names:
        recent_entries = (
            PrintedPartsCount.query
            .filter(PrintedPartsCount.part_name.in_(hardware_part_names))
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .limit(50)
            .all()
        )

        for entry in recent_entries:
            previous = (
                PrintedPartsCount.query
                .filter(PrintedPartsCount.part_name == entry.part_name)
                .filter(or_(
                    PrintedPartsCount.date < entry.date,
                    and_(PrintedPartsCount.date == entry.date, PrintedPartsCount.time < entry.time),
                ))
                .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                .first()
            )
            previous_count = previous.count if previous else initial_by_name.get(entry.part_name, 0)
            delta = (entry.count or 0) - (previous_count or 0)
            hardware_log.append({
                "part_name": entry.part_name,
                "date": entry.date,
                "time": entry.time,
                "new_count": entry.count,
                "delta": delta,
            })

    # 5. Render the template
    return render_template(
        'counting_hardware.html',
        hardware_parts=hardware_parts,
        hardware_counts=hardware_counts,
        selected_part=selected_part,
        hardware_log=hardware_log
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

    def pod_base_serial_for_form(serial):
        cleaned = strip_table_serial_suffixes(serial, remove_color=True, remove_lite=True)
        return re.sub(r"\s*-\s*[67]\s*$", "", cleaned, flags=re.IGNORECASE).strip()

    def remember_pod_completion_form():
        submitted_serial = request.form.get("serial_number", "")
        session["pod_completion_form_values"] = {
            "start_time": request.form.get("start_time", ""),
            "finish_time": request.form.get("finish_time", ""),
            "serial_number": submitted_serial,
            "base_serial_number": pod_base_serial_for_form(submitted_serial),
            "size_selector": request.form.get("size_selector", "7ft"),
            "table_type": request.form.get("table_type", "Champion"),
            "issue": request.form.get("issue", ""),
            "lunch": request.form.get("lunch", "No"),
        }
        session.modified = True

    def redirect_back_to_pod_form():
        remember_pod_completion_form()
        return redirect(url_for('pods'))
    
    if request.method == 'POST':
        worker = session['worker']
        raw_serial = (request.form.get('serial_number') or "").strip()
        issue_text = request.form['issue']
        lunch = request.form['lunch']
        size_selector = request.form.get('size_selector', '7ft')
        table_type_selector = request.form.get('table_type', 'Champion')
        selected_table_type = (
            TABLE_TYPE_LITE
            if table_type_selector.strip().lower() == "lite"
            else TABLE_TYPE_CHAMPION
        )

        clean_serial = strip_table_serial_suffixes(raw_serial, remove_color=True, remove_lite=True)
        clean_serial = re.sub(r"\s*-\s*[67]\s*$", "", clean_serial, flags=re.IGNORECASE).strip()
        if not clean_serial:
            flash("Please enter a valid serial number.", "error")
            return redirect_back_to_pod_form()

        if selected_table_type == TABLE_TYPE_LITE:
            if size_selector == '6ft':
                serial_number = f"{clean_serial} - 6 - L"
            else:
                serial_number = f"{clean_serial} - 7 - L"
        else:
            serial_number = f"{clean_serial} - 6" if size_selector == '6ft' else clean_serial

        actual_table_type = table_type_from_serial(serial_number)
        
        try:
            start_time = datetime.strptime(request.form['start_time'], "%H:%M").time()
        except ValueError:
            start_time = datetime.strptime(request.form['start_time'], "%H:%M:%S").time()
        try:
            finish_time = datetime.strptime(request.form['finish_time'], "%H:%M").time()
        except ValueError:
            finish_time = datetime.strptime(request.form['finish_time'], "%H:%M:%S").time()
        
        # Determine if it's a 6ft pod based on serial number or size selector
        is_6ft = size_selector == '6ft' or serial_is_6ft(serial_number)
        
        # Determine which felt and carpet to deduct
        felt_part = FELT_PART_NAME
        carpet_part = "6ft Carpet" if is_6ft else "7ft Carpet"
        
        low_stock_messages = []
        parts_to_deduct = []

        if actual_table_type == TABLE_TYPE_CHAMPION:
            felt_count = get_felt_count()
            if felt_count < 2:
                flash(f"Not enough {felt_part} in stock!", "error")
                return redirect_back_to_pod_form()
            parts_to_deduct.append((felt_part, felt_count, 2))

            carpet_entry = PrintedPartsCount.query.filter_by(part_name=carpet_part).order_by(
                PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
            if not carpet_entry or carpet_entry.count < 1:
                flash(f"Not enough {carpet_part} in stock!", "error")
                return redirect_back_to_pod_form()
            parts_to_deduct.append((carpet_part, carpet_entry.count, 1))

        # Check and deduct Tee Nuts
        tee_nuts_entry = PrintedPartsCount.query.filter_by(part_name="M10x13mm Tee Nut").order_by(
            PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
        if not tee_nuts_entry or tee_nuts_entry.count < 16:
            flash("Not enough M10x13mm Tee Nuts in stock! Need 16 per pod.", "error")
            return redirect_back_to_pod_form()
        parts_to_deduct.append(("M10x13mm Tee Nut", tee_nuts_entry.count, 16))

        if actual_table_type == TABLE_TYPE_CHAMPION:
            black_staples_entry = PrintedPartsCount.query.filter_by(part_name="Rows of Black Staples").order_by(
                PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc()).first()
            if not black_staples_entry or black_staples_entry.count < 2:
                flash("Not enough Rows of Black Staples in stock! Need 2 per pod.", "error")
                return redirect_back_to_pod_form()
            parts_to_deduct.append(("Rows of Black Staples", black_staples_entry.count, 2))
        
        try:
            # Create and save the new pod
            new_pod = CompletedPods(
                worker=worker,
                start_time=start_time,
                finish_time=finish_time,
                serial_number=serial_number,
                issue=issue_text,
                lunch=lunch,
                date=date.today()
            )

            def record_part_usage(part_name, current_count, decrement):
                new_count = current_count - decrement
                usage_entry = PrintedPartsCount(
                    part_name=part_name,
                    count=new_count,
                    date=datetime.utcnow().date(),
                    time=datetime.utcnow().time()
                )
                db.session.add(usage_entry)
                check_and_notify_low_stock(
                    part_name,
                    current_count,
                    new_count,
                    collected_warnings=low_stock_messages
                )

            # Record deductions as new inventory entries so history and UI stay in sync
            for part_name, current_count, decrement in parts_to_deduct:
                record_part_usage(part_name, current_count, decrement)

            db.session.add(new_pod)
            db.session.commit()
            deductions_summary = ", ".join(
                f"{decrement} {part_name}" for part_name, _, decrement in parts_to_deduct
            )
            flash(
                f"Pod entry added successfully! Deducted {deductions_summary}",
                "success"
            )

            start_dt = datetime.combine(date.today(), start_time)
            finish_dt = datetime.combine(date.today(), finish_time)

            # Handle overnight finish (next day)
            if finish_time < start_time:
                finish_dt = datetime.combine(date.today() + timedelta(days=1), finish_time)

            # Adjust for lunch break (30 minutes)
            if lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            # Format as H:MM hours and prevent negatives
            delta = finish_dt - start_dt
            total_minutes = max(0, int(delta.total_seconds() // 60))
            hours = total_minutes // 60
            minutes = total_minutes % 60
            time_taken_str = f"{hours}:{minutes:02d} hours"

            # --- NTFY Notification ---
            size = "6ft" if is_6ft else "7ft"
            type_label = "Lite" if actual_table_type == TABLE_TYPE_LITE else "Champion"
            message_lines = []
            if low_stock_messages:
                message_lines.append("LOW STOCK WARNING")
                for warning in low_stock_messages:
                    message_lines.append(f"- {warning}")
                message_lines.append("")
                message_lines.append("Completion Details:")
            message_lines.append(f"Serial: {serial_number}")
            message_lines.append(f"Time Taken: {time_taken_str}")
            message = "\n".join(message_lines)
            if low_stock_messages:
                title = f"[LOW STOCK] Pod Completed: {type_label} {size}"
            else:
                title = f"Pod Completed: {type_label} {size}"
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
            return redirect_back_to_pod_form()

        session.pop("pod_completion_form_values", None)
        return redirect(url_for('pods'))

    today = date.today()
    # Retrieve today's pods.
    completed_pods = CompletedPods.query.filter_by(date=today).all()
    last_entry = CompletedPods.query.order_by(CompletedPods.id.desc()).first()
    current_time = last_entry.finish_time.strftime("%H:%M") if last_entry else datetime.now().strftime("%H:%M")
    default_table_type = (
        "Lite"
        if last_entry and table_type_from_serial(last_entry.serial_number) == TABLE_TYPE_LITE
        else "Champion"
    )
    
    # Retrieve all pods for the current month.
    all_pods_this_month = CompletedPods.query.filter(
        extract('year', CompletedPods.date) == today.year,
        extract('month', CompletedPods.date) == today.month
    ).all()
    pods_this_month = len(all_pods_this_month)
    
    # Helper function: classify a pod as 6ft if its serial number (with spaces removed) ends with "-6"
    def is_6ft(serial):
        return serial_is_6ft(serial)
    
    current_production_pods_6ft = sum(1 for pod in all_pods_this_month if is_6ft(pod.serial_number))
    current_production_pods_7ft = pods_this_month - current_production_pods_6ft
    pod_type_totals = {"champion": 0, "lite": 0}
    pod_type_worker_counts = {}
    for pod in all_pods_this_month:
        pod_type = table_type_from_serial(pod.serial_number)
        type_key = "lite" if pod_type == TABLE_TYPE_LITE else "champion"
        pod_type_totals[type_key] += 1

        worker_name = (pod.worker or "Unknown").strip() or "Unknown"
        if worker_name not in pod_type_worker_counts:
            pod_type_worker_counts[worker_name] = {
                "worker": worker_name,
                "champion": 0,
                "lite": 0,
                "total": 0,
            }
        pod_type_worker_counts[worker_name][type_key] += 1
        pod_type_worker_counts[worker_name]["total"] += 1
    pod_type_worker_rows = sorted(
        pod_type_worker_counts.values(),
        key=lambda row: (-row["total"], row["worker"].lower())
    )
    
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

    daily_pods = (
        CompletedPods.query
        .filter(CompletedPods.date.in_(last_working_days))
        .order_by(CompletedPods.date.desc(), CompletedPods.id.asc())
        .all()
    )
    daily_history_by_date = {}
    for pod in daily_pods:
        if pod.date not in daily_history_by_date:
            daily_history_by_date[pod.date] = {
                "date": pod.date.strftime("%A %d/%m/%y"),
                "count": 0,
                "champion": 0,
                "lite": 0,
                "serial_numbers": []
            }
        entry = daily_history_by_date[pod.date]
        type_key = "lite" if table_type_from_serial(pod.serial_number) == TABLE_TYPE_LITE else "champion"
        entry[type_key] += 1
        entry["count"] += 1
        entry["serial_numbers"].append(pod.serial_number)

    daily_history_formatted = []
    for entry in daily_history_by_date.values():
        daily_history_formatted.append({
            "date": entry["date"],
            "count": entry["count"],
            "champion": entry["champion"],
            "lite": entry["lite"],
            "serial_numbers": ", ".join(entry["serial_numbers"])
        })

    def parse_time_string(value):
        if not value:
            return None
        if isinstance(value, time):
            return value
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
        return None

    def calculate_pod_duration(pod):
        start_time_obj = parse_time_string(pod.start_time)
        finish_time_obj = parse_time_string(pod.finish_time)
        if not start_time_obj or not finish_time_obj:
            return None

        start_dt = datetime.combine(pod.date, start_time_obj)
        finish_dt = datetime.combine(pod.date, finish_time_obj)
        if finish_time_obj < start_time_obj:
            overnight_dt = datetime.combine(pod.date + timedelta(days=1), finish_time_obj)
            if (overnight_dt - start_dt) <= timedelta(hours=12):
                finish_dt = overnight_dt

        if pod.lunch and str(pod.lunch).lower() == "yes":
            finish_dt -= timedelta(minutes=30)

        delta = finish_dt - start_dt
        if delta.total_seconds() < 0:
            return None
        if delta < timedelta(minutes=10):
            return None
        if delta > timedelta(hours=8):
            return None
        return delta

    def format_avg_duration(total_seconds, count):
        if not count:
            return "N/A"
        avg_seconds = total_seconds / count
        avg_seconds = max(0, int(round(avg_seconds)))
        hours, remainder = divmod(avg_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    
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

        month_pods = CompletedPods.query.filter(
            extract('year', CompletedPods.date) == yr,
            extract('month', CompletedPods.date) == mo
        ).all()

        type_counts = {
            TABLE_TYPE_CHAMPION: 0,
            TABLE_TYPE_LITE: 0,
        }
        type_stats = {
            TABLE_TYPE_CHAMPION: {"seconds": 0, "count": 0},
            TABLE_TYPE_LITE: {"seconds": 0, "count": 0},
        }
        for pod in month_pods:
            pod_type = table_type_from_serial(pod.serial_number)
            if pod_type not in type_counts:
                pod_type = TABLE_TYPE_CHAMPION
            type_counts[pod_type] += 1

            duration = calculate_pod_duration(pod)
            if duration is None:
                continue
            type_stats[pod_type]["seconds"] += duration.total_seconds()
            type_stats[pod_type]["count"] += 1

        monthly_totals_formatted.append({
            "month": date(year=yr, month=mo, day=1).strftime("%B %Y"),
            "count": total_pods,
            "champion_count": type_counts[TABLE_TYPE_CHAMPION],
            "lite_count": type_counts[TABLE_TYPE_LITE],
            "avg_hours_champion": format_avg_duration(
                type_stats[TABLE_TYPE_CHAMPION]["seconds"],
                type_stats[TABLE_TYPE_CHAMPION]["count"]
            ),
            "avg_hours_lite": format_avg_duration(
                type_stats[TABLE_TYPE_LITE]["seconds"],
                type_stats[TABLE_TYPE_LITE]["count"]
            )
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
    next_serial_number, default_size = _next_pod_serial_and_size()
    pod_form_values = session.get("pod_completion_form_values") or {}
    form_start_time = pod_form_values.get("start_time") or current_time
    form_finish_time = pod_form_values.get("finish_time") or current_time
    form_serial_number = pod_form_values.get("serial_number") or next_serial_number
    form_base_serial_number = pod_form_values.get("base_serial_number") or pod_base_serial_for_form(form_serial_number)
    form_size = pod_form_values.get("size_selector") or default_size
    form_table_type = pod_form_values.get("table_type") or default_table_type
    form_issue = pod_form_values.get("issue") or ""
    form_lunch = pod_form_values.get("lunch") or "No"
    
    return render_template(
        'pods.html',
        issues=issues,
        current_time=current_time,
        form_start_time=form_start_time,
        form_finish_time=form_finish_time,
        form_serial_number=form_serial_number,
        form_base_serial_number=form_base_serial_number,
        form_size=form_size,
        form_table_type=form_table_type,
        form_issue=form_issue,
        form_lunch=form_lunch,
        pod_form_restored=bool(pod_form_values),
        completed_pods=completed_pods,
        pods_this_month=pods_this_month,
        current_production_pods_7ft=current_production_pods_7ft,
        current_production_pods_6ft=current_production_pods_6ft,
        daily_history=daily_history_formatted,
        monthly_totals=monthly_totals_formatted,
        next_serial_number=next_serial_number,
        target_7ft=target_7ft,
        target_6ft=target_6ft,
        default_size=default_size,
        default_table_type=default_table_type,
        pod_type_totals=pod_type_totals,
        pod_type_worker_rows=pod_type_worker_rows
    )

@app.route('/admin/raw_data', methods=['GET', 'POST'])
def manage_raw_data():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    raw_data_sections = [
        {"key": "pods", "label": "Completed Pods"},
        {"key": "top_rails", "label": "Top Rails"},
        {"key": "bodies", "label": "Completed Bodies"},
    ]
    raw_data_models = {
        "pods": CompletedPods,
        "top_rails": TopRail,
        "bodies": CompletedTable,
    }
    raw_data_labels = {section["key"]: section["label"] for section in raw_data_sections}

    def redirect_to_raw_data(default_section="pods"):
        redirect_args = {}
        serial_filter = (request.form.get('serial_number_filter') or request.args.get('serial_number') or '').strip()
        section = request.form.get('section') or request.args.get('section') or default_section
        if section not in raw_data_models:
            section = default_section if default_section in raw_data_models else "pods"

        page = request.form.get('page') or request.args.get('page')
        per_page = request.form.get('per_page') or request.args.get('per_page')

        redirect_args["section"] = section
        if serial_filter:
            redirect_args["serial_number"] = serial_filter
        if page:
            redirect_args["page"] = page
        if per_page:
            redirect_args["per_page"] = per_page

        return redirect(url_for('manage_raw_data', **redirect_args))

    if request.method == 'POST':
        table = request.form.get('table')
        entry_id = request.form.get('id')
        entry = None
        model = raw_data_models.get(table)
        if model and entry_id:
            entry = model.query.get(entry_id)

        if entry:
            body_color_key_for_stock = None
            if 'delete' in request.form:
                # Revert inventory if deleting a table or top rail entry.
                if table == 'bodies':
                    body_table_type, body_color_key = get_body_build_metadata(entry)
                    body_color_key_for_stock = body_color_key
                    parts_used = body_parts_for_completion(
                        entry.serial_number,
                        body_table_type,
                        body_color_key
                    )

                    # Revert each part's inventory.
                    for part_name, qty in parts_used.items():
                        if part_name == BRAD_NAILS_PART_NAME:
                            adjust_fractional_strip_inventory(
                                part_name,
                                qty,
                                units_per_strip=BRAD_NAILS_UNITS_PER_STRIP
                            )
                            continue
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

                    if body_table_type == TABLE_TYPE_CHAMPION:
                        size_key = "6" if serial_is_6ft(entry.serial_number) else "7"
                        body_piece_keys = [
                            f"{body_color_key}_{size_key}_window_side",
                            f"{body_color_key}_{size_key}_blank_side",
                            f"{body_color_key}_{size_key}_triangle_end",
                            f"{body_color_key}_{size_key}_color_ball_end",
                        ]
                        for part_key in body_piece_keys:
                            part_entry = BodyPieceCount.query.filter_by(part_key=part_key).first()
                            if not part_entry:
                                part_entry = BodyPieceCount(part_key=part_key, count=0)
                                db.session.add(part_entry)
                            part_entry.count += 1
                    db.session.commit()

                elif table == 'top_rails':
                    # Parts used for a top rail
                    parts_used = {
                        **dict(TOP_RAIL_PARTS_REQUIREMENTS),
                        BRAD_NAILS_PART_NAME: 0.5
                    }
                    # Revert each part's inventory for the top rail.
                    for part_name, qty in parts_used.items():
                        if part_name == BRAD_NAILS_PART_NAME:
                            adjust_fractional_strip_inventory(
                                part_name,
                                qty,
                                units_per_strip=BRAD_NAILS_UNITS_PER_STRIP
                            )
                            continue
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
                    size = "6ft" if serial_is_6ft(entry.serial_number) else "7ft"
                    color_key = body_color_key_for_stock or color_key_from_serial(entry.serial_number)
                    stock_type = body_stock_type_key(size, body_table_type, color_key)

                    stock_entry = TableStock.query.filter_by(type=stock_type).first()
                    if stock_entry and stock_entry.count > 0:
                        old_count = stock_entry.count
                        stock_entry.count -= 1
                        record_table_stock_log(
                            stock_type,
                            "delete_body",
                            session.get('worker'),
                            -1,
                            old_count,
                            stock_entry.count,
                            f"Deleted body {entry.serial_number}"
                        )
                        db.session.commit()
                    delete_body_build_metadata(entry.id)
                # If deleting a top rail, also update the table stock
                elif table == 'top_rails':
                    def rail_is_6ft(serial):
                        return serial_is_6ft(serial)

                    def rail_color_key(serial):
                        norm = serial.replace(" ", "").upper()
                        if "-GO" in norm:
                            return "grey_oak"
                        if "-O" in norm and "-GO" not in norm:
                            return "rustic_oak"
                        if "-C" in norm:
                            return "stone"
                        if "-RB" in norm:
                            return "rustic_black"
                        return "black"

                    size = "6ft" if rail_is_6ft(entry.serial_number) else "7ft"
                    color_key = rail_color_key(entry.serial_number)
                    stock_type = f'top_rail_{size}_{color_key}'

                    stock_entry = TableStock.query.filter_by(type=stock_type).first()
                    if stock_entry and stock_entry.count > 0:
                        old_count = stock_entry.count
                        stock_entry.count -= 1
                        record_table_stock_log(
                            stock_type,
                            "delete_top_rail",
                            session.get('worker'),
                            -1,
                            old_count,
                            stock_entry.count,
                            f"Deleted top rail {entry.serial_number}"
                        )
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
                        return redirect_to_raw_data(table)
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
                        return redirect_to_raw_data(table)

                db.session.commit()
                flash(f"{table.capitalize()} entry updated successfully!", "success")
        else:
            flash("Raw data entry not found.", "error")

        return redirect_to_raw_data(table)

    serial_number_query = (request.args.get('serial_number') or '').strip()
    active_section = request.args.get('section') or "pods"
    if active_section not in raw_data_models:
        active_section = "pods"

    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1

    per_page = request.args.get('per_page', 25, type=int)
    if per_page not in (10, 25, 50, 100):
        per_page = 25

    def filtered_query(model):
        query = model.query
        if serial_number_query:
            original_like = f"%{serial_number_query.upper()}%"
            normalized_query = serial_number_query.replace(" ", "").upper()
            search_filters = [
                func.upper(model.serial_number).like(original_like)
            ]
            if normalized_query:
                normalized_serial = func.upper(func.replace(model.serial_number, " ", ""))
                search_filters.append(normalized_serial.like(f"%{normalized_query}%"))
            query = query.filter(or_(*search_filters))
        return query

    active_model = raw_data_models[active_section]
    active_query = filtered_query(active_model).order_by(active_model.date.desc(), active_model.id.desc())
    pagination = active_query.paginate(page=page, per_page=per_page, error_out=False)

    if pagination.pages and page > pagination.pages:
        redirect_args = {
            "section": active_section,
            "page": pagination.pages,
            "per_page": per_page,
        }
        if serial_number_query:
            redirect_args["serial_number"] = serial_number_query
        return redirect(url_for('manage_raw_data', **redirect_args))

    raw_counts = {active_section: pagination.total}
    for section in raw_data_sections:
        section_key = section["key"]
        if section_key != active_section:
            raw_counts[section_key] = filtered_query(raw_data_models[section_key]).count()

    page_start = 0 if pagination.total == 0 else ((pagination.page - 1) * per_page) + 1
    page_end = min(pagination.page * per_page, pagination.total)

    return render_template(
        'admin_raw_data.html',
        rows=pagination.items,
        raw_data_sections=raw_data_sections,
        raw_counts=raw_counts,
        active_section=active_section,
        active_label=raw_data_labels[active_section],
        serial_number_query=serial_number_query,
        pagination=pagination,
        per_page=per_page,
        page_start=page_start,
        page_end=page_end
    )

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

    def update_body_popup_counter(increment_amount):
        """Track how many new bodies have been counted and trigger the popup every 10."""
        if increment_amount <= 0:
            return
        pending_total = session.get('body_popup_counter', 0) + increment_amount
        triggers = pending_total // 10
        session['body_popup_counter'] = pending_total % 10
        if triggers:
            session['show_body_popup'] = True

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

        if section.endswith("Body") and new_entry.count > 0:
            update_body_popup_counter(new_entry.count)

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

    show_body_popup = session.pop('show_body_popup', False)

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
        current_year=today.year,
        show_body_popup=show_body_popup
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


CUSHION_SIZES = ["6ft", "7ft"]
CUSHION_SHAPES = [1, 2, 3, 4, 5, 6]
CUSHION_END_TYPES = ["Big end", "Small end"]
CUSHION_CONSUMABLE_GLOVES = [
    {"name": "Disposable Gloves", "label": "Disposable Gloves"},
    {"name": "Work Gloves", "label": "Work Gloves"},
]
CUSHION_CONSUMABLE_PAINT_BRUSH = {"name": "Paint Brush", "label": "Paint Brush"}
CUSHION_CONSUMABLE_TEE_NUTS = {"name": "M6 x 19mm Tee Nut", "label": "Tee Nuts"}
CUSHION_CONSUMABLE_SANDING = [
    {"name": "Sanding Belts", "label": "Sanding Belts"},
    {"name": "Sanding Pads", "label": "Sanding Pads"},
]
CUSHION_CONSUMABLES = [
    *CUSHION_CONSUMABLE_GLOVES,
    CUSHION_CONSUMABLE_PAINT_BRUSH,
    CUSHION_CONSUMABLE_TEE_NUTS,
    *CUSHION_CONSUMABLE_SANDING,
]
CUSHION_SPINDLE_REMINDER_INTERVAL = 30
CUSHION_GLUE_END_DISPLAY_TEE_NUTS_PER_CUSHION = 4
CUSHION_CUSHIONS_PER_SET = 6

CUSHION_STAGE_PLAIN = "plain"
CUSHION_STAGE_SIZE_SHAPE = "size_shape"
CUSHION_STAGE_SIZE_ONLY = "size_only"
CUSHION_STAGE_END_TYPE = "end_type"

CUSHION_WORKFLOW_STAGES = [
    {
        "key": "cut_1m",
        "label": "Cut into 1m Lengths",
        "short_label": "Cut 1m Lengths",
        "variant": CUSHION_STAGE_PLAIN,
        "unit_label": "lengths",
        "units_per_set": 6,
    },
    {
        "key": "spindle_mould",
        "label": "Spindle mould",
        "short_label": "Spindle",
        "variant": CUSHION_STAGE_PLAIN,
        "unit_label": "cushions",
        "units_per_set": 6,
    },
    {
        "key": "cutting_rubber_1m_lengths",
        "label": "Cut Rubber into 1m lengths",
        "short_label": "Cut Rubber into 1m lengths",
        "variant": CUSHION_STAGE_PLAIN,
        "unit_label": "lengths",
        "units_per_set": 6,
    },
    {
        "key": "spray_glue_join_rubber",
        "label": "Spray glue and join rubber",
        "short_label": "Glue rubber",
        "variant": CUSHION_STAGE_PLAIN,
        "unit_label": "cushions",
        "units_per_set": 6,
    },
    {
        "key": "router_slot",
        "label": "Put through router table to create slot",
        "short_label": "Router slot",
        "variant": CUSHION_STAGE_PLAIN,
        "unit_label": "cushions",
        "units_per_set": 6,
    },
    {
        "key": "shape_cushions",
        "label": "Shape cushions",
        "short_label": "Shape",
        "variant": CUSHION_STAGE_SIZE_SHAPE,
        "unit_label": "cushions",
        "units_per_set": 6,
    },
    {
        "key": "punch_rubber_ends",
        "label": "Punch out rubber ends",
        "short_label": "Punch ends",
        "variant": CUSHION_STAGE_END_TYPE,
        "unit_label": "ends",
        "units_per_set": 12,
    },
    {
        "key": "glue_ends",
        "label": "Glue ends on",
        "short_label": "Glue ends",
        "variant": CUSHION_STAGE_SIZE_SHAPE,
        "unit_label": "cushions",
        "units_per_set": 6,
    },
    {
        "key": "sand_ends",
        "label": "Shape ends",
        "short_label": "Shape ends",
        "variant": CUSHION_STAGE_SIZE_SHAPE,
        "unit_label": "cushions",
        "units_per_set": 6,
    },
    {
        "key": "sand_tops",
        "label": "Sand tops",
        "short_label": "Sand tops",
        "variant": CUSHION_STAGE_SIZE_SHAPE,
        "unit_label": "cushions",
        "units_per_set": 6,
    },
    {
        "key": "bundle",
        "label": "Bundle",
        "short_label": "Bundle",
        "variant": CUSHION_STAGE_SIZE_ONLY,
        "unit_label": "sets",
        "units_per_set": 1,
        "completes_stock": True,
    },
]

CUSHION_STAGE_BY_KEY = {stage["key"]: stage for stage in CUSHION_WORKFLOW_STAGES}
CUSHION_STAGE_INPUTS = {
    "spindle_mould": [("cut_1m", "", 0, "")],
    "spray_glue_join_rubber": [
        ("spindle_mould", "", 0, ""),
        ("cutting_rubber_1m_lengths", "", 0, ""),
    ],
    "router_slot": [("spray_glue_join_rubber", "", 0, "")],
    "shape_cushions": [("router_slot", "", 0, "")],
    "sand_ends": [("glue_ends", None, None, "")],
    "sand_tops": [("sand_ends", None, None, "")],
}


class CushionWorkflowCount(db.Model):
    __tablename__ = 'cushion_workflow_count'
    __table_args__ = (
        db.UniqueConstraint('stage_key', 'size_label', 'shape_no', 'end_type', name='uq_cushion_workflow_count_variant'),
    )

    id = db.Column(db.Integer, primary_key=True)
    stage_key = db.Column(db.String(50), nullable=False)
    size_label = db.Column(db.String(10), nullable=False, default="")
    shape_no = db.Column(db.Integer, nullable=False, default=0)
    end_type = db.Column(db.String(20), nullable=False, default="")
    count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, nullable=False, default=london_now)


class CushionWorkflowLog(db.Model):
    __tablename__ = 'cushion_workflow_log'

    id = db.Column(db.Integer, primary_key=True)
    action_type = db.Column(db.String(30), nullable=False, default="add")
    stage_key = db.Column(db.String(50), nullable=False)
    stage_label = db.Column(db.String(120), nullable=False)
    size_label = db.Column(db.String(10), nullable=False, default="")
    shape_no = db.Column(db.Integer, nullable=False, default=0)
    end_type = db.Column(db.String(20), nullable=False, default="")
    worker = db.Column(db.String(50), nullable=False)
    delta = db.Column(db.Integer, nullable=False, default=0)
    count_after = db.Column(db.Integer, nullable=False, default=0)
    seconds_taken = db.Column(db.Integer, nullable=True)
    batch_number = db.Column(db.Integer, nullable=True)
    batch_date = db.Column(db.Date, nullable=True)
    note = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=london_now)


class CushionBatch(db.Model):
    __tablename__ = 'cushion_batch'

    id = db.Column(db.Integer, primary_key=True)
    batch_number = db.Column(db.Integer, unique=True, nullable=False)
    batch_name = db.Column(db.String(120), nullable=True)
    batch_date = db.Column(db.Date, nullable=False, default=date.today)
    started_at = db.Column(db.DateTime, nullable=False, default=london_now)
    started_by = db.Column(db.String(50), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)

    def __repr__(self):
        return f"<CushionBatch #{self.batch_number} {self.batch_date}>"


class CushionCompletedSet(db.Model):
    __tablename__ = 'cushion_completed_set'

    id = db.Column(db.Integer, primary_key=True)
    size_label = db.Column(db.String(10), nullable=False)
    worker = db.Column(db.String(50), nullable=False)
    stock_type = db.Column(db.String(50), nullable=False)
    stock_count_after = db.Column(db.Integer, nullable=False)
    estimated_seconds = db.Column(db.Integer, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=False, default=london_now)


class CushionCompressorCheck(db.Model):
    __tablename__ = 'cushion_compressor_check'
    __table_args__ = (
        db.UniqueConstraint('check_date', 'worker', name='uq_cushion_compressor_check_day_worker'),
    )

    id = db.Column(db.Integer, primary_key=True)
    check_date = db.Column(db.Date, nullable=False)
    worker = db.Column(db.String(50), nullable=False)
    on_confirmed_at = db.Column(db.DateTime, nullable=True)
    off_confirmed_at = db.Column(db.DateTime, nullable=True)
    on_snoozed_until = db.Column(db.DateTime, nullable=True)
    off_snoozed_until = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=london_now)


class CushionStageLock(db.Model):
    __tablename__ = 'cushion_stage_lock'
    __table_args__ = (
        db.UniqueConstraint('worker', 'stage_key', name='uq_cushion_stage_lock_worker_stage'),
    )

    id = db.Column(db.Integer, primary_key=True)
    worker = db.Column(db.String(50), nullable=False)
    stage_key = db.Column(db.String(50), nullable=False)
    size_label = db.Column(db.String(10), nullable=False, default="")
    shape_no = db.Column(db.Integer, nullable=False, default=0)
    end_type = db.Column(db.String(20), nullable=False, default="")
    updated_at = db.Column(db.DateTime, nullable=False, default=london_now)


def ensure_cushion_workflow_tables():
    TableStock.__table__.create(db.engine, checkfirst=True)
    CushionWorkflowCount.__table__.create(db.engine, checkfirst=True)
    CushionWorkflowLog.__table__.create(db.engine, checkfirst=True)
    CushionBatch.__table__.create(db.engine, checkfirst=True)
    CushionCompletedSet.__table__.create(db.engine, checkfirst=True)
    CushionCompressorCheck.__table__.create(db.engine, checkfirst=True)
    CushionStageLock.__table__.create(db.engine, checkfirst=True)
    ensure_cushion_workflow_log_batch_columns()
    ensure_cushion_batch_columns()


def ensure_cushion_workflow_log_batch_columns():
    existing_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(cushion_workflow_log)")).fetchall()
    }
    columns_added = False
    if "batch_number" not in existing_columns:
        db.session.execute(text("ALTER TABLE cushion_workflow_log ADD COLUMN batch_number INTEGER"))
        columns_added = True
    if "batch_date" not in existing_columns:
        db.session.execute(text("ALTER TABLE cushion_workflow_log ADD COLUMN batch_date DATE"))
        columns_added = True
    if columns_added:
        db.session.commit()


def ensure_cushion_batch_columns():
    existing_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(cushion_batch)")).fetchall()
    }
    columns_added = False
    if "batch_name" not in existing_columns:
        db.session.execute(text("ALTER TABLE cushion_batch ADD COLUMN batch_name VARCHAR(120)"))
        columns_added = True
    if columns_added:
        db.session.commit()


def cushion_batch_display_name(batch):
    if not batch:
        return ""
    name = (batch.batch_name or "").strip()
    return name if name else f"Batch #{batch.batch_number}"


def get_active_cushion_batch():
    return (
        CushionBatch.query
        .filter_by(active=True)
        .order_by(CushionBatch.started_at.desc(), CushionBatch.id.desc())
        .first()
    )


def get_cushion_batch_by_number(batch_number):
    if batch_number in (None, "", "all", "current"):
        return None
    try:
        batch_number = int(batch_number)
    except (TypeError, ValueError):
        return None
    if batch_number <= 0:
        return None
    return CushionBatch.query.filter_by(batch_number=batch_number).first()


def start_new_cushion_batch(worker_name):
    active_batch = get_active_cushion_batch()
    if active_batch:
        active_batch.active = False

    last_batch_no = db.session.query(func.max(CushionBatch.batch_number)).scalar() or 0
    new_batch = CushionBatch(
        batch_number=last_batch_no + 1,
        batch_name=f"Batch #{last_batch_no + 1}",
        batch_date=london_now().date(),
        started_at=london_now(),
        started_by=worker_name,
        active=True
    )
    db.session.add(new_batch)
    db.session.flush()
    return new_batch


def get_or_create_active_cushion_batch(worker_name):
    active_batch = get_active_cushion_batch()
    if active_batch:
        return active_batch
    return start_new_cushion_batch(worker_name)


def cushion_timing_batch_filter(query, batch_number=None):
    if batch_number in (None, "", "all"):
        return query
    try:
        resolved = int(batch_number)
    except (TypeError, ValueError):
        return query
    if resolved <= 0:
        return query
    return query.filter(CushionWorkflowLog.batch_number == resolved)


def cushion_spindle_batch_added_total(batch_number=None):
    query = (
        db.session.query(func.coalesce(func.sum(CushionWorkflowLog.delta), 0))
        .filter(
            CushionWorkflowLog.action_type == "add",
            CushionWorkflowLog.stage_key == "spindle_mould",
            CushionWorkflowLog.delta > 0,
        )
    )
    query = cushion_timing_batch_filter(query, batch_number)
    return int(query.scalar() or 0)


def cushion_spindle_reminder_checkpoint(total_after, quantity, interval=CUSHION_SPINDLE_REMINDER_INTERVAL):
    try:
        total_after = max(0, int(total_after or 0))
        quantity = max(0, int(quantity or 0))
        interval = max(1, int(interval or 1))
    except (TypeError, ValueError):
        return None

    total_before = max(0, total_after - quantity)
    previous_checkpoint = total_before // interval
    current_checkpoint = total_after // interval
    if current_checkpoint > previous_checkpoint:
        return current_checkpoint * interval
    return None


def cushion_current_stage_key(batch_number=None):
    query = (
        CushionWorkflowLog.query
        .filter(CushionWorkflowLog.action_type == "add")
        .order_by(CushionWorkflowLog.created_at.desc(), CushionWorkflowLog.id.desc())
    )
    query = cushion_timing_batch_filter(query, batch_number)
    latest_log = query.first()
    return latest_log.stage_key if latest_log else None


def ensure_cushion_consumables():
    HardwarePart.__table__.create(db.engine, checkfirst=True)
    PrintedPartsCount.__table__.create(db.engine, checkfirst=True)
    ensure_part_threshold_schema()

    created_any = False
    for item in CUSHION_CONSUMABLES:
        if not HardwarePart.query.filter(func.lower(HardwarePart.name) == item["name"].lower()).first():
            db.session.add(HardwarePart(name=item["name"], initial_count=0))
            created_any = True
    if created_any:
        db.session.commit()


def consumable_stock_state(part_name):
    hardware_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == part_name.lower()).first()
    canonical_name = hardware_part.name if hardware_part else part_name
    latest_entry = (
        PrintedPartsCount.query
        .filter(func.lower(PrintedPartsCount.part_name) == canonical_name.lower())
        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
        .first()
    )
    count = latest_entry.count if latest_entry else (hardware_part.initial_count if hardware_part else 0)
    return count, canonical_name


def consumable_current_count(part_name):
    count, _ = consumable_stock_state(part_name)
    return count


def parse_positive_count(value, field_label="Quantity"):
    try:
        count = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_label} must be a whole number.")
    if count <= 0:
        raise ValueError(f"{field_label} must be at least 1.")
    if count > 999:
        raise ValueError(f"{field_label} is too high.")
    return count


def parse_cushion_add_quantity(quantity_value, manual_quantity=None, stage_key=None):
    if quantity_value == "manual":
        return parse_positive_count(manual_quantity, "Manual quantity")

    quantity = parse_positive_count(quantity_value or 1, "Quantity")
    allowed_quantities = (1, 5, 10)
    if stage_key == "spray_glue_join_rubber":
        allowed_quantities = (1, 5, 12)

    if quantity not in allowed_quantities:
        allowed_text = ", ".join(str(item) for item in allowed_quantities)
        stage_label = CUSHION_STAGE_BY_KEY.get(stage_key, {}).get("label", "this section")
        raise ValueError(
            f"For {stage_label}, choose {allowed_text} or enter a manual quantity."
        )
    return quantity


def cushion_stage_missing_requirements_message(stage_key, quantity, missing_requirements):
    stage = CUSHION_STAGE_BY_KEY.get(stage_key, {"label": stage_key})
    stage_label = stage["label"]

    top_missing = missing_requirements[0]
    source_stage = CUSHION_STAGE_BY_KEY.get(top_missing["input_stage_key"], {"label": top_missing["input_stage_key"]})
    source_stage_label = source_stage["label"]

    lines = [
        f"Cannot add {quantity} to {stage_label} yet.",
        f"You need more stock in {source_stage_label} first.",
        f"Needed: {top_missing['required_count']}, available: {top_missing['available']} ({top_missing['variant_label']}).",
        f"Go to {source_stage_label}, add enough pieces, then try this step again.",
    ]

    if len(missing_requirements) > 1:
        lines.append(f"There are {len(missing_requirements) - 1} other missing prerequisite stock item(s) as well.")

    return " ".join(lines)


def parse_consumable_delta(delta_value, manual_delta=None):
    try:
        delta = int(delta_value)
    except (TypeError, ValueError):
        raise ValueError("Choose a valid consumable adjustment.")
    if delta not in (-1, 1):
        raise ValueError("Choose -1 or +1 for consumables.")
    return delta


def cushion_consumables_for_stage(stage_key):
    items = list(CUSHION_CONSUMABLE_GLOVES)
    if stage_key == "glue_ends":
        items.append(CUSHION_CONSUMABLE_PAINT_BRUSH)
        items.append(CUSHION_CONSUMABLE_TEE_NUTS)
    if stage_key in ("sand_ends", "sand_tops"):
        items.extend(CUSHION_CONSUMABLE_SANDING)
    consumables = []
    for item in items:
        count, canonical_name = consumable_stock_state(item["name"])
        can_make_label = None
        if stage_key == "glue_ends" and item["name"] == CUSHION_CONSUMABLE_TEE_NUTS["name"]:
            can_make_count = max(0, int(count or 0)) // CUSHION_GLUE_END_DISPLAY_TEE_NUTS_PER_CUSHION
            set_count = can_make_count // CUSHION_CUSHIONS_PER_SET
            cushion_label = "cushion" if can_make_count == 1 else "cushions"
            set_label = "set" if set_count == 1 else "sets"
            can_make_label = f"Can make {can_make_count} {cushion_label} ({set_count} full {set_label})"
        consumables.append({
            **item,
            "name": canonical_name,
            "count": count,
            "can_make_label": can_make_label,
        })
    return consumables


def cushion_all_consumables():
    consumables = []
    for item in CUSHION_CONSUMABLES:
        count, canonical_name = consumable_stock_state(item["name"])
        consumables.append({
            **item,
            "name": canonical_name,
            "count": count,
        })
    return consumables


CUSHION_COMPRESSOR_WORKER_NAME = "katie"
CUSHION_COMPRESSOR_SNOOZE_MINUTES = 5
CUSHION_COMPRESSOR_OFF_REMINDER_TIME = time(16, 55)
CUSHION_COMPRESSOR_ACTIONS = {
    "compressor_confirm_on",
    "compressor_snooze_on",
    "compressor_confirm_off",
    "compressor_snooze_off",
}


def cushion_compressor_applies(worker_name):
    return CUSHION_COMPRESSOR_WORKER_NAME in (worker_name or "").strip().lower()


def get_or_create_cushion_compressor_check(worker_name, target_date=None):
    target_date = target_date or london_now().date()
    record = CushionCompressorCheck.query.filter_by(
        check_date=target_date,
        worker=worker_name
    ).first()
    if not record:
        record = CushionCompressorCheck(check_date=target_date, worker=worker_name)
        db.session.add(record)
        db.session.commit()
    return record


def cushion_compressor_context(worker_name):
    if not cushion_compressor_applies(worker_name):
        return {"enabled": False}

    now = london_now()
    record = get_or_create_cushion_compressor_check(worker_name, now.date())
    off_due = now.time() >= CUSHION_COMPRESSOR_OFF_REMINDER_TIME
    on_snoozed = record.on_snoozed_until and record.on_snoozed_until > now
    off_snoozed = record.off_snoozed_until and record.off_snoozed_until > now
    prompt_type = None
    refresh_candidates = []

    if off_due and not record.off_confirmed_at and not off_snoozed:
        prompt_type = "off"
    elif not off_due and not record.on_confirmed_at and not on_snoozed:
        prompt_type = "on"

    if not record.off_confirmed_at:
        off_due_at = datetime.combine(now.date(), CUSHION_COMPRESSOR_OFF_REMINDER_TIME)
        if off_due_at > now:
            refresh_candidates.append(off_due_at)
    if not record.on_confirmed_at and on_snoozed:
        refresh_candidates.append(record.on_snoozed_until)
    if not record.off_confirmed_at and off_snoozed:
        refresh_candidates.append(record.off_snoozed_until)

    next_refresh_seconds = None
    if refresh_candidates and not prompt_type:
        next_refresh_at = min(refresh_candidates)
        next_refresh_seconds = max(1, int((next_refresh_at - now).total_seconds()))

    if prompt_type == "off":
        prompt = {
            "title": "Turn the air compressor off",
            "message": "It is 4:55pm or later. Please turn the air compressor off before leaving.",
            "confirm_action": "compressor_confirm_off",
            "snooze_action": "compressor_snooze_off",
            "confirm_label": "Done, compressor is off",
            "snooze_label": "Remind me in 5 minutes",
        }
    elif prompt_type == "on":
        prompt = {
            "title": "Air compressor on?",
            "message": "Please turn the air compressor on before starting cushion work.",
            "confirm_action": "compressor_confirm_on",
            "snooze_action": "compressor_snooze_on",
            "confirm_label": "Yes, compressor is on",
            "snooze_label": "Remind me in 5 minutes",
        }
    else:
        prompt = None

    return {
        "enabled": True,
        "prompt_type": prompt_type,
        "prompt": prompt,
        "worker": record.worker,
        "check_date": record.check_date,
        "on_confirmed_at": record.on_confirmed_at,
        "off_confirmed_at": record.off_confirmed_at,
        "on_display": record.on_confirmed_at.strftime("%H:%M") if record.on_confirmed_at else None,
        "off_display": record.off_confirmed_at.strftime("%H:%M") if record.off_confirmed_at else None,
        "on_snoozed_until": record.on_snoozed_until,
        "off_snoozed_until": record.off_snoozed_until,
        "off_due": off_due,
        "off_due_display": CUSHION_COMPRESSOR_OFF_REMINDER_TIME.strftime("%H:%M"),
        "next_refresh_seconds": next_refresh_seconds,
    }


def handle_cushion_compressor_action(action, worker_name):
    if action not in CUSHION_COMPRESSOR_ACTIONS:
        return False
    if not cushion_compressor_applies(worker_name):
        flash("Compressor reminders are not enabled for this worker.", "info")
        return True

    now = london_now()
    record = get_or_create_cushion_compressor_check(worker_name, now.date())
    if action == "compressor_confirm_on":
        record.on_confirmed_at = now
        record.on_snoozed_until = None
        flash("Air compressor ON check saved.", "success")
    elif action == "compressor_snooze_on":
        record.on_snoozed_until = now + timedelta(minutes=CUSHION_COMPRESSOR_SNOOZE_MINUTES)
        flash("Air compressor ON reminder snoozed for 5 minutes.", "info")
    elif action == "compressor_confirm_off":
        record.off_confirmed_at = now
        record.off_snoozed_until = None
        flash("Air compressor OFF check saved.", "success")
    elif action == "compressor_snooze_off":
        record.off_snoozed_until = now + timedelta(minutes=CUSHION_COMPRESSOR_SNOOZE_MINUTES)
        flash("Air compressor OFF reminder snoozed for 5 minutes.", "info")

    record.updated_at = now
    db.session.commit()
    return True


def cushion_compressor_recent_checks(limit=14):
    return (
        CushionCompressorCheck.query
        .order_by(CushionCompressorCheck.check_date.desc(), CushionCompressorCheck.worker.asc())
        .limit(limit)
        .all()
    )


def cushion_stage_lock_payload(record):
    if not record:
        return None
    return {
        "size_label": record.size_label or "",
        "shape_no": record.shape_no or 0,
        "end_type": record.end_type or "",
    }


def get_cushion_stage_lock(worker_name, stage_key):
    if not worker_name or stage_key not in CUSHION_STAGE_BY_KEY:
        return None
    record = CushionStageLock.query.filter_by(worker=worker_name, stage_key=stage_key).first()
    if not record:
        return None
    try:
        normalize_cushion_variant(stage_key, record.size_label, record.shape_no, record.end_type)
    except ValueError:
        db.session.delete(record)
        db.session.commit()
        return None
    return cushion_stage_lock_payload(record)


def cushion_stage_locks_for_worker(worker_name):
    if not worker_name:
        return {}
    records = CushionStageLock.query.filter_by(worker=worker_name).all()
    locks = {}
    invalid_records = []
    for record in records:
        if record.stage_key not in CUSHION_STAGE_BY_KEY:
            invalid_records.append(record)
            continue
        try:
            normalize_cushion_variant(record.stage_key, record.size_label, record.shape_no, record.end_type)
        except ValueError:
            invalid_records.append(record)
            continue
        locks[record.stage_key] = cushion_stage_lock_payload(record)

    if invalid_records:
        for record in invalid_records:
            db.session.delete(record)
        db.session.commit()

    return locks


def save_cushion_stage_lock(worker_name, stage_key, size_label="", shape_no=0, end_type="", commit=False):
    if not worker_name:
        raise ValueError("Worker is required.")
    size_label, shape_no, end_type = normalize_cushion_variant(stage_key, size_label, shape_no, end_type)
    record = CushionStageLock.query.filter_by(worker=worker_name, stage_key=stage_key).first()
    if not record:
        record = CushionStageLock(worker=worker_name, stage_key=stage_key)
        db.session.add(record)
    record.size_label = size_label
    record.shape_no = shape_no
    record.end_type = end_type
    record.updated_at = london_now()
    if commit:
        db.session.commit()
    return record


def clear_cushion_stage_lock(worker_name, stage_key, commit=False):
    record = CushionStageLock.query.filter_by(worker=worker_name, stage_key=stage_key).first()
    if record:
        db.session.delete(record)
        if commit:
            db.session.commit()


def set_consumable_stock(part_name, new_count):
    allowed_names = {item["name"].lower(): item["name"] for item in CUSHION_CONSUMABLES}
    requested_name = (part_name or "").lower()
    if requested_name not in allowed_names:
        raise ValueError("Unknown consumable.")

    try:
        new_count = int(new_count)
    except (TypeError, ValueError):
        raise ValueError("Enter a valid consumable stock count.")
    if new_count < 0:
        raise ValueError("Consumable stock cannot be negative.")

    current_count, canonical_name = consumable_stock_state(allowed_names[requested_name])
    if new_count < current_count:
        check_and_notify_low_stock(canonical_name, current_count, new_count)

    now = london_now()
    db.session.add(PrintedPartsCount(
        part_name=canonical_name,
        count=new_count,
        date=now.date(),
        time=now.time()
    ))
    return canonical_name, current_count, new_count


def adjust_consumable_stock(part_name, delta):
    allowed_names = {item["name"].lower(): item["name"] for item in CUSHION_CONSUMABLES}
    requested_name = (part_name or "").lower()
    if requested_name not in allowed_names:
        raise ValueError("Unknown consumable.")

    current_count, canonical_name = consumable_stock_state(allowed_names[requested_name])
    new_count = current_count + int(delta)
    if new_count < 0:
        raise ValueError(f"Not enough {canonical_name} in stock.")

    if new_count < current_count:
        check_and_notify_low_stock(canonical_name, current_count, new_count)

    now = london_now()
    db.session.add(PrintedPartsCount(
        part_name=canonical_name,
        count=new_count,
        date=now.date(),
        time=now.time()
    ))
    return canonical_name, new_count


def cushion_stock_key(size_label):
    return f"cushion_set_{size_label.lower()}"


def cushion_format_duration(seconds):
    if seconds is None:
        return "N/A"
    seconds = max(0, int(round(float(seconds))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def cushion_variant_key(stage_key, size_label="", shape_no=0, end_type=""):
    return f"{stage_key}|{size_label or ''}|{int(shape_no or 0)}|{end_type or ''}"


def normalize_cushion_variant(stage_key, size_label="", shape_no=0, end_type=""):
    stage = CUSHION_STAGE_BY_KEY.get(stage_key)
    if not stage:
        raise ValueError("Unknown cushion stage.")

    variant = stage["variant"]
    if variant == CUSHION_STAGE_PLAIN:
        return "", 0, ""

    if variant == CUSHION_STAGE_END_TYPE:
        end_type = (end_type or "").strip()
        if end_type not in CUSHION_END_TYPES:
            raise ValueError("Choose a valid rubber end type.")
        return "", 0, end_type

    if variant == CUSHION_STAGE_SIZE_ONLY:
        size_label = (size_label or "").strip()
        if size_label not in CUSHION_SIZES:
            raise ValueError("Choose a valid cushion size.")
        return size_label, 0, ""

    if variant == CUSHION_STAGE_SIZE_SHAPE:
        size_label = (size_label or "").strip()
        try:
            shape_no = int(shape_no)
        except (TypeError, ValueError):
            shape_no = 0
        if size_label not in CUSHION_SIZES:
            raise ValueError("Choose a valid cushion size.")
        if shape_no not in CUSHION_SHAPES:
            raise ValueError("Choose a valid cushion shape.")
        return size_label, shape_no, ""

    raise ValueError("Unknown cushion stage variant.")


def get_cushion_count_record(stage_key, size_label="", shape_no=0, end_type="", create=True):
    size_label, shape_no, end_type = normalize_cushion_variant(stage_key, size_label, shape_no, end_type)
    record = CushionWorkflowCount.query.filter_by(
        stage_key=stage_key,
        size_label=size_label,
        shape_no=shape_no,
        end_type=end_type
    ).first()
    if not record and create:
        record = CushionWorkflowCount(
            stage_key=stage_key,
            size_label=size_label,
            shape_no=shape_no,
            end_type=end_type,
            count=0,
            updated_at=london_now()
        )
        db.session.add(record)
        db.session.flush()
    return record


def cushion_count_value(stage_key, size_label="", shape_no=0, end_type=""):
    record = get_cushion_count_record(stage_key, size_label, shape_no, end_type, create=False)
    return record.count if record else 0


def cushion_current_count_for_variant(stage_key, size_label="", shape_no=0, end_type=""):
    size_label, shape_no, end_type = normalize_cushion_variant(stage_key, size_label, shape_no, end_type)
    if stage_key == "bundle":
        stock_entry = TableStock.query.filter_by(type=cushion_stock_key(size_label)).first()
        return stock_entry.count if stock_entry else 0
    return cushion_count_value(stage_key, size_label, shape_no, end_type)


def cushion_variant_display(stage_key, size_label="", shape_no=0, end_type=""):
    stage = CUSHION_STAGE_BY_KEY.get(stage_key, {"label": stage_key})
    parts = [stage["label"]]
    if size_label:
        parts.append(size_label)
    if shape_no:
        parts.append(f"Shape {shape_no}")
    if end_type:
        parts.append(end_type)
    return " - ".join(parts)


def cushion_action_duration_seconds(stage_key, size_label, shape_no, end_type, worker, now, batch_number=None):
    previous = (
        cushion_timing_batch_filter(
            CushionWorkflowLog.query.filter_by(
            action_type="add",
            stage_key=stage_key,
            size_label=size_label,
            shape_no=shape_no,
            end_type=end_type,
            worker=worker
            ),
            batch_number
        )
        .filter(CushionWorkflowLog.delta > 0)
        .order_by(CushionWorkflowLog.created_at.desc(), CushionWorkflowLog.id.desc())
        .first()
    )
    if not previous:
        return None

    seconds = int((now - previous.created_at).total_seconds())
    if seconds < 10 or seconds > 8 * 60 * 60:
        return None
    return seconds


def apply_cushion_count_delta(stage_key, size_label, shape_no, end_type, delta, worker, action_type="add", note=None, seconds_taken=None, batch_number=None, batch_date=None):
    stage = CUSHION_STAGE_BY_KEY[stage_key]
    record = get_cushion_count_record(stage_key, size_label, shape_no, end_type, create=True)
    new_count = record.count + int(delta)
    if new_count < 0:
        raise ValueError(f"Not enough {cushion_variant_display(stage_key, record.size_label, record.shape_no, record.end_type)} to move on.")

    record.count = new_count
    record.updated_at = london_now()
    log_entry = CushionWorkflowLog(
        action_type=action_type,
        stage_key=stage_key,
        stage_label=stage["label"],
        size_label=record.size_label,
        shape_no=record.shape_no,
        end_type=record.end_type,
        worker=worker,
        delta=int(delta),
        count_after=record.count,
        seconds_taken=seconds_taken,
        batch_number=batch_number,
        batch_date=batch_date,
        note=note
    )
    db.session.add(log_entry)
    return record


def cushion_input_requirements(stage_key, size_label, shape_no, end_type):
    requirements = []
    for input_stage_key, input_size, input_shape, input_end in CUSHION_STAGE_INPUTS.get(stage_key, []):
        requirements.append((
            input_stage_key,
            size_label if input_size is None else input_size,
            shape_no if input_shape is None else input_shape,
            end_type if input_end is None else input_end,
        ))

    if stage_key == "glue_ends":
        requirements.append(("shape_cushions", size_label, shape_no, ""))
        if shape_no in (1, 6):
            requirements.append(("punch_rubber_ends", "", 0, "Big end"))
            requirements.append(("punch_rubber_ends", "", 0, "Big end"))
        else:
            requirements.append(("punch_rubber_ends", "", 0, "Big end"))
            requirements.append(("punch_rubber_ends", "", 0, "Small end"))

    if stage_key == "bundle":
        for bundle_shape_no in CUSHION_SHAPES:
            requirements.append(("sand_tops", size_label, bundle_shape_no, ""))

    return requirements


def cushion_estimated_set_seconds(size_label=None):
    total_seconds = 0
    has_data = False
    for stage in CUSHION_WORKFLOW_STAGES:
        query = (
            db.session.query(func.avg(CushionWorkflowLog.seconds_taken))
            .filter(
                CushionWorkflowLog.action_type == "add",
                CushionWorkflowLog.stage_key == stage["key"],
                CushionWorkflowLog.seconds_taken.isnot(None),
                CushionWorkflowLog.seconds_taken > 0
            )
        )
        if size_label and stage["variant"] in (CUSHION_STAGE_SIZE_SHAPE, CUSHION_STAGE_SIZE_ONLY):
            query = query.filter(CushionWorkflowLog.size_label == size_label)
        average_seconds = query.scalar()
        if average_seconds:
            has_data = True
            total_seconds += int(round(float(average_seconds) * stage["units_per_set"]))
    return total_seconds if has_data else None


def add_cushion_set_to_stock(size_label, worker, estimated_seconds=None):
    stock_type = cushion_stock_key(size_label)
    stock_entry = TableStock.query.filter_by(type=stock_type).first()
    if not stock_entry:
        stock_entry = TableStock(type=stock_type, count=0)
        db.session.add(stock_entry)
        db.session.flush()
    old_count = stock_entry.count
    stock_entry.count += 1
    record_table_stock_log(
        stock_type,
        "complete_cushion_set",
        worker,
        1,
        old_count,
        stock_entry.count,
        f"Completed {size_label} cushion set"
    )

    completed_set = CushionCompletedSet(
        size_label=size_label,
        worker=worker,
        stock_type=stock_type,
        stock_count_after=stock_entry.count,
        estimated_seconds=estimated_seconds if estimated_seconds is not None else cushion_estimated_set_seconds(size_label),
        completed_at=london_now()
    )
    db.session.add(completed_set)
    return completed_set


def complete_available_cushion_sets(size_label, worker):
    completed_sets = []
    while True:
        sanded_top_records = [
            get_cushion_count_record("sand_tops", size_label, shape_no, "", create=True)
            for shape_no in CUSHION_SHAPES
        ]
        if any(record.count <= 0 for record in sanded_top_records):
            break

        _, new_sets = record_cushion_stage_add("bundle", size_label, 0, "", worker)
        completed_sets.extend(new_sets)

    return completed_sets


def cushion_tee_nuts_required_for_glue_ends(size_label, quantity=1):
    if size_label == "6ft":
        return 3 * int(quantity)
    if size_label == "7ft":
        return 4 * int(quantity)
    return 0


def record_cushion_stage_add_many(stage_key, size_label, shape_no, end_type, quantity, worker):
    size_label, shape_no, end_type = normalize_cushion_variant(stage_key, size_label, shape_no, end_type)
    quantity = parse_positive_count(quantity, "Quantity")

    requirements = cushion_input_requirements(stage_key, size_label, shape_no, end_type)
    required_counts = defaultdict(int)
    for requirement in requirements:
        required_counts[requirement] += quantity

    missing_requirements = []
    for (input_stage_key, input_size, input_shape, input_end), required_count in required_counts.items():
        input_record = get_cushion_count_record(input_stage_key, input_size, input_shape, input_end, create=True)
        if input_record.count < required_count:
            available = input_record.count
            label = cushion_variant_display(input_stage_key, input_record.size_label, input_record.shape_no, input_record.end_type)
            missing_requirements.append({
                "input_stage_key": input_stage_key,
                "required_count": required_count,
                "available": available,
                "variant_label": label,
            })

    if missing_requirements:
        raise ValueError(cushion_stage_missing_requirements_message(stage_key, quantity, missing_requirements))

    tee_nuts_required = 0
    if stage_key == "glue_ends":
        tee_nuts_required = cushion_tee_nuts_required_for_glue_ends(size_label, quantity)
        current_tee_nuts, tee_nuts_name = consumable_stock_state(CUSHION_CONSUMABLE_TEE_NUTS["name"])
        if current_tee_nuts < tee_nuts_required:
            raise ValueError(f"Not enough {tee_nuts_name} in stock. Need {tee_nuts_required}, have {current_tee_nuts}.")

    # Transition-safe behavior: if no batch exists yet, adopt the current WIP flow
    # (even if not at cut_1m) into a new active batch.
    active_batch = get_or_create_active_cushion_batch(worker)

    batch_number = active_batch.batch_number if active_batch else None
    batch_date = active_batch.batch_date if active_batch else None

    for (input_stage_key, input_size, input_shape, input_end), required_count in required_counts.items():
        apply_cushion_count_delta(
            input_stage_key,
            input_size,
            input_shape,
            input_end,
            -required_count,
            worker,
            action_type="move_out",
            note=f"Moved to {CUSHION_STAGE_BY_KEY[stage_key]['label']}",
            batch_number=batch_number,
            batch_date=batch_date
        )

    now = london_now()
    seconds_taken = cushion_action_duration_seconds(
        stage_key,
        size_label,
        shape_no,
        end_type,
        worker,
        now,
        batch_number=batch_number
    )
    if seconds_taken and quantity > 1:
        seconds_taken = max(1, int(round(seconds_taken / quantity)))

    if stage_key == "bundle":
        completed_sets = [
            add_cushion_set_to_stock(size_label, worker, seconds_taken)
            for _ in range(quantity)
        ]
        target_record = get_cushion_count_record(stage_key, size_label, 0, "", create=True)
        target_record.count = completed_sets[-1].stock_count_after
        target_record.updated_at = now
        db.session.add(CushionWorkflowLog(
            action_type="add",
            stage_key=stage_key,
            stage_label=CUSHION_STAGE_BY_KEY[stage_key]["label"],
            size_label=size_label,
            shape_no=0,
            end_type="",
            worker=worker,
            delta=quantity,
            count_after=target_record.count,
            seconds_taken=seconds_taken,
            batch_number=batch_number,
            batch_date=batch_date,
            note=f"Bundled {quantity} completed cushion set(s)"
        ))
        return target_record, completed_sets

    if tee_nuts_required:
        adjust_consumable_stock(CUSHION_CONSUMABLE_TEE_NUTS["name"], -tee_nuts_required)

    target_record = apply_cushion_count_delta(
        stage_key,
        size_label,
        shape_no,
        end_type,
        quantity,
        worker,
        action_type="add",
        seconds_taken=seconds_taken,
        batch_number=batch_number,
        batch_date=batch_date
    )

    completed_sets = []
    return target_record, completed_sets


def record_cushion_stage_add(stage_key, size_label, shape_no, end_type, worker):
    return record_cushion_stage_add_many(stage_key, size_label, shape_no, end_type, 1, worker)


def set_cushion_stage_count(stage_key, size_label, shape_no, end_type, new_count, worker):
    size_label, shape_no, end_type = normalize_cushion_variant(stage_key, size_label, shape_no, end_type)
    try:
        new_count = int(new_count)
    except (TypeError, ValueError):
        raise ValueError("Enter a valid count.")
    if new_count < 0:
        raise ValueError("Count cannot be negative.")

    if stage_key == "bundle":
        stock_type = cushion_stock_key(size_label)
        stock_entry = TableStock.query.filter_by(type=stock_type).first()
        if not stock_entry:
            stock_entry = TableStock(type=stock_type, count=0)
            db.session.add(stock_entry)
            db.session.flush()
        old_count = stock_entry.count
        stock_entry.count = new_count

        record = get_cushion_count_record(stage_key, size_label, shape_no, end_type, create=True)
        record.count = new_count
        record.updated_at = london_now()
        if old_count != new_count:
            db.session.add(CushionWorkflowLog(
                action_type="correction",
                stage_key=stage_key,
                stage_label=CUSHION_STAGE_BY_KEY[stage_key]["label"],
                size_label=size_label,
                shape_no=shape_no,
                end_type=end_type,
                worker=worker,
                delta=new_count - old_count,
                count_after=new_count,
                note="Manual finished cushion stock correction"
            ))
            record_table_stock_log(
                stock_type,
                "correction",
                worker,
                new_count - old_count,
                old_count,
                new_count,
                "Manual finished cushion stock correction"
            )
        return record

    record = get_cushion_count_record(stage_key, size_label, shape_no, end_type, create=True)
    delta = new_count - record.count
    if delta:
        apply_cushion_count_delta(
            stage_key,
            size_label,
            shape_no,
            end_type,
            delta,
            worker,
            action_type="correction",
            note="Manual count correction"
        )
    return record


def cushion_variant_timing(stage_key, size_label="", shape_no=0, end_type="", worker_name=None, batch_number=None):
    size_label, shape_no, end_type = normalize_cushion_variant(stage_key, size_label, shape_no, end_type)
    average_query = (
        db.session.query(func.avg(CushionWorkflowLog.seconds_taken))
        .filter(
            CushionWorkflowLog.action_type == "add",
            CushionWorkflowLog.stage_key == stage_key,
            CushionWorkflowLog.size_label == size_label,
            CushionWorkflowLog.shape_no == shape_no,
            CushionWorkflowLog.end_type == end_type,
            CushionWorkflowLog.seconds_taken.isnot(None),
            CushionWorkflowLog.seconds_taken > 0
        )
    )
    last_query = (
        CushionWorkflowLog.query
        .filter_by(
            action_type="add",
            stage_key=stage_key,
            size_label=size_label,
            shape_no=shape_no,
            end_type=end_type
        )
        .filter(CushionWorkflowLog.seconds_taken.isnot(None), CushionWorkflowLog.seconds_taken > 0)
    )
    if worker_name:
        average_query = average_query.filter(CushionWorkflowLog.worker == worker_name)
        last_query = last_query.filter(CushionWorkflowLog.worker == worker_name)

    average_query = cushion_timing_batch_filter(average_query, batch_number)
    last_query = cushion_timing_batch_filter(last_query, batch_number)

    average_seconds = average_query.scalar()
    last_log = (
        last_query
        .order_by(CushionWorkflowLog.created_at.desc(), CushionWorkflowLog.id.desc())
        .first()
    )
    return {
        "average_seconds": int(round(float(average_seconds))) if average_seconds else None,
        "average_display": cushion_format_duration(average_seconds) if average_seconds else "N/A",
        "last_seconds": last_log.seconds_taken if last_log else None,
        "last_display": cushion_format_duration(last_log.seconds_taken) if last_log else "N/A",
    }


def cushion_ready_bundle_count(size_label):
    counts = [
        cushion_count_value("sand_tops", size_label, shape_no, "")
        for shape_no in CUSHION_SHAPES
    ]
    return min(counts) if counts else 0


def cushion_ready_count_for_variant(stage_key, size_label="", shape_no=0, end_type=""):
    requirements = cushion_input_requirements(stage_key, size_label, shape_no, end_type)
    if not requirements:
        return 0

    required_counts = defaultdict(int)
    for requirement in requirements:
        required_counts[requirement] += 1

    ready_counts = []
    for (input_stage_key, input_size, input_shape, input_end), required_count in required_counts.items():
        available = cushion_current_count_for_variant(input_stage_key, input_size, input_shape, input_end)
        ready_counts.append(available // required_count)

    return min(ready_counts) if ready_counts else 0


def cushion_ready_count_for_stage(stage_key):
    stage = CUSHION_STAGE_BY_KEY[stage_key]
    if stage_key == "bundle":
        return sum(cushion_ready_bundle_count(size_label) for size_label in CUSHION_SIZES)
    if stage["variant"] == CUSHION_STAGE_PLAIN:
        return cushion_ready_count_for_variant(stage_key)
    if stage["variant"] == CUSHION_STAGE_END_TYPE:
        return 0
    if stage["variant"] == CUSHION_STAGE_SIZE_ONLY:
        return sum(cushion_ready_count_for_variant(stage_key, size_label=size_label) for size_label in CUSHION_SIZES)

    ready_count = 0
    for size_label in CUSHION_SIZES:
        for shape_no in CUSHION_SHAPES:
            ready_count += cushion_ready_count_for_variant(stage_key, size_label=size_label, shape_no=shape_no)
    return ready_count


def build_cushion_stage_context(include_timing=False, worker_name=None, batch_number=None, highlight_stage_key=None):
    stage_context = []
    furthest_in_progress_index = None
    furthest_ready_index = None
    for stage in CUSHION_WORKFLOW_STAGES:
        stage_index = len(stage_context)
        stage_total = 0
        groups = []
        ready_bundle_count = 0

        if stage["variant"] == CUSHION_STAGE_PLAIN:
            count = cushion_count_value(stage["key"])
            timing = cushion_variant_timing(stage["key"], worker_name=worker_name, batch_number=batch_number) if include_timing else None
            stage_total += count
            groups.append({
                "label": stage["short_label"],
                "variants": [{
                    "label": stage["short_label"],
                    "button_label": f"+1 {stage['short_label']}",
                    "size_label": "",
                    "shape_no": 0,
                    "end_type": "",
                    "count": count,
                    "count_label": f"Current {count}",
                    "timing": timing,
                }]
            })
        elif stage["variant"] == CUSHION_STAGE_END_TYPE:
            variants = []
            for end_type in CUSHION_END_TYPES:
                count = cushion_count_value(stage["key"], end_type=end_type)
                timing = cushion_variant_timing(stage["key"], end_type=end_type, worker_name=worker_name, batch_number=batch_number) if include_timing else None
                stage_total += count
                variants.append({
                    "label": end_type,
                    "button_label": f"+1 {end_type}",
                    "size_label": "",
                    "shape_no": 0,
                    "end_type": end_type,
                    "count": count,
                    "count_label": f"Stock {count}",
                    "timing": timing,
                })
            groups.append({"label": "Rubber ends", "variants": variants})
        elif stage["variant"] == CUSHION_STAGE_SIZE_ONLY:
            variants = []
            stock_summary = cushion_stock_summary()
            for size_label in CUSHION_SIZES:
                count = stock_summary.get(size_label, 0)
                ready_bundle_count += cushion_ready_bundle_count(size_label)
                timing = cushion_variant_timing(stage["key"], size_label=size_label, worker_name=worker_name, batch_number=batch_number) if include_timing else None
                stage_total += count
                variants.append({
                    "label": f"{size_label} Set",
                    "button_label": f"+1 {size_label} Set",
                    "size_label": size_label,
                    "shape_no": 0,
                    "end_type": "",
                    "count": count,
                    "count_label": "",
                    "timing": timing,
                })
            groups.append({"label": "Completed sets", "variants": variants})
        else:
            for size_label in CUSHION_SIZES:
                variants = []
                for shape_no in CUSHION_SHAPES:
                    count = cushion_count_value(stage["key"], size_label=size_label, shape_no=shape_no)
                    timing = cushion_variant_timing(stage["key"], size_label=size_label, shape_no=shape_no, worker_name=worker_name, batch_number=batch_number) if include_timing else None
                    stage_total += count
                    variants.append({
                        "label": f"Shape {shape_no}",
                        "button_label": f"+1 {size_label} S{shape_no}",
                        "size_label": size_label,
                        "shape_no": shape_no,
                        "end_type": "",
                        "count": count,
                        "count_label": f"Current {count}",
                        "timing": timing,
                    })
                groups.append({"label": size_label, "variants": variants})

        ready_to_work_count = cushion_ready_count_for_stage(stage["key"])
        if stage["key"] == "bundle":
            # Bundle is driven by upstream readiness rather than in-stage stock.
            has_wip = False
            status_label = f"{ready_to_work_count} ready to bundle" if ready_to_work_count else ""
        else:
            # For stage cards, highlight where pieces currently are, not the next ready stage.
            has_wip = False
            status_label = f"{stage_total} at this stage" if stage_total else ""

        if stage["key"] != "bundle" and stage_total > 0:
            furthest_in_progress_index = stage_index
        elif furthest_in_progress_index is None and ready_to_work_count > 0:
            furthest_ready_index = stage_index

        stage_context.append({
            **stage,
            "total": stage_total,
            "groups": groups,
            "has_wip": has_wip,
            "status_label": status_label,
            "ready_to_work_count": ready_to_work_count,
            "ready_bundle_count": ready_bundle_count,
        })

    # Highlight only one stage card.
    # Prefer the stage most recently worked on for the active batch.
    highlight_index = None
    if highlight_stage_key:
        for index, stage_item in enumerate(stage_context):
            if stage_item["key"] == highlight_stage_key:
                highlight_index = index
                break
    # Fallback to stock-based position if there is no recent activity signal.
    if highlight_index is None:
        highlight_index = furthest_in_progress_index
    if highlight_index is None:
        highlight_index = furthest_ready_index
    if highlight_index is not None and 0 <= highlight_index < len(stage_context):
        stage_context[highlight_index]["has_wip"] = True

    return stage_context


def flatten_cushion_stage_variants(stage_context):
    variants = []
    for group in stage_context["groups"]:
        for variant in group["variants"]:
            lock_label_parts = []
            if variant["size_label"]:
                lock_label_parts.append(variant["size_label"])
            if variant["shape_no"]:
                lock_label_parts.append(f"Shape {variant['shape_no']}")
            if variant["end_type"]:
                lock_label_parts.append(variant["end_type"])
            if not lock_label_parts:
                lock_label_parts.append(stage_context["short_label"])

            variants.append({
                **variant,
                "group_label": group["label"],
                "variant_key": cushion_variant_key(
                    stage_context["key"],
                    variant["size_label"],
                    variant["shape_no"],
                    variant["end_type"]
                ),
                "lock_label": " - ".join(lock_label_parts),
            })
    return variants


def cushion_stock_summary():
    summary = {}
    for size_label in CUSHION_SIZES:
        stock_type = cushion_stock_key(size_label)
        entry = TableStock.query.filter_by(type=stock_type).first()
        summary[size_label] = entry.count if entry else 0
    return summary


def cushion_timing_summary(batch_number=None):
    summary = {}
    for stage in CUSHION_WORKFLOW_STAGES:
        base_query = db.session.query(
                func.count(CushionWorkflowLog.id),
                func.avg(CushionWorkflowLog.seconds_taken),
                func.min(CushionWorkflowLog.seconds_taken),
                func.max(CushionWorkflowLog.seconds_taken)
            )
        rows = (
            cushion_timing_batch_filter(base_query, batch_number)
            .filter(
                CushionWorkflowLog.action_type == "add",
                CushionWorkflowLog.stage_key == stage["key"],
                CushionWorkflowLog.seconds_taken.isnot(None),
                CushionWorkflowLog.seconds_taken > 0
            )
            .first()
        )
        sample_count = rows[0] if rows else 0
        average_seconds = rows[1] if rows else None
        summary[stage["key"]] = {
            "sample_count": sample_count or 0,
            "average_seconds": int(round(float(average_seconds))) if average_seconds else None,
            "average_display": cushion_format_duration(average_seconds) if average_seconds else "N/A",
            "fastest_display": cushion_format_duration(rows[2]) if rows and rows[2] else "N/A",
            "slowest_display": cushion_format_duration(rows[3]) if rows and rows[3] else "N/A",
        }
    return summary


def cushion_stage_timing(stage_key, worker_name=None, batch_number=None):
    filters = [
        CushionWorkflowLog.action_type == "add",
        CushionWorkflowLog.stage_key == stage_key,
        CushionWorkflowLog.seconds_taken.isnot(None),
        CushionWorkflowLog.seconds_taken > 0,
    ]
    if worker_name:
        filters.append(CushionWorkflowLog.worker == worker_name)

    rows = (
        cushion_timing_batch_filter(
            db.session.query(
            func.count(CushionWorkflowLog.id),
            func.avg(CushionWorkflowLog.seconds_taken),
            ),
            batch_number
        )
        .filter(*filters)
        .first()
    )
    last_log = (
        cushion_timing_batch_filter(CushionWorkflowLog.query, batch_number)
        .filter(*filters)
        .order_by(CushionWorkflowLog.created_at.desc(), CushionWorkflowLog.id.desc())
        .first()
    )
    sample_count = rows[0] if rows else 0
    average_seconds = rows[1] if rows else None
    return {
        "sample_count": sample_count or 0,
        "average_seconds": int(round(float(average_seconds))) if average_seconds else None,
        "average_display": cushion_format_duration(average_seconds) if average_seconds else "N/A",
        "last_seconds": last_log.seconds_taken if last_log else None,
        "last_display": cushion_format_duration(last_log.seconds_taken) if last_log else "N/A",
    }


def parse_cushion_history_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def cushion_history_filter_state(args):
    today = london_now().date()
    period = args.get("period", "month")
    if period not in {"today", "week", "month", "custom", "all"}:
        period = "month"

    start_date = None
    end_date = None
    if period == "today":
        start_date = today
        end_date = today
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif period == "month":
        start_date = today.replace(day=1)
        end_date = today
    elif period == "custom":
        start_date = parse_cushion_history_date(args.get("start_date"))
        end_date = parse_cushion_history_date(args.get("end_date"))

    if start_date and end_date and start_date > end_date:
        start_date, end_date = end_date, start_date

    shape_no = None
    shape_value = (args.get("shape_no") or "").strip()
    if shape_value:
        try:
            shape_no = int(shape_value)
        except (TypeError, ValueError):
            shape_no = None
        if shape_no not in CUSHION_SHAPES:
            shape_no = None

    return {
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "start_date_value": start_date.strftime("%Y-%m-%d") if start_date else "",
        "end_date_value": end_date.strftime("%Y-%m-%d") if end_date else "",
        "start_dt": datetime.combine(start_date, time.min) if start_date else None,
        "end_dt": datetime.combine(end_date + timedelta(days=1), time.min) if end_date else None,
        "worker": (args.get("worker") or "").strip(),
        "stage_key": (args.get("stage_key") or "").strip(),
        "size_label": (args.get("size_label") or "").strip(),
        "shape_no": shape_no,
        "shape_value": str(shape_no) if shape_no else "",
        "end_type": (args.get("end_type") or "").strip(),
        "action_type": (args.get("action_type") or "").strip(),
    }


def cushion_history_filter_options():
    workers = set()
    try:
        workers.update(
            worker.name
            for worker in Worker.query.order_by(Worker.name.asc()).all()
            if worker.name
        )
    except OperationalError:
        db.session.rollback()
    workers.update(
        worker
        for (worker,) in db.session.query(CushionWorkflowLog.worker).distinct().all()
        if worker
    )
    workers.update(
        worker
        for (worker,) in db.session.query(CushionCompletedSet.worker).distinct().all()
        if worker
    )
    action_types = [
        action_type
        for (action_type,) in (
            db.session.query(CushionWorkflowLog.action_type)
            .distinct()
            .order_by(CushionWorkflowLog.action_type.asc())
            .all()
        )
        if action_type
    ]
    return {
        "workers": sorted(workers, key=lambda name: name.lower()),
        "stages": CUSHION_WORKFLOW_STAGES,
        "sizes": CUSHION_SIZES,
        "shapes": CUSHION_SHAPES,
        "end_types": CUSHION_END_TYPES,
        "action_types": action_types,
    }


def cushion_history_log_query(filters):
    query = CushionWorkflowLog.query
    if filters.get("start_dt"):
        query = query.filter(CushionWorkflowLog.created_at >= filters["start_dt"])
    if filters.get("end_dt"):
        query = query.filter(CushionWorkflowLog.created_at < filters["end_dt"])
    if filters.get("worker"):
        query = query.filter(CushionWorkflowLog.worker == filters["worker"])
    if filters.get("stage_key"):
        query = query.filter(CushionWorkflowLog.stage_key == filters["stage_key"])
    if filters.get("size_label"):
        query = query.filter(CushionWorkflowLog.size_label == filters["size_label"])
    if filters.get("shape_no") is not None:
        query = query.filter(CushionWorkflowLog.shape_no == filters["shape_no"])
    if filters.get("end_type"):
        query = query.filter(CushionWorkflowLog.end_type == filters["end_type"])
    if filters.get("action_type"):
        query = query.filter(CushionWorkflowLog.action_type == filters["action_type"])
    return query


def cushion_history_completed_query(filters):
    query = CushionCompletedSet.query
    if filters.get("start_dt"):
        query = query.filter(CushionCompletedSet.completed_at >= filters["start_dt"])
    if filters.get("end_dt"):
        query = query.filter(CushionCompletedSet.completed_at < filters["end_dt"])
    if filters.get("worker"):
        query = query.filter(CushionCompletedSet.worker == filters["worker"])
    if filters.get("size_label"):
        query = query.filter(CushionCompletedSet.size_label == filters["size_label"])
    if filters.get("stage_key") and filters["stage_key"] != "bundle":
        query = query.filter(CushionCompletedSet.id == -1)
    if filters.get("shape_no") is not None or filters.get("end_type"):
        query = query.filter(CushionCompletedSet.id == -1)
    if filters.get("action_type") and filters["action_type"] != "add":
        query = query.filter(CushionCompletedSet.id == -1)
    return query


def cushion_history_consumable_query(filters):
    consumable_names = [item["name"] for item in CUSHION_CONSUMABLES]
    query = PrintedPartsCount.query.filter(PrintedPartsCount.part_name.in_(consumable_names))
    if filters.get("start_date"):
        query = query.filter(PrintedPartsCount.date >= filters["start_date"])
    if filters.get("end_date"):
        query = query.filter(PrintedPartsCount.date <= filters["end_date"])
    return query


def cushion_history_sum_delta(query):
    return int(query.with_entities(func.coalesce(func.sum(CushionWorkflowLog.delta), 0)).scalar() or 0)


def cushion_history_summary(filters):
    log_query = cushion_history_log_query(filters)
    completed_query = cushion_history_completed_query(filters)
    average_seconds = (
        log_query
        .filter(CushionWorkflowLog.seconds_taken.isnot(None), CushionWorkflowLog.seconds_taken > 0)
        .with_entities(func.avg(CushionWorkflowLog.seconds_taken))
        .scalar()
    )
    completed_by_size = {
        size_label: completed_query.filter(CushionCompletedSet.size_label == size_label).count()
        for size_label in CUSHION_SIZES
    }
    return {
        "total_actions": log_query.count(),
        "added_units": cushion_history_sum_delta(
            log_query.filter(CushionWorkflowLog.action_type == "add", CushionWorkflowLog.delta > 0)
        ),
        "moved_out_units": abs(cushion_history_sum_delta(
            log_query.filter(CushionWorkflowLog.action_type == "move_out", CushionWorkflowLog.delta < 0)
        )),
        "corrections": log_query.filter(CushionWorkflowLog.action_type == "correction").count(),
        "completed_sets": completed_query.count(),
        "completed_by_size": completed_by_size,
        "average_display": cushion_format_duration(average_seconds) if average_seconds else "N/A",
    }


def cushion_completed_size_stats(today=None):
    today = today or london_now().date()
    month_start = datetime.combine(today.replace(day=1), time.min)
    year_start = datetime.combine(date(today.year, 1, 1), time.min)
    end_dt = datetime.combine(today + timedelta(days=1), time.min)

    def counts_since(start_dt):
        rows = (
            db.session.query(CushionCompletedSet.size_label, func.count(CushionCompletedSet.id))
            .filter(
                CushionCompletedSet.completed_at >= start_dt,
                CushionCompletedSet.completed_at < end_dt
            )
            .group_by(CushionCompletedSet.size_label)
            .all()
        )
        return {size_label: int(count or 0) for size_label, count in rows}

    month_counts = counts_since(month_start)
    year_counts = counts_since(year_start)
    return [
        {
            "size": size_label,
            "month": month_counts.get(size_label, 0),
            "year": year_counts.get(size_label, 0),
        }
        for size_label in CUSHION_SIZES
    ]


def cushion_completed_previous_month_stats(today=None, month_count=6):
    today = today or london_now().date()
    current_month_start = today.replace(day=1)

    def previous_month_start(month_start):
        return (month_start - timedelta(days=1)).replace(day=1)

    def next_month_start(month_start):
        if month_start.month == 12:
            return date(month_start.year + 1, 1, 1)
        return date(month_start.year, month_start.month + 1, 1)

    rows = []
    month_start = previous_month_start(current_month_start)
    for _ in range(month_count):
        start_dt = datetime.combine(month_start, time.min)
        end_dt = datetime.combine(next_month_start(month_start), time.min)
        query_rows = (
            db.session.query(CushionCompletedSet.size_label, func.count(CushionCompletedSet.id))
            .filter(
                CushionCompletedSet.completed_at >= start_dt,
                CushionCompletedSet.completed_at < end_dt
            )
            .group_by(CushionCompletedSet.size_label)
            .all()
        )
        counts = {size_label: int(count or 0) for size_label, count in query_rows}
        rows.append({
            "label": month_start.strftime("%B %Y"),
            "sizes": {size_label: counts.get(size_label, 0) for size_label in CUSHION_SIZES},
            "total": sum(counts.get(size_label, 0) for size_label in CUSHION_SIZES),
        })
        month_start = previous_month_start(month_start)

    return rows


def cushion_history_stage_summary(filters):
    rows = []
    selected_stage = filters.get("stage_key")
    for stage in CUSHION_WORKFLOW_STAGES:
        if selected_stage and selected_stage != stage["key"]:
            continue
        stage_filters = {**filters, "stage_key": stage["key"]}
        query = cushion_history_log_query(stage_filters)
        average_seconds = (
            query
            .filter(CushionWorkflowLog.seconds_taken.isnot(None), CushionWorkflowLog.seconds_taken > 0)
            .with_entities(func.avg(CushionWorkflowLog.seconds_taken))
            .scalar()
        )
        rows.append({
            "label": stage["label"],
            "actions": query.count(),
            "added_units": cushion_history_sum_delta(
                query.filter(CushionWorkflowLog.action_type == "add", CushionWorkflowLog.delta > 0)
            ),
            "moved_out_units": abs(cushion_history_sum_delta(
                query.filter(CushionWorkflowLog.action_type == "move_out", CushionWorkflowLog.delta < 0)
            )),
            "corrections": query.filter(CushionWorkflowLog.action_type == "correction").count(),
            "average_display": cushion_format_duration(average_seconds) if average_seconds else "N/A",
        })
    return rows


def cushion_history_clean_query_args(args):
    cleaned = {}
    for key, value in args.items():
        if key == "page" or value in (None, ""):
            continue
        cleaned[key] = value
    return cleaned


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
    
    # Get the base serial numbers of all completed tables (without color/Lite suffixes)
    completed_table_serials = db.session.query(CompletedTable.serial_number).all()
    completed_base_serials = []
    
    for (serial,) in completed_table_serials:
        completed_base_serials.append(base_serial_for_pod_matching(serial))
    hidden_body_picker_pod_ids = load_hidden_body_picker_pod_ids()
    
    # Find pods that haven't been converted to tables (considering base serial numbers)
    unconverted_pods = []
    for pod in CompletedPods.query.all():
        if pod.id in hidden_body_picker_pod_ids:
            continue
        # Clean the pod serial number if it has the prefix
        pod_serial = pod.serial_number
        if "**Pod Serial Number:" in pod_serial:
            pod_serial = pod_serial.replace("**Pod Serial Number:", "").strip()
            
        # Check if the base serial is not in completed tables
        if base_serial_for_pod_matching(pod_serial) not in completed_base_serials:
            unconverted_pods.append(pod)

    def ensure_quick_add_hardware_part(part_name):
        hardware_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == part_name.lower()).first()
        if not hardware_part:
            db.session.add(HardwarePart(name=part_name, initial_count=0))
            db.session.flush()
        return part_name

    def remember_body_completion_form():
        session["body_completion_form_values"] = {
            "start_time": request.form.get("start_time", ""),
            "finish_time": request.form.get("finish_time", ""),
            "serial_number": request.form.get("serial_number", ""),
            "formatted_serial_number": request.form.get("formatted_serial_number", ""),
            "table_type": request.form.get("table_type", "Champion"),
            "color_selector": request.form.get("color_selector", "Black"),
            "issue": request.form.get("issue", ""),
            "lunch": request.form.get("lunch", "No"),
        }
        session.modified = True

    def redirect_back_to_body_form():
        remember_body_completion_form()
        return redirect(url_for('bodies'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'quick_add_body_part':
            part_name = request.form.get('part_name', '').strip()
            quick_part = next(
                (part for part in BODIES_QUICK_ADD_PARTS if part["part_name"] == part_name),
                None
            )
            if not quick_part:
                flash("Invalid quick-add part selected.", "error")
                return redirect(url_for('bodies'))

            try:
                amount = int(request.form.get('quick_amount', 1))
            except ValueError:
                flash("Quick-add amount must be a whole number.", "error")
                return redirect(url_for('bodies'))

            amount = abs(amount)
            if amount <= 0:
                flash("Quick-add amount must be greater than zero.", "error")
                return redirect(url_for('bodies'))

            canonical_part_name = part_name
            if quick_part.get("hardware"):
                canonical_part_name = ensure_quick_add_hardware_part(part_name)

            current_count = _latest_part_count(canonical_part_name)
            new_count = current_count + amount
            db.session.add(PrintedPartsCount(
                part_name=canonical_part_name,
                count=new_count,
                date=date.today(),
                time=datetime.utcnow().time()
            ))
            db.session.commit()
            flash(f"Added {amount} to {quick_part['label']}. New count: {new_count}", "success")
            return redirect(url_for('bodies'))

        worker = session['worker']
        start_time = request.form['start_time']
        finish_time = request.form['finish_time']
        serial_number = request.form['serial_number']
        selected_pod_serial = clean_pod_serial_value(serial_number)
        color_selector = request.form.get('color_selector', 'Black')
        table_type_selector = request.form.get('table_type', 'Champion')
        selected_table_type = (
            TABLE_TYPE_LITE
            if table_type_selector.strip().lower() == "lite"
            else TABLE_TYPE_CHAMPION
        )
        issue_text = request.form['issue']
        lunch = request.form['lunch']

        formatted_serial = request.form.get('formatted_serial_number', '').strip()
        raw_serial = formatted_serial if formatted_serial else serial_number

        if "**Pod Serial Number:" in raw_serial:
            raw_serial = raw_serial.replace("**Pod Serial Number:", "").strip()

        clean_serial = strip_table_serial_suffixes(raw_serial, remove_color=True, remove_lite=True)
        normalized_serial = clean_serial.replace(" ", "").upper()
        has_6 = serial_is_6ft(clean_serial)
        has_7 = normalized_serial.endswith("-7") or "-7-" in normalized_serial

        if selected_table_type == TABLE_TYPE_LITE:
            if not has_6 and not has_7:
                clean_serial = f"{clean_serial} - 7"
            clean_serial = re.sub(r"\s*-\s*L\s*$", "", clean_serial, flags=re.IGNORECASE).strip()
            serial_number = f"{clean_serial} - L"
        else:
            if color_selector == 'Grey Oak':
                clean_serial += ' - GO'
            elif color_selector == 'Rustic Oak':
                clean_serial += ' - O'
            elif color_selector == 'Stone':
                clean_serial += ' - C'
            elif color_selector == 'Rustic Black':
                clean_serial += ' - RB'
            serial_number = clean_serial

        issue_text = request.form['issue']
        lunch = request.form['lunch']

        # ---------------------------
        # PARTS DEDUCTION LOGIC
        # ---------------------------
        low_stock_messages = []
        actual_table_type = table_type_from_serial(serial_number)
        selected_pod_table_type = table_type_from_serial(selected_pod_serial)
        selected_pod_size = serial_size_display_label(selected_pod_serial)
        completed_body_size = serial_size_display_label(serial_number)
        body_pod_mismatch_messages = []
        if selected_pod_size != completed_body_size:
            body_pod_mismatch_messages.append(
                f"Size mismatch: pod {selected_pod_size}, body {completed_body_size}"
            )
        if selected_pod_table_type != actual_table_type:
            body_pod_mismatch_messages.append(
                "Type mismatch: "
                f"pod {table_type_display_label(selected_pod_table_type)}, "
                f"body {table_type_display_label(actual_table_type)}"
            )
        selected_color_key = color_key_from_selector(color_selector)
        laminate_color_key = (
            selected_color_key
            if actual_table_type == TABLE_TYPE_LITE
            else color_key_from_serial(serial_number)
        )
        parts_to_deduct = body_parts_for_completion(
            serial_number,
            actual_table_type,
            laminate_color_key
        )
        if actual_table_type == TABLE_TYPE_CHAMPION:
            size_key = "6" if serial_is_6ft(serial_number) else "7"
            color_key = color_key_from_serial(serial_number)
            body_piece_keys = [
                f"{color_key}_{size_key}_window_side",
                f"{color_key}_{size_key}_blank_side",
                f"{color_key}_{size_key}_triangle_end",
                f"{color_key}_{size_key}_color_ball_end",
            ]
            body_piece_entries = []
            for part_key in body_piece_keys:
                part_entry = BodyPieceCount.query.filter_by(part_key=part_key).first()
                if not part_entry:
                    flash(f"No inventory set up for body piece {part_key}!", "error")
                    db.session.rollback()
                    return redirect_back_to_body_form()
                if part_entry.count < 1:
                    flash(
                        f"Not enough inventory for body piece {part_key}! Need 1, have {part_entry.count}",
                        "error"
                    )
                    db.session.rollback()
                    return redirect_back_to_body_form()
                body_piece_entries.append(part_entry)
            for part_entry in body_piece_entries:
                old_count = part_entry.count
                part_entry.count -= 1
                check_and_notify_low_stock(
                    part_entry.part_key,
                    old_count,
                    part_entry.count,
                    collected_warnings=low_stock_messages
                )

        # Deduct each required part from the inventory
        for part_name, quantity_needed in parts_to_deduct.items():
            if part_name == BRAD_NAILS_PART_NAME:
                ok, canonical_name, available_strips = adjust_fractional_strip_inventory(
                    part_name,
                    -quantity_needed,
                    units_per_strip=BRAD_NAILS_UNITS_PER_STRIP,
                    collected_warnings=low_stock_messages
                )
                if not ok:
                    flash(
                        f"Not enough inventory for {canonical_name} (need {quantity_needed}, have {available_strips:.2f}) to complete the body!",
                        "error"
                    )
                    db.session.rollback()
                    return redirect_back_to_body_form()
                continue
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
            allow_negative_stock = allows_negative_inventory(part_name)
            if old_count >= quantity_needed or allow_negative_stock:
                part_entry.count = old_count - quantity_needed
                check_and_notify_low_stock(
                    part_name,
                    old_count,
                    part_entry.count,
                    collected_warnings=low_stock_messages
                )
                check_and_notify_chinese_parts_order_more(
                    part_name,
                    old_count,
                    part_entry.count,
                    collected_warnings=low_stock_messages
                )
            else:
                flash(f"Not enough inventory for {part_name} (need {quantity_needed}, have {part_entry.count}) to complete the body!", "error")
                db.session.rollback()
                return redirect_back_to_body_form()

        # Deduct pallet wrap: 1 roll covers 7 bodies
        pallet_wrap_name = "Pallet Wrap"
        bodies_per_wrap_roll = 7
        wrap_remainder_key = "pallet_wrap_remainder"
        wrap_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == pallet_wrap_name.lower()).first()
        if wrap_part:
            pallet_wrap_name = wrap_part.name  # use canonical stored name
        else:
            # ensure the hardware part exists so counts stay in sync with Counting Hardware page
            new_wrap_part = HardwarePart(name=pallet_wrap_name, initial_count=0)
            db.session.add(new_wrap_part)
            db.session.commit()

        def get_current_stock(part_name):
            latest_entry = (PrintedPartsCount.query
                            .filter(func.lower(PrintedPartsCount.part_name) == part_name.lower())
                            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                            .first())
            if latest_entry:
                return latest_entry.count
            hardware_part = HardwarePart.query.filter_by(name=part_name).first()
            if hardware_part:
                return hardware_part.initial_count
            return None

        current_wrap_stock = get_current_stock(pallet_wrap_name)
        if current_wrap_stock is None:
            flash(f"{pallet_wrap_name} is not set up in inventory yet. Please add it before completing bodies.", "error")
            db.session.rollback()
            return redirect_back_to_body_form()

        remainder_entry = TableStock.query.filter_by(type=wrap_remainder_key).first()
        used_in_current_roll = remainder_entry.count if remainder_entry else 0  # bodies already wrapped on the current roll

        # Total bodies available across all rolls (including the open one if any)
        bodies_available = current_wrap_stock * bodies_per_wrap_roll - used_in_current_roll
        if bodies_available <= 0:
            flash(f"Not enough {pallet_wrap_name} in stock to wrap this body.", "error")
            db.session.rollback()
            return redirect_back_to_body_form()

        bodies_available -= 1  # wrap this body

        # Compute new rolls left (integer) and remainder used on the current roll
        new_wrap_stock = ceil(bodies_available / bodies_per_wrap_roll) if bodies_available > 0 else 0
        used_in_current_roll = 0 if bodies_available <= 0 else (bodies_per_wrap_roll - (bodies_available % bodies_per_wrap_roll)) % bodies_per_wrap_roll

        wrap_entry = PrintedPartsCount(
            part_name=pallet_wrap_name,
            count=new_wrap_stock,
            date=date.today(),
            time=datetime.utcnow().time()
        )
        db.session.add(wrap_entry)
        check_and_notify_low_stock(
            pallet_wrap_name,
            current_wrap_stock,
            new_wrap_stock,
            collected_warnings=low_stock_messages
        )

        if remainder_entry:
            remainder_entry.count = used_in_current_roll
        else:
            db.session.add(TableStock(type=wrap_remainder_key, count=used_in_current_roll))
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
            try:
                start_time_obj = datetime.strptime(start_time, "%H:%M").time()
            except ValueError:
                start_time_obj = datetime.strptime(start_time, "%H:%M:%S").time()
            try:
                finish_time_obj = datetime.strptime(finish_time, "%H:%M").time()
            except ValueError:
                finish_time_obj = datetime.strptime(finish_time, "%H:%M:%S").time()

            start_dt = datetime.combine(date.today(), start_time_obj)
            finish_dt = datetime.combine(date.today(), finish_time_obj)

            # Handle overnight finish (next day)
            if finish_time_obj < start_time_obj:
                finish_dt = datetime.combine(date.today() + timedelta(days=1), finish_time_obj)

            # Adjust for lunch break (30 minutes)
            if lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            # Format as H:MM hours and prevent negatives
            delta = finish_dt - start_dt
            total_minutes = max(0, int(delta.total_seconds() // 60))
            hours = total_minutes // 60
            minutes = total_minutes % 60
            time_taken_str = f"{hours}:{minutes:02d} hours"


            # --- NTFY Notification ---
            size = completed_body_size
            color = laminate_color_key.replace('_', ' ').title()
            type_label = table_type_display_label(actual_table_type)
            message_lines = []
            if body_pod_mismatch_messages:
                message_lines.append("BODY/POD MISMATCH WARNING")
                for mismatch_message in body_pod_mismatch_messages:
                    message_lines.append(f"- {mismatch_message}")
                message_lines.append("")
            if low_stock_messages:
                message_lines.append("LOW STOCK WARNING")
                for warning in low_stock_messages:
                    message_lines.append(f"- {warning}")
                message_lines.append("")
            if body_pod_mismatch_messages or low_stock_messages:
                message_lines.append("Completion Details:")
            message_lines.append(f"Selected Pod: {selected_pod_serial}")
            message_lines.append(f"Completed Body: {serial_number}")
            message_lines.append(f"Time Taken: {time_taken_str}")
            message = "\n".join(message_lines)
            title_prefixes = []
            if body_pod_mismatch_messages:
                title_prefixes.append("[MISMATCH]")
            if low_stock_messages:
                title_prefixes.append("[LOW STOCK]")
            title_prefix = " ".join(title_prefixes)
            title = f"{title_prefix} Body Completed: {type_label} {size} {color}".strip()
            try:
                requests.post("https://ntfy.sh/PoolTableTracker",
                              data=message,
                              headers={"Title": title})
            except requests.RequestException as e:
                print(f"Ntfy notification failed: {e}")
            # --- End NTFY Notification ---
            if body_pod_mismatch_messages:
                flash(
                    "Warning: selected pod does not match the completed body. "
                    + "; ".join(body_pod_mismatch_messages),
                    "warning"
                )
        except IntegrityError:
            db.session.rollback()
            flash("Error: Serial number already exists. Please use a unique serial number.", "error")
            return redirect_back_to_body_form()

        # Persist body metadata (type/color) so Lite rows stay reversible without color in serial.
        save_body_build_metadata(new_table.id, actual_table_type, laminate_color_key)

        # Update table stock based on size and color
        size = "6ft" if serial_is_6ft(serial_number) else "7ft"
        stock_type = body_stock_type_key(size, actual_table_type, laminate_color_key)
        
        stock_entry = TableStock.query.filter_by(type=stock_type).first()
        if not stock_entry:
            stock_entry = TableStock(type=stock_type, count=0)
            db.session.add(stock_entry)
        old_count = stock_entry.count
        stock_entry.count += 1
        record_table_stock_log(
            stock_type,
            "complete_body",
            worker,
            1,
            old_count,
            stock_entry.count,
            f"Completed body {serial_number}"
        )
        db.session.commit()
        session.pop("body_completion_form_values", None)

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

    def body_color_label(body_entry):
        _, color_key = get_body_build_metadata(body_entry)
        return LAMINATE_COLOR_KEY_TO_LABEL.get(color_key, "Black")

    # Determine default color based on the last completed table
    last_table = CompletedTable.query.order_by(CompletedTable.id.desc()).first()
    default_color = body_color_label(last_table) if last_table else 'Black'
    default_table_type = (
        "Lite"
        if last_table and table_type_from_serial(last_table.serial_number) == TABLE_TYPE_LITE
        else "Champion"
    )

    current_production_6ft = sum(1 for table in all_bodies_this_month if serial_is_6ft(table.serial_number))
    current_production_7ft = sum(1 for table in all_bodies_this_month if not serial_is_6ft(table.serial_number))

    def normalize_worker_name(name):
        if not name:
            return ""
        return re.sub(r'[^a-z]', '', name.lower())

    def canonical_worker_key(name):
        worker_key = normalize_worker_name(name)
        if worker_key.startswith("jack"):
            return "jackb"
        return worker_key

    raw_worker_names = [
        (row[0] or "").strip()
        for row in (
            db.session.query(CompletedTable.worker)
            .filter(CompletedTable.worker.isnot(None))
            .distinct()
            .order_by(func.lower(CompletedTable.worker))
            .all()
        )
        if (row[0] or "").strip()
    ]
    worker_options_by_key = {}
    for worker_name in raw_worker_names:
        worker_key = canonical_worker_key(worker_name)
        if worker_key and worker_key not in worker_options_by_key:
            worker_options_by_key[worker_key] = "Jack B" if worker_key == "jackb" else worker_name
    worker_options_by_key.setdefault("jackb", "Jack B")
    worker_options_by_key.setdefault("all", "All Workers")

    worker_options = [
        {"value": worker_key, "label": worker_label}
        for worker_key, worker_label in sorted(
            worker_options_by_key.items(),
            key=lambda item: (item[0] != "jackb", item[0] != "all", item[1].lower())
        )
    ]
    selected_worker_key = canonical_worker_key(request.args.get("worker") or "All Workers") or "all"
    if selected_worker_key not in worker_options_by_key:
        selected_worker_key = "all"
    selected_worker = worker_options_by_key[selected_worker_key]

    def format_split_percentage(count, total):
        if not total:
            return "0%"
        percentage = (count / total) * 100
        percentage_text = f"{percentage:.1f}".rstrip("0").rstrip(".")
        return f"{percentage_text}%"

    def empty_count_stats(include_serials=False):
        stats = {
            "table_count": 0,
            "champion_count": 0,
            "lite_count": 0,
        }
        if include_serials:
            stats["serial_numbers"] = []
        return stats

    def empty_formatted_count_stats(include_serials=False):
        stats = {
            "count": 0,
            "champion": 0,
            "lite": 0,
            "champion_percent": "0%",
            "lite_percent": "0%",
        }
        if include_serials:
            stats["serial_numbers"] = ""
        return stats

    def build_count_worker_stats(bodies, include_serials=False):
        worker_stats = {
            worker_key: empty_count_stats(include_serials)
            for worker_key in worker_options_by_key.keys()
        }
        worker_stats.setdefault("all", empty_count_stats(include_serials))

        for body in bodies:
            body_type, _ = get_body_build_metadata(body)
            type_key = "lite" if body_type == TABLE_TYPE_LITE else "champion"
            worker_keys = ["all"]
            body_worker_key = canonical_worker_key(body.worker)
            if body_worker_key:
                worker_stats.setdefault(body_worker_key, empty_count_stats(include_serials))
                worker_keys.append(body_worker_key)

            for worker_key in worker_keys:
                stats = worker_stats[worker_key]
                stats["table_count"] += 1
                stats[f"{type_key}_count"] += 1
                if include_serials:
                    stats["serial_numbers"].append(body.serial_number)

        formatted_stats = {}
        for worker_key, stats in worker_stats.items():
            formatted = {
                "count": stats["table_count"],
                "champion": stats["champion_count"],
                "lite": stats["lite_count"],
                "champion_percent": format_split_percentage(
                    stats["champion_count"],
                    stats["table_count"]
                ),
                "lite_percent": format_split_percentage(
                    stats["lite_count"],
                    stats["table_count"]
                ),
            }
            if include_serials:
                formatted["serial_numbers"] = ", ".join(stats["serial_numbers"])
            formatted_stats[worker_key] = formatted

        return formatted_stats

    body_type_totals = {"champion": 0, "lite": 0}
    body_type_worker_counts = {}
    for table in all_bodies_this_month:
        table_type, _ = get_body_build_metadata(table)
        type_key = "lite" if table_type == TABLE_TYPE_LITE else "champion"
        body_type_totals[type_key] += 1

        worker_name = (table.worker or "Unknown").strip() or "Unknown"
        if worker_name not in body_type_worker_counts:
            body_type_worker_counts[worker_name] = {
                "worker": worker_name,
                "champion": 0,
                "lite": 0,
                "total": 0,
            }
        body_type_worker_counts[worker_name][type_key] += 1
        body_type_worker_counts[worker_name]["total"] += 1
    body_type_total_count = body_type_totals["champion"] + body_type_totals["lite"]
    body_type_split = {
        "champion": format_split_percentage(body_type_totals["champion"], body_type_total_count),
        "lite": format_split_percentage(body_type_totals["lite"], body_type_total_count),
    }
    for row in body_type_worker_counts.values():
        row["champion_percent"] = format_split_percentage(row["champion"], row["total"])
        row["lite_percent"] = format_split_percentage(row["lite"], row["total"])
    body_type_worker_rows = sorted(
        body_type_worker_counts.values(),
        key=lambda row: (-row["total"], row["worker"].lower())
    )

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
    daily_bodies = (
        CompletedTable.query
        .filter(CompletedTable.date.in_(last_working_days))
        .order_by(CompletedTable.date.desc(), CompletedTable.id.asc())
        .all()
    )
    daily_history_by_date = {}
    for body in daily_bodies:
        if body.date not in daily_history_by_date:
            daily_history_by_date[body.date] = {
                "date": body.date.strftime("%A %d/%m/%y"),
                "count": 0,
                "champion": 0,
                "lite": 0,
                "serial_numbers": [],
                "bodies": []
            }
        entry = daily_history_by_date[body.date]
        body_type, _ = get_body_build_metadata(body)
        type_key = "lite" if body_type == TABLE_TYPE_LITE else "champion"
        entry[type_key] += 1
        entry["count"] += 1
        entry["serial_numbers"].append(body.serial_number)
        entry["bodies"].append(body)

    daily_history_formatted = []
    daily_worker_stats = []
    for entry in daily_history_by_date.values():
        worker_stats = build_count_worker_stats(entry["bodies"], include_serials=True)
        selected_stats = worker_stats.get(
            selected_worker_key,
            empty_formatted_count_stats(include_serials=True)
        )
        daily_worker_stats.append(worker_stats)
        daily_history_formatted.append({
            "date": entry["date"],
            "count": entry["count"],
            "champion": entry["champion"],
            "lite": entry["lite"],
            "selected_worker_count": selected_stats["count"],
            "selected_worker_champion": selected_stats["champion"],
            "selected_worker_lite": selected_stats["lite"],
            "selected_worker_champion_percent": selected_stats["champion_percent"],
            "selected_worker_lite_percent": selected_stats["lite_percent"],
            "selected_worker_serial_numbers": selected_stats["serial_numbers"],
            "serial_numbers": ", ".join(entry["serial_numbers"])
        })

    # Weekly history (current week and previous 5 weeks)
    current_week_start = today - timedelta(days=today.weekday())
    weekly_history_by_start = {}
    for week_offset in range(6):
        week_start = current_week_start - timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)
        weekly_history_by_start[week_start] = {
            "week_start": week_start,
            "label": f"{week_start.strftime('%d/%m/%y')} - {week_end.strftime('%d/%m/%y')}",
            "bodies": [],
        }

    oldest_week_start = min(weekly_history_by_start.keys())
    weekly_bodies = (
        CompletedTable.query
        .filter(CompletedTable.date >= oldest_week_start, CompletedTable.date <= today)
        .order_by(CompletedTable.date.desc(), CompletedTable.id.asc())
        .all()
    )
    for body in weekly_bodies:
        week_start = body.date - timedelta(days=body.date.weekday())
        if week_start in weekly_history_by_start:
            weekly_history_by_start[week_start]["bodies"].append(body)

    weekly_history_formatted = []
    weekly_worker_stats = []
    for week_start in sorted(weekly_history_by_start.keys(), reverse=True):
        entry = weekly_history_by_start[week_start]
        worker_stats = build_count_worker_stats(entry["bodies"])
        selected_stats = worker_stats.get(selected_worker_key, empty_formatted_count_stats())
        weekly_worker_stats.append(worker_stats)
        weekly_history_formatted.append({
            "week": entry["label"],
            "selected_worker_count": selected_stats["count"],
            "selected_worker_champion": selected_stats["champion"],
            "selected_worker_lite": selected_stats["lite"],
            "selected_worker_champion_percent": selected_stats["champion_percent"],
            "selected_worker_lite_percent": selected_stats["lite_percent"],
        })

    def parse_time_string(value):
        if not value:
            return None
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
        return None

    def calculate_body_duration(body):
        start_time_obj = parse_time_string(body.start_time)
        finish_time_obj = parse_time_string(body.finish_time)
        if not start_time_obj or not finish_time_obj:
            return None

        start_dt = datetime.combine(body.date, start_time_obj)
        finish_dt = datetime.combine(body.date, finish_time_obj)
        if finish_time_obj < start_time_obj:
            # Only treat as overnight if within a sane window (<= 12 hours)
            overnight_dt = datetime.combine(body.date + timedelta(days=1), finish_time_obj)
            if (overnight_dt - start_dt) <= timedelta(hours=12):
                finish_dt = overnight_dt

        if body.lunch and str(body.lunch).lower() == "yes":
            finish_dt -= timedelta(minutes=30)

        delta = finish_dt - start_dt
        # Guard against bad inputs that skew averages
        if delta.total_seconds() < 0:
            return None
        if delta < timedelta(minutes=10):
            return None
        if delta > timedelta(hours=8):
            return None
        return delta

    def format_avg_duration(total_seconds, count):
        if not count:
            return "N/A"
        avg_seconds = total_seconds / count
        avg_seconds = max(0, int(round(avg_seconds)))
        hours, remainder = divmod(avg_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

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
    monthly_worker_stats = []
    for row in monthly_totals:
        yr = int(row.year)
        mo = int(row.month)
        total_bodies = row.total

        month_bodies = CompletedTable.query.filter(
            extract('year', CompletedTable.date) == yr,
            extract('month', CompletedTable.date) == mo
        ).all()

        # Use actual recorded durations for averages instead of estimated work hours
        total_duration_seconds = 0
        counted_bodies = 0
        worker_stats = {
            worker_key: {
                "seconds": 0,
                "duration_count": 0,
                "table_count": 0,
                "champion_count": 0,
                "lite_count": 0,
                "champion_seconds": 0,
                "champion_duration_count": 0,
                "lite_seconds": 0,
                "lite_duration_count": 0,
            }
            for worker_key in worker_options_by_key.keys()
        }
        all_worker_stats = worker_stats["all"]
        type_counts = {
            TABLE_TYPE_CHAMPION: 0,
            TABLE_TYPE_LITE: 0,
        }
        type_stats = {
            TABLE_TYPE_CHAMPION: {"seconds": 0, "count": 0},
            TABLE_TYPE_LITE: {"seconds": 0, "count": 0},
        }
        for body in month_bodies:
            body_type, _ = get_body_build_metadata(body)
            if body_type not in type_counts:
                body_type = TABLE_TYPE_CHAMPION
            type_counts[body_type] += 1

            body_worker_key = canonical_worker_key(body.worker)
            if body_worker_key and body_worker_key not in worker_stats:
                worker_stats[body_worker_key] = {
                    "seconds": 0,
                    "duration_count": 0,
                    "table_count": 0,
                    "champion_count": 0,
                    "lite_count": 0,
                    "champion_seconds": 0,
                    "champion_duration_count": 0,
                    "lite_seconds": 0,
                    "lite_duration_count": 0,
                }
            if body_worker_key:
                worker_stats[body_worker_key]["table_count"] += 1
                if body_type == TABLE_TYPE_LITE:
                    worker_stats[body_worker_key]["lite_count"] += 1
                else:
                    worker_stats[body_worker_key]["champion_count"] += 1
            all_worker_stats["table_count"] += 1
            if body_type == TABLE_TYPE_LITE:
                all_worker_stats["lite_count"] += 1
            else:
                all_worker_stats["champion_count"] += 1

            duration = calculate_body_duration(body)
            if duration is None:
                continue
            total_duration_seconds += duration.total_seconds()
            counted_bodies += 1
            if body_type in type_stats:
                type_stats[body_type]["seconds"] += duration.total_seconds()
                type_stats[body_type]["count"] += 1
            if body_worker_key:
                worker_stats[body_worker_key]["seconds"] += duration.total_seconds()
                worker_stats[body_worker_key]["duration_count"] += 1
                if body_type == TABLE_TYPE_LITE:
                    worker_stats[body_worker_key]["lite_seconds"] += duration.total_seconds()
                    worker_stats[body_worker_key]["lite_duration_count"] += 1
                else:
                    worker_stats[body_worker_key]["champion_seconds"] += duration.total_seconds()
                    worker_stats[body_worker_key]["champion_duration_count"] += 1
            all_worker_stats["seconds"] += duration.total_seconds()
            all_worker_stats["duration_count"] += 1
            if body_type == TABLE_TYPE_LITE:
                all_worker_stats["lite_seconds"] += duration.total_seconds()
                all_worker_stats["lite_duration_count"] += 1
            else:
                all_worker_stats["champion_seconds"] += duration.total_seconds()
                all_worker_stats["champion_duration_count"] += 1

        avg_hours_per_body_formatted = format_avg_duration(total_duration_seconds, counted_bodies)
        selected_worker_stats = worker_stats.get(
            selected_worker_key,
            {
                "seconds": 0,
                "duration_count": 0,
                "table_count": 0,
                "champion_count": 0,
                "lite_count": 0,
                "champion_seconds": 0,
                "champion_duration_count": 0,
                "lite_seconds": 0,
                "lite_duration_count": 0,
            }
        )
        formatted_worker_stats = {
            worker_key: {
                "count": stats["table_count"],
                "avg": format_avg_duration(stats["seconds"], stats["duration_count"]),
                "champion": stats["champion_count"],
                "lite": stats["lite_count"],
                "champion_percent": format_split_percentage(
                    stats["champion_count"],
                    stats["table_count"]
                ),
                "lite_percent": format_split_percentage(
                    stats["lite_count"],
                    stats["table_count"]
                ),
                "champion_avg": format_avg_duration(
                    stats["champion_seconds"],
                    stats["champion_duration_count"]
                ),
                "lite_avg": format_avg_duration(
                    stats["lite_seconds"],
                    stats["lite_duration_count"]
                ),
            }
            for worker_key, stats in worker_stats.items()
        }
        monthly_worker_stats.append(formatted_worker_stats)
        monthly_totals_formatted.append({
            "month": date(year=yr, month=mo, day=1).strftime("%B %Y"),
            "count": total_bodies,
            "champion_count": type_counts[TABLE_TYPE_CHAMPION],
            "lite_count": type_counts[TABLE_TYPE_LITE],
            "average_hours_per_body": avg_hours_per_body_formatted,
            "selected_worker_count": selected_worker_stats["table_count"],
            "selected_worker_avg": format_avg_duration(
                selected_worker_stats["seconds"],
                selected_worker_stats["duration_count"]
            ),
            "selected_worker_champion_count": selected_worker_stats["champion_count"],
            "selected_worker_lite_count": selected_worker_stats["lite_count"],
            "selected_worker_champion_percent": format_split_percentage(
                selected_worker_stats["champion_count"],
                selected_worker_stats["table_count"]
            ),
            "selected_worker_lite_percent": format_split_percentage(
                selected_worker_stats["lite_count"],
                selected_worker_stats["table_count"]
            ),
            "selected_worker_champion_avg": format_avg_duration(
                selected_worker_stats["champion_seconds"],
                selected_worker_stats["champion_duration_count"]
            ),
            "selected_worker_lite_avg": format_avg_duration(
                selected_worker_stats["lite_seconds"],
                selected_worker_stats["lite_duration_count"]
            ),
            "avg_hours_champion": format_avg_duration(
                type_stats[TABLE_TYPE_CHAMPION]["seconds"],
                type_stats[TABLE_TYPE_CHAMPION]["count"]
            ),
            "avg_hours_lite": format_avg_duration(
                type_stats[TABLE_TYPE_LITE]["seconds"],
                type_stats[TABLE_TYPE_LITE]["count"]
            )
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
    body_form_values = session.get("body_completion_form_values") or {}
    form_start_time = body_form_values.get("start_time") or current_time
    form_finish_time = body_form_values.get("finish_time") or current_time
    form_serial_number = body_form_values.get("serial_number") or ""
    form_table_type = body_form_values.get("table_type") or default_table_type
    form_color = body_form_values.get("color_selector") or default_color
    form_issue = body_form_values.get("issue") or ""
    form_lunch = body_form_values.get("lunch") or "No"
    unconverted_pod_serials = [pod.serial_number for pod in unconverted_pods]
    quick_add_parts = []
    for quick_part in BODIES_QUICK_ADD_PARTS:
        quick_add_parts.append({
            **quick_part,
            "current_count": _latest_part_count(quick_part["part_name"]),
        })

    return render_template(
        'bodies.html',
        issues=issues,
        current_time=current_time,
        form_start_time=form_start_time,
        form_finish_time=form_finish_time,
        form_serial_number=form_serial_number,
        form_table_type=form_table_type,
        form_color=form_color,
        form_issue=form_issue,
        form_lunch=form_lunch,
        body_form_restored=bool(body_form_values),
        unconverted_pod_serials=unconverted_pod_serials,
        unconverted_pods=unconverted_pods,
        completed_tables=completed_tables,
        current_month_bodies_count=current_month_bodies_count,
        daily_history=daily_history_formatted,
        daily_worker_stats=daily_worker_stats,
        weekly_history=weekly_history_formatted,
        weekly_worker_stats=weekly_worker_stats,
        monthly_totals=monthly_totals_formatted,
        target_7ft=target_7ft,
        target_6ft=target_6ft,
        current_production_7ft=current_production_7ft,
        current_production_6ft=current_production_6ft,
        default_color=default_color,
        default_table_type=default_table_type,
        quick_add_parts=quick_add_parts,
        body_type_totals=body_type_totals,
        body_type_split=body_type_split,
        body_type_worker_rows=body_type_worker_rows,
        worker_options=worker_options,
        selected_worker=selected_worker,
        selected_worker_key=selected_worker_key,
        monthly_worker_stats=monthly_worker_stats
    )


@app.route('/body_pod_audit')
def body_pod_audit():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    def clean_pod_serial(serial):
        cleaned = (serial or "").strip()
        if "**Pod Serial Number:" in cleaned:
            cleaned = cleaned.replace("**Pod Serial Number:", "").strip()
        return cleaned

    def picker_base_key(serial):
        return base_serial_for_pod_matching(clean_pod_serial(serial))

    def normalized_base_key(serial):
        base_serial = base_serial_for_pod_matching(clean_pod_serial(serial))
        return re.sub(r"[^A-Z0-9]+", "", base_serial.upper())

    def serial_root_key(serial):
        cleaned = strip_table_serial_suffixes(clean_pod_serial(serial), remove_color=True, remove_lite=True)
        match = re.search(r"\d+", cleaned)
        return match.group(0) if match else normalized_base_key(cleaned)

    def table_type_label(table_type):
        return "Lite" if table_type == TABLE_TYPE_LITE else "Champion"

    def serial_size_label(serial):
        return "6ft" if serial_is_6ft(serial) else "7ft"

    def format_entry_date(value):
        return value.strftime("%d/%m/%Y") if value else "-"

    today = london_now().date()
    hide_cutoff_date = today - timedelta(days=BODY_PICKER_HIDE_MIN_AGE_DAYS)
    hidden_body_picker_pod_ids = load_hidden_body_picker_pod_ids()
    completed_bodies = CompletedTable.query.order_by(CompletedTable.date.desc(), CompletedTable.id.desc()).all()
    completed_base_serials = {picker_base_key(body.serial_number) for body in completed_bodies}

    body_records = []
    body_by_normalized_key = defaultdict(list)
    body_by_root_key = defaultdict(list)
    for body in completed_bodies:
        body_type, _ = get_body_build_metadata(body)
        normalized_key = normalized_base_key(body.serial_number)
        root_key = serial_root_key(body.serial_number)
        record = {
            "id": body.id,
            "serial": body.serial_number,
            "worker": body.worker,
            "date": format_entry_date(body.date),
            "size": serial_size_label(body.serial_number),
            "table_type": body_type,
            "type_label": table_type_label(body_type),
            "normalized_key": normalized_key,
            "root_key": root_key,
        }
        body_records.append(record)
        if normalized_key:
            body_by_normalized_key[normalized_key].append(record)
        if root_key:
            body_by_root_key[root_key].append(record)

    completed_pods = CompletedPods.query.order_by(CompletedPods.date.desc(), CompletedPods.id.desc()).all()
    picker_rows = []
    hidden_picker_rows = []
    mismatch_rows = []
    all_pod_rows = []

    for pod in completed_pods:
        pod_serial = clean_pod_serial(pod.serial_number)
        pod_type = table_type_from_serial(pod_serial)
        age_days = max((today - pod.date).days, 0) if pod.date else 0
        pod_record = {
            "id": pod.id,
            "serial": pod_serial,
            "worker": pod.worker,
            "date": format_entry_date(pod.date),
            "age_days": age_days,
            "can_hide": bool(pod.date and pod.date <= hide_cutoff_date),
            "hidden": pod.id in hidden_body_picker_pod_ids,
            "size": serial_size_label(pod_serial),
            "table_type": pod_type,
            "type_label": table_type_label(pod_type),
            "picker_key": picker_base_key(pod_serial),
            "normalized_key": normalized_base_key(pod_serial),
            "root_key": serial_root_key(pod_serial),
        }
        all_pod_rows.append(pod_record)

        normalized_matches = body_by_normalized_key.get(pod_record["normalized_key"], [])
        root_matches = [
            body
            for body in body_by_root_key.get(pod_record["root_key"], [])
            if body not in normalized_matches
        ]
        candidate_matches = normalized_matches or root_matches
        best_match = candidate_matches[0] if candidate_matches else None

        notes = []
        if best_match:
            if pod_record["size"] != best_match["size"]:
                notes.append(f"Size mismatch: pod {pod_record['size']}, body {best_match['size']}")
            if pod_record["table_type"] != best_match["table_type"]:
                notes.append(f"Type mismatch: pod {pod_record['type_label']}, body {best_match['type_label']}")

            if not notes and normalized_matches:
                notes.append("Same serial after normalising formatting.")
            elif not notes and root_matches:
                notes.append("Same number, but serial format differs.")

        if normalized_matches:
            match_status = "Matched"
            match_quality = "Normalised serial match"
        elif root_matches:
            match_status = "Possible Match"
            match_quality = "Same serial number root"
        else:
            match_status = "No Body Found"
            match_quality = "No completed body has the same serial"

        if pod_record["picker_key"] not in completed_base_serials:
            if best_match and normalized_matches:
                picker_status = "Still in picker, but likely already built"
            elif best_match:
                picker_status = "Still in picker with possible body match"
            else:
                picker_status = "Still in picker, no body match found"

            picker_row = {
                "pod": pod_record,
                "body": best_match,
                "status": picker_status,
                "match_status": match_status,
                "match_quality": match_quality,
                "notes": notes,
                "match_count": len(candidate_matches),
            }
            if pod_record["hidden"]:
                hidden_picker_rows.append(picker_row)
            else:
                picker_rows.append(picker_row)

        for body in normalized_matches:
            mismatch_notes = []
            if pod_record["size"] != body["size"]:
                mismatch_notes.append(f"Size mismatch: pod {pod_record['size']}, body {body['size']}")
            if pod_record["table_type"] != body["table_type"]:
                mismatch_notes.append(f"Type mismatch: pod {pod_record['type_label']}, body {body['type_label']}")
            if mismatch_notes:
                mismatch_rows.append({
                    "pod": pod_record,
                    "body": body,
                    "notes": mismatch_notes,
                })

    likely_built_rows = [row for row in picker_rows if row["body"]]
    no_match_rows = [row for row in picker_rows if not row["body"]]
    type_mismatch_count = sum(
        1
        for row in mismatch_rows
        if any(note.startswith("Type mismatch") for note in row["notes"])
    )
    size_mismatch_count = sum(
        1
        for row in mismatch_rows
        if any(note.startswith("Size mismatch") for note in row["notes"])
    )

    summary = {
        "total_pods": len(all_pod_rows),
        "total_bodies": len(body_records),
        "pods_in_picker": len(picker_rows),
        "hidden_old_pods": len(hidden_picker_rows),
        "likely_already_built": len(likely_built_rows),
        "no_body_match": len(no_match_rows),
        "type_mismatches": type_mismatch_count,
        "size_mismatches": size_mismatch_count,
    }
    undo_payload = _get_body_pod_audit_undo_payload() or {}
    undo_items = undo_payload.get("items") if isinstance(undo_payload, dict) else []
    undo_items = undo_items if isinstance(undo_items, list) else []

    return render_template(
        'body_pod_audit.html',
        summary=summary,
        picker_rows=picker_rows,
        hidden_picker_rows=hidden_picker_rows,
        hide_min_age_days=BODY_PICKER_HIDE_MIN_AGE_DAYS,
        mismatch_rows=mismatch_rows,
        undo_available=bool(undo_items),
        undo_count=len(undo_items),
        undo_created_at=undo_payload.get("created_at") if isinstance(undo_payload, dict) else None,
        logged_in_worker=session.get('worker')
    )


@app.route('/body_pod_audit/hide_pod', methods=['POST'])
def body_pod_audit_hide_pod():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    action = (request.form.get("action") or "").strip().lower()
    try:
        pod_id = int(request.form.get("pod_id", 0))
    except (TypeError, ValueError):
        flash("Invalid pod selected.", "error")
        return redirect(url_for('body_pod_audit'))

    pod = CompletedPods.query.get(pod_id)
    if not pod:
        flash("Pod could not be found.", "error")
        return redirect(url_for('body_pod_audit'))

    hidden_ids = load_hidden_body_picker_pod_ids()
    try:
        if action == "hide":
            hide_cutoff_date = london_now().date() - timedelta(days=BODY_PICKER_HIDE_MIN_AGE_DAYS)
            if not pod.date or pod.date > hide_cutoff_date:
                flash("Only pods older than 2 months can be hidden from the picker.", "error")
                return redirect(url_for('body_pod_audit'))
            hidden_ids.add(pod.id)
            save_hidden_body_picker_pod_ids(hidden_ids)
            flash(f"Hidden pod {pod.serial_number} from the body picker.", "success")
        elif action == "unhide":
            hidden_ids.discard(pod.id)
            save_hidden_body_picker_pod_ids(hidden_ids)
            flash(f"Restored pod {pod.serial_number} to the body picker.", "success")
        else:
            flash("Invalid hide action.", "error")
    except OSError:
        flash("Could not save the hidden pod list.", "error")

    return redirect(url_for('body_pod_audit'))


BODY_POD_AUDIT_UNDO_SESSION_KEY = "body_pod_audit_last_undo_id"
BODY_POD_AUDIT_UNDO_CACHE = {}


def _get_body_pod_audit_undo_payload():
    undo_id = session.get(BODY_POD_AUDIT_UNDO_SESSION_KEY)
    if not undo_id:
        return None
    return BODY_POD_AUDIT_UNDO_CACHE.get(undo_id)


def _body_audit_clean_pod_serial(serial):
    cleaned = (serial or "").strip()
    if "**Pod Serial Number:" in cleaned:
        cleaned = cleaned.replace("**Pod Serial Number:", "").strip()
    return cleaned


def _body_audit_normalized_base_key(serial):
    base_serial = base_serial_for_pod_matching(_body_audit_clean_pod_serial(serial))
    return re.sub(r"[^A-Z0-9]+", "", base_serial.upper())


def _body_audit_size_label(serial):
    return "6ft" if serial_is_6ft(serial) else "7ft"


def _body_audit_type_label(table_type):
    return "Lite" if table_type == TABLE_TYPE_LITE else "Champion"


def _body_serial_root_without_size(serial):
    cleaned = strip_table_serial_suffixes(
        _body_audit_clean_pod_serial(serial),
        remove_color=True,
        remove_lite=True
    )
    cleaned = re.sub(r"\s*-\s*[67]\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _corrected_body_serial_from_pod(pod_serial, target_table_type, target_size, color_key):
    root_serial = _body_serial_root_without_size(pod_serial)
    if not root_serial:
        raise ValueError("Could not work out the corrected serial number.")

    if target_table_type == TABLE_TYPE_LITE:
        size_code = "6" if target_size == "6ft" else "7"
        return f"{root_serial} - {size_code} - L"

    serial = f"{root_serial} - 6" if target_size == "6ft" else root_serial
    color_suffixes = {
        "grey_oak": "GO",
        "rustic_oak": "O",
        "stone": "C",
        "rustic_black": "RB",
    }
    suffix = color_suffixes.get(color_key)
    return f"{serial} - {suffix}" if suffix else serial


def _get_or_create_table_stock(stock_type):
    stock_entry = TableStock.query.filter_by(type=stock_type).first()
    if not stock_entry:
        stock_entry = TableStock(type=stock_type, count=0)
        db.session.add(stock_entry)
        db.session.flush()
    return stock_entry


def _correct_body_to_match_pod(pod, body, worker_name=None):
    if not pod or not body:
        raise ValueError("Pod or body entry could not be found.")

    old_serial = body.serial_number
    old_table_type, color_key = get_body_build_metadata(body)
    old_size = _body_audit_size_label(body.serial_number)
    target_table_type = table_type_from_serial(pod.serial_number)
    target_size = _body_audit_size_label(pod.serial_number)

    if old_table_type == target_table_type and old_size == target_size:
        return {
            "changed": False,
            "message": f"{old_serial} already matches pod {pod.serial_number}."
        }

    new_serial = _corrected_body_serial_from_pod(
        pod.serial_number,
        target_table_type,
        target_size,
        color_key
    )
    duplicate_body = (
        CompletedTable.query
        .filter(CompletedTable.serial_number == new_serial, CompletedTable.id != body.id)
        .first()
    )
    if duplicate_body:
        raise ValueError(
            f"Cannot correct {old_serial}: another body already uses serial {new_serial}."
        )

    old_stock_type = body_stock_type_key(old_size, old_table_type, color_key)
    new_stock_type = body_stock_type_key(target_size, target_table_type, color_key)
    # Audit fixes only correct the completed body record. Table stock is left alone
    # because stock corrections may already have been handled manually.
    old_stock_decremented = False
    new_stock_incremented = False

    body.serial_number = new_serial
    save_body_build_metadata(body.id, target_table_type, color_key)

    return {
        "changed": True,
        "old_serial": old_serial,
        "new_serial": new_serial,
        "body_id": body.id,
        "pod_id": pod.id,
        "pod_serial": pod.serial_number,
        "old_table_type": old_table_type,
        "new_table_type": target_table_type,
        "color_key": color_key,
        "old_type": _body_audit_type_label(old_table_type),
        "new_type": _body_audit_type_label(target_table_type),
        "old_size": old_size,
        "new_size": target_size,
        "old_stock_type": old_stock_type,
        "new_stock_type": new_stock_type,
        "old_stock_decremented": old_stock_decremented,
        "new_stock_incremented": new_stock_incremented,
        "warning": None,
    }


def _body_pod_audit_undo_item(result):
    return {
        "body_id": result["body_id"],
        "pod_id": result["pod_id"],
        "pod_serial": result["pod_serial"],
        "old_serial": result["old_serial"],
        "new_serial": result["new_serial"],
        "old_table_type": result["old_table_type"],
        "new_table_type": result["new_table_type"],
        "color_key": result["color_key"],
        "old_size": result["old_size"],
        "new_size": result["new_size"],
        "old_type": result["old_type"],
        "new_type": result["new_type"],
        "old_stock_type": result["old_stock_type"],
        "new_stock_type": result["new_stock_type"],
        "old_stock_decremented": result["old_stock_decremented"],
        "new_stock_incremented": result["new_stock_incremented"],
    }


def _store_body_pod_audit_undo(items, action_label):
    if not items:
        return
    previous_undo_id = session.get(BODY_POD_AUDIT_UNDO_SESSION_KEY)
    if previous_undo_id:
        BODY_POD_AUDIT_UNDO_CACHE.pop(previous_undo_id, None)
    undo_id = uuid.uuid4().hex
    BODY_POD_AUDIT_UNDO_CACHE[undo_id] = {
        "action": action_label,
        "created_at": london_now().strftime("%d/%m/%Y %H:%M"),
        "items": items,
    }
    session[BODY_POD_AUDIT_UNDO_SESSION_KEY] = undo_id
    session.modified = True


@app.route('/body_pod_audit/fix', methods=['POST'])
def body_pod_audit_fix():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    action = request.form.get("action")
    worker_name = session.get("worker", "Unknown")

    try:
        if action == "fix_one":
            undo_items = []
            pod_id = int(request.form.get("pod_id", 0))
            body_id = int(request.form.get("body_id", 0))
            pod = CompletedPods.query.get(pod_id)
            body = CompletedTable.query.get(body_id)
            result = _correct_body_to_match_pod(pod, body, worker_name)
            db.session.commit()
            if result.get("changed"):
                undo_items.append(_body_pod_audit_undo_item(result))
                _store_body_pod_audit_undo(undo_items, "Fix Body")
                flash(
                    f"Corrected body {result['old_serial']} to {result['new_serial']} "
                    f"({result['old_size']} {result['old_type']} -> {result['new_size']} {result['new_type']}).",
                    "success"
                )
                if result.get("warning"):
                    flash(result["warning"], "warning")
            else:
                flash(result["message"], "info")
        elif action == "fix_all":
            completed_bodies = CompletedTable.query.order_by(CompletedTable.date.desc(), CompletedTable.id.desc()).all()
            bodies_by_key = defaultdict(list)
            for body in completed_bodies:
                bodies_by_key[_body_audit_normalized_base_key(body.serial_number)].append(body)

            fixed_count = 0
            warnings = []
            undo_items = []
            fixed_body_ids = set()
            completed_pods = CompletedPods.query.order_by(CompletedPods.date.desc(), CompletedPods.id.desc()).all()
            for pod in completed_pods:
                pod_key = _body_audit_normalized_base_key(pod.serial_number)
                pod_type = table_type_from_serial(pod.serial_number)
                pod_size = _body_audit_size_label(pod.serial_number)
                for body in bodies_by_key.get(pod_key, []):
                    if body.id in fixed_body_ids:
                        continue
                    body_type, _ = get_body_build_metadata(body)
                    body_size = _body_audit_size_label(body.serial_number)
                    if body_type == pod_type and body_size == pod_size:
                        continue
                    result = _correct_body_to_match_pod(pod, body, worker_name)
                    fixed_body_ids.add(body.id)
                    if result.get("changed"):
                        fixed_count += 1
                        undo_items.append(_body_pod_audit_undo_item(result))
                    if result.get("warning"):
                        warnings.append(result["warning"])

            db.session.commit()
            if fixed_count:
                _store_body_pod_audit_undo(undo_items, "Fix All Mismatches")
                flash(f"Corrected {fixed_count} body/pod mismatch(es).", "success")
                for warning in warnings[:3]:
                    flash(warning, "warning")
                if len(warnings) > 3:
                    flash(f"{len(warnings) - 3} more stock movement warning(s) were hidden.", "warning")
            else:
                flash("No body/pod mismatches needed correcting.", "info")
        else:
            flash("Invalid pod audit action.", "error")
    except (TypeError, ValueError) as error:
        db.session.rollback()
        flash(str(error), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Could not correct the mismatch because the corrected serial would not be unique.", "error")

    return redirect(url_for('body_pod_audit'))


@app.route('/body_pod_audit/undo', methods=['POST'])
def body_pod_audit_undo():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    undo_id = session.get(BODY_POD_AUDIT_UNDO_SESSION_KEY)
    undo_payload = BODY_POD_AUDIT_UNDO_CACHE.get(undo_id) if undo_id else {}
    undo_items = undo_payload.get("items") if isinstance(undo_payload, dict) else []
    if not undo_items:
        session.pop(BODY_POD_AUDIT_UNDO_SESSION_KEY, None)
        flash("There is no pod audit correction to undo.", "info")
        return redirect(url_for('body_pod_audit'))

    worker_name = session.get("worker", "Unknown")
    warnings = []
    undone_count = 0

    try:
        for item in reversed(undo_items):
            body = CompletedTable.query.get(item.get("body_id"))
            if not body:
                warnings.append(f"Body ID {item.get('body_id')} was not found.")
                continue

            old_serial = item.get("old_serial")
            new_serial = item.get("new_serial")
            if body.serial_number == old_serial:
                continue
            if body.serial_number != new_serial:
                raise ValueError(
                    f"Cannot undo {new_serial}: that body is now saved as {body.serial_number}."
                )

            duplicate_body = (
                CompletedTable.query
                .filter(CompletedTable.serial_number == old_serial, CompletedTable.id != body.id)
                .first()
            )
            if duplicate_body:
                raise ValueError(
                    f"Cannot undo {new_serial}: another body already uses serial {old_serial}."
                )

            old_stock_type = item.get("old_stock_type")
            new_stock_type = item.get("new_stock_type")
            if old_stock_type and new_stock_type and old_stock_type != new_stock_type:
                if item.get("new_stock_incremented"):
                    new_stock_entry = TableStock.query.filter_by(type=new_stock_type).first()
                    if new_stock_entry and new_stock_entry.count > 0:
                        new_before = new_stock_entry.count
                        new_stock_entry.count -= 1
                        record_table_stock_log(
                            new_stock_type,
                            "correction",
                            worker_name,
                            -1,
                            new_before,
                            new_stock_entry.count,
                            f"Undid pod audit correction for body {new_serial}"
                        )
                    else:
                        warnings.append(
                            f"No stock count was available to remove from {table_stock_type_label(new_stock_type)}."
                        )

                if item.get("old_stock_decremented"):
                    old_stock_entry = _get_or_create_table_stock(old_stock_type)
                    old_before = old_stock_entry.count
                    old_stock_entry.count += 1
                    record_table_stock_log(
                        old_stock_type,
                        "correction",
                        worker_name,
                        1,
                        old_before,
                        old_stock_entry.count,
                        f"Undid pod audit correction for body {new_serial}"
                    )

            body.serial_number = old_serial
            save_body_build_metadata(
                body.id,
                item.get("old_table_type", TABLE_TYPE_CHAMPION),
                item.get("color_key", "black")
            )
            undone_count += 1

        db.session.commit()
        if undo_id:
            BODY_POD_AUDIT_UNDO_CACHE.pop(undo_id, None)
        session.pop(BODY_POD_AUDIT_UNDO_SESSION_KEY, None)
        if undone_count:
            flash(f"Undid {undone_count} pod audit correction(s).", "success")
        else:
            flash("The last pod audit correction had already been undone.", "info")
        for warning in warnings[:3]:
            flash(warning, "warning")
        if len(warnings) > 3:
            flash(f"{len(warnings) - 3} more undo warning(s) were hidden.", "warning")
    except (TypeError, ValueError) as error:
        db.session.rollback()
        flash(str(error), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Could not undo the correction because the restored serial would not be unique.", "error")

    return redirect(url_for('body_pod_audit'))
@app.route('/top_rails', methods=['GET', 'POST'])
def top_rails():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    issues = [issue.description for issue in Issue.query.all()]

    def top_rail_base_serial_for_form(serial):
        cleaned = strip_table_serial_suffixes(serial, remove_color=True, remove_lite=True)
        return re.sub(r"\s*-\s*[67]\s*$", "", cleaned, flags=re.IGNORECASE).strip()

    def remember_top_rail_completion_form():
        submitted_serial = request.form.get("serial_number", "")
        session["top_rail_completion_form_values"] = {
            "start_time": request.form.get("start_time", ""),
            "finish_time": request.form.get("finish_time", ""),
            "serial_number": submitted_serial,
            "base_serial_number": top_rail_base_serial_for_form(submitted_serial),
            "size_selector": request.form.get("size_selector", "7ft"),
            "color_selector": request.form.get("color_selector", "Black"),
            "issue": request.form.get("issue", ""),
            "lunch": request.form.get("lunch", "No"),
        }
        session.modified = True

    def redirect_back_to_top_rail_form():
        remember_top_rail_completion_form()
        return redirect(url_for('top_rails'))

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
        has_size_suffix = serial_is_6ft(clean_serial)
        if not has_size_suffix and size_selector == '6ft':
            # Add size suffix if not present
            clean_serial = f"{clean_serial} - 6"
                
        # Set the corrected serial number with appropriate suffixes
        serial_number = clean_serial
        
        # Make sure color suffix is present if needed
        # Colors use the following convention: GO (Grey Oak), O (Rustic Oak), C (Stone), RB (Rustic Black), B or none (Black)
        has_color_suffix = any(
            suffix in serial_number
            for suffix in ['-GO', ' - GO', '-O', ' - O', '-C', ' - C', '-B', ' - B', '-RB', ' - RB']
        )
        
        if not has_color_suffix:
            # Add color suffix based on selection
            color_suffix = ''
            if color_selector == 'Grey Oak':
                color_suffix = ' - GO'
            elif color_selector == 'Rustic Oak':
                color_suffix = ' - O'
            elif color_selector == 'Stone':
                color_suffix = ' - C'
            elif color_selector == 'Rustic Black':
                color_suffix = ' - RB'
            # Black is default, so no suffix needed
            
            if color_suffix:
                serial_number = serial_number + color_suffix

        low_stock_messages = []
        # Parts and quantities needed for top rail completion
        parts_to_deduct = {
            **dict(TOP_RAIL_PARTS_REQUIREMENTS),
            BRAD_NAILS_PART_NAME: 0.5
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
            if part_name == BRAD_NAILS_PART_NAME:
                ok, canonical_name, available_strips = adjust_fractional_strip_inventory(
                    part_name,
                    -quantity_needed,
                    units_per_strip=BRAD_NAILS_UNITS_PER_STRIP,
                    collected_warnings=low_stock_messages
                )
                if not ok:
                    flash(
                        f"Not enough inventory for {canonical_name}! Need {quantity_needed}, have {available_strips:.2f}",
                        "error"
                    )
                    db.session.rollback()
                    return redirect_back_to_top_rail_form()
                db.session.commit()
                continue
            if part_name in [short_piece_name, long_piece_name]:
                # Get the top rail piece count
                part_entry = TopRailPieceCount.query.filter_by(part_key=part_name).first()
                if not part_entry:
                    flash(f"No inventory set up for {part_name}!", "error")
                    return redirect_back_to_top_rail_form()
                
                # Check if we have enough
                if part_entry.count < quantity_needed:
                    flash(f"Not enough inventory for {part_name}! Need {quantity_needed}, have {part_entry.count}", "error")
                    return redirect_back_to_top_rail_form()
                
                # Deduct from inventory
                old_piece_count = part_entry.count
                part_entry.count -= quantity_needed
                check_and_notify_low_stock(
                    part_name,
                    old_piece_count,
                    part_entry.count,
                    collected_warnings=low_stock_messages
                )
            else:
                # Handle other parts using PrintedPartsCount as before
                latest_entry = (PrintedPartsCount.query
                                .filter_by(part_name=part_name)
                                .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                                .first())

                allow_negative_stock = allows_negative_inventory(part_name)
                if not latest_entry and allow_negative_stock:
                    latest_entry = PrintedPartsCount(
                        part_name=part_name,
                        count=0,
                        date=date.today(),
                        time=datetime.utcnow().time()
                    )
                    db.session.add(latest_entry)

                if not latest_entry:
                    flash(f"No inventory set up for {part_name}!", "error")
                    return redirect_back_to_top_rail_form()

                if latest_entry.count < quantity_needed and not allow_negative_stock:
                    flash(f"Not enough inventory for {part_name}! Need {quantity_needed}, have {latest_entry.count}", "error")
                    return redirect_back_to_top_rail_form()

                old_count = latest_entry.count
                latest_entry.count = old_count - quantity_needed

                check_and_notify_low_stock(
                    part_name,
                    old_count,
                    latest_entry.count,
                    collected_warnings=low_stock_messages
                )
            
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

            # If finished after midnight (next day), roll finish forward a day
            if finish_time_obj < start_time_obj:
                finish_dt = datetime.combine(date.today() + timedelta(days=1), finish_time_obj)

            # Adjust for lunch break (30 min)
            if lunch.lower() == "yes":
                finish_dt -= timedelta(minutes=30)

            # Compute total elapsed minutes and format as H:MM hours
            delta = finish_dt - start_dt
            total_minutes = max(0, int(delta.total_seconds() // 60))
            hours = total_minutes // 60
            minutes = total_minutes % 60
            time_taken_str = f"{hours}:{minutes:02d} hours"

            
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
                return serial_is_6ft(serial)
            
            def get_color(serial):
                norm_serial = serial.replace(" ", "").upper()
                if "-GO" in norm_serial:
                    return "grey_oak"
                elif "-O" in norm_serial and "-GO" not in norm_serial:
                    return "rustic_oak"
                elif "-C" in norm_serial:
                    return "stone"
                elif "-RB" in norm_serial:
                    return "rustic_black"
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
            old_count = stock_entry.count
            stock_entry.count += 1
            record_table_stock_log(
                stock_type,
                "complete_top_rail",
                worker,
                1,
                old_count,
                stock_entry.count,
                f"Completed top rail {serial_number}"
            )
            db.session.commit()

            
            # --- NTFY Notification ---
            display_color = color.replace('_', ' ').title()
            message_lines = []
            if low_stock_messages:
                message_lines.append("LOW STOCK WARNING")
                for warning in low_stock_messages:
                    message_lines.append(f"- {warning}")
                message_lines.append("")
                message_lines.append("Completion Details:")
            message_lines.append(f"Serial: {serial_number}")
            message_lines.append(f"Time Taken: {time_taken_str}")
            message = "\n".join(message_lines)
            if low_stock_messages:
                title = f"[LOW STOCK] Top Rail Completed: {size} {display_color}"
            else:
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
            return redirect_back_to_top_rail_form()
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating top rail entry: {str(e)}", "error")
            return redirect_back_to_top_rail_form()
        
        session.pop("top_rail_completion_form_values", None)
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
            return serial_is_6ft(serial)
            
        def get_color(serial):
            norm_serial = serial.replace(" ", "").upper()
            if "-GO" in norm_serial:
                return "Grey Oak"
            elif "-O" in norm_serial and "-GO" not in norm_serial:
                return "Rustic Oak"
            elif "-C" in norm_serial:
                return "Stone"
            elif "-RB" in norm_serial:
                return "Rustic Black"
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
        return serial_is_6ft(serial)
    
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

    top_rail_form_values = session.get("top_rail_completion_form_values") or {}
    form_start_time = top_rail_form_values.get("start_time") or current_time
    form_finish_time = top_rail_form_values.get("finish_time") or current_time
    form_serial_number = top_rail_form_values.get("serial_number") or next_serial_number
    form_base_serial_number = (
        top_rail_form_values.get("base_serial_number")
        or top_rail_base_serial_for_form(form_serial_number)
    )
    form_size = top_rail_form_values.get("size_selector") or default_size
    form_color = top_rail_form_values.get("color_selector") or default_color
    form_issue = top_rail_form_values.get("issue") or ""
    form_lunch = top_rail_form_values.get("lunch") or "No"

    return render_template(
        'top_rails.html',
        issues=issues,
        current_time=current_time,
        form_start_time=form_start_time,
        form_finish_time=form_finish_time,
        form_serial_number=form_serial_number,
        form_base_serial_number=form_base_serial_number,
        form_size=form_size,
        form_color=form_color,
        form_issue=form_issue,
        form_lunch=form_lunch,
        top_rail_form_restored=bool(top_rail_form_values),
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

POD_PARTS_REQUIREMENTS = [
    {"name": FELT_PART_NAME, "per_pod": 2, "sizes": ["7ft", "6ft"]},
    {"name": "7ft Carpet", "per_pod": 1, "sizes": ["7ft"]},
    {"name": "6ft Carpet", "per_pod": 1, "sizes": ["6ft"]},
    {"name": "M10x13mm Tee Nut", "per_pod": 16, "sizes": ["7ft", "6ft"]},
    {"name": "Rows of Black Staples", "per_pod": 2, "sizes": ["7ft", "6ft"]},
]

BODY_PARTS_REQUIREMENTS = [
    {"name": "Large Ramp", "per_body": 1, "sizes": ["7ft"]},
    {"name": "6ft Large Ramp", "per_body": 1, "sizes": ["6ft"]},
    {"name": "Paddle", "per_body": 1, "sizes": ["7ft", "6ft"]},
    *[
        {"name": name, "per_body": 4, "sizes": ["7ft", "6ft"]}
        for name in LAMINATE_PART_NAMES
    ],
    {"name": "Spring Mount", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Spring Holder", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Small Ramp", "per_body": 1, "sizes": ["7ft"]},
    {"name": "Cue Ball Separator", "per_body": 1, "sizes": ["7ft"]},
    {"name": "6ft Cue Ball Separator", "per_body": 1, "sizes": ["6ft"]},
    {"name": "Bushing", "per_body": 2, "sizes": ["7ft", "6ft"]},
    {"name": "Table legs", "per_body": 4, "sizes": ["7ft", "6ft"]},
    {"name": "Ball Gullies 1", "per_body": 2, "sizes": ["7ft"]},
    {"name": "Ball Gullies 2", "per_body": 1, "sizes": ["7ft"]},
    {"name": "Ball Gullies 3", "per_body": 1, "sizes": ["7ft"]},
    {"name": "Ball Gullies 4", "per_body": 1, "sizes": ["7ft"]},
    {"name": "Ball Gullies 5", "per_body": 1, "sizes": ["7ft"]},
    {"name": "6ft Gully Set", "per_body": 1, "sizes": ["6ft"]},
    {"name": "Feet", "per_body": 4, "sizes": ["7ft", "6ft"]},
    {"name": "Triangle trim", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "White ball return trim", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Color ball trim", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Ball window trim", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Aluminum corner", "per_body": 4, "sizes": ["7ft", "6ft"]},
    {"name": "Ramp 170mm", "per_body": 1, "sizes": ["7ft"]},
    {"name": "Ramp 158mm", "per_body": 1, "sizes": ["7ft"]},
    {"name": "Ramp 918mm", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Ramp 376mm", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Chrome handles", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Sticker Set", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "4.8x16mm Self Tapping Screw", "per_body": 37, "sizes": ["7ft", "6ft"]},
    {"name": "4.0 x 50mm Wood Screw", "per_body": 4, "sizes": ["7ft", "6ft"]},
    {"name": "Plastic Window", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "4.2 x 16 No2 Self Tapping Screw", "per_body": 19, "sizes": ["7ft", "6ft"]},
    {"name": "Spring", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Handle Tube", "per_body": 1, "sizes": ["7ft", "6ft"]},
    {"name": "Latch", "per_body": 12, "sizes": ["7ft", "6ft"]},
    {"name": "Pallet Wrap", "per_body": 1/7, "sizes": ["7ft", "6ft"]},
    {"name": "7ft Bag of Bolts", "per_body": 1, "sizes": ["7ft"]},
    {"name": "6ft Bag of Bolts", "per_body": 1, "sizes": ["6ft"]},
    {"name": "7ft Ply Supports", "per_body": 2, "sizes": ["7ft"]},
]

# Parts that are 3D printed (used to separate dashboard display)
BODY_3D_PRINTED_PARTS = {
    "Large Ramp",
    "6ft Large Ramp",
    "Paddle",
    "Spring Mount",
    "Spring Holder",
    "Small Ramp",
    "Cue Ball Separator",
    "6ft Cue Ball Separator",
    "Bushing",
}

BODY_SUPPORT_PARTS = {
    "Pallet Wrap",
    "7ft Ply Supports",
    "7ft Bag of Bolts",
    "6ft Bag of Bolts",
    *LAMINATE_PART_NAMES,
    "Spring",
    "Handle Tube",
    "4.2 x 16 No2 Self Tapping Screw",
    "4.8x16mm Self Tapping Screw",
    "4.0 x 50mm Wood Screw",
}
TOP_RAIL_PARTS_REQUIREMENTS = [
    *TOP_RAIL_TRIM_PARTS.items(),
    ("Chrome corner", 4),
    ("Center pockets", 2),
    ("Corner pockets", 4),
    ("Catch Plate", 12),
    ("M5 x 20 Socket Cap Screw", 16),
    ("M5 x 18 x 1.25 Penny Mudguard Washer", 16),
    ("4.8x32mm Self Tapping Screw", 24),
]

TOP_RAIL_TABLE_STOCK_CONFIGS = [
    {
        "size": "7ft",
        "color": "Black",
        "body_key": "body_7ft_black",
        "body_extra_keys": ["body_7ft_lite"],
        "rail_key": "top_rail_7ft_black",
    },
    {"size": "7ft", "color": "Rustic Oak", "body_key": "body_7ft_rustic_oak", "rail_key": "top_rail_7ft_rustic_oak"},
    {"size": "7ft", "color": "Grey Oak", "body_key": "body_7ft_grey_oak", "rail_key": "top_rail_7ft_grey_oak"},
    {"size": "7ft", "color": "Stone", "body_key": "body_7ft_stone", "rail_key": "top_rail_7ft_stone"},
    {"size": "7ft", "color": "Rustic Black", "body_key": "body_7ft_rustic_black", "rail_key": "top_rail_7ft_rustic_black"},
    {
        "size": "6ft",
        "color": "Black",
        "body_key": "body_6ft_black",
        "body_extra_keys": ["body_6ft_lite"],
        "rail_key": "top_rail_6ft_black",
    },
    {"size": "6ft", "color": "Rustic Oak", "body_key": "body_6ft_rustic_oak", "rail_key": "top_rail_6ft_rustic_oak"},
    {"size": "6ft", "color": "Grey Oak", "body_key": "body_6ft_grey_oak", "rail_key": "top_rail_6ft_grey_oak"},
    {"size": "6ft", "color": "Stone", "body_key": "body_6ft_stone", "rail_key": "top_rail_6ft_stone"},
    {"size": "6ft", "color": "Rustic Black", "body_key": "body_6ft_rustic_black", "rail_key": "top_rail_6ft_rustic_black"},
]


def _format_minutes_display(minutes_value):
    if minutes_value is None:
        return "N/A"
    total_seconds = int(max(0, round(minutes_value * 60)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d} hours"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _format_seconds_display(seconds_value):
    total_seconds = int(max(0, round(seconds_value)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _latest_part_count(part_name):
    latest_entry = (
        PrintedPartsCount.query
        .filter_by(part_name=part_name)
        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
        .first()
    )
    if latest_entry:
        return latest_entry.count
    hardware_part = HardwarePart.query.filter_by(name=part_name).first()
    if hardware_part:
        return hardware_part.initial_count
    return 0


def _table_stock_count(stock_key):
    entry = TableStock.query.filter_by(type=stock_key).first()
    return entry.count if entry else 0


def _top_rail_balance_body_stock(config):
    body_stock = _table_stock_count(config["body_key"])
    for stock_key in config.get("body_extra_keys", []):
        body_stock += _table_stock_count(stock_key)
    return body_stock


def _is_6ft_table(serial):
    if not serial:
        return False
    return serial_is_6ft(serial)


def _is_6ft_pod(serial):
    if not serial:
        return False
    return serial_is_6ft(serial)


def _next_pod_serial_and_size():
    last_pod = CompletedPods.query.order_by(CompletedPods.id.desc()).first()
    next_serial = "1000"
    default_size = "7ft"

    if last_pod and last_pod.serial_number:
        serial = last_pod.serial_number
        default_size = "6ft" if _is_6ft_pod(serial) else "7ft"
        base_serial = serial.split('-')[0].strip()
        try:
            next_serial = str(int(base_serial) + 1)
        except ValueError:
            pass

    return next_serial, default_size


def _next_body_serial_and_size():
    last_table = CompletedTable.query.order_by(CompletedTable.id.desc()).first()
    next_serial = "1000"
    default_size = "7ft"

    if last_table and last_table.serial_number:
        serial = last_table.serial_number
        default_size = "6ft" if _is_6ft_table(serial) else "7ft"
        match = re.match(r"(\d+)", serial)
        if match:
            try:
                next_serial = str(int(match.group(1)) + 1)
            except ValueError:
                pass

    return next_serial, default_size


@app.route('/bonus_goals', methods=['GET', 'POST'])
def bonus_goals():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    ensure_bonus_goal_tables()
    today = date.today()

    if request.method == 'POST':
        try:
            selected_year = int(request.form.get('year', today.year))
            selected_month = int(request.form.get('month', today.month))
        except (TypeError, ValueError):
            flash("Invalid bonus goal month.", "error")
            return redirect(url_for('bonus_goals'))

        if selected_month < 1 or selected_month > 12:
            flash("Invalid bonus goal month.", "error")
            return redirect(url_for('bonus_goals'))

        try:
            row_count = int(request.form.get('row_count', 0))
        except (TypeError, ValueError):
            row_count = 0

        for index in range(row_count):
            worker_name = (request.form.get(f"worker_{index}") or "").strip()
            if not worker_name:
                continue
            for area in BONUS_GOAL_AREA_LABELS:
                raw_target = (request.form.get(f"target_{area}_{index}") or "").strip()
                try:
                    target_count = int(raw_target) if raw_target else 0
                except ValueError:
                    flash(f"Invalid target for {worker_name}.", "error")
                    return redirect(url_for('bonus_goals', year=selected_year, month=selected_month))
                target_count = max(target_count, 0)

                goal = BonusGoal.query.filter_by(
                    area=area,
                    worker_name=worker_name,
                    year=selected_year,
                    month=selected_month
                ).first()
                if target_count > 0:
                    if not goal:
                        goal = BonusGoal(
                            area=area,
                            worker_name=worker_name,
                            year=selected_year,
                            month=selected_month
                        )
                        db.session.add(goal)
                    goal.target_count = target_count
                    goal.active = True
                elif goal:
                    db.session.delete(goal)

        db.session.commit()
        flash("Bonus goals saved.", "success")
        return redirect(url_for('bonus_goals', year=selected_year, month=selected_month))

    try:
        selected_year = int(request.args.get('year', today.year))
        selected_month = int(request.args.get('month', today.month))
    except (TypeError, ValueError):
        selected_year = today.year
        selected_month = today.month

    if selected_month < 1 or selected_month > 12:
        selected_month = today.month

    worker_names = {
        worker.name
        for worker in Worker.query.order_by(Worker.name.asc()).all()
        if worker.name
    }
    worker_names.update(
        goal.worker_name
        for goal in BonusGoal.query.filter_by(year=selected_year, month=selected_month).all()
        if goal.worker_name
    )
    worker_names = sorted(worker_names, key=lambda name: name.lower())

    existing_goals = {
        (goal.worker_name, goal.area): goal.target_count
        for goal in BonusGoal.query.filter_by(year=selected_year, month=selected_month).all()
    }
    rows = []
    for index, worker_name in enumerate(worker_names):
        rows.append({
            "index": index,
            "worker": worker_name,
            "targets": {
                area["key"]: existing_goals.get((worker_name, area["key"]), "")
                for area in BONUS_GOAL_AREAS
            }
        })

    month_options = [
        {"value": month, "label": date(selected_year, month, 1).strftime("%B")}
        for month in range(1, 13)
    ]
    year_options = list(range(today.year - 1, today.year + 3))

    return render_template(
        'bonus_goals.html',
        areas=BONUS_GOAL_AREAS,
        rows=rows,
        row_count=len(rows),
        selected_year=selected_year,
        selected_month=selected_month,
        selected_month_label=bonus_goal_month_label(selected_year, selected_month),
        month_options=month_options,
        year_options=year_options
    )


@app.route('/top_rail_dashboard')
def top_rail_dashboard_view():
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())

    stats = {
        "daily": TopRail.query.filter(TopRail.date == today).count(),
        "weekly": TopRail.query.filter(TopRail.date >= start_of_week, TopRail.date <= today).count(),
        "monthly": TopRail.query.filter(
            extract('year', TopRail.date) == today.year,
            extract('month', TopRail.date) == today.month
        ).count(),
        "yearly": TopRail.query.filter(extract('year', TopRail.date) == today.year).count()
    }

    next_serial = "1000"
    last_rail = TopRail.query.order_by(TopRail.id.desc()).first()
    if last_rail and last_rail.serial_number:
        match = re.match(r'(\d+)', last_rail.serial_number)
        if match:
            next_serial = str(int(match.group(1)) + 1)

    active_timer_info = None
    active_timer = TopRailTiming.query.filter_by(completed=False).order_by(TopRailTiming.start_time.asc()).first()
    if active_timer:
        elapsed_seconds = (datetime.now() - active_timer.start_time).total_seconds()
        active_timer_info = {
            "worker": active_timer.worker,
            "elapsed": _format_seconds_display(elapsed_seconds)
        }

    completed_timings = (
        TopRailTiming.query
        .filter(TopRailTiming.completed == True, TopRailTiming.duration_minutes.isnot(None))
        .order_by(TopRailTiming.date.desc(), TopRailTiming.end_time.desc())
        .limit(20)
        .all()
    )

    average_minutes = None
    if completed_timings:
        durations = [t.duration_minutes for t in completed_timings if t.duration_minutes]
        if durations:
            average_minutes = sum(durations) / len(durations)

    predicted_total = None
    if average_minutes and average_minutes > 0:
        workday_minutes = 7.5 * 60
        predicted_total = max(stats["daily"], floor(workday_minutes / average_minutes))

    parts_data = []
    min_rails_possible = None
    for part_name, qty_per_rail in TOP_RAIL_PARTS_REQUIREMENTS:
        stock = _latest_part_count(part_name)
        rails_possible = max(stock, 0) // qty_per_rail if qty_per_rail else max(stock, 0)
        status = 'ok'
        if rails_possible < 5:
            status = 'critical'
        elif rails_possible < 10:
            status = 'warning'

        parts_data.append({
            "name": part_name,
            "stock": stock,
            "per_rail": qty_per_rail,
            "rails_possible": rails_possible,
            "status": status
        })

        if min_rails_possible is None:
            min_rails_possible = rails_possible
        else:
            min_rails_possible = min(min_rails_possible, rails_possible)

    parts_data.sort(key=lambda item: item["rails_possible"])
    limiting_parts = [item for item in parts_data if item["rails_possible"] == min_rails_possible]

    deficits_by_size = {"7ft": [], "6ft": []}
    for config in TOP_RAIL_TABLE_STOCK_CONFIGS:
        body_stock = _top_rail_balance_body_stock(config)
        rail_stock = _table_stock_count(config["rail_key"])

        if body_stock == 0 and rail_stock == 0:
            status_text = "No bodies or rails."
            status_class = "empty"
        elif body_stock == rail_stock:
            status_text = "Balanced"
            status_class = "balanced"
        elif body_stock > rail_stock:
            status_text = f"{body_stock - rail_stock} more Top Rails needed."
            status_class = "need-rails"
        else:
            status_text = f"{rail_stock - body_stock} more Bodies needed."
            status_class = "need-bodies"

        deficits_by_size[config["size"]].append({
            "color": config["color"],
            "body_stock": body_stock,
            "rail_stock": rail_stock,
            "status": status_text,
            "status_class": status_class
        })

    for size in deficits_by_size:
        deficits_by_size[size].sort(key=lambda item: item["color"])

    def parse_time_string(value):
        if not value:
            return None
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
        return None

    def calculate_top_rail_duration(rail):
        start_time_obj = parse_time_string(rail.start_time)
        finish_time_obj = parse_time_string(rail.finish_time)
        if not start_time_obj or not finish_time_obj:
            return None

        start_dt = datetime.combine(rail.date, start_time_obj)
        finish_dt = datetime.combine(rail.date, finish_time_obj)
        if finish_time_obj < start_time_obj:
            overnight_dt = datetime.combine(rail.date + timedelta(days=1), finish_time_obj)
            if (overnight_dt - start_dt) <= timedelta(hours=12):
                finish_dt = overnight_dt

        if rail.lunch and str(rail.lunch).lower() == "yes":
            finish_dt -= timedelta(minutes=30)

        delta = finish_dt - start_dt
        if delta.total_seconds() < 0 or delta < timedelta(minutes=10) or delta > timedelta(hours=8):
            return None
        return delta

    def format_avg_duration(total_seconds, count):
        if not count:
            return "N/A"
        avg_seconds = total_seconds / count
        avg_seconds = max(0, int(round(avg_seconds)))
        hours, remainder = divmod(avg_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    def top_rail_duration_summary(rails):
        total_duration_seconds = 0
        counted_rails = 0
        last_rail_dt = None
        last_rail_duration = None
        for rail in rails:
            duration = calculate_top_rail_duration(rail)
            if duration is None:
                continue
            total_duration_seconds += duration.total_seconds()
            counted_rails += 1
            finish_obj = parse_time_string(rail.finish_time)
            finish_dt = datetime.combine(rail.date, finish_obj) if finish_obj else rail.date
            if last_rail_dt is None or finish_dt > last_rail_dt:
                last_rail_dt = finish_dt
                last_rail_duration = duration
        return total_duration_seconds, counted_rails, last_rail_duration

    current_month_rails = TopRail.query.filter(
        extract('year', TopRail.date) == today.year,
        extract('month', TopRail.date) == today.month
    ).all()
    total_duration_seconds, counted_rails, last_rail_duration = top_rail_duration_summary(current_month_rails)

    avg_top_rail_time = format_avg_duration(total_duration_seconds, counted_rails)
    last_top_rail_time = format_avg_duration(last_rail_duration.total_seconds(), 1) if last_rail_duration else "N/A"

    start_of_month = today.replace(day=1)
    previous_month_end = start_of_month - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    previous_month_rails = TopRail.query.filter(
        TopRail.date >= previous_month_start,
        TopRail.date <= previous_month_end
    ).all()
    previous_total_seconds, previous_counted_rails, _ = top_rail_duration_summary(previous_month_rails)
    last_month_avg_top_rail_time = format_avg_duration(previous_total_seconds, previous_counted_rails)
    bonus_progress = dashboard_bonus_progress("top_rails", today.year, today.month)

    return render_template(
        'top_rail_dashboard.html',
        stats=stats,
        next_serial=next_serial,
        active_timer=active_timer_info,
        average_time_display=_format_minutes_display(average_minutes),
        predicted_total=predicted_total,
        parts_data=parts_data,
        limiting_parts=limiting_parts,
        min_rails_possible=min_rails_possible,
        deficits_by_size=deficits_by_size,
        avg_top_rail_time=avg_top_rail_time,
        last_month_avg_top_rail_time=last_month_avg_top_rail_time,
        last_top_rail_time=last_top_rail_time,
        bonus_progress=bonus_progress,
        bonus_month_label=bonus_goal_month_label(today.year, today.month)
    )


@app.route('/pod_dashboard')
def pod_dashboard_view():
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    start_of_month = today.replace(day=1)
    start_of_year = today.replace(month=1, day=1)

    stats = {
        "daily": CompletedPods.query.filter(CompletedPods.date == today).count(),
        "weekly": CompletedPods.query.filter(CompletedPods.date >= start_of_week, CompletedPods.date <= today).count(),
        "monthly": CompletedPods.query.filter(
            extract('year', CompletedPods.date) == today.year,
            extract('month', CompletedPods.date) == today.month
        ).count(),
        "yearly": CompletedPods.query.filter(extract('year', CompletedPods.date) == today.year).count()
    }

    next_serial, default_size = _next_pod_serial_and_size()
    next_serial_display = f"{next_serial} - 6" if default_size == "6ft" else next_serial

    part_stock = {
        part["name"]: _latest_part_count(part["name"])
        for part in POD_PARTS_REQUIREMENTS
    }

    parts_data = []
    for part in POD_PARTS_REQUIREMENTS:
        stock = part_stock.get(part["name"], 0)
        pods_possible = stock // part["per_pod"] if part["per_pod"] else stock
        status = 'ok'
        if pods_possible < 5:
            status = 'critical'
        elif pods_possible < 10:
            status = 'warning'

        parts_data.append({
            "name": part["name"],
            "stock": stock,
            "per_pod": part["per_pod"],
            "pods_possible": pods_possible,
            "status": status,
            "sizes_display": ", ".join(part["sizes"])
        })

    parts_data.sort(key=lambda item: item["pods_possible"])

    capacity_by_size = {}
    for size in ["7ft", "6ft"]:
        relevant_parts = [p for p in POD_PARTS_REQUIREMENTS if size in p["sizes"]]
        min_possible = None
        limiting_parts = []
        requirements = []

        for part in relevant_parts:
            requirements.append(f"{part['per_pod']} x {part['name']}")
            stock = part_stock.get(part["name"], 0)
            pods_possible = stock // part["per_pod"] if part["per_pod"] else stock

            if min_possible is None or pods_possible < min_possible:
                min_possible = pods_possible
                limiting_parts = [part["name"]]
            elif pods_possible == min_possible:
                limiting_parts.append(part["name"])

        capacity_by_size[size] = {
            "pods_possible": min_possible if min_possible is not None else 0,
            "limiting_parts": limiting_parts,
            "requirements": requirements
        }

    min_capacity = min(data["pods_possible"] for data in capacity_by_size.values())
    limiting_overall = sorted({
        part_name
        for data in capacity_by_size.values()
        if data["pods_possible"] == min_capacity
        for part_name in data["limiting_parts"]
    })

    def parse_time_string(value):
        if not value:
            return None
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
        return None

    def calculate_pod_duration(pod):
        start_time_obj = pod.start_time
        finish_time_obj = pod.finish_time
        if isinstance(start_time_obj, str):
            start_time_obj = parse_time_string(start_time_obj)
        if isinstance(finish_time_obj, str):
            finish_time_obj = parse_time_string(finish_time_obj)
        if not start_time_obj or not finish_time_obj:
            return None

        start_dt = datetime.combine(pod.date, start_time_obj)
        finish_dt = datetime.combine(pod.date, finish_time_obj)
        if finish_time_obj < start_time_obj:
            overnight_dt = datetime.combine(pod.date + timedelta(days=1), finish_time_obj)
            if (overnight_dt - start_dt) <= timedelta(hours=12):
                finish_dt = overnight_dt

        if pod.lunch and str(pod.lunch).lower() == "yes":
            finish_dt -= timedelta(minutes=30)

        delta = finish_dt - start_dt
        if delta.total_seconds() < 0 or delta < timedelta(minutes=10) or delta > timedelta(hours=8):
            return None
        return delta

    def format_avg_duration(total_seconds, count):
        if not count:
            return "N/A"
        avg_seconds = total_seconds / count
        avg_seconds = max(0, int(round(avg_seconds)))
        hours, remainder = divmod(avg_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    previous_month = (start_of_month - timedelta(days=1)).replace(day=1)

    def pod_type_stats_for_month(month_date):
        month_pods = CompletedPods.query.filter(
            extract('year', CompletedPods.date) == month_date.year,
            extract('month', CompletedPods.date) == month_date.month
        ).all()
        type_stats = {
            TABLE_TYPE_CHAMPION: {"seconds": 0, "count": 0},
            TABLE_TYPE_LITE: {"seconds": 0, "count": 0},
        }
        for pod in month_pods:
            duration = calculate_pod_duration(pod)
            if duration is None:
                continue
            pod_type = table_type_from_serial(pod.serial_number)
            if pod_type not in type_stats:
                pod_type = TABLE_TYPE_CHAMPION
            type_stats[pod_type]["seconds"] += duration.total_seconds()
            type_stats[pod_type]["count"] += 1
        return type_stats

    def average_seconds(type_stats):
        if not type_stats["count"]:
            return None
        return type_stats["seconds"] / type_stats["count"]

    def format_avg_seconds(avg_seconds):
        if avg_seconds is None:
            return "N/A"
        return format_avg_duration(avg_seconds, 1)

    def comparison_for_average(current_avg, previous_avg):
        if current_avg is None or previous_avg is None:
            return {
                "text": "No last-month comparison",
                "class": "muted"
            }
        difference = current_avg - previous_avg
        if abs(difference) < 1:
            return {
                "text": "Same as last month",
                "class": "same"
            }
        direction = "slower" if difference > 0 else "faster"
        status_class = "slower" if difference > 0 else "faster"
        return {
            "text": f"{format_avg_duration(abs(difference), 1)} {direction}",
            "class": status_class
        }

    current_type_stats = pod_type_stats_for_month(today)
    previous_type_stats = pod_type_stats_for_month(previous_month)
    pod_type_average_rows = []
    for table_type, label in (
        (TABLE_TYPE_CHAMPION, "Champion"),
        (TABLE_TYPE_LITE, "Lite"),
    ):
        current_avg = average_seconds(current_type_stats[table_type])
        previous_avg = average_seconds(previous_type_stats[table_type])
        comparison = comparison_for_average(current_avg, previous_avg)
        pod_type_average_rows.append({
            "label": label,
            "current_avg": format_avg_seconds(current_avg),
            "last_month_avg": format_avg_seconds(previous_avg),
            "comparison_text": comparison["text"],
            "comparison_class": comparison["class"],
        })

    bonus_progress = dashboard_bonus_progress(
        "pods",
        today.year,
        today.month,
        label_overrides={"Tom F": "Tom F Pod Goal"}
    )
    tom_f_body_bonus = dashboard_bonus_progress(
        "bodies",
        today.year,
        today.month,
        include_workers=["Tom F"],
        label_overrides={"Tom F": "Tom F Body Goal"}
    )
    bonus_progress = sorted(
        [*bonus_progress, *tom_f_body_bonus],
        key=lambda row: (
            row.get("area") != "pods",
            1 if row.get("next_bonus") else 0,
            -row.get("percentage", 0),
            row.get("display_worker", row.get("worker", "")).lower()
        )
    )
    celebration_goal_labels = {"Tom F Pod Goal", "Tom F Body Goal"}
    celebration_goals = [
        goal for goal in bonus_progress
        if goal.get("display_worker", goal.get("worker")) in celebration_goal_labels
    ]
    pod_goal_celebration = (
        len(celebration_goals) == len(celebration_goal_labels)
        and all(goal.get("target_hit") for goal in celebration_goals)
    )
    pod_goal_celebration_key = f"{today.year}-{today.month:02d}"

    return render_template(
        'pod_dashboard.html',
        stats=stats,
        next_serial=next_serial_display,
        default_size=default_size,
        parts_data=parts_data,
        capacity_by_size=capacity_by_size,
        limiting_overall=limiting_overall,
        min_capacity=min_capacity,
        pod_type_average_rows=pod_type_average_rows,
        previous_month_label=previous_month.strftime("%B %Y"),
        bonus_progress=bonus_progress,
        bonus_month_label=bonus_goal_month_label(today.year, today.month),
        pod_goal_celebration=pod_goal_celebration,
        pod_goal_celebration_key=pod_goal_celebration_key
    )


@app.route('/body_dashboard')
def body_dashboard_view():
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday())
    start_of_month = today.replace(day=1)
    start_of_year = today.replace(month=1, day=1)

    stats = {
        "daily": CompletedTable.query.filter(CompletedTable.date == today).count(),
        "weekly": CompletedTable.query.filter(CompletedTable.date >= start_of_week, CompletedTable.date <= today).count(),
        "monthly": CompletedTable.query.filter(
            extract('year', CompletedTable.date) == today.year,
            extract('month', CompletedTable.date) == today.month
        ).count(),
        "yearly": CompletedTable.query.filter(extract('year', CompletedTable.date) == today.year).count()
    }

    next_serial, default_size = _next_body_serial_and_size()
    next_serial_display = f"{next_serial} - 6" if default_size == "6ft" else next_serial

    part_stock = {
        part["name"]: _latest_part_count(part["name"])
        for part in BODY_PARTS_REQUIREMENTS
    }

    def pallet_wrap_display_count():
        target_name = "Pallet Wrap"
        wrap_part = HardwarePart.query.filter(func.lower(HardwarePart.name) == target_name.lower()).first()
        part_name = wrap_part.name if wrap_part else target_name
        latest_entry = (PrintedPartsCount.query
                        .filter(func.lower(PrintedPartsCount.part_name) == part_name.lower())
                        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                        .first())
        roll_count = latest_entry.count if latest_entry else (wrap_part.initial_count if wrap_part else 0)
        remainder_entry = TableStock.query.filter_by(type="pallet_wrap_remainder").first()
        used_in_current_roll = remainder_entry.count if remainder_entry else 0
        bodies_per_wrap_roll = 7
        if used_in_current_roll <= 0 or used_in_current_roll >= bodies_per_wrap_roll:
            fraction_remaining = 0
        else:
            fraction_remaining = (bodies_per_wrap_roll - used_in_current_roll) / bodies_per_wrap_roll
        display_count = max(0.0, roll_count + fraction_remaining)
        return round(display_count, 2)

    # Show fractional rolls remaining for pallet wrap
    part_stock["Pallet Wrap"] = pallet_wrap_display_count()

    def format_per_body(value):
        """Show whole numbers without decimals; otherwise round to 2 decimals."""
        if value is None:
            return "0"
        if abs(value - int(value)) < 1e-9:
            return str(int(value))
        return f"{value:.2f}"

    def parse_time_string(value):
        if not value:
            return None
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue
        return None

    def calculate_body_duration(body):
        start_time_obj = parse_time_string(body.start_time)
        finish_time_obj = parse_time_string(body.finish_time)
        if not start_time_obj or not finish_time_obj:
            return None

        start_dt = datetime.combine(body.date, start_time_obj)
        finish_dt = datetime.combine(body.date, finish_time_obj)
        if finish_time_obj < start_time_obj:
            overnight_dt = datetime.combine(body.date + timedelta(days=1), finish_time_obj)
            if (overnight_dt - start_dt) <= timedelta(hours=12):
                finish_dt = overnight_dt

        if body.lunch and str(body.lunch).lower() == "yes":
            finish_dt -= timedelta(minutes=30)

        delta = finish_dt - start_dt
        if delta.total_seconds() < 0 or delta < timedelta(minutes=10) or delta > timedelta(hours=8):
            return None
        return delta

    def format_avg_duration(total_seconds, count):
        if not count:
            return "N/A"
        avg_seconds = total_seconds / count
        avg_seconds = max(0, int(round(avg_seconds)))
        hours, remainder = divmod(avg_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    worker_aliases = {
        "Jack B": ["jackb", "jack"],
    }

    def normalize_worker_name(name):
        if not name:
            return ""
        return re.sub(r'[^a-z]', '', name.lower())

    def map_to_worker(name):
        norm = normalize_worker_name(name)
        for worker, aliases in worker_aliases.items():
            for alias in aliases:
                if norm.startswith(alias):
                    return worker
        return None

    # Current month Jack-only averages using actual recorded durations.
    current_month_bodies = CompletedTable.query.filter(
        extract('year', CompletedTable.date) == today.year,
        extract('month', CompletedTable.date) == today.month
    ).all()
    jack_type_stats_current = {
        TABLE_TYPE_CHAMPION: {"seconds": 0, "count": 0},
        TABLE_TYPE_LITE: {"seconds": 0, "count": 0},
    }
    for body in current_month_bodies:
        if map_to_worker(body.worker) != "Jack B":
            continue
        duration = calculate_body_duration(body)
        if duration is None:
            continue
        body_type = table_type_from_serial(body.serial_number)
        if body_type in jack_type_stats_current:
            jack_type_stats_current[body_type]["seconds"] += duration.total_seconds()
            jack_type_stats_current[body_type]["count"] += 1

    previous_month = (start_of_month - timedelta(days=1)).replace(day=1)

    def average_seconds(stats):
        if not stats["count"]:
            return None
        return stats["seconds"] / stats["count"]

    def format_avg_seconds(avg_seconds):
        if avg_seconds is None:
            return "N/A"
        return format_avg_duration(avg_seconds, 1)

    def comparison_for_average(current_avg, previous_avg):
        if current_avg is None or previous_avg is None:
            return {
                "text": "No last-month comparison",
                "class": "muted"
            }
        difference = current_avg - previous_avg
        if abs(difference) < 1:
            return {
                "text": "Same as last month",
                "class": "same"
            }
        direction = "slower" if difference > 0 else "faster"
        status_class = "slower" if difference > 0 else "faster"
        return {
            "text": f"{format_avg_duration(abs(difference), 1)} {direction}",
            "class": status_class
        }

    def jack_type_stats_for_month(month_date):
        month_bodies = CompletedTable.query.filter(
            extract('year', CompletedTable.date) == month_date.year,
            extract('month', CompletedTable.date) == month_date.month
        ).all()
        stats = {
            TABLE_TYPE_CHAMPION: {"seconds": 0, "count": 0},
            TABLE_TYPE_LITE: {"seconds": 0, "count": 0},
        }
        for body in month_bodies:
            if map_to_worker(body.worker) != "Jack B":
                continue
            duration = calculate_body_duration(body)
            if duration is None:
                continue
            body_type = table_type_from_serial(body.serial_number)
            if body_type in stats:
                stats[body_type]["seconds"] += duration.total_seconds()
                stats[body_type]["count"] += 1
        return stats

    jack_type_stats_previous = jack_type_stats_for_month(previous_month)
    jack_type_average_rows = []
    for table_type, label in (
        (TABLE_TYPE_CHAMPION, "Champion"),
        (TABLE_TYPE_LITE, "Lite"),
    ):
        current_avg = average_seconds(jack_type_stats_current[table_type])
        previous_avg = average_seconds(jack_type_stats_previous[table_type])
        comparison = comparison_for_average(current_avg, previous_avg)
        jack_type_average_rows.append({
            "label": label,
            "current_avg": format_avg_seconds(current_avg),
            "last_month_avg": format_avg_seconds(previous_avg),
            "comparison_text": comparison["text"],
            "comparison_class": comparison["class"],
        })

    parts_data = []
    for part in BODY_PARTS_REQUIREMENTS:
        stock = part_stock.get(part["name"], 0)
        bodies_possible = int(stock // part["per_body"]) if part["per_body"] else int(stock)
        status = 'ok'
        if bodies_possible < 5:
            status = 'critical'
        elif bodies_possible < 10:
            status = 'warning'

        parts_data.append({
            "name": part["name"],
            "stock": stock,
            "per_body": part["per_body"],
            "per_body_display": format_per_body(part["per_body"]),
            "bodies_possible": bodies_possible,
            "status": status,
            "sizes_display": ", ".join(part["sizes"])
        })

    parts_data.sort(key=lambda item: item["bodies_possible"])
    printed_parts_data = [p for p in parts_data if p["name"] in BODY_3D_PRINTED_PARTS]
    support_parts_data = [p for p in parts_data if p["name"] in BODY_SUPPORT_PARTS]
    other_parts_data = [p for p in parts_data if p["name"] not in BODY_3D_PRINTED_PARTS and p["name"] not in BODY_SUPPORT_PARTS]

    capacity_by_size = {}
    for size in ["7ft", "6ft"]:
        relevant_parts = [p for p in BODY_PARTS_REQUIREMENTS if size in p["sizes"]]
        min_possible = None
        limiting_parts = []
        requirements = []

        for part in relevant_parts:
            requirements.append(f"{part['per_body']} x {part['name']}")
            stock = part_stock.get(part["name"], 0)
            bodies_possible = int(stock // part["per_body"]) if part["per_body"] else int(stock)

            if min_possible is None or bodies_possible < min_possible:
                min_possible = bodies_possible
                limiting_parts = [part["name"]]
            elif bodies_possible == min_possible:
                limiting_parts.append(part["name"])

        capacity_by_size[size] = {
            "bodies_possible": min_possible if min_possible is not None else 0,
            "limiting_parts": limiting_parts,
            "requirements": requirements
        }

    min_capacity = min(data["bodies_possible"] for data in capacity_by_size.values())
    limiting_overall = sorted({
        part_name
        for data in capacity_by_size.values()
        if data["bodies_possible"] == min_capacity
        for part_name in data["limiting_parts"]
    })
    bonus_progress = dashboard_bonus_progress(
        "bodies",
        today.year,
        today.month,
        exclude_workers=["Tom F"]
    )

    return render_template(
        'body_dashboard.html',
        stats=stats,
        next_serial=next_serial_display,
        default_size=default_size,
        parts_data=parts_data,
        capacity_by_size=capacity_by_size,
        limiting_overall=limiting_overall,
        min_capacity=min_capacity,
        previous_month_label=previous_month.strftime("%B %Y"),
        jack_type_average_rows=jack_type_average_rows,
        printed_parts_data=printed_parts_data,
        support_parts_data=support_parts_data,
        other_parts_data=other_parts_data,
        bonus_progress=bonus_progress,
        bonus_month_label=bonus_goal_month_label(today.year, today.month)
    )


@app.route('/cnc_queue_manager')
def cnc_queue_manager():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    ensure_cnc_tables()

    jobs = (
        CncJob.query
        .order_by(CncJob.created_at.desc(), CncJob.id.desc())
        .all()
    )
    queue_counts_rows = (
        db.session.query(CncQueueItem.job_id, func.count(CncQueueItem.id))
        .filter(CncQueueItem.status == CNC_STATUS_QUEUED)
        .group_by(CncQueueItem.job_id)
        .all()
    )
    queued_counts = {job_id: count for job_id, count in queue_counts_rows}
    queues = _cnc_queue_snapshot()
    monthly_cut_history = cnc_monthly_cut_file_history()

    return render_template(
        'cnc_queue_manager.html',
        jobs=jobs,
        queued_counts=queued_counts,
        queues=queues,
        monthly_cut_history=monthly_cut_history,
        machine_numbers=CNC_MACHINE_NUMBERS
    )


@app.route('/cnc_dashboard')
def cnc_dashboard():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    ensure_cnc_tables()
    queues = _cnc_queue_snapshot()
    today = london_now().date()
    today_start_utc, today_end_utc = london_period_utc_bounds(today.year, today.month, today.day)
    completed_base_filters = (
        CncQueueItem.status == CNC_STATUS_COMPLETED,
        CncQueueItem.completed_at.isnot(None),
    )
    completed_today_filters = completed_base_filters + (
        CncQueueItem.completed_at >= today_start_utc,
        CncQueueItem.completed_at < today_end_utc,
    )
    completed_today_count = cnc_completed_quantity_total(year=today.year, month=today.month, day=today.day)
    completed_month_count = cnc_completed_quantity_total(year=today.year, month=today.month)
    elapsed_workdays = elapsed_weekdays_in_month(today)
    daily_avg_sheets = completed_month_count / elapsed_workdays if elapsed_workdays else 0
    daily_avg_sheets_display = f"{daily_avg_sheets:.1f}".rstrip("0").rstrip(".")
    completed_today = (
        CncQueueItem.query
        .options(joinedload(CncQueueItem.job))
        .filter(*completed_today_filters)
        .order_by(CncQueueItem.completed_at.desc(), CncQueueItem.id.desc())
        .limit(200)
        .all()
    )
    last_recorded_rows = (
        db.session.query(CncQueueItem.machine_number, func.max(CncQueueItem.completed_at))
        .filter(*completed_today_filters)
        .group_by(CncQueueItem.machine_number)
        .all()
    )
    last_recorded_by_machine = {
        machine_number: completed_at
        for machine_number, completed_at in last_recorded_rows
    }
    machine_run_rows = (
        db.session.query(CncQueueItem.machine_number, func.count(CncQueueItem.id))
        .filter(*completed_today_filters)
        .group_by(CncQueueItem.machine_number)
        .all()
    )
    machine_runs_today_by_machine = {machine_number: 0 for machine_number in CNC_MACHINE_NUMBERS}
    for machine_number, run_count in machine_run_rows:
        if machine_number in machine_runs_today_by_machine:
            machine_runs_today_by_machine[machine_number] = int(run_count or 0)
    total_machine_runs_today = sum(machine_runs_today_by_machine.values())
    bonus_progress = dashboard_bonus_progress("cnc", today.year, today.month)
    pacing_goal = max(bonus_progress, key=lambda goal: goal.get("remaining", 0), default=None)
    cnc_goal_target = pacing_goal.get("target", 0) if pacing_goal else 0
    cnc_goal_remaining = pacing_goal.get("remaining", 0) if pacing_goal else 0
    remaining_workdays = remaining_weekdays_in_month(today)
    if pacing_goal and pacing_goal.get("next_bonus"):
        next_bonus_year = pacing_goal.get("period_year")
        next_bonus_month = pacing_goal.get("period_month")
        remaining_workdays += weekdays_in_month(next_bonus_year, next_bonus_month)
    if cnc_goal_target <= 0:
        required_sheets_per_day_display = "No Goal"
        goal_pacing_note = "Set a CNC goal"
    elif cnc_goal_remaining <= 0:
        required_sheets_per_day_display = "0"
        goal_pacing_note = "Goal reached"
    elif remaining_workdays <= 0:
        required_sheets_per_day_display = "N/A"
        goal_pacing_note = f"{cnc_goal_remaining} left"
    else:
        required_sheets_per_day = cnc_goal_remaining / remaining_workdays
        required_sheets_per_day_display = f"{required_sheets_per_day:.1f}".rstrip("0").rstrip(".")
        goal_pacing_note = f"{cnc_goal_remaining} left / {remaining_workdays} days"
    mdf_inventory = _get_or_create_mdf_inventory()
    if mdf_inventory in db.session.new:
        db.session.commit()

    return render_template(
        'cnc_dashboard.html',
        queues=queues,
        machine_numbers=CNC_MACHINE_NUMBERS,
        completed_today=completed_today,
        completed_today_count=completed_today_count,
        last_recorded_by_machine=last_recorded_by_machine,
        machine_runs_today_by_machine=machine_runs_today_by_machine,
        total_machine_runs_today=total_machine_runs_today,
        daily_avg_sheets=daily_avg_sheets_display,
        required_sheets_per_day=required_sheets_per_day_display,
        goal_pacing_note=goal_pacing_note,
        completed_month_count=completed_month_count,
        bonus_progress=bonus_progress,
        bonus_month_label=bonus_goal_month_label(today.year, today.month),
        mdf_inventory=mdf_inventory,
        render_time=london_now().strftime("%d/%m/%Y %H:%M")
    )


@app.route('/api/cnc/jobs', methods=['POST'])
def api_cnc_create_job():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    notes = (data.get('notes') or '').strip()

    if not name:
        return jsonify({"success": False, "error": "Job name is required."}), 400

    try:
        quantity = int(data.get('quantity', 1))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Quantity must be a whole number."}), 400
    quantity = max(quantity, 1)

    new_job = CncJob(name=name, quantity=quantity, notes=notes)
    db.session.add(new_job)
    db.session.commit()

    return jsonify({"success": True, "job_id": new_job.id}), 200


@app.route('/api/cnc/jobs/update', methods=['POST'])
def api_cnc_update_job():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}

    try:
        job_id = int(data.get('job_id', 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid job ID."}), 400

    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"success": False, "error": "Job name is required."}), 400

    job = CncJob.query.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found."}), 404

    job.name = name
    db.session.commit()
    return jsonify({"success": True}), 200


@app.route('/api/cnc/jobs/bulk_delete', methods=['POST'])
def api_cnc_bulk_delete_jobs():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}
    job_ids = _coerce_positive_int_list(data.get('job_ids', []))
    if not job_ids:
        return jsonify({"success": False, "error": "No jobs selected."}), 400

    jobs = CncJob.query.filter(CncJob.id.in_(job_ids)).all()
    if not jobs:
        return jsonify({"success": False, "error": "Selected jobs not found."}), 404

    previous_counts = _cnc_capture_queue_counts()

    for job in jobs:
        db.session.delete(job)

    for machine_number in CNC_MACHINE_NUMBERS:
        _cnc_reindex_machine(machine_number)

    db.session.commit()
    if not _payload_bool(data.get('suppress_low_queue_notification')):
        _cnc_notify_low_queue_transitions(previous_counts)
    return jsonify({"success": True, "deleted_jobs": len(jobs)}), 200


@app.route('/api/cnc/queue/add', methods=['POST'])
def api_cnc_queue_add():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}

    try:
        job_id = int(data.get('job_id', 0))
        machine_number = int(data.get('machine_number', 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid job or machine."}), 400

    if machine_number not in CNC_MACHINE_NUMBERS:
        return jsonify({"success": False, "error": "Machine must be between 1 and 4."}), 400

    job = CncJob.query.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found."}), 404

    next_position = (
        db.session.query(func.count(CncQueueItem.id))
        .filter_by(machine_number=machine_number, status=CNC_STATUS_QUEUED)
        .scalar() or 0
    ) + 1

    queue_item = CncQueueItem(
        job_id=job.id,
        machine_number=machine_number,
        position=next_position,
        status=CNC_STATUS_QUEUED
    )
    db.session.add(queue_item)
    db.session.commit()

    return jsonify({"success": True, "queue_item_id": queue_item.id}), 200


@app.route('/api/cnc/queue/move', methods=['POST'])
def api_cnc_queue_move():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}

    try:
        item_id = int(data.get('item_id', 0))
        machine_number = int(data.get('machine_number', 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid move payload."}), 400

    if machine_number not in CNC_MACHINE_NUMBERS:
        return jsonify({"success": False, "error": "Machine must be between 1 and 4."}), 400

    item = CncQueueItem.query.get(item_id)
    if not item or item.status != CNC_STATUS_QUEUED:
        return jsonify({"success": False, "error": "Queue item not found."}), 404

    original_machine = item.machine_number
    previous_counts = _cnc_capture_queue_counts([original_machine, machine_number])
    next_position = (
        db.session.query(func.count(CncQueueItem.id))
        .filter(
            CncQueueItem.machine_number == machine_number,
            CncQueueItem.status == CNC_STATUS_QUEUED,
            CncQueueItem.id != item.id
        )
        .scalar() or 0
    ) + 1

    item.machine_number = machine_number
    item.position = next_position

    _cnc_reindex_machine(machine_number)
    if original_machine != machine_number:
        _cnc_reindex_machine(original_machine)

    db.session.commit()
    if not _payload_bool(data.get('suppress_low_queue_notification')):
        _cnc_notify_low_queue_transitions(previous_counts)
    return jsonify({"success": True}), 200


@app.route('/api/cnc/queue/reorder', methods=['POST'])
def api_cnc_queue_reorder():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}

    try:
        item_id = int(data.get('item_id', 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid queue item."}), 400

    direction = (data.get('direction') or '').strip().lower()
    if direction not in ('up', 'down', 'top'):
        return jsonify({"success": False, "error": "Direction must be 'up', 'down', or 'top'."}), 400

    item = CncQueueItem.query.get(item_id)
    if not item or item.status != CNC_STATUS_QUEUED:
        return jsonify({"success": False, "error": "Queue item not found."}), 404

    machine_items = (
        CncQueueItem.query
        .filter_by(machine_number=item.machine_number, status=CNC_STATUS_QUEUED)
        .order_by(CncQueueItem.position.asc(), CncQueueItem.id.asc())
        .all()
    )
    if len(machine_items) <= 1:
        return jsonify({"success": True}), 200

    target_index = None
    for idx, machine_item in enumerate(machine_items):
        if machine_item.id == item.id:
            target_index = idx
            break

    if target_index is None:
        return jsonify({"success": False, "error": "Queue item not found in machine queue."}), 404

    if direction == 'top':
        if target_index == 0:
            return jsonify({"success": True}), 200
        machine_items.pop(target_index)
        machine_items.insert(0, item)
        for index, machine_item in enumerate(machine_items, start=1):
            machine_item.position = index
        db.session.commit()
        return jsonify({"success": True}), 200

    if direction == 'up':
        swap_index = target_index - 1
    else:
        swap_index = target_index + 1

    if swap_index < 0 or swap_index >= len(machine_items):
        return jsonify({"success": True}), 200

    swap_item = machine_items[swap_index]
    item.position, swap_item.position = swap_item.position, item.position
    _cnc_reindex_machine(item.machine_number)
    db.session.commit()

    return jsonify({"success": True}), 200


@app.route('/api/cnc/queue/bulk_copy', methods=['POST'])
def api_cnc_bulk_copy_queue_items():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}
    item_ids = _coerce_positive_int_list(data.get('item_ids', []))
    machine_numbers = _coerce_positive_int_list(data.get('machine_numbers', []))

    machine_numbers = [m for m in machine_numbers if m in CNC_MACHINE_NUMBERS]
    if not item_ids:
        return jsonify({"success": False, "error": "No queue items selected."}), 400
    if not machine_numbers:
        return jsonify({"success": False, "error": "Select at least one CNC machine to copy to."}), 400

    selected_items = (
        CncQueueItem.query
        .filter(CncQueueItem.id.in_(item_ids), CncQueueItem.status == CNC_STATUS_QUEUED)
        .order_by(CncQueueItem.id.asc())
        .all()
    )
    if not selected_items:
        return jsonify({"success": False, "error": "Selected queue items not found."}), 404

    created_count = 0
    for machine_number in machine_numbers:
        next_position = (
            db.session.query(func.count(CncQueueItem.id))
            .filter_by(machine_number=machine_number, status=CNC_STATUS_QUEUED)
            .scalar() or 0
        )
        for selected_item in selected_items:
            next_position += 1
            db.session.add(CncQueueItem(
                job_id=selected_item.job_id,
                machine_number=machine_number,
                position=next_position,
                status=CNC_STATUS_QUEUED
            ))
            created_count += 1

    db.session.commit()
    return jsonify({"success": True, "created_items": created_count}), 200


@app.route('/api/cnc/queue/bulk_duplicate_same_queue', methods=['POST'])
def api_cnc_bulk_duplicate_same_queue_items():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}
    item_ids = _coerce_positive_int_list(data.get('item_ids', []))

    try:
        copies = int(data.get('copies', 1))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Copies must be a whole number."}), 400

    if copies < 1:
        return jsonify({"success": False, "error": "Copies must be at least 1."}), 400
    if copies > 100:
        return jsonify({"success": False, "error": "Copies per item cannot exceed 100."}), 400
    if not item_ids:
        return jsonify({"success": False, "error": "No queue items selected."}), 400

    selected_items = (
        CncQueueItem.query
        .filter(CncQueueItem.id.in_(item_ids), CncQueueItem.status == CNC_STATUS_QUEUED)
        .order_by(CncQueueItem.machine_number.asc(), CncQueueItem.position.asc(), CncQueueItem.id.asc())
        .all()
    )
    if not selected_items:
        return jsonify({"success": False, "error": "Selected queue items not found."}), 404

    created_count = 0
    selected_ids_by_machine = defaultdict(set)
    for selected_item in selected_items:
        selected_ids_by_machine[selected_item.machine_number].add(selected_item.id)

    for machine_number, selected_ids in selected_ids_by_machine.items():
        machine_items = (
            CncQueueItem.query
            .filter_by(machine_number=machine_number, status=CNC_STATUS_QUEUED)
            .order_by(CncQueueItem.position.asc(), CncQueueItem.id.asc())
            .all()
        )
        reordered_items = []
        for machine_item in machine_items:
            reordered_items.append(machine_item)
            if machine_item.id not in selected_ids:
                continue
            for _ in range(copies):
                duplicate_item = CncQueueItem(
                    job_id=machine_item.job_id,
                    machine_number=machine_number,
                    position=0,
                    status=CNC_STATUS_QUEUED
                )
                db.session.add(duplicate_item)
                reordered_items.append(duplicate_item)
                created_count += 1

        for position, queue_item in enumerate(reordered_items, start=1):
            queue_item.position = position

    db.session.commit()
    return jsonify({"success": True, "created_items": created_count}), 200


@app.route('/api/cnc/queue/bulk_remove', methods=['POST'])
def api_cnc_bulk_remove_queue_items():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}
    item_ids = _coerce_positive_int_list(data.get('item_ids', []))
    if not item_ids:
        return jsonify({"success": False, "error": "No queue items selected."}), 400

    selected_items = (
        CncQueueItem.query
        .filter(CncQueueItem.id.in_(item_ids), CncQueueItem.status == CNC_STATUS_QUEUED)
        .all()
    )
    if not selected_items:
        return jsonify({"success": False, "error": "Selected queue items not found."}), 404

    affected_machines = sorted({item.machine_number for item in selected_items})
    previous_counts = _cnc_capture_queue_counts(affected_machines)
    for item in selected_items:
        db.session.delete(item)

    for machine_number in affected_machines:
        _cnc_reindex_machine(machine_number)

    db.session.commit()
    if not _payload_bool(data.get('suppress_low_queue_notification')):
        _cnc_notify_low_queue_transitions(previous_counts)
    return jsonify({"success": True, "removed_items": len(selected_items)}), 200


@app.route('/api/cnc/queue/clear_all', methods=['POST'])
def api_cnc_clear_all_queues():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}

    queued_items = CncQueueItem.query.filter(CncQueueItem.status == CNC_STATUS_QUEUED).all()
    if not queued_items:
        return jsonify({"success": True, "removed_items": 0}), 200

    previous_counts = _cnc_capture_queue_counts()
    removed_items = len(queued_items)
    for item in queued_items:
        db.session.delete(item)

    db.session.commit()
    if not _payload_bool(data.get('suppress_low_queue_notification')):
        _cnc_notify_low_queue_transitions(previous_counts)
    return jsonify({"success": True, "removed_items": removed_items}), 200


@app.route('/api/cnc/queue/complete', methods=['POST'])
def api_cnc_complete_queue_item():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}

    try:
        item_id = int(data.get('item_id', 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid queue item."}), 400

    item = CncQueueItem.query.get(item_id)
    if not item or item.status != CNC_STATUS_QUEUED:
        return jsonify({"success": False, "error": "Queue item not found."}), 404

    machine_number = item.machine_number
    previous_counts = _cnc_capture_queue_counts([machine_number])
    try:
        wood_result = _record_cnc_job_wood_count(item.job)
    except ValueError as error:
        db.session.rollback()
        return jsonify({"success": False, "error": str(error)}), 400

    item.status = CNC_STATUS_COMPLETED
    item.completed_at = datetime.utcnow()
    item.completed_by = session.get('worker', 'Unknown')
    _cnc_reindex_machine(machine_number)
    db.session.commit()
    _remember_cnc_wood_log(item.id, wood_result)
    _cnc_notify_low_queue_transitions(previous_counts)

    return jsonify({
        "success": True,
        "item_id": item.id,
        "machine_number": machine_number,
        "job_name": item.job.name if item.job else "",
        "wood_counted": bool(wood_result.get("logged")),
        "wood_message": wood_result.get("message", ""),
    }), 200


@app.route('/api/cnc/queue/undo_complete', methods=['POST'])
def api_cnc_undo_complete_queue_item():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cnc_tables()
    data = request.get_json(silent=True) or {}

    try:
        item_id = int(data.get('item_id', 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid queue item."}), 400

    item = CncQueueItem.query.options(joinedload(CncQueueItem.job)).get(item_id)
    if not item or item.status != CNC_STATUS_COMPLETED:
        return jsonify({"success": False, "error": "Completed queue item not found."}), 404

    machine_number = item.machine_number
    try:
        wood_result = _reverse_remembered_cnc_wood_log(item.id)
    except ValueError as error:
        db.session.rollback()
        return jsonify({"success": False, "error": str(error)}), 400

    queued_items = (
        CncQueueItem.query
        .filter_by(machine_number=machine_number, status=CNC_STATUS_QUEUED)
        .order_by(CncQueueItem.position.asc(), CncQueueItem.id.asc())
        .all()
    )
    for queued_item in queued_items:
        queued_item.position += 1

    item.status = CNC_STATUS_QUEUED
    item.position = 1
    item.completed_at = None
    item.completed_by = None

    _cnc_reindex_machine(machine_number)
    db.session.commit()
    if wood_result.get("logged"):
        _forget_cnc_wood_log(item.id)

    return jsonify({
        "success": True,
        "item_id": item.id,
        "machine_number": machine_number,
        "job_name": item.job.name if item.job else "",
        "wood_counted": bool(wood_result.get("logged")),
        "wood_message": wood_result.get("message", ""),
    }), 200


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

    ensure_table_stock_log_table()
    worker_name = session.get('worker', 'Unknown')

    # Process POST submissions (stock adjustments)
    if request.method == 'POST':
        stock_type = request.form.get('stock_type')
        action = request.form.get('action')
        current_section = request.form.get('current_section', 'bodies-content')  # Get current section
        
        try:
            amount = int(request.form.get('amount', 0))
        except ValueError:
            flash("Invalid amount entered.", "error")
            return redirect(url_for('table_stock'))
        if amount <= 0:
            flash("Amount must be a positive number.", "error")
            return redirect(url_for('table_stock'))

        stock_entry = TableStock.query.filter_by(type=stock_type).first()

        if not stock_entry:
            if action == 'remove':
                flash(f"No stock entry found for '{stock_type}'. Cannot remove stock.", "error")
                return redirect(url_for('table_stock'))
            else:
                stock_entry = TableStock(type=stock_type, count=0)
                db.session.add(stock_entry)
                db.session.commit()
                flash(f"Created new stock entry for '{stock_type}'.", "info")

        old_count = stock_entry.count
        if action == 'add':
            stock_entry.count += amount
            record_table_stock_log(
                stock_type,
                "add",
                worker_name,
                amount,
                old_count,
                stock_entry.count,
                "Manual table stock adjustment"
            )
            db.session.commit()
            flash(f"Added {amount} to {stock_type} stock. New count: {stock_entry.count}", "success")
        elif action == 'remove':
            if stock_entry.count < amount:
                flash(f"Not enough stock to remove. Current count for '{stock_type}': {stock_entry.count}", "error")
            else:
                stock_entry.count -= amount
                record_table_stock_log(
                    stock_type,
                    "remove",
                    worker_name,
                    -amount,
                    old_count,
                    stock_entry.count,
                    "Manual table stock adjustment"
                )
                db.session.commit()
                flash(f"Removed {amount} from {stock_type} stock. New count: {stock_entry.count}", "success")
        elif action == 'set':
            # Additional action to directly set the stock count
            stock_entry.count = amount
            record_table_stock_log(
                stock_type,
                "set",
                worker_name,
                stock_entry.count - old_count,
                old_count,
                stock_entry.count,
                "Manual table stock correction"
            )
            db.session.commit()
            flash(f"Set {stock_type} stock to {amount}", "success")

        return redirect(url_for('table_stock'))

    # For GET requests, set up stock display and cost calculations.
    # Define the dimensions (sizes and colors)
    sizes = ['7ft', '6ft']
    colors = ['Black', 'Rustic Oak', 'Grey Oak', 'Stone', 'Rustic Black']

    # Initialize dictionaries for each stock category
    table_data = {}
    lite_body_data = {}
    top_rail_data = {}
    cushion_data = {}
    other_data = {}

    # Get all stock entries
    all_stock = TableStock.query.all()
    
    # Process each stock entry into appropriate category
    legacy_keys = {"body_6ft", "body_7ft", "top_rail_6ft", "top_rail_7ft"}
    lite_body_keys = {"body_7ft_lite", "body_6ft_lite"}

    for entry in all_stock:
        stock_type = entry.type
        if stock_type in legacy_keys:
            # Ignore legacy aggregate keys so totals match the per-color rows shown in the UI
            continue
        if stock_type.endswith("_remainder"):
            continue
        
        # Handle body stock
        if stock_type in lite_body_keys:
            lite_body_data[stock_type] = entry.count
        elif stock_type.startswith('body_'):
            table_data[stock_type] = entry.count
        
        # Handle top rail stock
        elif stock_type.startswith('top_rail_'):
            top_rail_data[stock_type] = entry.count
        
        # Handle cushion sets
        elif stock_type.startswith('cushion_set_'):
            cushion_data[stock_type] = entry.count
        
        # Other stock items
        else:
            other_data[stock_type] = entry.count

    # Pre-calculate totals for the section headers
    total_champion_bodies = sum(value for key, value in table_data.items() if key.startswith('body_'))
    total_lite_bodies = sum(value for key, value in lite_body_data.items() if key in lite_body_keys)
    total_bodies = total_champion_bodies + total_lite_bodies
    total_rails = sum(
        top_rail_data.get(f"top_rail_{size.lower()}_{color.lower().replace(' ', '_')}", 0)
        for size in sizes
        for color in colors
    )
    total_cushions = sum(value for key, value in cushion_data.items() if key.startswith('cushion_set_'))

    recent_stock_logs = []
    for entry in (
        TableStockLog.query
        .order_by(TableStockLog.created_at.desc(), TableStockLog.id.desc())
        .limit(100)
        .all()
    ):
        recent_stock_logs.append({
            "created_at": entry.created_at,
            "worker": entry.worker,
            "action_label": table_stock_action_label(entry.action_type),
            "stock_label": table_stock_type_label(entry.stock_type),
            "stock_type": entry.stock_type,
            "delta": entry.delta,
            "count_before": entry.count_before,
            "count_after": entry.count_after,
            "note": entry.note,
        })

    # Calculate costs for stock value panel
    stock_costs = {size: {} for size in sizes}
    stock_costs_raw = {size: {} for size in sizes}  # Store raw numeric values
    stock_costs_lite = {}
    grand_total = 0
    
    # Calculate body costs by size and color
    for size in sizes:
        for color in colors:
            color_key = color.lower().replace(' ', '_')
            stock_key = f'body_{size.lower()}_{color_key}'
            count = table_data.get(stock_key, 0)
            
            # Black bodies cost £993.60 (incl. VAT); colored bodies cost £1089.60
            unit_cost = 993.6 if color.lower() in ['black'] else 1089.6
            item_cost = count * unit_cost
            
            # Store both formatted and raw values
            stock_costs[size][color] = f"£{item_cost:,.2f}"
            stock_costs_raw[size][color] = item_cost
            
            grand_total += item_cost

        lite_key = f'body_{size.lower()}_lite'
        lite_count = lite_body_data.get(lite_key, 0)
        lite_unit_cost = 993.6
        lite_cost = lite_count * lite_unit_cost
        stock_costs_lite[size] = {
            "count": lite_count,
            "cost": f"£{lite_cost:,.2f}",
            "raw_cost": lite_cost
        }
        grand_total += lite_cost

    # Format the grand total
    formatted_grand_total = f"£{grand_total:,.2f} (incl. VAT)"

    return render_template(
        'table_stock.html',
        sizes=sizes,
        colors=colors,
        table_data=table_data,
        lite_body_data=lite_body_data,
        top_rail_data=top_rail_data,
        cushion_data=cushion_data,
        other_data=other_data,
        stock_costs=stock_costs,
        stock_costs_raw=stock_costs_raw,
        stock_costs_lite=stock_costs_lite,
        grand_total=formatted_grand_total,
        total_bodies=total_bodies,
        total_lite_bodies=total_lite_bodies,
        total_rails=total_rails,
        total_cushions=total_cushions,
        recent_stock_logs=recent_stock_logs
    )


@app.route('/admin/table_stock_export.csv')
def table_stock_export_csv():
    color_codes = {
        "Black": "B",
        "Rustic Oak": "RO",
        "Grey Oak": "GO",
        "Rustic Black": "RB",
        "Stone": "S",
    }

    rows = [("Type", "Size", "Color", "Code", "Count")]
    # Export in planning-friendly format
    rows = []
    rows.append(("Top Rails", "Needed", "Have", "To Make"))
    rail_order = [
        ("6ft", "Black"), ("7ft", "Black"),
        ("6ft", "Stone"), ("7ft", "Stone"),
        ("6ft", "Rustic Oak"), ("7ft", "Rustic Oak"),
        ("6ft", "Grey Oak"), ("7ft", "Grey Oak"),
        ("6ft", "Rustic Black"), ("7ft", "Rustic Black"),
    ]
    for size, color in rail_order:
        cfg = next((c for c in TOP_RAIL_TABLE_STOCK_CONFIGS if c["size"] == size and c["color"] == color), None)
        rail_count = _table_stock_count(cfg["rail_key"]) if cfg else 0
        rows.append((f"{size} - {color}", "", rail_count, ""))

    rows.append(("", "", "", ""))
    rows.append(("Bodies", "Needed", "Have", "To Make"))
    for size, color in rail_order:
        cfg = next((c for c in TOP_RAIL_TABLE_STOCK_CONFIGS if c["size"] == size and c["color"] == color), None)
        body_count = _table_stock_count(cfg["body_key"]) if cfg else 0
        rows.append((f"{size} - {color}", "", body_count, ""))
    for size in ["6ft", "7ft"]:
        lite_key = f"body_{size.lower()}_lite"
        lite_count = _table_stock_count(lite_key)
        rows.append((f"{size} - Lite", "", lite_count, ""))

    output = StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow(row)

    resp = make_response(output.getvalue())
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=table_stock_export.csv"
    return resp


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

    parts = [
        "Large Ramp", "Paddle", *LAMINATE_PART_NAMES, "Spring Mount", "Spring Holder",
        "Small Ramp", "Cue Ball Separator", "Bushing",
        "6ft Cue Ball Separator", "6ft Large Ramp",
        "6ft Carpet", "7ft Carpet", FELT_PART_NAME
    ]

    def latest_count(part_name):
        """Return the latest recorded inventory count for a part (not the sum of all historical rows)."""
        if part_name == FELT_PART_NAME:
            return get_felt_count()
        latest_entry = (PrintedPartsCount.query
                        .filter_by(part_name=part_name)
                        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                        .first())
        return latest_entry.count if latest_entry else 0

    selected_part = request.form.get('part') or request.args.get('selected') or (parts[0] if parts else None)

    if request.method == 'POST':
        part = request.form.get('part') or selected_part
        action = request.form.get('action')

        if not part or part not in parts:
            flash("Invalid part selected.", "error")
            return redirect(url_for('counting_3d_printing_parts'))

        try:
            if action == 'quick_add':
                amount_str = request.form.get('quick_amount', '1')
            elif action == 'reject':
                amount_str = request.form.get('reject_amount', '1')
            elif action == 'bulk':
                amount_str = request.form.get('amount', '').strip()
                if not amount_str:
                    flash("Please enter a bulk amount to adjust stock.", "error")
                    return redirect(url_for('counting_3d_printing_parts', selected=part))
            else:
                amount_str = request.form.get('increment_amount', '1')

            amount = int(amount_str)
        except ValueError:
            flash("Amount must be a number.", "error")
            return redirect(url_for('counting_3d_printing_parts', selected=part))

        current_count = latest_count(part)
        new_count = current_count

        if action == 'reject':
            if current_count < amount:
                flash(f"Not enough inventory to reject {amount} of {part}.", "error")
                return redirect(url_for('counting_3d_printing_parts', selected=part))
            new_count -= amount
            flash(f"Rejected {amount} of {part} from inventory.", "success")
        elif action == 'bulk':
            if amount < 0 and current_count < abs(amount):
                flash(f"Not enough inventory to remove. Current count for '{part}': {current_count}", "error")
                return redirect(url_for('counting_3d_printing_parts', selected=part))
            new_count += amount
            flash(f"{part} adjusted by {amount}. New count: {new_count}", "success")
        else:
            new_count += amount
            flash(f"Added {amount} to {part}. New count: {new_count}", "success")

        if new_count < current_count:
            check_and_notify_low_stock(part, current_count, new_count)

        new_entry = PrintedPartsCount(
            part_name=part,
            count=new_count,
            date=datetime.utcnow().date(),
            time=datetime.utcnow().time()
        )
        db.session.add(new_entry)
        db.session.commit()

        return redirect(url_for('counting_3d_printing_parts', selected=part))

    parts_counts = {part: latest_count(part) for part in parts}

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
        return serial_is_6ft(serial)

    bodies_built_6ft = sum(1 for table in all_bodies_this_month if is_6ft(table.serial_number))
    bodies_built_7ft = sum(1 for table in all_bodies_this_month if not is_6ft(table.serial_number))

    # Define usage per table for each part.
    parts_usage_per_body = {
        "Large Ramp": 1,
        "Paddle": 1,
        **{name: 4 for name in LAMINATE_PART_NAMES},
        "Spring Mount": 1,
        "Spring Holder": 1,
        "Small Ramp": 1,
        "Cue Ball Separator": 1,
        "Bushing": 2,
        "6ft Cue Ball Separator": 1,
        "6ft Large Ramp": 1,
        "6ft Carpet": 1,
        FELT_PART_NAME: 2,
        "7ft Carpet": 1
    }

    # FIXED: Calculate parts status more accurately
    parts_status = {}
    for part, usage in parts_usage_per_body.items():
        # Determine required quantities based on table size
        if part in ["Large Ramp", "Cue Ball Separator", "7ft Carpet"]:  # Added 7ft items
            total_required = target_7ft * usage
            already_used = bodies_built_7ft * usage
            still_needed = (target_7ft - bodies_built_7ft) * usage
        elif part in ["6ft Large Ramp", "6ft Cue Ball Separator", "6ft Carpet"]:  # Added 6ft items
            total_required = target_6ft * usage
            already_used = bodies_built_6ft * usage
            still_needed = (target_6ft - bodies_built_6ft) * usage
        else:
            total_required = (target_7ft + target_6ft) * usage
            already_used = (bodies_built_7ft + bodies_built_6ft) * usage
            still_needed = ((target_7ft + target_6ft) - (bodies_built_7ft + bodies_built_6ft)) * usage

        # Current inventory count
        still_needed = total_required - already_used
        current_inventory = inventory_counts.get(part, 0)
        surplus = current_inventory - still_needed

        if surplus < 0:
            parts_status[part] = f"{-surplus} left to make"
        else:
            parts_status[part] = f"{surplus} extras"

    carpet_felt_parts = ["6ft Carpet", "7ft Carpet", FELT_PART_NAME]
    laminate_parts = list(LAMINATE_PART_NAMES)
    printed_parts = [
        part for part in parts
        if part not in carpet_felt_parts and part not in laminate_parts
    ]

    parts_log = []
    if parts:
        recent_entries = (
            PrintedPartsCount.query
            .filter(PrintedPartsCount.part_name.in_(parts))
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .limit(50)
            .all()
        )

        for entry in recent_entries:
            previous = (
                PrintedPartsCount.query
                .filter(PrintedPartsCount.part_name == entry.part_name)
                .filter(or_(
                    PrintedPartsCount.date < entry.date,
                    and_(PrintedPartsCount.date == entry.date, PrintedPartsCount.time < entry.time),
                ))
                .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
                .first()
            )
            previous_count = previous.count if previous else 0
            delta = (entry.count or 0) - (previous_count or 0)
            parts_log.append({
                "part_name": entry.part_name,
                "date": entry.date,
                "time": entry.time,
                "new_count": entry.count,
                "delta": delta,
            })

    return render_template(
        'counting_3d_printing_parts.html',
        parts_counts=parts_counts,
        parts_status=parts_status,
        selected_part=selected_part,
        parts_log=parts_log,
        printed_parts=printed_parts,
        carpet_felt_parts=carpet_felt_parts,
        laminate_parts=laminate_parts
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

    ensure_cushion_workflow_tables()
    ensure_cushion_consumables()
    worker_name = session['worker']

    if request.method == 'POST':
        action = request.form.get('action')
        if handle_cushion_compressor_action(action, worker_name):
            return redirect(url_for('counting_cushions'))
        try:
            if action == "add":
                stage_key = request.form.get('stage_key', '')
                size_label = request.form.get('size_label', '')
                shape_no = request.form.get('shape_no', 0)
                end_type = request.form.get('end_type', '')
                quantity = parse_cushion_add_quantity(
                    request.form.get('quantity', '1'),
                    request.form.get('manual_quantity'),
                    stage_key=stage_key
                )
                target_record, completed_sets = record_cushion_stage_add_many(
                    stage_key,
                    size_label,
                    shape_no,
                    end_type,
                    quantity,
                    worker_name
                )
                db.session.commit()

                if completed_sets:
                    flash(f"Added {len(completed_sets)} completed {target_record.size_label} cushion set(s) to stock.", "success")
            elif action == "set_count":
                stage_key = request.form.get('stage_key', '')
                size_label = request.form.get('size_label', '')
                shape_no = request.form.get('shape_no', 0)
                end_type = request.form.get('end_type', '')
                new_count = request.form.get('new_count', 0)
                record = set_cushion_stage_count(
                    stage_key,
                    size_label,
                    shape_no,
                    end_type,
                    new_count,
                    worker_name
                )
                db.session.commit()
                flash(
                    f"Set {cushion_variant_display(stage_key, record.size_label, record.shape_no, record.end_type)} to {record.count}.",
                    "success"
                )
            elif action == "reconcile_bundles":
                size_label = request.form.get('size_label', '')
                if size_label not in CUSHION_SIZES:
                    raise ValueError("Choose a valid cushion size.")
                completed_sets = complete_available_cushion_sets(size_label, worker_name)
                db.session.commit()
                if completed_sets:
                    flash(f"Added {len(completed_sets)} completed {size_label} cushion set(s) to stock.", "success")
                else:
                    flash(f"No complete {size_label} bundle sets are ready yet.", "info")
            elif action == "consumable_adjust":
                part_name = request.form.get('part_name', '')
                delta = parse_consumable_delta(
                    request.form.get('delta'),
                    request.form.get('manual_delta')
                )
                allowed_names = {item["name"].lower() for item in cushion_consumables_for_stage('')}
                if part_name.lower() not in allowed_names:
                    raise ValueError("That consumable is not available on this page.")
                adjust_consumable_stock(part_name, delta)
                db.session.commit()
            else:
                flash("Unknown cushion action.", "error")
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
        except Exception:
            db.session.rollback()
            raise

        return redirect(url_for('counting_cushions'))

    active_batch = get_active_cushion_batch()
    current_stage_key = cushion_current_stage_key(active_batch.batch_number if active_batch else None)
    stage_context = build_cushion_stage_context(highlight_stage_key=current_stage_key)
    stock_summary = cushion_stock_summary()

    return render_template(
        'counting_cushions.html',
        stages=stage_context,
        sizes=CUSHION_SIZES,
        shapes=CUSHION_SHAPES,
        stock_summary=stock_summary,
        completed_size_stats=cushion_completed_size_stats(),
        previous_month_size_stats=cushion_completed_previous_month_stats(),
        compressor_context=cushion_compressor_context(worker_name),
        admin_url=url_for('cushion_production_admin')
    )


@app.route('/counting_cushions/stage/<stage_key>', methods=['GET', 'POST'])
def counting_cushion_stage(stage_key):
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    ensure_cushion_workflow_tables()
    ensure_cushion_consumables()
    stage = CUSHION_STAGE_BY_KEY.get(stage_key)
    if not stage:
        flash("Unknown cushion stage.", "error")
        return redirect(url_for('counting_cushions'))

    worker_name = session['worker']
    selected_batch = (request.args.get('batch') or 'current').strip().lower()
    active_batch = get_active_cushion_batch()
    if selected_batch == 'current':
        timing_batch_number = active_batch.batch_number if active_batch else None
    elif selected_batch == 'all':
        timing_batch_number = None
    else:
        selected_batch_record = get_cushion_batch_by_number(selected_batch)
        timing_batch_number = selected_batch_record.batch_number if selected_batch_record else None

    if request.method == 'POST':
        action = request.form.get('action')
        if handle_cushion_compressor_action(action, worker_name):
            return redirect(url_for('counting_cushion_stage', stage_key=stage_key, batch=selected_batch))
        size_label = request.form.get('size_label', '')
        shape_no = request.form.get('shape_no', 0)
        end_type = request.form.get('end_type', '')
        spindle_reminder_message = None
        try:
            if action == "start_batch":
                if stage_key != "cut_1m":
                    raise ValueError("Batches can only be started from the Cut into 1m Lengths stage.")
                new_batch = start_new_cushion_batch(worker_name)
                db.session.commit()
                flash(f"Started cushion batch #{new_batch.batch_number} ({new_batch.batch_date.strftime('%d/%m/%Y')}).", "success")
                return redirect(url_for('counting_cushion_stage', stage_key=stage_key, batch='current'))
            if action == "add":
                quantity = parse_cushion_add_quantity(
                    request.form.get('quantity', '1'),
                    request.form.get('manual_quantity'),
                    stage_key=stage_key
                )
                target_record, completed_sets = record_cushion_stage_add_many(
                    stage_key,
                    size_label,
                    shape_no,
                    end_type,
                    quantity,
                    worker_name
                )
                save_cushion_stage_lock(worker_name, stage_key, size_label, shape_no, end_type)
                if stage_key == "spindle_mould":
                    active_batch_after_add = get_active_cushion_batch()
                    if active_batch_after_add:
                        spindle_total = cushion_spindle_batch_added_total(active_batch_after_add.batch_number)
                        spindle_checkpoint = cushion_spindle_reminder_checkpoint(spindle_total, quantity)
                        if spindle_checkpoint:
                            spindle_reminder_message = (
                                "Please check the rubber length still fit flush. "
                                f"Batch #{active_batch_after_add.batch_number} has now reached {spindle_checkpoint} spindle lengths."
                            )
                db.session.commit()
                if completed_sets:
                    flash(f"Added {len(completed_sets)} completed {target_record.size_label} cushion set(s) to stock.", "success")
                if spindle_reminder_message:
                    flash(spindle_reminder_message, "inspection-warning")
            elif action == "set_count":
                new_count = request.form.get('new_count', 0)
                record = set_cushion_stage_count(
                    stage_key,
                    size_label,
                    shape_no,
                    end_type,
                    new_count,
                    worker_name
                )
                save_cushion_stage_lock(worker_name, stage_key, size_label, shape_no, end_type)
                db.session.commit()
                flash(
                    f"Set {cushion_variant_display(stage_key, record.size_label, record.shape_no, record.end_type)} to {record.count}.",
                    "success"
                )
            elif action == "consumable_adjust":
                part_name = request.form.get('part_name', '')
                delta = parse_consumable_delta(
                    request.form.get('delta'),
                    request.form.get('manual_delta')
                )
                allowed_names = {item["name"].lower() for item in cushion_consumables_for_stage(stage_key)}
                if part_name.lower() not in allowed_names:
                    raise ValueError("That consumable is not available on this page.")
                adjust_consumable_stock(part_name, delta)
                db.session.commit()
            else:
                flash("Unknown cushion action.", "error")
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
        except Exception:
            db.session.rollback()
            raise

        return redirect(url_for('counting_cushion_stage', stage_key=stage_key, batch=selected_batch))

    stage_context = next(
        item for item in build_cushion_stage_context(include_timing=True, batch_number=timing_batch_number)
        if item["key"] == stage_key
    )
    variants = flatten_cushion_stage_variants(stage_context)
    stage_timing = cushion_stage_timing(stage_key, batch_number=timing_batch_number)
    for variant in variants:
        timing = variant.get("timing") or {}
        variant["display_timing"] = {
            "average_display": (
                timing.get("average_display")
                if timing.get("average_seconds") is not None
                else stage_timing["average_display"]
            ),
            "last_display": (
                timing.get("last_display")
                if timing.get("last_seconds") is not None
                else stage_timing["last_display"]
            ),
        }
    stage_keys = [stage_item["key"] for stage_item in CUSHION_WORKFLOW_STAGES]
    stage_index = stage_keys.index(stage_key)
    previous_stage = CUSHION_WORKFLOW_STAGES[stage_index - 1] if stage_index > 0 else None
    next_stage = CUSHION_WORKFLOW_STAGES[stage_index + 1] if stage_index < len(CUSHION_WORKFLOW_STAGES) - 1 else None
    recent_batches = (
        CushionBatch.query
        .order_by(CushionBatch.batch_number.desc())
        .limit(20)
        .all()
    )

    return render_template(
        'counting_cushion_stage.html',
        stage=stage_context,
        variants=variants,
        variant_map={variant["variant_key"]: variant for variant in variants},
        sizes=CUSHION_SIZES,
        shapes=CUSHION_SHAPES,
        end_types=CUSHION_END_TYPES,
        overview_url=url_for('counting_cushions'),
        admin_url=url_for('cushion_production_admin'),
        previous_stage=previous_stage,
        next_stage=next_stage,
        stage_timing=stage_timing,
        compressor_context=cushion_compressor_context(worker_name),
        stage_lock=get_cushion_stage_lock(worker_name, stage_key),
        consumables=cushion_consumables_for_stage(stage_key),
        active_batch=active_batch,
        selected_batch=selected_batch,
        timing_batch_number=timing_batch_number,
        recent_batches=recent_batches
    )


@app.route('/api/cushion_stage_lock', methods=['POST'])
def api_cushion_stage_lock():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cushion_workflow_tables()
    data = request.get_json(silent=True) or {}
    stage_key = data.get('stage_key', '')
    if stage_key not in CUSHION_STAGE_BY_KEY:
        return jsonify({"success": False, "error": "Unknown cushion stage."}), 400

    try:
        record = save_cushion_stage_lock(
            session['worker'],
            stage_key,
            data.get('size_label', ''),
            data.get('shape_no', 0),
            data.get('end_type', ''),
            commit=True
        )
    except ValueError as error:
        db.session.rollback()
        return jsonify({"success": False, "error": str(error)}), 400

    return jsonify({
        "success": True,
        "selection": cushion_stage_lock_payload(record)
    }), 200


@app.route('/api/cushion_stage_lock/clear', methods=['POST'])
def api_clear_cushion_stage_lock():
    if 'worker' not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    ensure_cushion_workflow_tables()
    data = request.get_json(silent=True) or {}
    stage_key = data.get('stage_key', '')
    if stage_key not in CUSHION_STAGE_BY_KEY:
        return jsonify({"success": False, "error": "Unknown cushion stage."}), 400

    clear_cushion_stage_lock(session['worker'], stage_key, commit=True)
    return jsonify({"success": True}), 200


@app.route('/cushion_production_admin', methods=['GET', 'POST'])
def cushion_production_admin():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    ensure_cushion_workflow_tables()
    ensure_cushion_consumables()
    worker_name = session['worker']
    selected_batch = (request.args.get('batch') or 'current').strip().lower()
    active_batch = get_active_cushion_batch()
    if selected_batch == 'current':
        timing_batch_number = active_batch.batch_number if active_batch else None
    elif selected_batch == 'all':
        timing_batch_number = None
    else:
        selected_batch_record = get_cushion_batch_by_number(selected_batch)
        timing_batch_number = selected_batch_record.batch_number if selected_batch_record else None

    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == "update_batch":
                batch_number = request.form.get('batch_number', '')
                batch = get_cushion_batch_by_number(batch_number)
                if not batch:
                    raise ValueError("Batch not found.")

                batch_name = (request.form.get('batch_name') or '').strip()
                batch_date_raw = (request.form.get('batch_date') or '').strip()
                if not batch_date_raw:
                    raise ValueError("Batch start date is required.")
                try:
                    new_batch_date = datetime.strptime(batch_date_raw, "%Y-%m-%d").date()
                except ValueError:
                    raise ValueError("Batch start date must use YYYY-MM-DD format.")

                batch.batch_name = batch_name or f"Batch #{batch.batch_number}"
                batch.batch_date = new_batch_date

                CushionWorkflowLog.query.filter_by(batch_number=batch.batch_number).update(
                    {CushionWorkflowLog.batch_date: new_batch_date},
                    synchronize_session=False
                )

                db.session.commit()
                flash(f"Updated {cushion_batch_display_name(batch)}.", "success")
            elif action == "delete_batch":
                batch_number = request.form.get('batch_number', '')
                batch = get_cushion_batch_by_number(batch_number)
                if not batch:
                    raise ValueError("Batch not found.")

                batch_label = cushion_batch_display_name(batch)
                was_active = bool(batch.active)
                linked_logs = CushionWorkflowLog.query.filter_by(batch_number=batch.batch_number).count()

                CushionWorkflowLog.query.filter_by(batch_number=batch.batch_number).update(
                    {
                        CushionWorkflowLog.batch_number: None,
                        CushionWorkflowLog.batch_date: None,
                    },
                    synchronize_session=False
                )

                db.session.delete(batch)
                db.session.flush()

                if was_active:
                    replacement_batch = (
                        CushionBatch.query
                        .order_by(CushionBatch.batch_number.desc(), CushionBatch.id.desc())
                        .first()
                    )
                    if replacement_batch:
                        replacement_batch.active = True

                db.session.commit()
                flash(
                    f"Deleted {batch_label}. Unlinked {linked_logs} log entr{'y' if linked_logs == 1 else 'ies'} from that batch.",
                    "success"
                )

                if selected_batch == str(batch_number):
                    selected_batch = 'current'
            elif action == "set_count":
                stage_key = request.form.get('stage_key', '')
                size_label = request.form.get('size_label', '')
                shape_no = request.form.get('shape_no', 0)
                end_type = request.form.get('end_type', '')
                new_count = request.form.get('new_count', 0)
                record = set_cushion_stage_count(stage_key, size_label, shape_no, end_type, new_count, worker_name)
                db.session.commit()
                flash(
                    f"Set {cushion_variant_display(stage_key, record.size_label, record.shape_no, record.end_type)} to {record.count}.",
                    "success"
                )
            elif action == "bulk_set_counts":
                stage_keys = request.form.getlist('stage_key')
                size_labels = request.form.getlist('size_label')
                shape_nos = request.form.getlist('shape_no')
                end_types = request.form.getlist('end_type')
                new_counts = request.form.getlist('new_count')
                row_count = len(stage_keys)
                if not (
                    row_count
                    and len(size_labels) == row_count
                    and len(shape_nos) == row_count
                    and len(end_types) == row_count
                    and len(new_counts) == row_count
                ):
                    raise ValueError("Count update rows were incomplete. Please try again.")

                changed_count = 0
                for index in range(row_count):
                    stage_key = stage_keys[index]
                    size_label = size_labels[index]
                    shape_no = shape_nos[index]
                    end_type = end_types[index]
                    try:
                        new_count = int(new_counts[index])
                    except (TypeError, ValueError):
                        label = cushion_variant_display(stage_key, size_label, shape_no, end_type)
                        raise ValueError(f"Enter a valid count for {label}.")
                    if new_count < 0:
                        label = cushion_variant_display(stage_key, size_label, shape_no, end_type)
                        raise ValueError(f"Count cannot be negative for {label}.")

                    current_count = cushion_current_count_for_variant(stage_key, size_label, shape_no, end_type)
                    if new_count == current_count:
                        continue

                    set_cushion_stage_count(stage_key, size_label, shape_no, end_type, new_count, worker_name)
                    changed_count += 1

                db.session.commit()
                if changed_count:
                    flash(f"Updated {changed_count} cushion count(s).", "success")
                else:
                    flash("No count changes to save.", "info")
            elif action == "bulk_update_consumables":
                part_names = request.form.getlist('part_name')
                stock_counts = request.form.getlist('stock_count')
                stock_adjustments = request.form.getlist('stock_adjustment')
                row_count = len(part_names)
                if not (
                    row_count
                    and len(stock_counts) == row_count
                    and len(stock_adjustments) == row_count
                ):
                    raise ValueError("Consumable update rows were incomplete. Please try again.")

                changed_count = 0
                for index in range(row_count):
                    part_name = part_names[index]
                    try:
                        target_count = int(stock_counts[index])
                    except (TypeError, ValueError):
                        raise ValueError("Enter valid whole-number consumable stock counts.")
                    if target_count < 0:
                        raise ValueError("Consumable stock cannot be negative.")

                    adjustment_text = (stock_adjustments[index] or "").strip()
                    if adjustment_text:
                        try:
                            target_count += int(adjustment_text)
                        except (TypeError, ValueError):
                            raise ValueError("Consumable adjustments must be whole numbers.")
                    if target_count < 0:
                        raise ValueError("Consumable stock cannot be negative after adjustment.")

                    current_count, _ = consumable_stock_state(part_name)
                    if target_count == current_count:
                        continue

                    set_consumable_stock(part_name, target_count)
                    changed_count += 1

                db.session.commit()
                if changed_count:
                    flash(f"Updated {changed_count} consumable stock count(s).", "success")
                else:
                    flash("No consumable changes to save.", "info")
            elif action == "reconcile_bundles":
                size_label = request.form.get('size_label', '')
                if size_label not in CUSHION_SIZES:
                    raise ValueError("Choose a valid cushion size.")
                completed_sets = complete_available_cushion_sets(size_label, worker_name)
                db.session.commit()
                if completed_sets:
                    flash(f"Added {len(completed_sets)} completed {size_label} cushion set(s) to stock.", "success")
                else:
                    flash(f"No complete {size_label} bundle sets are ready yet.", "info")
            else:
                flash("Unknown cushion admin action.", "error")
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
        except Exception:
            db.session.rollback()
            raise

        return redirect(url_for('cushion_production_admin', batch=selected_batch))

    stage_context = build_cushion_stage_context(include_timing=True, batch_number=timing_batch_number)
    timing_summary = cushion_timing_summary(batch_number=timing_batch_number)
    completed_sets = (
        CushionCompletedSet.query
        .order_by(CushionCompletedSet.completed_at.desc(), CushionCompletedSet.id.desc())
        .limit(50)
        .all()
    )
    recent_logs = (
        cushion_timing_batch_filter(CushionWorkflowLog.query, timing_batch_number)
        .order_by(CushionWorkflowLog.created_at.desc(), CushionWorkflowLog.id.desc())
        .limit(100)
        .all()
    )
    recent_batches = (
        CushionBatch.query
        .order_by(CushionBatch.batch_number.desc())
        .limit(20)
        .all()
    )
    batch_admin_rows = CushionBatch.query.order_by(CushionBatch.batch_number.desc(), CushionBatch.id.desc()).all()

    return render_template(
        'cushion_production_admin.html',
        stages=stage_context,
        timing_summary=timing_summary,
        stock_summary=cushion_stock_summary(),
        consumables=cushion_all_consumables(),
        completed_sets=completed_sets,
        recent_logs=recent_logs,
        compressor_checks=cushion_compressor_recent_checks(),
        total_wip=sum(stage["total"] for stage in stage_context),
        estimated_set_times={
            size_label: cushion_format_duration(cushion_estimated_set_seconds(size_label))
            for size_label in CUSHION_SIZES
        },
        counting_url=url_for('counting_cushions'),
        history_url=url_for('cushion_history'),
        active_batch=active_batch,
        selected_batch=selected_batch,
        timing_batch_number=timing_batch_number,
        recent_batches=recent_batches,
        batch_admin_rows=batch_admin_rows,
        cushion_batch_display_name=cushion_batch_display_name,
    )


@app.route('/cushion_history')
def cushion_history():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    ensure_cushion_workflow_tables()
    ensure_cushion_consumables()

    filters = cushion_history_filter_state(request.args)
    log_query = cushion_history_log_query(filters)
    total_actions = log_query.count()
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(max(per_page, 25), 200)
    total_pages = max(1, int(ceil(total_actions / per_page))) if total_actions else 1
    page = request.args.get('page', 1, type=int)
    page = min(max(page, 1), total_pages)

    action_logs = (
        log_query
        .order_by(CushionWorkflowLog.created_at.desc(), CushionWorkflowLog.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    completed_sets = (
        cushion_history_completed_query(filters)
        .order_by(CushionCompletedSet.completed_at.desc(), CushionCompletedSet.id.desc())
        .all()
    )
    consumable_entries = (
        cushion_history_consumable_query(filters)
        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc(), PrintedPartsCount.id.desc())
        .limit(200)
        .all()
    )

    query_args = cushion_history_clean_query_args(request.args.to_dict(flat=True))
    prev_url = None
    next_url = None
    if page > 1:
        prev_url = url_for('cushion_history', **{**query_args, "page": page - 1})
    if page < total_pages:
        next_url = url_for('cushion_history', **{**query_args, "page": page + 1})

    return render_template(
        'cushion_history.html',
        filters=filters,
        filter_options=cushion_history_filter_options(),
        summary=cushion_history_summary(filters),
        stage_summary=cushion_history_stage_summary(filters),
        action_logs=action_logs,
        completed_sets=completed_sets,
        consumable_entries=consumable_entries,
        pagination={
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_actions": total_actions,
            "first_item": ((page - 1) * per_page + 1) if total_actions else 0,
            "last_item": min(page * per_page, total_actions),
            "prev_url": prev_url,
            "next_url": next_url,
        },
        export_url=url_for('cushion_history_export_csv', **query_args),
        admin_url=url_for('cushion_production_admin'),
        counting_url=url_for('counting_cushions')
    )


@app.route('/cushion_history/export.csv')
def cushion_history_export_csv():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    ensure_cushion_workflow_tables()
    ensure_cushion_consumables()

    filters = cushion_history_filter_state(request.args)
    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(["Cushion History Export"])
    writer.writerow(["Period", filters["period"]])
    writer.writerow(["Start Date", filters["start_date_value"]])
    writer.writerow(["End Date", filters["end_date_value"]])
    writer.writerow(["Worker", filters["worker"]])
    writer.writerow(["Stage", filters["stage_key"]])
    writer.writerow(["Size", filters["size_label"]])
    writer.writerow(["Shape", filters["shape_value"]])
    writer.writerow(["End Type", filters["end_type"]])
    writer.writerow(["Action", filters["action_type"]])
    writer.writerow([])

    writer.writerow(["Action Log"])
    writer.writerow([
        "When", "Worker", "Stage", "Size", "Shape", "End Type",
        "Action", "Delta", "Count After", "Seconds Taken", "Note"
    ])
    for entry in (
        cushion_history_log_query(filters)
        .order_by(CushionWorkflowLog.created_at.desc(), CushionWorkflowLog.id.desc())
        .all()
    ):
        writer.writerow([
            entry.created_at.strftime("%Y-%m-%d %H:%M:%S") if entry.created_at else "",
            entry.worker,
            entry.stage_label,
            entry.size_label,
            entry.shape_no or "",
            entry.end_type,
            entry.action_type,
            entry.delta,
            entry.count_after,
            entry.seconds_taken if entry.seconds_taken is not None else "",
            entry.note or "",
        ])

    writer.writerow([])
    writer.writerow(["Completed Cushion Sets"])
    writer.writerow(["Completed", "Size", "Worker", "Stock After", "Estimated Seconds"])
    for completed in (
        cushion_history_completed_query(filters)
        .order_by(CushionCompletedSet.completed_at.desc(), CushionCompletedSet.id.desc())
        .all()
    ):
        writer.writerow([
            completed.completed_at.strftime("%Y-%m-%d %H:%M:%S") if completed.completed_at else "",
            completed.size_label,
            completed.worker,
            completed.stock_count_after,
            completed.estimated_seconds if completed.estimated_seconds is not None else "",
        ])

    writer.writerow([])
    writer.writerow(["Consumable Stock Entries"])
    writer.writerow(["Date", "Time", "Part", "Count After"])
    for entry in (
        cushion_history_consumable_query(filters)
        .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc(), PrintedPartsCount.id.desc())
        .all()
    ):
        writer.writerow([
            entry.date.strftime("%Y-%m-%d") if entry.date else "",
            entry.time.strftime("%H:%M:%S") if entry.time else "",
            entry.part_name,
            entry.count,
        ])

    filename_start = filters["start_date_value"] or "all"
    filename_end = filters["end_date_value"] or "all"
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv"
    response.headers["Content-Disposition"] = f"attachment; filename=cushion_history_{filename_start}_to_{filename_end}.csv"
    return response


@app.route('/counting_cushions_legacy', methods=['GET', 'POST'])
def counting_cushions_legacy():
    flash("The cushion page has been rebuilt. Use the new cushion production page.", "info")
    return redirect(url_for('counting_cushions'))

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
    part_key = db.Column(db.String(50), unique=True, nullable=False)  # e.g., 'black_6_short' or 'uncut'
    count = db.Column(db.Integer, default=0, nullable=False)


class BodyPieceCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_key = db.Column(db.String(60), unique=True, nullable=False)  # e.g., 'black_6_window_side'
    count = db.Column(db.Integer, default=0, nullable=False)


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

@app.route('/order_chinese_parts', methods=['GET', 'POST'])
def order_chinese_parts():
    if 'worker' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('login'))

    def safe_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def safe_float(value, default=0.0):
        try:
            if isinstance(value, str):
                cleaned = value.replace(",", "").strip()
                if cleaned == "":
                    return default
                return float(cleaned)
            return float(value)
        except (TypeError, ValueError):
            return default

    def part_cost_ex_vat(part_name):
        key = f"parts_inventory__{slugify_key(part_name)}"
        try:
            entry = StockItemCost.query.filter_by(item_key=key).first()
        except OperationalError:
            db.session.rollback()
            return 0.0
        if not entry:
            return 0.0
        # Use material-only (unit + shipping); exclude labour from order cost
        return (entry.unit_cost or 0.0) + (entry.shipping_cost or 0.0)

    latches_per_table = 12

    # Parts that are planned against a generic table target on this page.
    chinese_parts = {
        "Table legs": 4,
        "Ball Gullies 1": 2,
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
        **TOP_RAIL_TRIM_PARTS,
        "Ramp 170mm": 1,
        "Ramp 158mm": 1,
        "Ramp 918mm": 1,
        "Ramp 376mm": 1,
        "Chrome handles": 1,
        "Center pockets": 2,
        "Corner pockets": 4,
        "Sticker Set": 1
    }
    supplemental_parts = MANUAL_ONLY_CHINESE_PARTS + SIX_FOOT_ONLY_CHINESE_PARTS
    hidden_gully_parts = {
        "Gullies Untouched": 1,
        "6ft Gully Set": 6,
    }

    # Fetch latest count for each part
    part_stock = {}
    for part in list(chinese_parts) + supplemental_parts:
        latest_entry = (
            db.session.query(PrintedPartsCount.count)
            .filter_by(part_name=part)
            .order_by(PrintedPartsCount.date.desc(), PrintedPartsCount.time.desc())
            .first()
        )
        part_stock[part] = latest_entry[0] if latest_entry else 0

    gullies_parts = [p for p in chinese_parts if p.lower().startswith("ball gullies")]
    gullies_per_table = sum(chinese_parts.get(p, 0) for p in gullies_parts)

    def load_on_order():
        return load_chinese_parts_on_order()

    def save_on_order(data):
        try:
            with open(CHINESE_PARTS_ON_ORDER_FILE, "w") as f:
                json.dump(data, f)
            return True
        except OSError:
            return False

    saved_on_order = load_on_order()
    saved_parts_on_order = saved_on_order.get("parts", {})
    saved_gullies_units = saved_on_order.get("gullies_units", 0) or 0
    saved_hidden_gully_units = sum(
        safe_int(saved_parts_on_order.get(part), 0) * multiplier
        for part, multiplier in hidden_gully_parts.items()
    )
    saved_payments = saved_on_order.get("payments", {})
    saved_manual_suppliers = saved_on_order.get("manual_suppliers", {})
    if not isinstance(saved_manual_suppliers, dict):
        saved_manual_suppliers = {}
    saved_latches = saved_manual_suppliers.get("latches", {})
    if not isinstance(saved_latches, dict):
        saved_latches = {}
    saved_arrivals = saved_on_order.get("arrivals", [])
    saved_target_tables = safe_int(saved_on_order.get("last_target_tables"), None)
    saved_latches_stock = safe_int(saved_latches.get("stock"), 0)
    saved_latches_order_quantity = safe_int(saved_latches.get("order_quantity"), 0)
    saved_latches_order_total = safe_float(
        saved_latches.get("order_total"),
        safe_float(saved_payments.get("latches", {}).get("order_total") if isinstance(saved_payments.get("latches"), dict) else 0.0),
    )
    saved_latches_cost_each = safe_float(saved_latches.get("cost_each"), 0.0)
    if saved_latches_cost_each <= 0 and saved_latches_order_quantity > 0 and saved_latches_order_total > 0:
        saved_latches_cost_each = saved_latches_order_total / saved_latches_order_quantity

    target_table_count = None
    action = None
    paid_all_supplier = None
    if request.method == 'GET':
        gullies_units_on_order = saved_gullies_units + saved_hidden_gully_units
        target_table_count = saved_target_tables
        latches_stock = saved_latches_stock
        latches_cost_each = saved_latches_cost_each
    else:
        # On POST, always take the submitted units; if blank/invalid, default to 0
        gullies_units_on_order = safe_int(request.form.get('gullies_on_order_units'), 0)
        latches_stock = safe_int(request.form.get('latches_stock'), saved_latches_stock)
        latches_cost_each = safe_float(request.form.get('latches_cost_each'), saved_latches_cost_each)
        action = request.form.get('action')
        if action and action.startswith('paid_all:'):
            paid_all_supplier = action.split(':', 1)[1]
            action = 'paid_all'

    # Pull "on order" quantities from the form (default to saved), using a single input for gullies (tables' worth)
    part_on_order = {}
    if gullies_per_table > 0:
        remaining = gullies_units_on_order
        for idx, part in enumerate(gullies_parts):
            qty = chinese_parts[part]
            if idx == len(gullies_parts) - 1:
                alloc = remaining
            else:
                alloc = (gullies_units_on_order * qty) // gullies_per_table
                remaining -= alloc
            part_on_order[part] = alloc
    for part in chinese_parts:
        if part in gullies_parts:
            continue
        default_saved = saved_parts_on_order.get(part, 0)
        part_on_order[part] = default_saved if request.method == 'GET' else safe_int(
            request.form.get(f"on_order_{slugify_key(part)}"), default_saved)
    for part in supplemental_parts:
        default_saved = saved_parts_on_order.get(part, 0)
        part_on_order[part] = default_saved if request.method == 'GET' else 0

    # Combine on-hand and on-order to get total available
    part_total_available = {
        part: part_stock.get(part, 0) + part_on_order.get(part, 0)
        for part in chinese_parts
    }

    # Calculate how many tables can be built based on current part limits
    tables_possible_per_part_stock = {
        part: max(part_stock[part], 0) // qty
        for part, qty in chinese_parts.items()
    }
    tables_possible_per_part_total = {
        part: max(part_total_available[part], 0) // qty
        for part, qty in chinese_parts.items()
    }

    parts_to_order = {}
    part_costs = {
        part: part_cost_ex_vat(part)
        for part in list(chinese_parts) + supplemental_parts
    }
    order_costs = {}
    total_order_cost = 0.0
    display_name_map = {
        "Ramp 170mm": "170",
        "Ramp 918mm": "918",
        "Ramp 158mm": "158",
        "Ramp 376mm": "376",
        "Aluminum corner": "Aluminum Corner",
        "Top Rail Trim - 814mm (Left)": "Top Rail Trim - 814mm (Left)",
        "Top Rail Trim - 814mm (Right)": "Top Rail Trim - 814mm (Right)",
        "Top Rail Trim - 822mm": "Top Rail Trim - 822mm",
        "Ball window trim": "Ball Window Trim",
        "Color ball trim": "Colour Ball Trim",
        "White ball return trim": "Cue ball Trim",
        "Triangle trim": "Triangle holder Trim",
    }

    gullies_stock = sum(part_stock.get(p, 0) for p in gullies_parts)
    gullies_stock += sum(
        part_stock.get(part, 0) * multiplier
        for part, multiplier in hidden_gully_parts.items()
    )
    gullies_on_order = gullies_units_on_order
    gullies_on_order += sum(
        part_on_order.get(part, 0) * multiplier
        for part, multiplier in hidden_gully_parts.items()
    )
    gullies_total_available = gullies_stock + gullies_on_order
    gullies_can_build = (max(gullies_stock, 0) // gullies_per_table) if gullies_per_table else 0
    gullies_can_build_total = (max(gullies_total_available, 0) // gullies_per_table) if gullies_per_table else 0

    if request.method == 'POST':
        target_tables_raw = request.form.get('target_tables')
        try:
            target_table_count = int(target_tables_raw) if target_tables_raw not in [None, ""] else None
        except ValueError:
            target_table_count = None
            flash("Please enter a valid number for target tables.", "error")

        if target_table_count is not None:
            saved_target_tables = target_table_count
        elif action in {'save_payments', 'paid_all'} and saved_target_tables is not None:
            target_table_count = saved_target_tables

    if target_table_count is not None:
        for part, qty_per_table in chinese_parts.items():
            needed = target_table_count * qty_per_table
            current = part_total_available.get(part, 0)
            parts_to_order[part] = max(0, needed - current)
            order_costs[part] = parts_to_order[part] * part_costs.get(part, 0.0)
            total_order_cost += order_costs[part]

        gullies_need = max(0, target_table_count * gullies_per_table - gullies_total_available) if gullies_per_table else 0
        gullies_order_cost = sum(order_costs.get(p, 0.0) for p in gullies_parts)
    else:
        gullies_need = None
        gullies_order_cost = None

    standard_parts = []
    for part, qty_per_table in chinese_parts.items():
        if part in gullies_parts:
            continue
        standard_parts.append({
            "name": part,
            "display_name": display_name_map.get(part, part),
            "stock": part_stock.get(part, 0),
            "on_order": part_on_order.get(part, 0),
            "total_available": part_total_available.get(part, 0),
            "per_table": qty_per_table,
            "can_build": tables_possible_per_part_total.get(part, 0),
            "can_build_now": tables_possible_per_part_stock.get(part, 0),
            "cost_each": part_costs.get(part, 0.0),
            "need_to_order": parts_to_order.get(part, 0) if target_table_count else None,
            "order_cost": order_costs.get(part, 0.0) if target_table_count else None,
        })

    gullies_summary = {
        "name": "Gullies Untouched",
        "stock": gullies_stock,
        "on_order": gullies_on_order,
        "on_order_units": gullies_units_on_order,
        "total_available": gullies_total_available,
        "per_table": gullies_per_table,
        "can_build": gullies_can_build_total,
        "can_build_now": gullies_can_build,
        "cost_each": None,
        "need_to_order": gullies_need,
        "order_cost": gullies_order_cost,
    }

    metal_supplier_parts = {
        "Triangle trim",
        "White ball return trim",
        "Color ball trim",
        "Ball window trim",
        "Aluminum corner",
        *TOP_RAIL_TRIM_PARTS.keys(),
        "Ramp 170mm",
        "Ramp 158mm",
        "Ramp 918mm",
        "Ramp 376mm",
    }

    metal_order = [
        "Ramp 170mm",
        "Ramp 918mm",
        "Ramp 158mm",
        "Ramp 376mm",
        "Aluminum corner",
        "Top Rail Trim - 822mm",
        "Top Rail Trim - 814mm (Left)",
        "Top Rail Trim - 814mm (Right)",
        "Ball window trim",
        "Color ball trim",
        "White ball return trim",
        "Triangle trim",
    ]

    metal_parts = [row for row in standard_parts if row["name"] in metal_supplier_parts]
    metal_parts.sort(key=lambda r: metal_order.index(r["name"]) if r["name"] in metal_order else len(metal_order))
    plastic_parts = [row for row in standard_parts if row["name"] not in metal_supplier_parts]

    plastic_order = [
        "Table legs",
        "Corner pockets",
        "Center pockets",
        "__GULLIES__",
        "Chrome handles",
        "Chrome corner",
    ]

    plastic_rows = []
    remaining_plastic = plastic_parts.copy()
    for name in plastic_order:
        if name == "__GULLIES__":
            if gullies_summary:
                plastic_rows.append({"type": "gullies", "data": gullies_summary})
            continue
        for item in list(remaining_plastic):
            if item["name"] == name:
                plastic_rows.append({"type": "part", "data": item})
                remaining_plastic.remove(item)
                break
    for item in remaining_plastic:
        plastic_rows.append({"type": "part", "data": item})

    max_tables_possible_candidates = [row["can_build_now"] for row in standard_parts]
    max_tables_possible_candidates_with_on_order = [row["can_build"] for row in standard_parts]
    if gullies_per_table:
        max_tables_possible_candidates.append(gullies_can_build)
        max_tables_possible_candidates_with_on_order.append(gullies_can_build_total)
    max_tables_possible = min(max_tables_possible_candidates) if max_tables_possible_candidates else 0
    max_tables_possible_with_on_order = min(max_tables_possible_candidates_with_on_order) if max_tables_possible_candidates_with_on_order else 0
    metal_total_order_cost = sum(row.get("order_cost") or 0.0 for row in metal_parts)
    plastic_total_order_cost = 0.0
    for row in plastic_rows:
        data = row.get("data", {})
        plastic_total_order_cost += data.get("order_cost") or 0.0
    latches_required = target_table_count * latches_per_table if target_table_count is not None else None
    latches_need_to_order = max(0, latches_required - latches_stock) if latches_required is not None else None
    latches_order_quantity = latches_need_to_order if latches_need_to_order is not None else 0
    latches_order_total = latches_order_quantity * latches_cost_each
    combined_total_order_cost = total_order_cost + latches_order_total
    latches_supplier = {
        "stock": latches_stock,
        "per_table": latches_per_table,
        "required": latches_required,
        "can_build_now": latches_stock // latches_per_table if latches_per_table else 0,
        "need_to_order": latches_need_to_order,
        "order_quantity": latches_order_quantity,
        "cost_each": latches_cost_each,
        "order_total": latches_order_total,
    }

    supplier_defaults = {
        "metal": metal_total_order_cost,
        "plastic": plastic_total_order_cost,
        "latches": latches_order_total,
        "feet": order_costs.get("Feet", 0.0),
        "filament": 0.0,
        "sticker": order_costs.get("Sticker Set", 0.0),
        "shipper": 0.0,
    }
    supplier_upfront = {
        "metal": 0.30,
        "plastic": 0.70,
        "latches": 0.50,
        "feet": 0.50,
        "filament": 0.50,
        "sticker": 0.50,
        "shipper": 1.00,
    }
    supplier_labels = {
        "metal": "Metal Supplier",
        "plastic": "Plastic Supplier",
        "latches": "Latches Supplier",
        "feet": "Feet Supplier",
        "filament": "3D Printing Filament Supplier",
        "sticker": "Sticker Supplier",
        "shipper": "Container Shipper",
    }

    payments = []
    total_paid = 0.0
    total_upfront_due = 0.0
    total_balance_due = 0.0
    supplier_keys = ["metal", "plastic", "latches", "feet", "filament", "sticker", "shipper"]
    for key in supplier_keys:
        saved_entry = saved_payments.get(key, {})
        order_total = supplier_defaults.get(key, 0.0)
        if key not in {"metal", "plastic", "latches"} and saved_entry.get("order_total") is not None:
            order_total = safe_float(saved_entry.get("order_total"), order_total)
        paid_so_far = safe_float(
            request.form.get(f"{key}_paid_so_far") if request.method == 'POST' else saved_entry.get("paid_so_far"),
            0.0,
        )
        if order_total <= 0 and paid_so_far > 0 and supplier_upfront[key] > 0:
            order_total = paid_so_far / supplier_upfront[key]
        if request.method == 'POST' and action == 'paid_all' and paid_all_supplier == key:
            paid_so_far = order_total
        upfront_required = order_total * supplier_upfront[key]
        balance_due_upfront = max(0.0, upfront_required - paid_so_far)
        balance_due_total = max(0.0, order_total - paid_so_far)
        total_paid += paid_so_far
        total_upfront_due += balance_due_upfront
        total_balance_due += balance_due_total
        payments.append({
            "key": key,
            "label": supplier_labels[key],
            "upfront_percent": int(supplier_upfront[key] * 100),
            "order_total": order_total,
            "paid_so_far": paid_so_far,
            "upfront_required": upfront_required,
            "balance_due": balance_due_total,
            "balance_due_upfront": balance_due_upfront,
            "show_paid_all": key in {"latches", "feet", "filament", "sticker", "shipper"},
        })
        saved_payments[key] = {"order_total": order_total, "paid_so_far": paid_so_far}

    if request.method == 'POST' and action == 'parts_arrived':
        arrival_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_paid": total_paid,
            "total_upfront_due": total_upfront_due,
            "total_balance_due": total_balance_due,
            "payments": [
                {
                    "key": item["key"],
                    "label": item["label"],
                    "order_total": item["order_total"],
                    "paid_so_far": item["paid_so_far"],
                    "balance_due": item["balance_due"],
                    "upfront_percent": item["upfront_percent"],
                }
                for item in payments
            ],
        }
        saved_arrivals.append(arrival_entry)

    arrivals = []
    for entry in saved_arrivals:
        display_time = entry.get("timestamp", "")
        try:
            display_time = datetime.fromisoformat(display_time).strftime("%d/%m/%Y %H:%M")
        except (TypeError, ValueError):
            pass
        arrivals.append({
            "display_time": display_time,
            "total_paid": entry.get("total_paid", 0.0),
            "total_upfront_due": entry.get("total_upfront_due"),
            "total_balance_due": entry.get("total_balance_due", 0.0),
            "payments": entry.get("payments", []),
        })

    if request.method == 'POST':
        manual_suppliers_to_save = dict(saved_manual_suppliers)
        manual_suppliers_to_save["latches"] = latches_supplier
        saved_successfully = save_on_order({
            "parts": {part: part_on_order.get(part, 0) for part in chinese_parts if part not in gullies_parts},
            "gullies_units": gullies_units_on_order,
            "payments": saved_payments,
            "manual_suppliers": manual_suppliers_to_save,
            "last_target_tables": saved_target_tables,
            "arrivals": saved_arrivals
        })
        if not saved_successfully:
            flash("Could not save on-order figures.", "error")
        else:
            order_more_message = check_and_notify_chinese_parts_order_more(
                CHINESE_PARTS_ORDER_MORE_PART,
                part_stock.get(CHINESE_PARTS_ORDER_MORE_PART, 0),
                part_stock.get(CHINESE_PARTS_ORDER_MORE_PART, 0),
                old_on_order_count=safe_int(saved_parts_on_order.get(CHINESE_PARTS_ORDER_MORE_PART), 0),
                new_on_order_count=part_on_order.get(CHINESE_PARTS_ORDER_MORE_PART, 0)
            )
            if order_more_message:
                flash(order_more_message, "warning")
            if action == 'save_payments':
                flash("Payments and on-order figures saved.", "success")
            elif action == 'parts_arrived':
                flash("Parts arrival logged and on-order figures saved.", "success")
            elif action == 'paid_all':
                flash("Payment updated and on-order figures saved.", "success")
            else:
                flash("On-order figures saved.", "success")
        return redirect(url_for('order_chinese_parts'))

    return render_template(
        'order_chinese_parts.html',
        chinese_parts=chinese_parts,
        part_stock=part_stock,
        part_on_order=part_on_order,
        part_total_available=part_total_available,
        tables_possible_per_part=tables_possible_per_part_total,
        tables_possible_per_part_stock=tables_possible_per_part_stock,
        max_tables_possible=max_tables_possible,
        max_tables_possible_with_on_order=max_tables_possible_with_on_order,
        gullies_units_on_order=gullies_units_on_order,
        parts_to_order=parts_to_order,
        target_table_count=target_table_count,
        part_costs=part_costs,
        order_costs=order_costs,
        total_order_cost=total_order_cost,
        combined_total_order_cost=combined_total_order_cost,
        metal_parts=metal_parts,
        plastic_rows=plastic_rows,
        gullies_summary=gullies_summary,
        metal_total_order_cost=metal_total_order_cost,
        plastic_total_order_cost=plastic_total_order_cost,
        latches_supplier=latches_supplier,
        chinese_parts_order_more_part=CHINESE_PARTS_ORDER_MORE_PART,
        chinese_parts_order_more_threshold=CHINESE_PARTS_ORDER_MORE_THRESHOLD,
        payments=payments,
        total_paid=total_paid,
        total_upfront_due=total_upfront_due,
        total_balance_due=total_balance_due,
        arrivals=arrivals
    )

class LaminatePieceCount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    part_key = db.Column(db.String(50), unique=True, nullable=False)  # e.g., 'black_6_short' or 'uncut'
    count = db.Column(db.Integer, default=0, nullable=False)


@app.route('/top_rail_pieces', methods=['GET', 'POST'])
def top_rail_pieces():
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

            # Increment top rail count
            part = TopRailPieceCount.query.filter_by(part_key=part_key).first()
            if not part:
                part = TopRailPieceCount(part_key=part_key, count=1)
                db.session.add(part)
            else:
                part.count += 1

            # NEW: Add laminate deduction logic
            # Parse the part_key to determine the corresponding laminate piece
            parts = part_key.split('_')
            if len(parts) >= 3:
                if len(parts) == 4:  # rustic_black case
                    color = f"{parts[0]}_{parts[1]}"
                else:
                    color = parts[0]
                size = parts[-2]  # Second to last part
                length = parts[-1]  # Last part
                
                # Map to laminate piece key
                laminate_key = f"{color}_{size}_{length}"
                
                # Determine deduction amount based on size and length
                if size == '7' and length == 'short':
                    deduction = 1
                elif size == '7' and length == 'long':
                    deduction = 1.0
                elif size == '6':
                    deduction = 1
                else:
                    deduction = 0
                
                # Find and deduct from laminate if deduction is needed
                deducted_laminate = 0
                laminate_response_key = None
                
                if deduction > 0:
                    laminate_part = LaminatePieceCount.query.filter_by(part_key=laminate_key).first()
                    if laminate_part and laminate_part.count >= deduction:
                        laminate_part.count -= deduction
                        deducted_laminate = deduction
                        laminate_response_key = laminate_key

            db.session.commit()

            response_data = {
                "success": True,
                "message": f"Logged 1 top rail ({part_key})",
                "part_key": part_key
            }
            
            # Add laminate deduction info to response if applicable
            if deducted_laminate > 0:
                response_data["deducted_laminate"] = deducted_laminate
                response_data["laminate_key"] = laminate_response_key
                response_data["message"] += f", deducted {deducted_laminate} laminate piece"

            return jsonify(response_data), 200

        # Handle manual form submissions
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

    # Display counts
    counts = {}
    all_parts = TopRailPieceCount.query.all()
    for part in all_parts:
        counts[f"piece_{part.part_key}"] = part.count
    
    # Calculate total pieces and max top rails we can make
    colors = ['black', 'rustic_oak', 'grey_oak', 'stone', 'rustic_black']
    total_6ft_short = 0
    total_6ft_long = 0
    total_7ft_short = 0
    total_7ft_long = 0
    for color in colors:
        total_6ft_short += counts.get(f'piece_{color}_6_short', 0)
        total_6ft_long += counts.get(f'piece_{color}_6_long', 0)
        total_7ft_short += counts.get(f'piece_{color}_7_short', 0)
        total_7ft_long += counts.get(f'piece_{color}_7_long', 0)
    total_6ft_pieces = total_6ft_short + total_6ft_long
    total_7ft_pieces = total_7ft_short + total_7ft_long

    max_top_rails = 0
    for color in colors:
        # For 6ft
        short_6 = counts.get(f'piece_{color}_6_short', 0)
        long_6 = counts.get(f'piece_{color}_6_long', 0)
        # Each top rail requires 2 short and 2 long pieces.
        # The number of rails is limited by the minimum of sets you can make.
        max_6ft = min(short_6 // 2, long_6 // 2)
        
        # For 7ft
        short_7 = counts.get(f'piece_{color}_7_short', 0)
        long_7 = counts.get(f'piece_{color}_7_long', 0)
        max_7ft = min(short_7 // 2, long_7 // 2)
        
        max_top_rails += max_6ft + max_7ft
    
    return render_template(
        'top_rail_pieces.html',
        counts=counts,
        max_top_rails=max_top_rails,
        total_6ft_pieces=total_6ft_pieces,
        total_7ft_pieces=total_7ft_pieces,
        total_6ft_short=total_6ft_short,
        total_6ft_long=total_6ft_long,
        total_7ft_short=total_7ft_short,
        total_7ft_long=total_7ft_long
    )


@app.route('/body_pieces', methods=['GET', 'POST'])
def body_pieces():
    BodyPieceCount.__table__.create(db.engine, checkfirst=True)
    color_defs = [
        ("black", "Black"),
        ("rustic_oak", "Rustic Oak"),
        ("grey_oak", "Grey Oak"),
        ("stone", "Stone"),
        ("rustic_black", "Rustic Black"),
    ]
    size_defs_for_keys = [("6", "6ft"), ("7", "7ft")]
    size_defs_for_display = [("7", "7ft"), ("6", "6ft")]
    size_defs = size_defs_for_keys
    piece_defs = [
        ("window_side", "Window Side"),
        ("blank_side", "Blank Side"),
        ("triangle_end", "Colour End"),
        ("color_ball_end", "White Ball End"),
    ]
    key_codes = [
        "KeyA", "KeyB", "KeyC", "KeyD", "KeyE",
        "KeyF", "KeyG", "KeyH", "KeyI", "KeyJ",
        "KeyK", "KeyL", "KeyM", "KeyN", "KeyO",
        "KeyP", "KeyQ", "KeyR", "KeyS", "KeyT",
        "KeyU", "KeyV", "KeyW", "KeyX", "KeyY",
        "KeyZ", "Digit1", "Digit2", "Digit3", "Digit4",
        "Digit5", "Digit6", "Digit7", "Digit8", "Digit9",
        "Digit0", "BracketLeft", "BracketRight", "Comma", "Quote",
    ]
    key_display_map = {
        "BracketLeft": "[",
        "BracketRight": "]",
        "Comma": ",",
        "Quote": "'",
    }
    key_map = {}
    shortcut_groups = []
    part_keys = []
    key_index = 0

    def display_key(code):
        if code.startswith("Key"):
            return code[3:]
        if code.startswith("Digit"):
            return code[5:]
        return key_display_map.get(code, code)

    for size_key, size_label in size_defs_for_keys:
        for piece_key, piece_label in piece_defs:
            for color_key, color_label in color_defs:
                part_key = f"{color_key}_{size_key}_{piece_key}"
                code = key_codes[key_index]
                key_index += 1
                key_map[code] = part_key
                part_keys.append(part_key)

    part_key_to_code = {part: code for code, part in key_map.items()}

    for color_key, color_label in color_defs:
        color_group = {"color": color_label, "sizes": []}
        for size_key, size_label in size_defs_for_display:
            items = []
            for piece_key, piece_label in piece_defs:
                part_key = f"{color_key}_{size_key}_{piece_key}"
                code = part_key_to_code.get(part_key, "")
                items.append({
                    "key": display_key(code) if code else "",
                    "label": piece_label,
                    "part_key": part_key
                })
            color_group["sizes"].append({"size": size_label, "entries": items})
        shortcut_groups.append(color_group)

    if request.method == 'POST':
        key_code = request.form.get('key_code')
        action = request.form.get('action', 'add')
        part_key = None
        if key_code and key_code in key_map:
            part_key = key_map[key_code]
        else:
            direct_key = request.form.get('part_key')
            if direct_key in part_keys:
                part_key = direct_key
        if part_key:
            delta = -1 if action == 'remove' else 1
            part = BodyPieceCount.query.filter_by(part_key=part_key).first()
            if not part:
                part = BodyPieceCount(part_key=part_key, count=0)
                db.session.add(part)
            part.count = max(part.count + delta, 0)
            db.session.commit()
            return jsonify({
                "success": True,
                "part_key": part_key,
                "new_count": part.count,
                "action": action
            }), 200

        for part_key in part_keys:
            input_value = request.form.get(f"piece_{part_key}")
            if input_value is not None:
                try:
                    count = int(input_value)
                    part = BodyPieceCount.query.filter_by(part_key=part_key).first()
                    if not part:
                        part = BodyPieceCount(part_key=part_key, count=count)
                        db.session.add(part)
                    else:
                        part.count = count
                except ValueError:
                    flash(f"Invalid number for {part_key}", "error")

        db.session.commit()
        flash("Body piece counts updated successfully.", "success")
        return redirect(url_for('body_pieces'))

    counts = {}
    all_parts = BodyPieceCount.query.all()
    for part in all_parts:
        counts[f"piece_{part.part_key}"] = part.count

    max_bodies = 0
    max_6ft = 0
    max_7ft = 0
    for color_key, _ in color_defs:
        for size_key, _ in size_defs:
            window = counts.get(f"piece_{color_key}_{size_key}_window_side", 0)
            blank = counts.get(f"piece_{color_key}_{size_key}_blank_side", 0)
            triangle = counts.get(f"piece_{color_key}_{size_key}_triangle_end", 0)
            color_ball = counts.get(f"piece_{color_key}_{size_key}_color_ball_end", 0)
            size_total = min(window, blank, triangle, color_ball)
            max_bodies += size_total
            if size_key == "6":
                max_6ft += size_total
            else:
                max_7ft += size_total

    return render_template(
        'body_pieces.html',
        counts=counts,
        max_bodies=max_bodies,
        max_6ft=max_6ft,
        max_7ft=max_7ft,
        shortcut_groups=shortcut_groups,
        key_map=key_map
    )


@app.route('/counting_laminate', methods=['GET', 'POST'])
def counting_laminate():
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
        'u': 'black_uncut',
        'v': 'rustic_oak_uncut',
        'w': 'grey_oak_uncut',
        'x': 'stone_uncut',
        'y': 'rustic_black_uncut',
        # Bulk buttons
        '1': 'black_7_short_bulk',
        '2': 'black_7_long_bulk',
    }

    if request.method == 'POST':
        key_code = request.form.get('key_code')
        if key_code and key_code in key_map:
            part_key = key_map[key_code]

            # Bulk increments
            if part_key == 'black_7_short_bulk':
                part_key_actual = 'black_7_short'
                uncut_key = 'black_uncut'
                add_count = 10
                to_deduct = 5  # 10 x 0.5
                color = 'black'
            elif part_key == 'black_7_long_bulk':
                part_key_actual = 'black_7_long'
                uncut_key = 'black_uncut'
                add_count = 10
                to_deduct = 10  # 10 x 1
                color = 'black'
            else:
                part_key_actual = part_key
                add_count = 1

                if part_key.endswith('uncut'):
                    part = LaminatePieceCount.query.filter_by(part_key=part_key).first()
                    if not part:
                        part = LaminatePieceCount(part_key=part_key, count=1)
                        db.session.add(part)
                    else:
                        part.count += 1
                    db.session.commit()
                    return jsonify({
                        "success": True,
                        "message": f"Logged 1 to {part_key}",
                        "part_key": part_key,
                        "deducted_uncut": 0,
                        "uncut_key": part_key
                    }), 200

                # Parse part key to get color, size, length
                parts = part_key.split('_')
                if len(parts) == 4:  # rustic_black case
                    color = f"{parts[0]}_{parts[1]}"  # rustic_black
                    size = parts[2]
                    length = parts[3]
                else:  # Handle other colors
                    color = parts[0]
                    size = parts[1]
                    length = parts[2]
                
                uncut_key = f"{color}_uncut"

                # Determine deduction for normal keys
                if size == '7' and length == 'short':
                    to_deduct = 0.5
                elif size == '7' and length == 'long':
                    to_deduct = 1
                elif size == '6':
                    to_deduct = 0.5
                else:
                    to_deduct = 0
            print(part_key, part_key_actual, uncut_key, to_deduct)
            # Deduct uncut logic
            uncut_part = LaminatePieceCount.query.filter_by(part_key=uncut_key).first()
            if not uncut_part or uncut_part.count < to_deduct:
                return jsonify({
                    "success": False,
                    "message": f"Not enough uncut sheets for {color}. Needed {to_deduct}, have {uncut_part.count if uncut_part else 0:.1f}"
                }, 400)

            uncut_part.count -= to_deduct

            # Update main piece count
            part = LaminatePieceCount.query.filter_by(part_key=part_key_actual).first()
            if not part:
                part = LaminatePieceCount(part_key=part_key_actual, count=add_count)
                db.session.add(part)
            else:
                part.count += add_count

            db.session.commit()
            return jsonify({
                "success": True,
                "message": f"Added {add_count} to {part_key_actual}, deducted {to_deduct} uncut sheet(s)",
                "part_key": part_key_actual,
                "uncut_key": uncut_key,
                "deducted_uncut": to_deduct,
                "amount": add_count
            }), 200

        # Handle manual form submission
        colors = ['black', 'rustic_oak', 'grey_oak', 'stone', 'rustic_black']
        all_keys = [f"{color}_{size}_{length}" for color in colors for size in ['6', '7'] for length in ['short', 'long']] + \
                   [f"{color}_uncut" for color in colors]

        for key in all_keys:
            input_value = request.form.get(f"piece_{key}")
            if input_value is not None:
                try:
                    count = float(input_value)
                    part = LaminatePieceCount.query.filter_by(part_key=key).first()
                    if not part:
                        part = LaminatePieceCount(part_key=key, count=count)
                        db.session.add(part)
                    else:
                        part.count = count
                except ValueError:
                    flash(f"Invalid number for {key}", "error")

        db.session.commit()
        flash("Laminate piece counts updated successfully.", "success")
        return redirect(url_for('counting_laminate'))

    # GET request handling
    counts = {}
    all_parts = LaminatePieceCount.query.all()
    for part in all_parts:
        counts[f"piece_{part.part_key}"] = part.count

    return render_template('counting_laminate.html', counts=counts)  # Added the missing return statement
    # Parse part key to handle rustic_black properly
    parts = part_key.split('_')
    if len(parts) == 4:  # Handle rustic_black case
        color = f"{parts[0]}_{parts[1]}"  # rustic_black
        size = parts[2]
        length = parts[3]
    else:  # Handle other colors
        color = parts[0]
        size = parts[1]
        length = parts[2]
    
    uncut_key = f"{color}_uncut"

    if size == '7' and length == 'short':
        to_deduct_per = 0.5
    elif size == '7' and length == 'long':
        to_deduct_per = 1
    elif size == '6':
        to_deduct_per = 0.5
    else:
        to_deduct_per = 0

    total_deduction = to_deduct_per * amount

    uncut_part = LaminatePieceCount.query.filter_by(part_key=uncut_key).first()
    if not uncut_part or uncut_part.count < total_deduction:
        return jsonify({
            "success": False,
            "message": f"Not enough uncut sheets for {color}. Needed {total_deduction}, have {uncut_part.count if uncut_part else 0:.1f}"
        }, 400)

    uncut_part.count -= total_deduct_per

    part = LaminatePieceCount.query.filter_by(part_key=part_key).first()
    if not part:
        part = LaminatePieceCount(part_key=part_key, count=amount)
        db.session.add(part)
    else:
        part.count += amount

    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Added {amount} to {part_key}, deducted {total_deduction} uncut sheet(s)",
        "part_key": part_key,
        "uncut_key": uncut_key,
        "deducted_uncut": total_deduction,
        "amount": amount
    }), 200
