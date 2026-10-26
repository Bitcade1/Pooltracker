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

    def test_logged_out_page_polling_and_stock_updates_work(self):
        with self.client.session_transaction() as login_session:
            login_session.clear()
        page = self.client.get('/counting_cut_36mm_wood')
        self.assertEqual(200, page.status_code)
        self.assertNotIn(b'type="password"', page.data)
        response = self.post(key_code='1')
        self.assertEqual(200, response.status_code)
        self.assertEqual(8, response.json['counts']['6_long'])
        self.assertTrue(response.json['log'])
        refresh = self.client.get(
            '/counting_cut_36mm_wood', headers={'Accept': 'application/json'},
        )
        self.assertEqual(200, refresh.status_code)
        self.assertEqual(response.json['counts'], refresh.json['counts'])
        with self.client.session_transaction() as login_session:
            self.assertNotIn('worker', login_session)

    def test_logged_out_forms_and_queue_focus_work_without_unlocking_other_pages(self):
        with self.client.session_transaction() as login_session:
            login_session.clear()
        response = self.client.post('/counting_cut_36mm_wood', data={
            'action': 'set', 'part_key': '6_long', 'amount': '12',
        }, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual(12, tracker.cut_36mm_wood_counts()['6_long'])
        response = self.post(action='set_focus', focus='6ft')
        self.assertEqual(200, response.status_code)
        self.assertEqual('6ft', response.json['cnc_queue']['focus'])
        self.assertEqual('6ft', self.client.get_cookie('cut_36mm_queue_focus').value)
        self.assertEqual(302, self.client.get('/counting_wood').status_code)
        with self.client.session_transaction() as login_session:
            self.assertNotIn('worker', login_session)

    def test_inventory_and_api_expose_new_parts(self):
        self.post(key_code='1')
        inventory = self.client.get('/inventory')
        self.assertEqual(200, inventory.status_code)
        self.assertIn(b'Cut 36mm Wood - 6ft Long', inventory.data)
        summary = self.client.get('/api/inventory/summary', headers={'X-API-Token': 'bitcade_api_key_1'})
        self.assertEqual(200, summary.status_code)
        self.assertEqual(tracker.cut_36mm_wood_counts(), summary.json['cut_36mm_wood_current'])
        self.assertTrue(set(tracker.CUT_36MM_WOOD_PART_NAMES).issubset(VALID_PARTS))

    def test_queue_is_below_totals_and_updates_after_counts_change(self):
        page = self.client.get('/counting_cut_36mm_wood')
        self.assertLess(page.data.index(b'id="count-7_short"'), page.data.index(b'id="cnc-queue-body"'))
        initial = self.client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
        self.assertEqual('3', initial.json['cnc_queue']['cuts'][0]['key_code'])
        cut = self.post(key_code='3')
        self.assertEqual('1', cut.json['cnc_queue']['cuts'][0]['key_code'])
        adjusted = self.post(action='adjust', part_key='6_long', amount='20')
        self.assertEqual('2', adjusted.json['cnc_queue']['cuts'][0]['key_code'])
        removed = self.post(key_code='9')
        self.assertEqual(7, removed.json['counts']['7_long'])
        self.assertEqual(5, len(removed.json['cnc_queue']['cuts']))
        refresh = self.client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
        self.assertEqual(removed.json['cnc_queue'], refresh.json['cnc_queue'])
        self.assertEqual(0, tracker.CncQueueItem.query.count())

    def test_top_rail_shortcuts_consume_correct_wood_for_every_colour(self):
        for part_key in tracker.CUT_36MM_WOOD_PARTS:
            self.post(action='set', part_key=part_key, amount='10')
        expected = {part_key: 10 for part_key in tracker.CUT_36MM_WOOD_PARTS}
        for index, shortcut in enumerate('abcdefghijklmnopqrst'):
            with self.subTest(shortcut=shortcut):
                wood_key = ('6' if index < 10 else '7') + ('_short' if index % 2 == 0 else '_long')
                response = self.client.post('/top_rail_pieces', data={'key_code': shortcut})
                self.assertEqual(200, response.status_code)
                self.assertEqual(wood_key, response.json['wood_part_key'])
                self.assertEqual(1, response.json['deducted_wood'])
                self.assertEqual(1, response.json['count'])
                expected[wood_key] -= 1
                self.assertEqual(expected, tracker.cut_36mm_wood_counts())
        self.assertEqual(20, tracker.TopRailPieceCount.query.count())
        self.assertEqual(20, tracker.TopRailPieceCountLog.query.filter_by(action_type='quick_add').count())

    def test_missing_wood_does_not_log_finished_piece_or_consume_laminate(self):
        tracker.db.session.add(tracker.LaminatePieceCount(part_key='grey_oak_7_long', count=5))
        tracker.db.session.commit()
        response = self.client.post('/top_rail_pieces', data={'key_code': 'p'})
        self.assertEqual(400, response.status_code)
        self.assertIn('Not enough 7ft Long', response.json['message'])
        self.assertEqual(0, tracker.TopRailPieceCount.query.count())
        self.assertEqual(0, tracker.TopRailPieceCountLog.query.count())
        self.assertEqual(5, tracker.LaminatePieceCount.query.first().count)

    def test_manual_top_rail_increases_deduct_combined_amount_once(self):
        self.post(action='set', part_key='7_long', amount='20')
        self.post(action='set', part_key='6_short', amount='15')
        tracker.db.session.add(tracker.TopRailPieceCount(part_key='grey_oak_7_long', count=4))
        tracker.db.session.commit()
        form = {'piece_grey_oak_7_long': '7', 'piece_rustic_black_7_long': '2', 'piece_stone_6_short': '4'}
        response = self.client.post('/top_rail_pieces', data=form)
        self.assertEqual(303, response.status_code)
        self.assertEqual({'6_long': 0, '6_short': 11, '7_long': 15, '7_short': 0}, tracker.cut_36mm_wood_counts())
        self.client.post('/top_rail_pieces', data=form)
        self.assertEqual(15, tracker.cut_36mm_wood_counts()['7_long'])
        self.client.post('/top_rail_pieces', data={'piece_grey_oak_7_long': '3'})
        self.assertEqual(15, tracker.cut_36mm_wood_counts()['7_long'])

    def test_manual_shortage_rejects_whole_submission(self):
        self.post(action='set', part_key='6_long', amount='20')
        self.post(action='set', part_key='7_long', amount='2')
        response = self.client.post('/top_rail_pieces', data={
            'piece_black_6_long': '5', 'piece_black_7_long': '2', 'piece_stone_7_long': '1',
        })
        self.assertEqual(303, response.status_code)
        self.assertEqual(20, tracker.cut_36mm_wood_counts()['6_long'])
        self.assertEqual(2, tracker.cut_36mm_wood_counts()['7_long'])
        self.assertEqual(0, tracker.TopRailPieceCount.query.count())
        self.assertEqual(0, tracker.TopRailPieceCountLog.query.count())

    def test_finished_piece_updates_wood_log_queue_and_existing_laminate_deduction(self):
        for part_key, amount in [('6_long', 2), ('6_short', 2), ('7_long', 6), ('7_short', 6)]:
            self.post(action='set', part_key=part_key, amount=str(amount))
        tracker.db.session.add(tracker.LaminatePieceCount(part_key='rustic_black_6_short', count=5))
        tracker.db.session.commit()
        before = self.client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
        self.assertEqual('3', before.json['cnc_queue']['cuts'][0]['key_code'])
        response = self.client.post('/top_rail_pieces', data={'key_code': 'i'})
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json['deducted_laminate'])
        self.assertEqual(4, tracker.LaminatePieceCount.query.first().count)
        dashboard = self.client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
        self.assertEqual(1, dashboard.json['counts']['6_short'])
        self.assertEqual('6ft Short', dashboard.json['log'][0]['part'])
        self.assertEqual(-1, dashboard.json['log'][0]['delta'])
        self.assertEqual('2', dashboard.json['cnc_queue']['cuts'][0]['key_code'])

    def test_invalid_top_rail_actions_do_not_consume_wood(self):
        self.post(key_code='3')
        for form in [{'key_code': 'z'}, {'piece_black_7_long': '-1'}, {'piece_black_7_long': '1.5'}]:
            with self.subTest(form=form):
                self.client.post('/top_rail_pieces', data=form)
                self.assertEqual(8, tracker.cut_36mm_wood_counts()['7_long'])
                self.assertEqual(0, tracker.TopRailPieceCount.query.count())

    def test_6ft_focus_persists_and_does_not_change_inventory(self):
        self.post(key_code='3')
        before = tracker.cut_36mm_wood_counts()
        history_count = tracker.PrintedPartsCount.query.count()
        response = self.post(action='set_focus', focus='6ft')
        self.assertEqual(200, response.status_code)
        self.assertEqual('6ft', response.json['cnc_queue']['focus'])
        self.assertTrue(all(cut['key_code'] in ('1', '2') for cut in response.json['cnc_queue']['cuts']))
        page = self.client.get('/counting_cut_36mm_wood')
        self.assertIn(b'role="switch" checked', page.data)
        refresh = self.client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
        self.assertEqual('6ft', refresh.json['cnc_queue']['focus'])
        self.assertEqual(before, tracker.cut_36mm_wood_counts())
        self.assertEqual(history_count, tracker.PrintedPartsCount.query.count())
        cut = self.post(key_code='1')
        self.assertEqual('6ft', cut.json['cnc_queue']['focus'])
        restored = self.post(action='set_focus')
        self.assertEqual('default', restored.json['cnc_queue']['focus'])
        self.assertEqual(tracker.build_cut_36mm_wood_queue(restored.json['counts']), restored.json['cnc_queue'])

    def test_focus_invalid_values_rejected_and_no_javascript_form_works(self):
        self.assertEqual(400, self.post(action='set_focus', focus='other').status_code)
        response = self.client.post('/counting_cut_36mm_wood', data={'action': 'set_focus', 'focus': '6ft'}, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn(b'role="switch" checked', response.data)
        self.assertEqual(4, tracker.PrintedPartsCount.query.count())
        self.assertEqual([], tracker.cut_36mm_wood_log())

    def test_focus_survives_a_new_login_session_with_only_preference_cookie(self):
        for focus in ('6ft', 'default'):
            with self.subTest(focus=focus):
                response = self.post(action='set_focus', focus=focus)
                self.assertEqual(200, response.status_code)
                preference = self.client.get_cookie('cut_36mm_queue_focus')
                self.assertEqual(focus, preference.value)
                self.assertEqual(365 * 24 * 60 * 60, preference.max_age)
                self.assertTrue(preference.http_only)
                fresh_client = self.app.test_client()
                fresh_client.set_cookie('cut_36mm_queue_focus', preference.value)
                with fresh_client.session_transaction() as login_session:
                    login_session['worker'] = 'CNC Operator'
                refresh = fresh_client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
                self.assertEqual(focus, refresh.json['cnc_queue']['focus'])
                self.assertEqual(0, tracker.PrintedPartsCount.query.count())
                with self.client.session_transaction() as login_session:
                    self.assertFalse(login_session.permanent)

    def test_invalid_focus_does_not_overwrite_saved_preference(self):
        self.post(action='set_focus', focus='6ft')
        response = self.post(action='set_focus', focus='bad')
        self.assertEqual(400, response.status_code)
        self.assertEqual('6ft', self.client.get_cookie('cut_36mm_queue_focus').value)
        self.assertEqual('6ft', response.json['cnc_queue']['focus'])
        self.client.set_cookie('cut_36mm_queue_focus', 'bad')
        refresh = self.client.get('/counting_cut_36mm_wood', headers={'Accept': 'application/json'})
        self.assertEqual('default', refresh.json['cnc_queue']['focus'])


class Cut36mmWoodQueueTests(unittest.TestCase):
    @staticmethod
    def counts(long6=0, short6=0, long7=0, short7=0):
        return {'6_long': long6, '6_short': short6, '7_long': long7, '7_short': short7}

    def test_empty_stock_starts_with_7ft_and_plans_five_cuts(self):
        counts = self.counts()
        plan = tracker.build_cut_36mm_wood_queue(counts)
        self.assertEqual(['3', '1', '4', '3', '2'], [cut['key_code'] for cut in plan['cuts']])
        self.assertEqual(4, plan['projected']['sets_6ft'])
        self.assertEqual(8, plan['projected']['sets_7ft'])
        self.assertEqual(self.counts(), counts)

    def test_complete_sets_require_two_of_both_pieces(self):
        summary = tracker.cut_36mm_wood_stock_summary(self.counts(21, 2, 6, 7))
        self.assertEqual({'sets_6ft': 1, 'sets_7ft': 3, 'percent_6ft': 25.0, 'percent_7ft': 75.0}, summary)

    def test_target_balance_prefers_7ft(self):
        plan = tracker.build_cut_36mm_wood_queue(self.counts(8, 8, 24, 24))
        self.assertEqual('3', plan['cuts'][0]['key_code'])

    def test_6ft_focus_only_replenishes_6ft_even_when_7ft_is_empty(self):
        plan = tracker.build_cut_36mm_wood_queue(self.counts(100, 100, 0, 0), focus='6ft')
        self.assertEqual('6ft', plan['focus'])
        self.assertEqual(5, len(plan['cuts']))
        self.assertTrue(all(cut['key_code'] in ('1', '2') for cut in plan['cuts']))
        self.assertEqual(0, plan['projected']['sets_7ft'])

    def test_6ft_focus_balances_long_and_short_stock(self):
        for counts, expected in [(self.counts(), '1'), (self.counts(40, 0), '2'), (self.counts(0, 40), '1')]:
            with self.subTest(counts=counts):
                plan = tracker.build_cut_36mm_wood_queue(counts, focus='6ft')
                self.assertEqual(expected, plan['cuts'][0]['key_code'])
                self.assertTrue(all('6ft focus is on' in cut['reason'] for cut in plan['cuts']))

    def test_size_below_target_is_replenished(self):
        for counts, expected in [
            (self.counts(80, 80, 8, 8), '3'),
            (self.counts(8, 8, 80, 80), '1'),
        ]:
            with self.subTest(counts=counts):
                self.assertEqual(expected, tracker.build_cut_36mm_wood_queue(counts)['cuts'][0]['key_code'])

    def test_unmatched_pieces_trigger_the_file_for_missing_pieces(self):
        for counts, expected in [
            (self.counts(80, 80, 80, 0), '4'),
            (self.counts(80, 80, 0, 80), '3'),
            (self.counts(80, 0, 80, 80), '2'),
            (self.counts(0, 80, 80, 80), '1'),
        ]:
            with self.subTest(counts=counts):
                self.assertEqual(expected, tracker.build_cut_36mm_wood_queue(counts)['cuts'][0]['key_code'])

    def test_following_queue_converges_to_target_and_balances_lengths(self):
        for initial in [self.counts(), self.counts(200, 200, 0, 0), self.counts(0, 0, 600, 600)]:
            counts = dict(initial)
            for _ in range(300):
                next_cut = tracker.build_cut_36mm_wood_queue(counts)['cuts'][0]
                for part, amount in tracker.CUT_36MM_WOOD_FILES[next_cut['key_code']]['yields'].items():
                    counts[part] += amount
            summary = tracker.cut_36mm_wood_stock_summary(counts)
            with self.subTest(initial=initial):
                self.assertLess(abs(summary['percent_6ft'] - 25), 1)
                self.assertLessEqual(abs(counts['6_long'] - counts['6_short']), 16)
                self.assertLessEqual(abs(counts['7_long'] - counts['7_short']), 16)


if __name__ == '__main__':
    unittest.main()
