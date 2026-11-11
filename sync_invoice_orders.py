"""Run once from the server's five-minute systemd timer."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.environ.get("POOLTRACKER_DATABASE_PATH"),
                        help="Absolute path to the existing live SQLite database (required).")
    parser.add_argument("--dry-run", action="store_true", help="Read and validate Google Sheets without changing the database.")
    args = parser.parse_args(argv)
    if not args.database:
        parser.error("Set --database or POOLTRACKER_DATABASE_PATH to the existing live database.")
    path = Path(args.database).expanduser().resolve()
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("The database must already exist and contain tracker tables.")
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                raise ValueError("The database contains no tracker tables.")
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Invoice sync not started: {exc}", file=sys.stderr)
        return 1
    os.environ["POOLTRACKER_DATABASE_PATH"] = str(path)
    from flask_app import app, db, sync_invoice_sheet, invoice_order_seed_source, invoice_sync_credentials_path
    from invoice_sheet_sync import InvoiceSyncError, fetch_invoice_source
    with app.app_context():
        try:
            if args.dry_run:
                source = fetch_invoice_source(invoice_order_seed_source(), invoice_sync_credentials_path())
                result = {"validated_orders": len(source["invoices"]), "products": len(source["products"])}
                print("Read-only connection check passed; database unchanged: " + json.dumps(result))
            else:
                result = sync_invoice_sheet()
                print("Invoice sheet sync completed: " + json.dumps(result, sort_keys=True))
            return 0
        except InvoiceSyncError as exc:
            print("Invoice sheet sync failed: " + str(exc), file=sys.stderr)
            return 1
        finally:
            db.session.remove()


if __name__ == "__main__":
    raise SystemExit(main())
