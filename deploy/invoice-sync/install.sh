#!/usr/bin/env bash
# Run on the DigitalOcean Droplet after deploying the sync source files.
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo 'Run this installer with sudo.' >&2
    exit 1
fi

app_dir=/var/www/pooltabletracker.com
database="$app_dir/pool_table_tracker.db"
credentials=/etc/pooltracker-invoice-sync/google-service-account.json
uploaded_key="${1:-$credentials}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

test -s "$database"
test -s "$uploaded_key"
test -f "$app_dir/sync_invoice_orders.py"
db_user="$(stat -c '%U' "$database")"
db_group="$(stat -c '%G' "$database")"

app_python=''
for candidate in "$app_dir/venv/bin/python" "$app_dir/.venv/bin/python" "$app_dir/env/bin/python" /usr/bin/python3; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys, flask, flask_sqlalchemy; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
        app_python="$candidate"
        break
    fi
done
if [ -z "$app_python" ]; then
    echo 'Could not find the app Python environment (Python 3.10+, Flask and Flask-SQLAlchemy required).' >&2
    exit 1
fi

# Refuse an OAuth client file or a key for a different service account.
"$app_python" - "$uploaded_key" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8-sig') as source:
    data = json.load(source)
if (data.get('type') != 'service_account'
        or data.get('client_email') != 'admin-305@pooltracker-backup.iam.gserviceaccount.com'
        or not data.get('private_key')
        or data.get('token_uri') != 'https://oauth2.googleapis.com/token'):
    raise SystemExit('Expected the JSON key for admin-305@pooltracker-backup.iam.gserviceaccount.com.')
print('Verified the service-account key type and email.')
PY

cd "$app_dir"
# Preserve the live database before creating the additive sync-state table.
runuser -u "$db_user" -- "$app_python" backup_database.py --database "$database" --output-dir "$app_dir/backups" --local-only
"$app_python" -m pip install 'google-auth==2.62.2'
install -d -o "$db_user" -g "$db_group" -m 700 /etc/pooltracker-invoice-sync
if [ "$(realpath "$uploaded_key")" != "$(realpath -m "$credentials")" ]; then
    install -o "$db_user" -g "$db_group" -m 600 "$uploaded_key" "$credentials"
else
    chown "$db_user:$db_group" "$credentials"
    chmod 600 "$credentials"
fi

runuser -u "$db_user" -- env POOLTRACKER_SHEETS_CREDENTIALS="$credentials" \
    "$app_python" sync_invoice_orders.py --database "$database" --dry-run

# Generate the service with the verified interpreter and actual database owner.
"$app_python" - "$script_dir" "$app_python" "$db_user" "$db_group" <<'PY'
from pathlib import Path
import sys
directory, interpreter, user, group = sys.argv[1:]
service = (Path(directory) / 'pooltracker-invoice-sync.service').read_text()
service = service.replace('User=root2', 'User=' + user).replace('Group=root2', 'Group=' + group)
service = service.replace('/var/www/pooltabletracker.com/venv/bin/python', interpreter)
Path('/etc/systemd/system/pooltracker-invoice-sync.service').write_text(service)
Path('/etc/pooltracker-invoice-sync.env').write_text(
    'POOLTRACKER_DATABASE_PATH=/var/www/pooltabletracker.com/pool_table_tracker.db\n'
    'POOLTRACKER_SHEETS_CREDENTIALS=/etc/pooltracker-invoice-sync/google-service-account.json\n')
PY
chmod 600 /etc/pooltracker-invoice-sync.env
install -m 644 "$script_dir/pooltracker-invoice-sync.timer" /etc/systemd/system/pooltracker-invoice-sync.timer
chmod 644 /etc/systemd/system/pooltracker-invoice-sync.service
systemctl daemon-reload
systemctl start pooltracker-invoice-sync.service
# Only enable the schedule after a successful real sync.
systemctl enable --now pooltracker-invoice-sync.timer
systemctl list-timers pooltracker-invoice-sync.timer --all --no-pager
echo 'Invoice sync is installed. Check Last synced on the Invoice Generator page.'
