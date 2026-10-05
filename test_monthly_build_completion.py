import os
import unittest
from datetime import date, datetime
from unittest.mock import patch

from flask import Flask

import flask_app as tracker


class MonthlyBuildAutoCompletionTests(unittest.TestCase):
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
        with self.client.session_transaction() as login_session:
            login_session['worker'] = 'Alice'

    def tearDown(self):
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def item(self, model='Signature Champion', size='7FT', colour='Black', quantity=1,
             due_at=datetime(2026, 10, 15, 17), archived=False):
        build_list = tracker.MonthlyBuildList(
            name='October Orders', month_start=date(2026, 10, 1), created_by='Manager',
            archived=archived,
        )
        deadline = tracker.MonthlyBuildDeadline(build_list=build_list, label='Ready for', due_at=due_at)
        item = tracker.MonthlyBuildItem(
            deadline=deadline, model_name=model, size=size, colour=colour, quantity=quantity,
        )
        tracker.db.session.add(item)
        tracker.db.session.commit()
        return item

    def body(self, serial='1001'):
        body = tracker.CompletedTable(
            worker='Alice', start_time='09:00', finish_time='10:00', serial_number=serial,
            date=date(2026, 10, 5),
        )
        tracker.db.session.add(body)
        tracker.db.session.flush()
        return body

    def complete(self, serial='1001', table_type=tracker.TABLE_TYPE_CHAMPION, colour='black'):
        completion = tracker.auto_tick_monthly_build_body(self.body(serial), table_type, colour)
        tracker.db.session.commit()
        return completion

    def test_exact_model_size_and_colour_match(self):
        self.item(model='Signature League')
        self.item(size='6FT')
        self.item(colour='Rustic Black')
        self.item(model='Unknown model')
        matching = self.item()
        completion = self.complete()
        self.assertEqual(matching.id, completion.item_id)
        self.assertEqual('Alice', completion.completed_by)
        self.assertEqual(1, tracker.MonthlyBuildCompletion.query.count())

    def test_earliest_deadline_precedes_stock_and_later_dates(self):
        self.item(due_at=None)
        self.item(due_at=datetime(2026, 11, 5, 17))
        earliest = self.item(due_at=datetime(2026, 10, 15, 17))
        self.item(due_at=datetime(2026, 10, 1, 17), archived=True)
        self.assertEqual(earliest.id, self.complete().item_id)

    def test_manual_ticks_are_preserved_and_full_rows_are_skipped(self):
        first = self.item(quantity=3)
        later = self.item(due_at=datetime(2026, 10, 27, 17))
        tracker.db.session.add(tracker.MonthlyBuildCompletion(
            item=first, unit_number=2, completed_by='Manager',
        ))
        tracker.db.session.commit()
        self.assertEqual(1, self.complete('1001').unit_number)
        self.assertEqual(3, self.complete('1002').unit_number)
        self.assertEqual(later.id, self.complete('1003').item_id)
        manual = tracker.MonthlyBuildCompletion.query.filter_by(item_id=first.id, unit_number=2).one()
        self.assertEqual('Manager', manual.completed_by)
        self.assertIsNone(self.complete('1004'))

    def test_league_body_uses_selected_colour_and_size(self):
        self.item(model='Signature League', size='6FT', colour='Black')
        self.item(size='6FT', colour='Rustic Black')
        matching = self.item(model='Signature League', size='6FT', colour='Rustic Black')
        completion = self.complete('1001 - 6 - L', tracker.TABLE_TYPE_LITE, 'rustic_black')
        self.assertEqual(matching.id, completion.item_id)

    def test_colour_and_model_aliases(self):
        matching = self.item(model=' champion premium ', size='7 ft', colour=' Gray Oak ')
        self.assertEqual(matching.id, self.complete('1001 - GO', colour='grey_oak').item_id)
        self.assertEqual(tracker.TABLE_TYPE_LITE, tracker.monthly_build_model_type('Champion Lite'))
        self.assertIsNone(tracker.monthly_build_model_type('Special Champion Edition'))

    def test_no_matching_or_archived_rows_are_not_ticked(self):
        self.item(colour='Stone')
        self.item(archived=True)
        self.assertIsNone(self.complete())
        self.assertEqual(0, tracker.MonthlyBuildCompletion.query.count())

    def test_body_and_tick_rollback_together(self):
        self.item()
        tracker.auto_tick_monthly_build_body(self.body(), tracker.TABLE_TYPE_CHAMPION, 'black')
        tracker.db.session.rollback()
        self.assertEqual(0, tracker.CompletedTable.query.count())
        self.assertEqual(0, tracker.MonthlyBuildCompletion.query.count())

    def seed_inventory(self, serial, table_type, colour):
        parts = tracker.body_parts_for_completion(serial, table_type, colour)
        for part_name in set(parts) | {'Pallet Wrap'}:
            tracker.db.session.add(tracker.new_printed_parts_snapshot(part_name, 1000))
        for part_key in tracker.body_piece_keys_for(serial, table_type, colour):
            tracker.db.session.add(tracker.BodyPieceCount(part_key=part_key, count=10))
        tracker.db.session.commit()

    def submit_body(self, serial='1001', table_type='Champion', colour='Black', **extra):
        data = {
            'start_time': '09:00', 'finish_time': '10:00', 'serial_number': serial,
            'table_type': table_type, 'color_selector': colour, 'issue': 'None', 'lunch': 'No',
        }
        data.update(extra)
        with patch.object(tracker.requests, 'post'), patch.object(tracker, 'fetch_uk_bank_holidays', return_value={}):
            response = self.client.post('/bodies', data=data)
        self.assertIn(response.status_code, (302, 303))
        return response

    def test_body_submission_updates_inventory_log_and_matching_tick_once(self):
        matching = self.item(quantity=2)
        self.seed_inventory('1001', tracker.TABLE_TYPE_CHAMPION, 'black')
        self.submit_body()
        self.assertEqual(1, tracker.CompletedTable.query.count())
        self.assertEqual(1, tracker.MonthlyBuildCompletion.query.count())
        self.assertEqual(matching.id, tracker.MonthlyBuildCompletion.query.one().item_id)
        self.assertEqual(996, tracker._latest_part_count('Laminate - Black'))
        self.assertEqual(1, tracker.TableStock.query.filter_by(type='body_7ft_black').one().count)
        self.assertEqual(1, tracker.TableStockLog.query.filter_by(action_type='complete_body').count())
        self.submit_body()
        self.assertEqual(1, tracker.MonthlyBuildCompletion.query.count())
        self.assertEqual(996, tracker._latest_part_count('Laminate - Black'))
        html = self.client.get('/monthly_build_list').get_data(as_text=True)
        self.assertIn('class="unit-check completed"', html)
        self.assertIn('Completed by Alice', html)

    def test_league_submission_retains_selected_colour_in_match(self):
        matching = self.item(model='Signature League', size='6FT', colour='Rustic Black')
        self.seed_inventory('1001 - 6 - L', tracker.TABLE_TYPE_LITE, 'rustic_black')
        self.submit_body('1001 - 6 - L', 'Lite', 'Rustic Black')
        self.assertEqual(matching.id, tracker.MonthlyBuildCompletion.query.one().item_id)
        self.assertEqual(996, tracker._latest_part_count('Laminate - Rustic Black'))

    def test_shortage_prompt_does_not_tick_until_completion_is_confirmed(self):
        self.item()
        self.submit_body()
        self.assertEqual(0, tracker.CompletedTable.query.count())
        self.assertEqual(0, tracker.MonthlyBuildCompletion.query.count())
        with self.client.session_transaction() as login_session:
            token = login_session['body_shortage_confirmation']['token']
        self.submit_body(confirm_shortages='yes', shortage_confirmation_token=token)
        self.assertEqual(1, tracker.CompletedTable.query.count())
        self.assertEqual(1, tracker.MonthlyBuildCompletion.query.count())

    def test_body_without_matching_order_still_completes(self):
        self.item(colour='Stone')
        self.seed_inventory('1001', tracker.TABLE_TYPE_CHAMPION, 'black')
        self.submit_body()
        self.assertEqual(1, tracker.CompletedTable.query.count())
        self.assertEqual(0, tracker.MonthlyBuildCompletion.query.count())
        with self.client.session_transaction() as login_session:
            self.assertTrue(any('No matching unticked body' in text for _, text in login_session['_flashes']))

    def test_manual_corrections_still_work(self):
        matching = self.item()
        completion = self.complete()
        response = self.client.post(
            f'/api/monthly_build_list/items/{matching.id}/units/{completion.unit_number}',
            json={'completed': False},
        )
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(0, tracker.MonthlyBuildCompletion.query.count())


if __name__ == '__main__':
    unittest.main()
