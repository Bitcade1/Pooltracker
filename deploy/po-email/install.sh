#!/usr/bin/env bash
# Install only after enabling Mail.Read and connecting PO access in the tracker.
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo 'Run this installer with sudo.' >&2
    exit 1
fi
app_dir=/var/www/pooltabletracker.com
database="$app_dir/pool_table_tracker.db"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
test -s "$database"
test -s /etc/pooltracker-invoice-email/config.json
test -f "$app_dir/sync_po_email_orders.py"
db_user="$(stat -c '%U' "$database")"
db_group="$(stat -c '%G' "$database")"
app_python=''
for candidate in "$app_dir/venv/bin/python" "$app_dir/.venv/bin/python" "$app_dir/env/bin/python"; do
    if [ -x "$candidate" ] && "$candidate" -c 'import flask, flask_sqlalchemy, msal, pypdf' >/dev/null 2>&1; then
        app_python="$candidate"
        break
    fi
done
if [ -z "$app_python" ]; then
    echo 'Could not find the tracker Python environment with its invoice email dependencies.' >&2
    exit 1
fi
cd "$app_dir"
runuser -u "$db_user" -- "$app_python" backup_database.py --database "$database" --output-dir "$app_dir/backups" --local-only
runuser -u "$db_user" -- env POOLTRACKER_DATABASE_PATH="$database" "$app_python" - <<'PY'
from flask_app import app, db, InvoiceSpecialOrder
with app.app_context():
    InvoiceSpecialOrder.__table__.create(db.engine, checkfirst=True)
PY
"$app_python" - "$script_dir" "$app_python" "$db_user" "$db_group" <<'PY'
from pathlib import Path
import sys
directory, interpreter, user, group = sys.argv[1:]
service = (Path(directory) / 'pooltracker-po-email.service').read_text()
service = service.replace('User=root2', 'User=' + user).replace('Group=root2', 'Group=' + group)
service = service.replace('/var/www/pooltabletracker.com/venv/bin/python', interpreter)
Path('/etc/systemd/system/pooltracker-po-email.service').write_text(service)
PY
chmod 644 /etc/systemd/system/pooltracker-po-email.service
install -m 644 "$script_dir/pooltracker-po-email.timer" /etc/systemd/system/pooltracker-po-email.timer
systemctl daemon-reload
systemctl enable --now pooltracker-po-email.timer
systemctl list-timers pooltracker-po-email.timer --all
