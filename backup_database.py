"""Create a verified SQLite snapshot and upload it with rclone."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid


LOGGER = logging.getLogger(__name__)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(database, output_dir, timeout=120):
    """Snapshot the live DB, including committed WAL data, without Flask imports."""
    database = Path(database).expanduser().resolve(strict=True)
    if not database.is_file() or database.stat().st_size == 0:
        raise ValueError('The database must be an existing, non-empty SQLite file.')
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = datetime.now(timezone.utc)
    backup_id = started.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    deadline = time.monotonic() + timeout

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError('SQLite backup exceeded its time limit.')

    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=output_dir) as temp_dir:
        snapshot = Path(temp_dir) / 'snapshot.db'
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=10)) as source:
            if not source.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                raise ValueError('The database has no tables; check the live database path.')
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                integrity = destination.execute('PRAGMA integrity_check').fetchall()
                if integrity != [('ok',)]:
                    raise ValueError('The SQLite backup failed its integrity check.')

        archive = Path(temp_dir) / 'pool_table_tracker.db.gz'
        with snapshot.open('rb') as source, gzip.open(archive, 'wb', compresslevel=6) as destination:
            shutil.copyfileobj(source, destination)
        manifest = {
            'backup_id': backup_id,
            'created_at_utc': started.isoformat(),
            'source_database': database.name,
            'archive': archive.name,
            'archive_sha256': sha256_file(archive),
            'archive_bytes': archive.stat().st_size,
            'database_sha256': sha256_file(snapshot),
            'database_bytes': snapshot.stat().st_size,
            'sqlite_integrity': 'ok',
            'sqlite_version': sqlite3.sqlite_version,
        }
        manifest_path = Path(temp_dir) / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='ascii')
        snapshot.unlink()
        # Publish the complete local backup as one directory rename.
        backup_dir = output_dir / backup_id
        Path(temp_dir).rename(backup_dir)
    LOGGER.info('Verified local backup created: %s', backup_dir)
    return backup_dir


def upload_backup(backup_dir, remote, rclone='rclone', config=None):
    """Copy only this snapshot and confirm its uploaded contents match."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+:.+', remote) or '\n' in remote or '\r' in remote:
        raise ValueError('Use a named rclone remote and folder, such as gdrive:PoolTrackerBackups.')
    executable = shutil.which(str(rclone))
    if not executable:
        raise FileNotFoundError('rclone is not installed or is not on PATH.')
    command = [executable]
    if config:
        config = Path(config).expanduser().resolve(strict=True)
        command.extend(['--config', str(config)])
    backup_dir = Path(backup_dir).resolve(strict=True)
    destination = remote.rstrip('/') + '/' + backup_dir.name
    subprocess.run(command + [
        'copy', str(backup_dir), destination, '--immutable',
        '--retries', '3', '--low-level-retries', '5', '--log-level', 'NOTICE',
    ], check=True, timeout=300)
    subprocess.run(command + [
        'check', str(backup_dir), destination, '--download', '--one-way',
        '--log-level', 'NOTICE',
    ], check=True, timeout=300)
    LOGGER.info('Google Drive backup uploaded and verified: %s', destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=os.getenv('POOLTRACKER_BACKUP_DATABASE'))
    parser.add_argument('--output-dir', default=os.getenv('POOLTRACKER_BACKUP_DIR', str(Path(__file__).parent / 'backups')))
    parser.add_argument('--remote', default=os.getenv('POOLTRACKER_BACKUP_REMOTE'))
    parser.add_argument('--rclone', default=os.getenv('POOLTRACKER_BACKUP_RCLONE', 'rclone'))
    parser.add_argument('--rclone-config', default=os.getenv('POOLTRACKER_BACKUP_RCLONE_CONFIG'))
    parser.add_argument('--local-only', action='store_true', help='Test snapshot creation without uploading.')
    args = parser.parse_args(argv)
    if not args.database:
        parser.error('Set --database or POOLTRACKER_BACKUP_DATABASE to the live database file.')
    if not args.local_only and not args.remote:
        parser.error('Set --remote or POOLTRACKER_BACKUP_REMOTE, or use --local-only for a local test.')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        backup_dir = create_backup(args.database, args.output_dir)
        if args.local_only:
            LOGGER.info('Local-only test complete. Google Drive upload was not requested.')
        else:
            upload_backup(backup_dir, args.remote, args.rclone, args.rclone_config)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        LOGGER.error('Backup failed: %s. Any completed local backup is retained.', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
