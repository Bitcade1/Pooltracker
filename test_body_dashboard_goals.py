import os
import unittest
from datetime import date, datetime
from unittest.mock import patch

from flask import Flask

import flask_app as tracker


class BodyDashboardGoalTests(unittest.TestCase):
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
        tracker.db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def goal(self, worker, target=60, month=10, active=True):
        tracker.db.session.add(tracker.BonusGoal(
            area='bodies', worker_name=worker, target_count=target,
            year=2026, month=month, active=active,
        ))

    def bodies(self, worker, count, month=10):
        serial_start = tracker.CompletedTable.query.count() + 1000
        tracker.db.session.add_all([
            tracker.CompletedTable(
                worker=worker, serial_number=str(serial_start + index),
                start_time='09:00', finish_time='10:00', date=date(2026, month, 15),
            )
            for index in range(count)
        ])
        tracker.db.session.flush()

    def dashboard(self):
        tracker.db.session.commit()
        with patch.object(tracker, 'london_now', return_value=datetime(2026, 10, 29, 12)), \
                patch.object(tracker, 'fetch_uk_bank_holidays', return_value={}):
            response = self.client.get('/body_dashboard')
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_team_goal_hit_when_one_worker_is_three_short(self):
        self.goal('Jack B')
        self.goal('Alice')
        self.bodies('Jack B', 63)
        self.bodies('Alice', 57)
        html = self.dashboard()
        self.assertIn('Combined goal of 120 hit', html)
        self.assertIn('Combined pace needed: 0.00 tables/day', html)
        self.assertIn('3 to go', html)
        self.assertNotIn('3 needed for combined goal', html)

    def test_remaining_uses_month_total_including_other_workers(self):
        self.goal('Jack B')
        self.goal('Alice')
        self.bodies('Jack B', 63)
        self.bodies('Alice', 50)
        self.bodies('Other worker', 4)
        self.bodies('Jack B', 20, month=9)
        html = self.dashboard()
        self.assertIn('3 needed for combined goal of 120', html)
        self.assertNotIn('Combined pace needed: 0.00', html)

    def test_next_month_worker_bonus_does_not_change_monthly_team_goal(self):
        self.goal('Jack B')
        self.goal('Alice')
        self.goal('Jack B', target=80, month=11)
        self.bodies('Jack B', 63)
        self.bodies('Alice', 57)
        html = self.dashboard()
        self.assertIn('Combined goal of 120 hit', html)
        self.assertIn('Next bonus (November 2026): 77 to go', html)
        self.assertIn('October 2026; UK bank holidays excluded', html)

    def test_carryover_stays_on_individual_bonus(self):
        self.goal('Jack B', month=9)
        self.goal('Jack B')
        self.goal('Alice')
        self.bodies('Jack B', 70, month=9)
        self.bodies('Jack B', 50)
        self.bodies('Alice', 60)
        html = self.dashboard()
        self.assertIn('10 needed for combined goal of 120', html)
        self.assertIn('60 / 60', html)

    def test_team_target_keeps_james_and_existing_goal_exclusions(self):
        self.goal('Jack B', target=90)
        self.goal('James', target=30)
        self.goal('Tom F', target=50)
        self.goal('Inactive', target=50, active=False)
        self.bodies('Jack B', 125)
        html = self.dashboard()
        self.assertIn('Combined goal of 120 hit', html)
        self.assertNotIn('needed for combined goal', html)

    def test_no_goals_hides_combined_message(self):
        self.bodies('Jack B', 120)
        html = self.dashboard()
        self.assertNotIn('Combined goal', html)
        self.assertNotIn('Combined pace needed', html)


if __name__ == '__main__':
    unittest.main()
