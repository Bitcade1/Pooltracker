import os
import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask

import flask_app as tracker


class PodExtraTimeTests(unittest.TestCase):
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

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.cleanup_database)
        tracker.db.create_all()
        self.now = datetime(2026, 11, 4, 9)
        now_patch = patch.object(tracker, 'london_now', return_value=self.now)
        now_patch.start()
        self.addCleanup(now_patch.stop)
        holidays_patch = patch.object(tracker, 'fetch_uk_bank_holidays', return_value={})
        holidays_patch.start()
        self.addCleanup(holidays_patch.stop)
        self.client = self.app.test_client()
        with self.client.session_transaction() as login:
            login['worker'] = 'Manager'

    def cleanup_database(self):
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def post(self, minutes):
        return self.client.post('/pods', data={
            'action': 'add_pod_extra_minutes', 'extra_minutes': minutes,
        })

    def dashboard(self):
        response = self.client.get('/pod_dashboard')
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_dashboard_has_monthly_seven_and_a_half_hour_target_without_pod_goal(self):
        html = self.dashboard()
        self.assertIn('Nick - Extra Job Time', html)
        self.assertIn('0.00 / 7.50 hours', html)
        self.assertIn('450 minutes to go', html)
        self.assertIn('/pods#pod-extra-time', html)

    def test_minutes_persist_with_author_and_leave_production_stock_unchanged(self):
        response = self.post('90')
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.location.endswith('/pods#pod-extra-time'))
        tracker.db.session.remove()
        entry = tracker.CushionExtraTimeLog.query.one()
        self.assertEqual(('Nick', 90, 'Manager', self.now), (
            entry.worker_name, entry.minutes, entry.added_by, entry.recorded_at,
        ))
        self.assertEqual(0, tracker.CompletedPods.query.count())
        self.assertEqual(0, tracker.PrintedPartsCount.query.count())
        self.assertEqual(0, tracker.TableStock.query.count())
        html = self.dashboard()
        self.assertIn('1.50 / 7.50 hours', html)
        self.assertIn('360 minutes to go', html)
        response = self.client.get('/pods')
        self.assertEqual(200, response.status_code)
        self.assertIn(b'04/11/2026 09:00', response.data)
        self.assertIn(b'<td>90</td><td>Manager</td>', response.data)
        self.assertIn(b'<details>', response.data)
        self.assertNotIn(b'<details open', response.data)

    def test_invalid_minutes_do_not_create_logs(self):
        for minutes in ('0', '-1', '1.5', '', 'bad'):
            with self.subTest(minutes=minutes):
                self.assertEqual(302, self.post(minutes).status_code)
                self.assertEqual(0, tracker.CushionExtraTimeLog.query.count())

    def test_login_is_required_to_record_time(self):
        with self.client.session_transaction() as login:
            login.pop('worker')
        response = self.post('30')
        self.assertEqual(302, response.status_code)
        self.assertIn('/login', response.location)
        self.assertEqual(0, tracker.CushionExtraTimeLog.query.count())

    def test_other_workers_and_months_do_not_count_toward_nicks_target(self):
        tracker.db.session.add_all([
            tracker.CushionExtraTimeLog(worker_name=worker, minutes=minutes,
                                       added_by='Manager', recorded_at=recorded_at)
            for worker, minutes, recorded_at in [
                ('Katie', 90, self.now),
                ('Nick', 60, datetime(2026, 10, 31, 16)),
                ('Nick', 30, datetime(2026, 12, 1, 9)),
            ]
        ])
        tracker.db.session.commit()
        self.post('30')
        self.assertIn('0.50 / 7.50 hours', self.dashboard())
        katie = tracker.cushion_extra_time_progress('Katie', 2026, 11)
        self.assertEqual(90, katie['completed_minutes'])
        self.assertEqual(0, katie['target_minutes'])
        with patch.object(tracker, 'london_now', return_value=datetime(2027, 1, 4, 9)):
            self.assertIn('0.00 / 7.50 hours', self.dashboard())

    def test_completed_target_caps_progress(self):
        self.post('480')
        html = self.dashboard()
        self.assertIn('8.00 / 7.50 hours', html)
        self.assertIn('Goal reached', html)
        self.assertIn('style="width: 100%;"', html)
        self.assertNotIn('-30 minutes to go', html)


if __name__ == '__main__':
    unittest.main()
