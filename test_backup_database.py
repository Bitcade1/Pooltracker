from contextlib import closing
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup_database as backups


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix='pooltracker-backup-test-')
        self.addCleanup(self.temp_dir.cleanup)
        self.directory = Path(self.temp_dir.name)
        self.database = self.directory / 'live database.db'
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('CREATE TABLE inventory (part TEXT, count INTEGER)')
            connection.execute("INSERT INTO inventory VALUES ('7ft Long', 8)")
            connection.commit()
        self.output = self.directory / 'backups'

    def restore(self, backup):
        restored = self.directory / 'restored.db'
        with gzip.open(backup / 'pool_table_tracker.db.gz', 'rb') as source, restored.open('xb') as destination:
            shutil.copyfileobj(source, destination)
        return restored

    def test_backup_can_be_restored_and_hashes_match(self):
        source_hash = backups.sha256_file(self.database)
        backup = backups.create_backup(self.database, self.output)
        manifest = json.loads((backup / 'manifest.json').read_text())
        restored = self.restore(backup)
        self.assertEqual(manifest['archive_sha256'], backups.sha256_file(backup / manifest['archive']))
        self.assertEqual(manifest['database_sha256'], backups.sha256_file(restored))
        self.assertEqual(source_hash, backups.sha256_file(self.database))
        with closing(sqlite3.connect(restored)) as connection:
            self.assertEqual([('7ft Long', 8)], connection.execute('SELECT * FROM inventory').fetchall())
            self.assertEqual([('ok',)], connection.execute('PRAGMA integrity_check').fetchall())
        self.assertEqual({'pool_table_tracker.db.gz', 'manifest.json'}, {path.name for path in backup.iterdir()})

    def test_committed_wal_data_is_backed_up_while_database_is_open(self):
        with closing(sqlite3.connect(self.database)) as live_connection:
            live_connection.execute('PRAGMA journal_mode=WAL')
            live_connection.execute("INSERT INTO inventory VALUES ('6ft Short', 16)")
            live_connection.commit()
            self.assertTrue(Path(str(self.database) + '-wal').is_file())
            backup = backups.create_backup(self.database, self.output)
            restored = self.restore(backup)
            with closing(sqlite3.connect(restored)) as connection:
                self.assertEqual(2, connection.execute('SELECT count(*) FROM inventory').fetchone()[0])

    def test_missing_empty_and_invalid_sources_fail_without_creating_a_backup(self):
        empty = self.directory / 'empty.db'
        empty.touch()
        invalid = self.directory / 'invalid.db'
        invalid.write_text('not a database')
        for source in [self.directory / 'missing.db', empty, invalid]:
            with self.subTest(source=source):
                with self.assertRaises((OSError, ValueError, sqlite3.Error)):
                    backups.create_backup(source, self.output)
                self.assertFalse(any(self.output.iterdir()) if self.output.exists() else False)
        self.assertFalse((self.directory / 'missing.db').exists())

    def test_backups_have_unique_names_and_keep_previous_copies(self):
        first = backups.create_backup(self.database, self.output)
        second = backups.create_backup(self.database, self.output)
        self.assertNotEqual(first, second)
        self.assertTrue((first / 'manifest.json').is_file())
        self.assertTrue((second / 'manifest.json').is_file())

    def test_upload_copies_then_verifies_only_the_completed_backup(self):
        backup = backups.create_backup(self.database, self.output)
        config = self.directory / 'rclone.conf'
        config.touch()
        with patch.object(backups.shutil, 'which', return_value='rclone'), patch.object(backups.subprocess, 'run') as run:
            destination = backups.upload_backup(backup, 'gdrive:PoolTrackerBackups', config=config)
        self.assertEqual('gdrive:PoolTrackerBackups/' + backup.name, destination)
        copy, check = [call.args[0] for call in run.call_args_list]
        self.assertIn('copy', copy)
        self.assertIn('--immutable', copy)
        self.assertIn(str(backup), copy)
        self.assertIn('check', check)
        self.assertIn('--download', check)
        self.assertIn('--one-way', check)
        self.assertNotIn('sync', copy)
        self.assertTrue(run.call_args_list[0].kwargs['check'])

    def test_upload_failure_is_reported_and_local_backup_is_retained(self):
        with patch.object(backups.shutil, 'which', return_value='rclone'), patch.object(
            backups.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['rclone', 'copy'])
        ):
            result = backups.main(['--database', str(self.database), '--output-dir', str(self.output), '--remote', 'gdrive:PoolTrackerBackups'])
        self.assertEqual(1, result)
        self.assertEqual(1, len(list(self.output.glob('*/manifest.json'))))

    def test_remote_verification_failure_is_reported(self):
        with patch.object(backups.shutil, 'which', return_value='rclone'), patch.object(
            backups.subprocess, 'run', side_effect=[None, subprocess.CalledProcessError(1, ['rclone', 'check'])]
        ):
            self.assertEqual(1, backups.main([
                '--database', str(self.database), '--output-dir', str(self.output), '--remote', 'gdrive:PoolTrackerBackups',
            ]))
        self.assertEqual(1, len(list(self.output.glob('*/manifest.json'))))

    def test_local_only_does_not_upload(self):
        with patch.object(backups, 'upload_backup') as upload:
            self.assertEqual(0, backups.main(['--database', str(self.database), '--output-dir', str(self.output), '--local-only']))
            upload.assert_not_called()

    def test_backup_timeout_leaves_no_completed_backup(self):
        with self.assertRaises(TimeoutError):
            backups.create_backup(self.database, self.output, timeout=-1)
        self.assertEqual([], list(self.output.iterdir()))


if __name__ == '__main__':
    unittest.main()
