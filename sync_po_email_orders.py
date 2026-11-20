"""Run the PO email sync against an explicitly selected tracker database."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.environ.get('POOLTRACKER_DATABASE_PATH'))
    args = parser.parse_args(argv)
    if not args.database:
        parser.error('Set --database or POOLTRACKER_DATABASE_PATH to the existing tracker database.')
    database = Path(args.database).expanduser().resolve()
    if not database.is_file() or not database.stat().st_size:
        print('PO sync not started: the tracker database must already exist.', file=sys.stderr)
        return 1
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='generated_invoice'").fetchone():
            print('PO sync not started: open the invoice generator first.', file=sys.stderr)
            return 1
    os.environ['POOLTRACKER_DATABASE_PATH'] = str(database)
    from flask_app import app, db, sync_special_po_emails
    from invoice_email import InvoiceEmailError
    from invoice_po_email import POEmailError
    with app.app_context():
        try:
            result = sync_special_po_emails()
            print('PO email sync completed: ' + json.dumps(result, sort_keys=True))
            return 0
        except (InvoiceEmailError, POEmailError) as exc:
            print('PO email sync failed: ' + str(exc), file=sys.stderr)
            return 1
        finally:
            db.session.remove()


if __name__ == '__main__':
    raise SystemExit(main())
