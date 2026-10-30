import os
import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask

import flask_app as tracker


class CushionCreditReassignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__, template_folder=os.path.join(tracker.basedir, 'templates'))
        cls.app.config.update(
            TESTING=True, SECRET_KEY='test-only',
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        tracker.db.init_app(cls.app)
        cls.app.url_map = tracker.app.url_map
        cls.app.view_functions.update(tracker.app.view_functions)
        cls.app.jinja_env.filters.update(tracker.app.jinja_env.filters)
        cls.app.after_request(tracker.prevent_stale_count_pages)

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        tracker.db.create_all()
        self.addCleanup(self.cleanup_database)
        now_patch = patch.object(tracker, 'london_now', return_value=datetime(2026, 10, 30, 12))
        now_patch.start()
        self.addCleanup(now_patch.stop)
        tracker.db.session.add_all([tracker.Worker(name=name) for name in ['Katie', 'Jack B', 'Manager']])
        tracker.db.session.add(tracker.BonusGoal(
            area='cushions', worker_name='Katie', target_count=140, year=2026, month=10,
        ))
        self.katie_sets = self.completed_sets('Katie', 130)
        self.jack_sets = self.completed_sets('Jack B', 11)
        self.old_sets = self.completed_sets('Jack B', 1, month=9)
        tracker.db.session.add_all([
            tracker.TableStock(type='cushion_6ft', count=70),
            tracker.TableStock(type='cushion_7ft', count=71),
            tracker.new_printed_parts_snapshot('Silica Gel', 500),
        ])
        self.bundle_log = tracker.CushionWorkflowLog(
            action_type='add', stage_key='bundle', stage_label='Bundle',
            size_label='7ft', worker='Jack B', delta=11, count_after=71,
            note='Bundled 11 completed cushion set(s)',
        )
        tracker.db.session.add(self.bundle_log)
        tracker.db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as login:
            login['worker'] = 'Manager'
        self.query = '?period=month&worker=Jack+B'
        self.history_url = '/cushion_history' + self.query
        self.post_url = '/cushion_history/reassign' + self.query
        response = self.client.get(self.history_url)
        self.assertEqual(200, response.status_code)
        with self.client.session_transaction() as login:
            self.token = login['cushion_credit_token']

    def cleanup_database(self):
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def completed_sets(self, worker, count, month=10):
        rows = [tracker.CushionCompletedSet(
            size_label='7ft' if index % 2 else '6ft', worker=worker,
            stock_type='cushion_7ft' if index % 2 else 'cushion_6ft',
            stock_count_after=index + 1, estimated_seconds=120,
            completed_at=datetime(2026, month, 28, 12),
        ) for index in range(count)]
        tracker.db.session.add_all(rows)
        tracker.db.session.flush()
        return rows

    def post(self, ids=None, **extra):
        data = {
            'completed_ids': [str(row.id) for row in self.jack_sets[:10]] if ids is None else ids,
            'target_worker': 'Katie', 'credit_token': self.token,
        }
        data.update(extra)
        return self.client.post(self.post_url, data=data, follow_redirects=True)

    def inventory_state(self):
        return (
            [(row.id, row.type, row.count) for row in tracker.TableStock.query.order_by(tracker.TableStock.id)],
            [(row.id, row.part_name, row.count) for row in tracker.PrintedPartsCount.query.order_by(tracker.PrintedPartsCount.id)],
            tracker.TableStockLog.query.count(),
            tracker.CushionCompletedSet.query.count(),
        )

    def assert_no_reassignment(self):
        self.assertEqual(130, tracker.bonus_goal_actual_count('cushions', 'Katie', 2026, 10))
        self.assertEqual(11, tracker.bonus_goal_actual_count('cushions', 'Jack B', 2026, 10))
        self.assertEqual(0, tracker.CushionWorkflowLog.query.filter_by(action_type='reassign').count())

    def test_selected_ten_move_to_katie_and_goal_hits_without_inventory_changes(self):
        before_inventory = self.inventory_state()
        before_details = [(row.id, row.completed_at, row.estimated_seconds, row.stock_count_after)
                          for row in self.jack_sets]
        response = self.post()
        self.assertEqual(200, response.status_code)
        self.assertIn(b'Reassigned 10 completed set(s) from Jack B to Katie', response.data)
        self.assertEqual(140, tracker.bonus_goal_actual_count('cushions', 'Katie', 2026, 10))
        self.assertEqual(1, tracker.bonus_goal_actual_count('cushions', 'Jack B', 2026, 10))
        self.assertTrue(tracker.bonus_goal_progress('cushions', 2026, 10)[0]['target_hit'])
        self.assertEqual(before_inventory, self.inventory_state())
        self.assertEqual(before_details, [(row.id, row.completed_at, row.estimated_seconds, row.stock_count_after)
                                         for row in self.jack_sets])
        self.assertEqual('Jack B', self.old_sets[0].worker)
        self.assertEqual('Jack B', self.bundle_log.worker)
        self.assertEqual(11, self.bundle_log.delta)
        audits = tracker.CushionWorkflowLog.query.filter_by(action_type='reassign').all()
        self.assertEqual(10, len(audits))
        for audit in audits:
            self.assertEqual('Manager', audit.worker)
            self.assertEqual(0, audit.delta)
            self.assertIn('credit changed from Jack B to Katie', audit.note)

    def test_missing_invalid_or_out_of_filter_ids_change_nothing(self):
        for ids in [[], ['bad-id'], ['-1'], ['999999'],
                    [str(self.jack_sets[0].id), str(self.katie_sets[0].id)],
                    [str(self.jack_sets[0].id), str(self.old_sets[0].id)]]:
            with self.subTest(ids=ids):
                self.post(ids=ids)
                self.assert_no_reassignment()

    def test_invalid_or_same_destination_changes_nothing(self):
        for target in ['', 'Missing worker', 'Jack B']:
            with self.subTest(target=target):
                self.post(target_worker=target)
                self.assert_no_reassignment()

    def test_repeated_submit_does_not_move_or_log_sets_twice(self):
        self.post()
        response = self.post()
        self.assertIn(b'no longer match these filters', response.data)
        self.assertEqual(140, tracker.bonus_goal_actual_count('cushions', 'Katie', 2026, 10))
        self.assertEqual(10, tracker.CushionWorkflowLog.query.filter_by(action_type='reassign').count())

    def test_duplicate_ids_only_reassign_once(self):
        self.post(ids=[str(self.jack_sets[0].id)] * 2)
        self.assertEqual(131, tracker.bonus_goal_actual_count('cushions', 'Katie', 2026, 10))
        self.assertEqual(1, tracker.CushionWorkflowLog.query.filter_by(action_type='reassign').count())

    def test_login_and_form_token_required(self):
        for token in ['', 'invalid']:
            with self.subTest(token=token):
                response = self.post(credit_token=token)
                self.assertIn(b'Reload Cushion History', response.data)
                self.assert_no_reassignment()
        with self.client.session_transaction() as login:
            login.pop('worker')
        response = self.client.post(self.post_url, data={})
        self.assertIn('/login', response.location)
        self.assert_no_reassignment()

    def test_all_workers_filter_cannot_reassign(self):
        self.post_url = '/cushion_history/reassign?period=month'
        response = self.post()
        self.assertIn(b'Filter to the current worker', response.data)
        self.assert_no_reassignment()

    def test_history_and_csv_show_corrected_credit_and_audit(self):
        self.post()
        response = self.client.get('/cushion_history?period=month&worker=Katie')
        self.assertIn(b'140', response.data)
        self.assertIn(b'name="completed_ids"', response.data)
        self.assertIn(b'Choose a worker', response.data)
        response = self.client.get('/cushion_history/export.csv?period=month')
        self.assertIn(b'credit changed from Jack B to Katie', response.data)
        self.assertIn(b'Completed Cushion Sets', response.data)

    def test_database_failure_rolls_back_credit_and_audit_together(self):
        with patch.object(tracker.db.session, 'commit', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                self.post()
        self.assert_no_reassignment()


if __name__ == '__main__':
    unittest.main()
