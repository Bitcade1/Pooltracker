# Hourly Google Drive database backups

The live database is `/var/www/pooltabletracker.com/pool_table_tracker.db` on
DigitalOcean. Its confirmed owner is `root2:root2`, and the backup service runs
as `root2`. If the database owner changes later, update the service's `User`
and `Group`, backup directory ownership, and `sudo -u` commands together.

`backup_database.py` takes a consistent SQLite snapshot while the app is
running, checks its integrity, compresses it, and writes SHA-256 checksums.
Each run gets a unique UTC timestamp folder containing `pool_table_tracker.db.gz`
and `manifest.json`. It keeps that folder locally and uploads it to
`gdrive:PoolTrackerBackups/<timestamp-folder>`. Upload success requires a
download verification of the uploaded files.

The job runs hourly, including when no one has the website open. The timer
catches up once after a server outage. Failures are recorded in the system
journal and return a failing service status. Completed local backups remain
available after upload failures. Backups accumulate locally and in Drive;
choose a retention period once the schedule is working and monitor free space.

The SQLite snapshot includes the database, rather than external invoice files,
images, or JSON settings stored elsewhere in the project.

## 1. Install the backup tools and files on DigitalOcean

From the deployed project folder, after these files have reached the server:

```bash
cd /var/www/pooltabletracker.com
sudo apt-get update
sudo apt-get install -y python3 rclone
sudo install -d -m 755 /opt/pooltracker-backup
sudo install -m 755 backup_database.py /opt/pooltracker-backup/backup_database.py
sudo install -d -o root2 -g root2 -m 700 /var/lib/pooltracker-backups
sudo install -d -o root2 -g root2 -m 700 /var/lib/pooltracker-backups/snapshots
sudo install -m 600 deploy/backup/pooltracker-backup.env.example /etc/pooltracker-backup.env
sudo install -m 644 deploy/backup/pooltracker-backup.service /etc/systemd/system/pooltracker-backup.service
sudo install -m 644 deploy/backup/pooltracker-backupmd/system/pooltracker-backup.timer
```

Check the account has access to the live database and its SQLite sidecar files:

```bash
sudo -u root2 test -r /var/www/pooltabletracker.com/pool_table_tracker.db
```

Use the app's existing account instead of changing the live database's
permissions. Review `/etc/pooltracker-backup.env` and the service's `User` and
`Group` before continuing. The script itself needs only Python's standard
library, so it does not need the Flask virtual environment.

## 2. Connect Google Drive once

Create your own Google OAuth desktop client with the Google Drive API enabled,
following [rclone's client setup guide](https://rclone.org/drive/#making-your-own-client-id).
Use a production OAuth app, or an internal app for your Google Workspace
organisation. An external OAuth app left in Testing can receive refresh tokens
that expire after seven days; see [Google's token expiry documentation](https://developers.google.com/identity/protocols/oauth2#expiration).

On the server:

```bash
sudo -u root2 rclone --config /var/lib/pooltracker-backups/rclone.conf config
```

Create a remote named `gdrive`, choose Google Drive, and provide your OAuth
client ID and secret. Choose the `drive.file` scope (access to files created
by rclone). Let rclone create `PoolTrackerBackups`; this scope cannot access a
folder created separately in the Drive website. See the
[Drive scope documentation](https://rclone.org/drive/#drivefile).

When prompted for browser authentication, choose **No** on the server. Follow
the displayed authorization command on your own computer, using the same
rclone version, then paste the result into the server's SSH terminal.
The [headless setup guide](https://rclone.org/remote_setup/) explains this step.
Keep client secrets, authorization tokens, and `rclone.conf` out of Git and
chat messages.

Protect the saved configuration:

```bash
sudo chmod 600 /var/lib/pooltracker-backups/rclone.conf
```

## 3. Test a real upload before enabling the timer

```bash
sudo systemctl daemon-reload
sudo systemctl start pooltracker-backup.service
sudo systemctl status pooltracker-backup.service --no-pager
sudo journalctl -u pooltracker-backup.service -n 30 --no-pager
```

A successful oneshot service may show `inactive (dead)` afterwards. Look for
`status=0/SUCCESS` and the log line `Google Drive backup uploaded and verified`.
Open Google Drive and confirm the new timestamp folder contains both files.

If this test fails, correct the path, account permissions, or Google connection
before enabling the timer. The completed local copy stays in
`/var/lib/pooltracker-backups/snapshots`.

## 4. Enable hourly backups

```bash
sudo systemctl enable --now pooltracker-backup.timer
sudo systemctl list-timers pooltracker-backup.timer --all
```

The timer fires every hour at minute zero UTC, with up to one minute of jitter.
The interval remains hourly during UK daylight saving changes. The next
scheduled run is displayed by `list-timers`.

After a later update to the backup script, reinstall it into
`/opt/pooltracker-backup` using the same `install` command. It is kept outside
the website folder so deployments do not replace its credentials or snapshots.

To pause automatic backups:

```bash
sudo systemctl disable --now pooltracker-backup.timer
```

## Local snapshot test without Google Drive

This also works on Windows with `python` in place of `python3`:

```bash
python3 backup_database.py --database pool_table_tracker.db --output-dir backups --local-only
```

This tests the local database in the current folder, rather than the live
DigitalOcean database. The script fails for a missing, empty, or invalid
database and does not create a replacement empty database.

## Recovery check

Download a timestamp folder from Google Drive, including its `manifest.json`.
Run this in that downloaded folder to verify and unpack a copy:

```bash
python3 - <<'PY'
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing

manifest = json.loads(Path('manifest.json').read_text())
archive = Path('pool_table_tracker.db.gz')
def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

if checksum(archive) != manifest['archive_sha256']:
    raise SystemExit('Archive checksum mismatch')
restored = Path('restored_pool_table_tracker.db')
with gzip.open(archive, 'rb') as source, restored.open('xb') as destination:
    shutil.copyfileobj(source, destination)
if checksum(restored) != manifest['database_sha256']:
    raise SystemExit('Database checksum mismatch')
with closing(sqlite3.connect(restored.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise SystemExit('Database integrity check failed')
print('Verified recovery copy:', restored)
PY
```

The recovery check writes a separate file and refuses to overwrite an existing
recovery copy. Before replacing the live database, stop all app processes and
the backup timer, preserve the current database with its WAL/SHM files, and use
the verified recovery copy. Confirm the actual Flask service name before
performing that replacement; it is not recorded in this repository.

Implementation references:
[SQLite online backups](https://www.sqlite.org/backup.html),
[Python SQLite backup API](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup),
[rclone copy](https://rclone.org/commands/rclone_copy/), and
[rclone verification](https://rclone.org/commands/rclone_check/).
