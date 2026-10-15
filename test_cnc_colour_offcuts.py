import os
import unittest
from unittest.mock import patch

from flask import Flask

import flask_app as tracker


class CncColourOffcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__, template_folder=os.path.join(tracker.basedir, 'templates'))
        cls.app.config.update(
            TESTING=True, SECRET_KEY='test-only', SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
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
            login_session['worker'] = 'CNC Operator'
        self.notifications = patch.object(tracker, '_cnc_notify_low_queue_transitions')
        self.notifications.start()
        self.holidays = patch.object(tracker, 'fetch_uk_bank_holidays', return_value={})
        self.holidays.start()

    def tearDown(self):
        self.holidays.stop()
        self.notifications.stop()
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def job(self, name, quantity=1):
        job = tracker.CncJob(name=name, quantity=quantity)
        tracker.db.session.add(job)
        tracker.db.session.commit()
        return job

    def post(self, action, **payload):
        return self.client.post('/api/cnc/queue/' + action, json=payload)

    def queued(self, machine=1):
        return tracker.CncQueueItem.query.filter_by(machine_number=machine, status=tracker.CNC_STATUS_QUEUED).order_by(tracker.CncQueueItem.position, tracker.CncQueueItem.id).all()

    def names(self, machine=1):
        return [item.job.name for item in self.queued(machine)]

    def test_both_exact_sides_files_get_an_offcut_directly_below_on_same_machine(self):
        first = self.job('7ft Champ - Colours Sides')
        second = self.job('6ft Champ - Colour Sides')
        other = self.job('Unrelated file')
        self.post('add', job_id=other.id, machine_number=2)
        self.assertEqual(2, self.post('add', job_id=first.id, machine_number=2).json['created_items'])
        self.post('add', job_id=second.id, machine_number=2)
        self.assertEqual(['Unrelated file', first.name, 'colour off cut', second.name, 'colour off cut'], self.names(2))
        self.assertEqual([1, 2, 3, 4, 5], [item.position for item in self.queued(2)])
        self.assertEqual([], self.names(1))
        self.assertEqual(1, tracker.CncJob.query.filter_by(name='colour off cut').count())

    def test_existing_colour_off_cut_file_is_reused(self):
        offcut = self.job('Colour Off Cut')
        sides = self.job('7ft Champ - Colours Sides')
        self.post('add', job_id=sides.id, machine_number=1)
        self.assertEqual(offcut.id, self.queued()[1].job_id)
        self.assertEqual(2, tracker.CncJob.query.count())

    def test_name_matching_handles_case_spacing_and_colour_plural(self):
        for name in ['7FT CHAMP - COLOURS SIDES', '6ft Champ - Colour Sides', ' 6ft  Champ - Colours Sides ', '7 ft Champ - Colour Sides']:
            with self.subTest(name=name):
                self.assertTrue(tracker.cnc_needs_colour_offcut(name))
        for name in ['7ft Lite - Colour Sides', '7ft Champ - Black Sides', 'colour off cut', 'Colour Sides', '7ft Champ - Colour Bases']:
            with self.subTest(name=name):
                self.assertFalse(tracker.cnc_needs_colour_offcut(name))

    def test_unrelated_jobs_and_standalone_offcuts_do_not_spawn_companions(self):
        for name in ['7ft Lite - Colour Sides', '7ft Champ - Black Sides', 'colour off cut']:
            job = self.job(name)
            self.assertEqual(1, self.post('add', job_id=job.id, machine_number=1).json['created_items'])
        self.assertEqual(3, len(self.queued()))

    def test_bulk_copy_sides_only_adds_one_offcut_per_target_machine(self):
        sides = self.job('6ft Champ - Colour Sides')
        item_id = self.post('add', job_id=sides.id, machine_number=1).json['queue_item_id']
        copied = self.post('bulk_copy', item_ids=[item_id], machine_numbers=[2, 3])
        self.assertEqual(4, copied.json['created_items'])
        self.assertEqual([sides.name, 'colour off cut'], self.names(2))
        self.assertEqual([sides.name, 'colour off cut'], self.names(3))

    def test_bulk_copy_a_selected_pair_does_not_double_add_offcut(self):
        sides = self.job('7ft Champ - Colours Sides')
        self.post('add', job_id=sides.id, machine_number=1)
        copied = self.post('bulk_copy', item_ids=[item.id for item in self.queued()], machine_numbers=[2])
        self.assertEqual(2, copied.json['created_items'])
        self.assertEqual([sides.name, 'colour off cut'], self.names(2))

    def test_duplicate_sides_retains_original_pair_and_creates_paired_copies(self):
        sides = self.job('7ft Champ - Colours Sides')
        side_id = self.post('add', job_id=sides.id, machine_number=1).json['queue_item_id']
        original_pair_ids = [item.id for item in self.queued()]
        response = self.post('bulk_duplicate_same_queue', item_ids=[side_id], copies=2)
        self.assertEqual(4, response.json['created_items'])
        self.assertEqual([sides.name, 'colour off cut'] * 3, self.names())
        self.assertEqual(original_pair_ids, [item.id for item in self.queued()[:2]])
        self.assertEqual(list(range(1, 7)), [item.position for item in self.queued()])

    def test_duplicate_both_selected_pair_rows_creates_only_one_new_pair(self):
        sides = self.job('6ft Champ - Colour Sides')
        self.post('add', job_id=sides.id, machine_number=1)
        response = self.post('bulk_duplicate_same_queue', item_ids=[item.id for item in self.queued()], copies=1)
        self.assertEqual(2, response.json['created_items'])
        self.assertEqual([sides.name, 'colour off cut'] * 2, self.names())

    def test_duplicate_offcut_only_does_not_duplicate_parent(self):
        sides = self.job('7ft Champ - Colours Sides')
        self.post('add', job_id=sides.id, machine_number=1)
        response = self.post('bulk_duplicate_same_queue', item_ids=[self.queued()[1].id], copies=1)
        self.assertEqual(1, response.json['created_items'])
        self.assertEqual([sides.name, 'colour off cut', 'colour off cut'], self.names())

    def test_offcut_completion_credits_quarter_without_consuming_a_new_sheet(self):
        offcut = self.job('colour off cut')
        tracker.db.session.add(tracker.MDFInventory(plain_mdf=10, black_mdf=20, plain_mdf_36=30))
        tracker.db.session.commit()
        item_id = self.post('add', job_id=offcut.id, machine_number=1).json['queue_item_id']
        response = self.post('complete', item_id=item_id)
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json['wood_counted'])
        self.assertEqual(0.25, tracker.cnc_completed_quantity_total())
        inventory = tracker.MDFInventory.query.first()
        self.assertEqual((10, 20, 30), (inventory.plain_mdf, inventory.black_mdf, inventory.plain_mdf_36))
        self.assertEqual(0, tracker.WoodCount.query.count())
        self.assertEqual(404, self.post('complete', item_id=item_id).status_code)
        self.assertEqual(0.25, tracker.cnc_completed_quantity_total())
        self.assertEqual(200, self.post('undo_complete', item_id=item_id).status_code)
        self.assertEqual(0, tracker.cnc_completed_quantity_total())
        self.assertEqual(200, self.post('complete', item_id=item_id).status_code)
        self.assertEqual(0.25, tracker.cnc_completed_quantity_total())

    def test_completion_totals_and_history_preserve_quarters_and_egger_weight(self):
        for name in ['Normal job', 'colour off cut', 'Egger job']:
            job = self.job(name)
            item_id = self.post('add', job_id=job.id, machine_number=1).json['queue_item_id']
            self.assertEqual(200, self.post('complete', item_id=item_id).status_code)
        self.assertEqual(3.25, tracker.cnc_completed_quantity_total())
        local_now = tracker.london_now()
        self.assertEqual(3.25, tracker.cnc_completed_quantity_total(year=local_now.year, month=local_now.month, day=local_now.day))
        history = tracker.cnc_monthly_cut_file_history()[0]
        self.assertEqual(3.25, history['total_quantity'])
        self.assertEqual(3, history['total_runs'])
        offcut = next(row for row in history['files'] if row['name'] == 'colour off cut')
        self.assertEqual(0.25, offcut['quantity'])
        self.assertEqual(1, offcut['runs'])
        self.assertEqual(0.75, tracker.cnc_effective_completed_quantity('colour off cut', 3))

    def test_cnc_bonus_progress_retains_fractional_credit(self):
        local_now = tracker.london_now()
        tracker.db.session.add(tracker.BonusGoal(area='cnc', worker_name='Tom E', target_count=10, year=local_now.year, month=local_now.month, active=True))
        tracker.db.session.commit()
        offcut = self.job('colour off cut')
        item_id = self.post('add', job_id=offcut.id, machine_number=1).json['queue_item_id']
        self.post('complete', item_id=item_id)
        progress = tracker.bonus_goal_progress('cnc', local_now.year, local_now.month)[0]
        self.assertEqual(0.25, progress['current'])
        self.assertEqual(9.75, progress['remaining'])
        self.assertFalse(progress['target_hit'])

    def test_dashboard_displays_companion_and_quarter_credit(self):
        sides = self.job('7ft Champ - Colours Sides')
        self.post('add', job_id=sides.id, machine_number=1)
        response = self.client.get('/cnc_dashboard')
        self.assertEqual(200, response.status_code)
        self.assertIn(b'0.25 of a job', response.data)
        self.post('complete', item_id=self.queued()[1].id)
        response = self.client.get('/cnc_dashboard')
        self.assertIn(b'id="completed-today-count">0.25<', response.data)
        self.assertIn(b'id="completed-month-count">0.25<', response.data)

    def test_logged_out_user_cannot_add_jobs(self):
        sides = self.job('7ft Champ - Colours Sides')
        with self.client.session_transaction() as login_session:
            login_session.clear()
        self.assertEqual(401, self.post('add', job_id=sides.id, machine_number=1).status_code)
        self.assertEqual(0, tracker.CncQueueItem.query.count())


if __name__ == '__main__':
    unittest.main()
