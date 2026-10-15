import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import inspect

import flask_app as tracker


class PrintedPartQuickAddTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = Flask(__name__, template_folder=os.path.join(tracker.basedir, 'templates'))
        self.app.config.update(
            TESTING=True,
            SECRET_KEY='test-only',
            SQLALCHEMY_DATABASE_URI='sqlite:///' + os.path.join(self.temp_dir.name, 'test.db'),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        tracker.db.init_app(self.app)
        self.app.url_map = tracker.app.url_map
        self.app.view_functions.update(tracker.app.view_functions)
        self.app.jinja_env.filters.update(tracker.app.jinja_env.filters)
        self.app.after_request(tracker.prevent_stale_count_pages)
        self.context = self.app.app_context()
        self.context.push()
        # Start with the existing schema, so the route must add its settings table.
        tracker.db.metadata.create_all(
            bind=tracker.db.engine,
            tables=[table for table in tracker.db.metadata.sorted_tables
                    if table is not tracker.PrintedPartQuickAdd.__table__],
        )
        self.client = self.logged_in_client()

    def tearDown(self):
        tracker.db.session.remove()
        tracker.db.engine.dispose()
        self.context.pop()
        self.temp_dir.cleanup()

    def logged_in_client(self):
        client = self.app.test_client()
        with client.session_transaction() as login_session:
            login_session['worker'] = 'Test Worker'
        return client

    def post(self, action, part='Large Ramp', **data):
        return self.client.post('/counting_3d_printing_parts', data={
            'action': action, 'part': part, **data,
        }, follow_redirects=True)

    def test_settings_survive_new_connection_and_new_client_without_changing_stock(self):
        tracker.db.session.add(tracker.new_printed_parts_snapshot('Large Ramp', 10))
        tracker.db.session.commit()
        response = self.post('save_quick_add', saved_quick_amount='24')
        self.assertEqual(200, response.status_code)
        self.assertIn(b'Add +24 (saved)', response.data)
        self.assertEqual(1, tracker.PrintedPartsCount.query.count())
        self.assertEqual(10, tracker.PrintedPartsCount.query.one().count)

        tracker.db.session.remove()
        tracker.db.engine.dispose()
        response = self.logged_in_client().get('/counting_3d_printing_parts?selected=Large+Ramp')
        self.assertIn(b'Add +24 (saved)', response.data)
        self.assertEqual(24, tracker.db.session.get(tracker.PrintedPartQuickAdd, 'Large Ramp').amount)

    def test_parts_keep_independent_amounts_and_edits_replace_only_the_selected_setting(self):
        for part, amount in [('Large Ramp', '24'), ('Paddle', '12'), ('Large Ramp', '30'),
                             ('6ft Carpet', '8'), (tracker.LAMINATE_PART_NAMES[0], '16')]:
            self.post('save_quick_add', part=part, saved_quick_amount=amount)
        presets = {row.part_name: row.amount for row in tracker.PrintedPartQuickAdd.query.all()}
        self.assertEqual({'Large Ramp': 30, 'Paddle': 12, '6ft Carpet': 8,
                          tracker.LAMINATE_PART_NAMES[0]: 16}, presets)
        self.assertEqual(0, tracker.PrintedPartsCount.query.count())

    def test_saved_add_uses_database_amount_and_records_stock_and_log(self):
        self.post('save_quick_add', saved_quick_amount='24')
        response = self.post('quick_add', use_saved_amount='1', quick_amount='999')
        self.assertIn(b'New count: 24', response.data)
        self.assertIn(b'Large Ramp', response.data)
        self.assertIn(b'+24', response.data)
        self.assertEqual(24, tracker.PrintedPartsCount.query.one().count)
        self.assertEqual('Large Ramp', tracker.PrintedPartsCount.query.one().part_name)

        self.post('save_quick_add', saved_quick_amount='12')
        self.post('quick_add', use_saved_amount='1')
        rows = tracker.PrintedPartsCount.query.order_by(tracker.PrintedPartsCount.id).all()
        self.assertEqual([24, 36], [row.count for row in rows])

    def test_invalid_settings_and_quick_adds_leave_inventory_and_saved_value_unchanged(self):
        self.post('save_quick_add', saved_quick_amount='24')
        for value in ['', '0', '-5', '1.5', 'abc', '2147483648', '9' * 100]:
            with self.subTest(value=value):
                response = self.post('save_quick_add', saved_quick_amount=value)
                self.assertIn(b'whole number', response.data)
                self.post('quick_add', quick_amount=value)
                self.assertEqual(24, tracker.db.session.get(tracker.PrintedPartQuickAdd, 'Large Ramp').amount)
                self.assertEqual(0, tracker.PrintedPartsCount.query.count())
        response = self.post('quick_add', part='Paddle', use_saved_amount='1')
        self.assertIn(b'Save a quick-add amount for this part first.', response.data)
        self.post('save_quick_add', part='Unknown part', saved_quick_amount='12')
        self.assertEqual(1, tracker.PrintedPartQuickAdd.query.count())
        self.assertEqual(0, tracker.PrintedPartsCount.query.count())

    def test_standard_stock_controls_still_work_and_log_remains_hidden(self):
        for amount in ['1', '5', '10']:
            self.post('quick_add', quick_amount=amount)
        self.post('bulk', amount='4')
        with patch.object(tracker, 'check_and_notify_low_stock'):
            response = self.post('reject', reject_amount='3')
        rows = tracker.PrintedPartsCount.query.order_by(tracker.PrintedPartsCount.id).all()
        self.assertEqual([1, 6, 16, 20, 17], [row.count for row in rows])
        self.assertIn(b'id="parts-log-content" style="margin-top: 10px; display: none;"', response.data)

    def test_login_required_and_schema_is_added_without_changing_existing_stock(self):
        response = self.app.test_client().get('/counting_3d_printing_parts')
        self.assertEqual(302, response.status_code)
        self.assertNotIn(tracker.PrintedPartQuickAdd.__tablename__, inspect(tracker.db.engine).get_table_names())
        tracker.db.session.add(tracker.new_printed_parts_snapshot('Paddle', 7))
        tracker.db.session.commit()
        for _ in range(2):
            response = self.client.get('/counting_3d_printing_parts?selected=Unknown')
            self.assertEqual(200, response.status_code)
        self.assertIn(tracker.PrintedPartQuickAdd.__tablename__, inspect(tracker.db.engine).get_table_names())
        self.assertEqual(7, tracker.PrintedPartsCount.query.one().count)
        self.assertEqual(0, tracker.PrintedPartQuickAdd.query.count())


if __name__ == '__main__':
    unittest.main()
