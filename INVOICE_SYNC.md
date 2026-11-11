# Automatic invoice sync on pooltabletracker.com

The Droplet runs a systemd job every five minutes, even when the website is
closed. The job reads the Orders tab through the Google Sheets API and commits
one validated snapshot to SQLite. It does not write to Google Sheets.

New dated columns become invoices. Unsaved drafts receive current quantities
and references. Saved invoices keep their quantities, prices, addresses and
notes until someone reviews a sheet change. The page offers Apply sheet changes
or Keep saved invoice. Applying a change preserves each SKU's saved price;
new SKUs remain unpriced until entered. Removed columns do not delete invoices.

Use Delete on an Upcoming invoices card to remove a completed invoice from the
active list. Deleted invoices retain their saved contents and sync identity,
so the sheet does not recreate them. Restore them from the collapsed Deleted
invoices section if needed. Restoring checks the latest imported quantities;
saved invoices still require review when those quantities have changed.
Saving a priced invoice also saves its ordered SKU prices for other unsaved
invoices. Existing saved invoices keep their individual prices.

Invoice references are allocated from INV-391 for the 13 November 2026
collection, then sequentially for later collections in date order. Existing
non-placeholder references are preserved and their numbers are reserved.
Allocation happens once under the SQLite write lock. Subsequent syncs, moved
columns, deletion and restoration do not renumber invoices or reuse numbers.
An order inserted later receives the next unused number; existing invoices
keep their numbers. The Google Sheet itself remains read-only.

Each invoice includes Delivery (Per trip): three lorries at GBP 210 per trip,
GBP 630 before VAT. This fixed service charge is displayed after the products
and before VAT, and is included once in the taxable subtotal and grand total.
It does not change table quantities, SKU prices, or inventory counts. Delivery
labels include the year, for example Delivery Date 13/11/26 - INV-391.

Custom invoice items can be added with a description, whole-number quantity,
and unit cost. They are stored with that invoice, use its VAT rate and appear
before delivery in the printed invoice. They survive sheet sync, change review,
deletion and restoration. They do not affect table quantities, inventory or the
shared SKU price list. A blank custom price can be saved as an unpriced draft;
all item prices must be complete before printing.

## 1. Google connection

The account is `admin-305@pooltracker-backup.iam.gserviceaccount.com`.
Enable the Google Sheets API in its Cloud project and share this spreadsheet
with that address as Viewer:

https://docs.google.com/spreadsheets/d/1YTJ4_XkldQtqyfp4McNPXbO7Z9NyIjZCFHCBP9yCyvA/edit

Use the service-account JSON key, whose `type` is `service_account`.
An OAuth web-client JSON file cannot authenticate this background job.
Keep the key outside the checkout and Git. The installer places it at
`/etc/pooltracker-invoice-sync/google-service-account.json`, readable by the
database owner only. The connection requests the `spreadsheets.readonly` scope.
This is separate from the existing rclone database-backup connection.

## 2. Deploy the code

Before deploying, take a database backup using the existing backup script:

```bash
cd /var/www/pooltabletracker.com
sudo -u root2 /usr/bin/python3 backup_database.py --database /var/www/pooltabletracker.com/pool_table_tracker.db --output-dir /var/www/pooltabletracker.com/backups --local-only
```

Deploy the repository changes through the normal website deployment. The web
process must load the updated Flask code. No web-service restart command is
assumed here because its actual service name is not recorded in the repository.

The sync files are `invoice_sheet_sync.py`, `sync_invoice_orders.py`, the updated
Flask app and template, and `deploy/invoice-sync/`. Install the updated requirements
in the app environment. The new dependency is `google-auth==2.62.2`; Python 3.10+
is required. The source snapshot JSON remains the initial fallback for first run.

## 3. Transfer the key to the Droplet

From PowerShell on the computer where the key was downloaded, replace the SSH
user and IP with the same values used to access the Droplet:

```powershell
scp "C:\Users\Sales\Downloads\pooltracker-backup-163898d09d69.json" YOUR_SSH_USER@YOUR_DROPLET_IP:~/pooltracker-invoice-sync-key.json
```

Alternatively, place the key at its final path using the DigitalOcean console.
Do not place it under `/var/www/pooltabletracker.com` or in a public web folder.

## 4. Install and test the schedule

In the Droplet terminal, after deploying the files and transferring the key:

```bash
cd /var/www/pooltabletracker.com
sudo bash deploy/invoice-sync/install.sh "$HOME/pooltracker-invoice-sync-key.json"
```

If the key is already at its final `/etc` location, omit the last argument.

The installer finds the app's `venv`, `.venv`, or `env` interpreter (or a usable
system Python), uses the actual SQLite database owner, makes a verified local
backup, validates the key type and account email, and tests read-only Google
access before running a real sync. It enables the timer only after that sync
succeeds. The existing backup job is unchanged.

Refresh Invoice Generator and check Last synced. If the web process runs as
a different user from the database owner, the timer still runs as the database
owner. To enable the page's Sync now button too, arrange read access to the
credentials for the actual web-service account without making the file public.

Inspect the service and schedule:

```bash
sudo systemctl list-timers pooltracker-invoice-sync.timer --all
sudo journalctl -u pooltracker-invoice-sync.service -n 30 --no-pager
```

A completed oneshot service can show `inactive (dead)`; a successful run logs
`Invoice sheet sync completed` and exits with status zero. The timer runs at
minutes 00, 05, 10, etc. UTC, and catches up once after downtime.

## Order identity and source layout

The parser finds the Model / Sku header, the month-heading row immediately above
it, the table-count and invoice-reference rows immediately below it, and the
Total row beneath the products. Each SKU must be unique and quantities must be
non-negative whole numbers. Future columns without dates are skipped only if
they contain no ordered quantities. A malformed or incomplete sheet aborts
the update; the last successful data remains available.

By default, orders are matched by their collection date and the season in the
spreadsheet title (for example 26/27, August through March). Column letters are
display locations, not identities, so moving or inserting columns is supported.
Keep the title's season accurate. Use a new connection/database import plan for
a different workbook rather than changing the configured spreadsheet blindly.

For duplicate collection dates, or if collection dates need to change, add an
`Order ID` row directly below the invoice-reference row with a permanent unique
value in every dated order column. The first sync adopts these IDs for existing
dates. Let it complete before changing dates; thereafter IDs remain stable when
columns move or dates change. Do not reuse or edit an existing Order ID.
Ambiguous date/ID replacements stop the sync for review rather than matching
invoices by column position.

The page shows pending changes and the last sync failure; it warns after 15
minutes without a successful sync. Reloading shows new server data. A form
opened before a background quantity change must be reviewed before saving.

Pause the schedule with:

```bash
sudo systemctl disable --now pooltracker-invoice-sync.timer
```

Implementation references: [Google Sheets reads](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/get),
[Google read-only scopes](https://developers.google.com/workspace/sheets/api/scopes), and
[service-account access](https://developers.google.com/workspace/guides/create-credentials#access-google-workspace-files-directly-with-a-service-account).
