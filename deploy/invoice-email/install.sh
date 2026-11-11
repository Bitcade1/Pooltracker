#!/usr/bin/env bash
# Configure Microsoft 365 sending on the existing DigitalOcean Droplet.
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo 'Run this installer with sudo.' >&2
    exit 1
fi
app_dir=/var/www/pooltabletracker.com
database="$app_dir/pool_table_tracker.db"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
test -s "$database"
test -f "$app_dir/invoice_email.py"
db_user="$(stat -c '%U' "$database")"
db_group="$(stat -c '%G' "$database")"
app_python=''
for candidate in "$app_dir/venv/bin/python" "$app_dir/.venv/bin/python" "$app_dir/env/bin/python"; do
    if [ -x "$candidate" ] && "$candidate" -c 'import sys, flask, flask_sqlalchemy; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
        app_python="$candidate"
        break
    fi
done
if [ -z "$app_python" ]; then
    echo 'Could not find the app virtual environment (Python 3.10+ required).' >&2
    exit 1
fi

# Prompt for the secret through getpass, never as a command-line argument.
"$app_python" "$script_dir/configure.py"
install -d -o "$db_user" -g "$db_group" -m 700 /etc/pooltracker-invoice-email /var/lib/pooltracker-invoice-email
chown "$db_user:$db_group" /etc/pooltracker-invoice-email/config.json
chmod 600 /etc/pooltracker-invoice-email/config.json
cd "$app_dir"
runuser -u "$db_user" -- "$app_python" backup_database.py --database "$database" --output-dir "$app_dir/backups" --local-only
"$app_python" -m pip install 'msal==1.39.0' 'playwright==1.63.0'
install -d -m 755 /opt/pooltracker-invoice-email/browsers
PLAYWRIGHT_BROWSERS_PATH=/opt/pooltracker-invoice-email/browsers "$app_python" -m playwright install --with-deps chromium

# Verify PDF creation as the same user as the app/database, without making any email request.
runuser -u "$db_user" -- env PLAYWRIGHT_BROWSERS_PATH=/opt/pooltracker-invoice-email/browsers "$app_python" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as driver:
    with driver.chromium.launch(headless=True) as browser:
        page = browser.new_page()
        page.set_content('<html><body>Invoice PDF installation check</body></html>')
        assert page.pdf().startswith(b'%PDF-')
print('Chromium PDF check passed. No email was sent.')
PY

# Only create the new history table. Existing invoice tables/data are untouched.
runuser -u "$db_user" -- env POOLTRACKER_DATABASE_PATH="$database" "$app_python" - <<'PY'
from flask_app import app, db, InvoiceEmailAttempt
with app.app_context():
    InvoiceEmailAttempt.__table__.create(db.engine, checkfirst=True)
print('Invoice email history table is ready.')
PY
echo 'Restart your Flask/Gunicorn web service, sign in again, and open a saved invoice > Email invoice > Connect Microsoft 365.'
echo 'The private session key changes the old default: existing tracker sessions will need to sign in again.'
