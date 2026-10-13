import os
import unittest
from datetime import datetime
from unittest.mock import patch

from flask import Flask

import flask_app as tracker
from api_routes import VALID_PARTS


class Cut36mmWoodTests(unittest.TestCase):
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
        self.client = self.app.test_client()
        with self.client.session_transaction() as login_session:
            login_session['worker'] = 'CNC Operator'

    def tearDown(self):
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def post(self, **data):
        return self.client.post(
            '/counting_cut_36mm_wood', data=data,
            headers={'Accept': 'application/json'},
        )

    def test_each_cnc_file_adds_its_confirmed_yield(self):
        expected = {'6_long': 0, '6_short': 0, '7_long': 0, '7_short': 0}
        for key, delta in [
            ('1', {'6_long': 8, '6_short': 3}),
            ('2', {'6_short': 16}),
            ('3', {'7_long': 8, '7_short': 2}),
            ('4', {'7_short': 16}),
        ]:
            with self.subTest(key=key):
                for part, amount in delta.items():
                    expected[part] += amount
                response = self.post(key_code=key)
                self.assertEqual(200, response.status_code)
                self.assertEqual(expected, response.json['counts'])
        self.assertEqual(6, len(response.json['log']))

    def test_remove_keys_remove_one_piece_and_never_reverse_whole_batches(self):
        for key in '1234':
            self.post(key_code=key)
        for key in '7890':
            self.assertEqual(200, self.post(key_code=key).status_code)
        self.assertEqual(
            {'6_long': 7, '6_short': 18, '7_long': 7, '7_short': 17},
            tracker.cut_36mm_wood_counts(),
        )

    def test_empty_stock_removal_is_rejected_without_a_log(self):
        for key in '7890':
            response = self.post(key_code=key)
            self.assertEqual(400, response.status_code)
            self.assertFalse(response.json['success'])
            self.assertEqual([], response.json['log'])
        self.assertEqual(0, tracker.PrintedPartsCount.query.count())

    def test_manual_add_remove_and_set_persist_across_page_visits(self):
        self.post(action='adjust', part_key='7_long', amount='25')
        self.post(action='adjust', part_key='7_long', amount='-3')
        response = self.post(action='set', part_key='6_short', amount='42')
        self.assertEqual(42, response.json['counts']['6_short'])
        self.assertEqual(22, response.json['counts']['7_long'])
        self.assertEqual(200, self.client.get('/counting_cut_36mm_wood').status_code)
        refreshed = self.client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
        self.assertEqual(response.json['counts'], refreshed.json['counts'])
        self.assertEqual('no-store', refreshed.headers['Cache-Control'])

    def test_invalid_inputs_and_over_removal_leave_stock_unchanged(self):
        self.post(key_code='3')
        before = tracker.cut_36mm_wood_counts()
        row_count = tracker.PrintedPartsCount.query.count()
        for data in [
            {'key_code': '5'}, {'key_code': 'bad', 'action': 'adjust', 'part_key': '7_long', 'amount': '5'},
            {'action': 'adjust', 'part_key': 'unknown', 'amount': '1'},
            {'action': 'adjust', 'part_key': '7_long', 'amount': '-9'},
            {'action': 'adjust', 'part_key': '7_long', 'amount': '0'},
            {'action': 'set', 'part_key': '7_long', 'amount': '-1'},
            {'action': 'set', 'part_key': '7_long', 'amount': '1.5'},
            {'action': 'adjust', 'part_key': '7_long', 'amount': ''},
            {'action': 'delete'},
        ]:
            with self.subTest(data=data):
                self.assertEqual(400, self.post(**data).status_code)
                self.assertEqual(before, tracker.cut_36mm_wood_counts())
                self.assertEqual(row_count, tracker.PrintedPartsCount.query.count())

    def test_page_seeds_only_missing_parts_and_hides_log_by_default(self):
        tracker.db.session.add(tracker.new_printed_parts_snapshot(tracker.CUT_36MM_WOOD_PARTS['6_long'], 12))
        tracker.db.session.commit()
        for _ in range(2):
            response = self.client.get('/counting_cut_36mm_wood')
            self.assertEqual(200, response.status_code)
        self.assertEqual(4, tracker.PrintedPartsCount.query.count())
        self.assertEqual(12, tracker.cut_36mm_wood_counts()['6_long'])
        self.assertIn(b'<details class="panel wood-log">', response.data)
        self.assertNotIn(b'<details class="panel wood-log" open', response.data)

    def test_log_orders_equal_timestamps_by_id_and_ignores_no_change(self):
        recorded_at = datetime(2026, 10, 13, 10, 30)
        with patch.object(tracker, 'london_now', return_value=recorded_at):
            self.post(action='adjust', part_key='6_long', amount='5')
            self.post(action='adjust', part_key='6_long', amount='-2')
            response = self.post(action='set', part_key='6_long', amount='3')
        self.assertEqual(3, response.json['counts']['6_long'])
        self.assertEqual([-2, 5], [entry['delta'] for entry in response.json['log']])

    def test_shortcuts_and_manual_forms_work_without_javascript(self):
        response = self.client.post('/counting_cut_36mm_wood', data={'key_code': '1'})
        self.assertEqual(303, response.status_code)
        self.assertEqual(8, tracker.cut_36mm_wood_counts()['6_long'])
        response = self.client.post('/counting_cut_36mm_wood', data={
            'action': 'adjust', 'part_key': '6_long', 'amount': '10',
        }, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual(18, tracker.cut_36mm_wood_counts()['6_long'])

    def test_logged_out_requests_cannot_change_stock(self):
        with self.client.session_transaction() as login_session:
            login_session.clear()
        self.assertEqual(401, self.post(key_code='1').status_code)
        self.assertEqual(302, self.client.get('/counting_cut_36mm_wood').status_code)
        self.assertEqual(0, tracker.PrintedPartsCount.query.count())

    def test_inventory_and_api_expose_new_parts(self):
        self.post(key_code='1')
        inventory = self.client.get('/inventory')
        self.assertEqual(200, inventory.status_code)
        self.assertIn(b'Cut 36mm Wood - 6ft Long', inventory.data)
        summary = self.client.get('/api/inventory/summary', headers={'X-API-Token': 'bitcade_api_key_1'})
        self.assertEqual(200, summary.status_code)
        self.assertEqual(tracker.cut_36mm_wood_counts(), summary.json['cut_36mm_wood_current'])
        self.assertTrue(set(tracker.CUT_36MM_WOOD_PART_NAMES).issubset(VALID_PARTS))


if __name__ == '__main__':
    unittest.main()
