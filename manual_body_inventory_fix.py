"""
Utility script to backfill inventory deductions for bodies that were inserted
directly into SQLite (and therefore skipped the normal Flask route logic).

Usage example (PowerShell / CMD):
    cd C:\\Users\\Sales\\Pooltracker-1
    .\\venv\\Scripts\\python.exe manual_body_inventory_fix.py ^
        --db C:\\Users\\Sales\\pool_table_tracker.db ^
        --serial 461 462 470 478 480

If you omit --db the script looks for pool_table_tracker.db next to this file and,
if it is missing, falls back to %USERPROFILE%\\pool_table_tracker.db.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from typing import Dict


LAMINATE_COLOR_KEY_TO_LABEL = {
    "black": "Black",
    "rustic_oak": "Rustic Oak",
    "grey_oak": "Grey Oak",
    "stone": "Stone",
    "rustic_black": "Rustic Black",
}


BASE_PARTS: Dict[str, int] = {
    "Large Ramp": 1,
    "Paddle": 1,
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
    "Sticker Set": 1,
    "4.8x16mm Self Tapping Screw": 37,
    "4.0 x 50mm Wood Screw": 4,
    "Plastic Window": 1,
    "4.2 x 16 No2 Self Tapping Screw": 19,
    "Spring": 1,
    "Handle Tube": 1,
    "Latch": 12,
}


def is_6ft(serial: str) -> bool:
    cleaned = serial.replace(" ", "")
    return cleaned.endswith("-6") or "-6-" in cleaned or " - 6 - " in serial


def color_key(serial: str) -> str:
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


def apply_adjustments(parts: Dict[str, int], serial: str) -> Dict[str, int]:
    adjusted = dict(parts)
    laminate_label = LAMINATE_COLOR_KEY_TO_LABEL.get(color_key(serial), "Black")
    adjusted[f"Laminate - {laminate_label}"] = 4
    if is_6ft(serial):
        adjusted.pop("Large Ramp", None)
        adjusted.pop("Cue Ball Separator", None)
        adjusted.pop("Small Ramp", None)
        adjusted.pop("Ramp 170mm", None)
        adjusted.pop("Ramp 158mm", None)
        adjusted["6ft Large Ramp"] = 1
        adjusted["6ft Cue Ball Separator"] = 1
    return adjusted


def update_part_count(conn: sqlite3.Connection, part_name: str, quantity: int) -> None:
    row = conn.execute(
        """
        SELECT id, count
        FROM printed_parts_count
        WHERE part_name = ?
        ORDER BY date DESC, time DESC, id DESC
        LIMIT 1
        """,
        (part_name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No inventory row found for '{part_name}'.")
    current = row[1] or 0
    if current < quantity:
        raise RuntimeError(
            f"Not enough '{part_name}' in stock. Need {quantity}, have {current}."
        )
    conn.execute(
        "UPDATE printed_parts_count SET count = count - ? WHERE id = ?",
        (quantity, row[0]),
    )


def increment_table_stock(conn: sqlite3.Connection, size: str, color: str) -> None:
    stock_type = f"body_{size.lower()}_{color}"
    row = conn.execute(
        "SELECT id, count FROM table_stock WHERE type = ?", (stock_type,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO table_stock (type, count) VALUES (?, ?)",
            (stock_type, 1),
        )
    else:
        conn.execute(
            "UPDATE table_stock SET count = ? WHERE id = ?",
            (row[1] + 1, row[0]),
        )


def process_serial(conn: sqlite3.Connection, serial: str) -> None:
    parts = apply_adjustments(BASE_PARTS, serial)
    for part_name, qty in parts.items():
        update_part_count(conn, part_name, qty)
    size = "6ft" if is_6ft(serial) else "7ft"
    increment_table_stock(conn, size, color_key(serial))
    conn.commit()
    print(f"Processed body {serial}: deducted {len(parts)} parts and updated table stock.")


def default_db_path() -> str:
    home_db = os.path.join(os.path.expanduser("~"), "pool_table_tracker.db")
    repo_db = os.path.join(os.path.abspath(os.path.dirname(__file__)), "pool_table_tracker.db")
    if os.path.exists(home_db):
        return home_db
    return repo_db  # fall back to repo location even if missing


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill inventory deductions for manually inserted bodies."
    )
    parser.add_argument(
        "--db",
        default=default_db_path(),
        help="Path to pool_table_tracker.db (defaults to repo root, falls back to %USERPROFILE%).",
    )
    parser.add_argument(
        "--serial",
        nargs="+",
        required=True,
        help="Serial numbers of the bodies that skipped the normal deduction logic.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.db):
        raise FileNotFoundError(f"Database not found: {args.db}")

    conn = sqlite3.connect(args.db)
    try:
        for serial in args.serial:
            process_serial(conn, serial)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
