import json
import os
import unittest

from flask import Flask

import flask_app as tracker


class PackagingPlanManagementTests(unittest.TestCase):
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
        with self.client.session_transaction() as login:
            login['worker'] = 'Packaging Operator'
        for number in (27, 28, 29):
            tracker.db.session.add(tracker.InvoicePackagingJob(
                id=number, title=f'Delivery {number}', created_by='Packaging Operator',
                items_json=json.dumps([{
                    'id': f'item-{number}', 'description': '6ft Black top rail',
                    'item_type': 'top_rail', 'size': '6ft', 'colour': 'Black',
                    'quantity': 2, 'po_number': 'PO-12345',
                }]),
                pallets_json='[{"id": "manual-pallet", "manual_override": true}]',
                notes='Keep these notes',
                config_json=json.dumps({'top_rail_extras': [
                    {'id': 'extra', 'size': '6ft', 'colour': 'Black', 'quantity': 2},
                ]}) if number == 27 else '{}',
                stock_removed_at=tracker.london_now() if number == 27 else None,
                stock_removed_by='Packaging Operator' if number == 27 else None,
                stock_removal_json='[{"type": "top_rail_6ft", "quantity": 2}]',
            ))
        tracker.db.session.add(tracker.TableStock(type='top_rail_6ft', count=10))
        tracker.db.session.commit()

    def tearDown(self):
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def test_keep_28_hides_others_without_changing_plan_data_or_stock_history(self):
        before = {
            job.id: {column.name: getattr(job, column.name)
                     for column in tracker.InvoicePackagingJob.__table__.columns
                     if column.name != 'is_hidden'}
            for job in tracker.InvoicePackagingJob.query.all()
        }
        response = self.client.post('/invoice_packaging/28/visibility', data={'action': 'hide_others'})
        self.assertEqual(302, response.status_code)
        self.assertEqual([28], [job.id for job in tracker.InvoicePackagingJob.query.filter_by(is_hidden=False)])
        for job in tracker.InvoicePackagingJob.query.all():
            for name, value in before[job.id].items():
                self.assertEqual(value, getattr(job, name))
        self.assertEqual(10, tracker.TableStock.query.one().count)
        self.assertEqual({('6ft', 'Black'): 2}, tracker.packaging_top_rail_balance())
        self.assertTrue(tracker.packaging_historical_po_warnings(
            json.loads(tracker.db.session.get(tracker.InvoicePackagingJob, 28).items_json), 28,
        ))
        self.assertEqual(3, len(tracker.packaging_po_search_results('12345')))
        page = self.client.get('/invoice_packaging?plan=28')
        self.assertEqual(200, page.status_code)
        self.assertIn(b'Hidden plans (2)', page.data)
        selector = page.data.split(b'id="saved-plan-select"', 1)[1].split(b'</select>', 1)[0]
        self.assertIn(b'Delivery 28', selector)
        self.assertNotIn(b'Delivery 27', selector)
        self.assertNotIn(b'Delivery 29', selector)

    def test_hidden_plan_remains_accessible_and_can_be_restored(self):
        self.client.post('/invoice_packaging/28/visibility', data={'action': 'hide'})
        page = self.client.get('/invoice_packaging?plan=28')
        self.assertEqual(200, page.status_code)
        self.assertIn(b'Plan #28 - Hidden', page.data)
        self.client.post('/invoice_packaging/28/visibility', data={'action': 'restore'})
        self.assertFalse(tracker.db.session.get(tracker.InvoicePackagingJob, 28).is_hidden)
        # Keeping a hidden plan also restores it.
        self.client.post('/invoice_packaging/28/visibility', data={'action': 'hide'})
        self.client.post('/invoice_packaging/28/visibility', data={'action': 'hide_others'})
        self.assertFalse(tracker.db.session.get(tracker.InvoicePackagingJob, 28).is_hidden)

    def test_rename_processed_plan_only_changes_name_and_timestamp(self):
        job = tracker.db.session.get(tracker.InvoicePackagingJob, 27)
        before = {column.name: getattr(job, column.name)
                  for column in tracker.InvoicePackagingJob.__table__.columns
                  if column.name not in ('title', 'updated_at')}
        response = self.client.post('/api/invoice_packaging/27/rename', json={
            'title': '  November dispatch  ', 'items': [], 'pallets': [],
        })
        self.assertEqual(200, response.status_code)
        self.assertEqual('November dispatch', job.title)
        for name, value in before.items():
            self.assertEqual(value, getattr(job, name))
        page = self.client.get('/invoice_packaging?plan=27')
        self.assertEqual(200, page.status_code)
        self.assertIn(b'November dispatch (#27)', page.data)
        self.assertIn(b'value="November dispatch"', page.data)

    def test_invalid_names_and_visibility_actions_do_not_change_plan(self):
        for title in ('', '   ', 'a' * 161, 123, None):
            response = self.client.post('/api/invoice_packaging/28/rename', json={'title': title})
            self.assertEqual(400, response.status_code)
        response = self.client.post('/invoice_packaging/28/visibility', data={'action': 'delete'})
        self.assertEqual(400, response.status_code)
        job = tracker.db.session.get(tracker.InvoicePackagingJob, 28)
        self.assertEqual('Delivery 28', job.title)
        self.assertFalse(job.is_hidden)

    def test_management_requires_login(self):
        with self.client.session_transaction() as login:
            login.clear()
        self.assertEqual(401, self.client.post('/api/invoice_packaging/28/rename', json={'title': 'Changed'}).status_code)
        self.assertEqual(302, self.client.post('/invoice_packaging/28/visibility', data={'action': 'hide_others'}).status_code)
        self.assertEqual(3, tracker.InvoicePackagingJob.query.filter_by(is_hidden=False).count())

    def test_existing_database_upgrade_preserves_plans_and_defaults_to_visible(self):
        # Simulate the previous schema only inside this disposable test database.
        tracker.db.session.execute(tracker.text('ALTER TABLE invoice_packaging_job DROP COLUMN is_hidden'))
        tracker.db.session.commit()
        tracker.ensure_invoice_packaging_tables()
        tracker.db.session.expire_all()
        self.assertEqual(3, tracker.InvoicePackagingJob.query.filter_by(is_hidden=False).count())
        self.assertEqual('Delivery 28', tracker.db.session.get(tracker.InvoicePackagingJob, 28).title)
        # Repeated page visits must not reset the user's visibility choices.
        self.client.post('/invoice_packaging/28/visibility', data={'action': 'hide_others'})
        tracker.ensure_invoice_packaging_tables()
        self.assertEqual(1, tracker.InvoicePackagingJob.query.filter_by(is_hidden=False).count())


if __name__ == '__main__':
    unittest.main()
