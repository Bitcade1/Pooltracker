import io
import json
import os
import unittest

from flask import Flask

import flask_app as tracker


class PackagingRailCarryoverTests(unittest.TestCase):
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
        with self.client.session_transaction() as login:
            login['worker'] = 'Packaging Operator'
        self.rail_key = tracker.top_rail_stock_type_key('6ft', 'black')
        tracker.db.session.add(tracker.TableStock(type=self.rail_key, count=50))
        tracker.db.session.add(tracker.InvoicePackagingJob(
            title='Earlier extra rails', created_by='Packaging Operator',
            config_json=json.dumps({'top_rail_extras': [
                {'id': 'early-rails', 'size': '6ft', 'colour': 'Black', 'quantity': 5},
            ]}),
            stock_removed_at=tracker.london_now(),
        ))
        tracker.db.session.commit()

    def tearDown(self):
        tracker.db.session.remove()
        tracker.db.drop_all()
        self.context.pop()

    def upload_plan(self):
        response = self.client.post('/invoice_packaging', data={
            'invoices': (io.BytesIO(b'Description,Quantity\n6ft Black top rail,10\n'), 'invoice.csv'),
        })
        self.assertEqual(303, response.status_code)
        return tracker.InvoicePackagingJob.query.order_by(tracker.InvoicePackagingJob.id.desc()).first()

    def generate(self, job, deduction=0):
        config = tracker.packaging_json_load(job.config_json, {})
        if deduction:
            config['top_rail_carryovers'] = [
                {'id': 'chosen-deduction', 'size': '6ft', 'colour': 'Black', 'quantity': deduction},
            ]
        response = self.client.post(f'/api/invoice_packaging/{job.id}/generate', json={
            'items': tracker.packaging_json_load(job.items_json, []),
            'pallets': [], 'config': config,
        })
        self.assertEqual(200, response.status_code)
        return response.json

    def process(self, job):
        response = self.client.post(f'/api/invoice_packaging/{job.id}/remove_from_stock', json={'confirm': True})
        self.assertEqual(200, response.status_code, response.json)

    def undo(self, job_id, processed_at):
        return self.client.post(f'/api/invoice_packaging/{job_id}/undo_stock_processing', json={
            'stock_removed_at': processed_at,
        })

    def test_new_deliveries_keep_full_rail_quantity_despite_outstanding_extras(self):
        job = self.upload_plan()
        for value in (job.config_json, job.automatic_config_json):
            self.assertEqual([], json.loads(value)['top_rail_carryovers'])
        result = self.generate(job)
        self.assertEqual(10, result['summary']['total_top_rails'])
        self.assertEqual(0, result['summary']['carried_forward_top_rails'])
        self.assertEqual({('6ft', 'Black'): 5}, tracker.packaging_top_rail_balance())
        payload = tracker.packaging_job_payload(job)
        self.assertEqual([{'size': '6ft', 'colour': 'Black', 'quantity': 5}], payload['outstanding_top_rails'])

    def test_extras_can_skip_a_processed_delivery_and_be_used_partially_later(self):
        next_delivery = self.upload_plan()
        self.generate(next_delivery)
        self.process(next_delivery)
        self.assertEqual(40, tracker.TableStock.query.filter_by(type=self.rail_key).one().count)
        self.assertEqual({('6ft', 'Black'): 5}, tracker.packaging_top_rail_balance())

        later_delivery = self.upload_plan()
        result = self.generate(later_delivery, deduction=3)
        self.assertEqual(7, result['summary']['total_top_rails'])
        self.assertEqual(3, result['summary']['carried_forward_top_rails'])
        self.assertEqual({('6ft', 'Black'): 5}, tracker.packaging_top_rail_balance())
        self.process(later_delivery)
        self.assertEqual(33, tracker.TableStock.query.filter_by(type=self.rail_key).one().count)
        self.assertEqual({('6ft', 'Black'): 2}, tracker.packaging_top_rail_balance())

        another_delivery = self.upload_plan()
        self.assertEqual(10, self.generate(another_delivery)['summary']['total_top_rails'])

    def test_existing_saved_deductions_remain_explicitly_editable(self):
        job = self.upload_plan()
        self.generate(job, deduction=2)
        response = self.client.get(f'/invoice_packaging?plan={job.id}')
        self.assertEqual(200, response.status_code)
        self.assertIn(b'Add Deduction', response.data)
        self.assertIn(b'No deductions are added automatically.', response.data)
        self.assertEqual(2, json.loads(job.config_json)['top_rail_carryovers'][0]['quantity'])
        config = json.loads(job.config_json)
        config['top_rail_carryovers'] = []
        job.config_json = json.dumps(config)
        tracker.db.session.commit()
        self.assertEqual(10, self.generate(job)['summary']['total_top_rails'])

    def test_undo_uses_saved_deductions_and_current_stock(self):
        job = self.upload_plan()
        self.generate(job)
        self.process(job)
        processed_at = job.stock_removed_at.isoformat()
        self.assertIn(b'Undo Stock Processing', self.client.get(f'/invoice_packaging?plan={job.id}').data)

        stock = tracker.TableStock.query.filter_by(type=self.rail_key).one()
        stock.count += 6
        # The current invoice and stock can change after the original deduction.
        items = json.loads(job.items_json)
        items[0]['quantity'] = 2
        job.items_json = json.dumps(items)
        job.notes = 'Dispatch notes'
        pallets_json = job.pallets_json
        tracker.db.session.commit()
        with self.client.session_transaction() as login:
            login['worker'] = 'Stock Operator'

        response = self.undo(job.id, processed_at)
        self.assertEqual(200, response.status_code, response.json)
        self.assertEqual(10, response.json['restored_quantity'])
        self.assertEqual(56, stock.count)
        self.assertIsNone(job.stock_removed_at)
        self.assertIsNone(job.stock_removed_by)
        self.assertEqual('[]', job.stock_removal_json)
        self.assertEqual(items, json.loads(job.items_json))
        self.assertEqual('Dispatch notes', job.notes)
        self.assertEqual(pallets_json, job.pallets_json)
        dispatch = tracker.TableStockLog.query.filter_by(action_type='invoice_dispatch').one()
        self.assertEqual(-10, dispatch.delta)
        reversal = tracker.TableStockLog.query.filter_by(action_type='invoice_dispatch_undo').one()
        self.assertEqual((10, 46, 56), (reversal.delta, reversal.count_before, reversal.count_after))
        self.assertEqual('Stock Operator', reversal.worker)
        self.assertIn(f'plan #{job.id}', reversal.note)

        self.assertEqual(409, self.undo(job.id, processed_at).status_code)
        self.process(job)
        self.assertEqual(54, stock.count)
        self.assertEqual(409, self.undo(job.id, processed_at).status_code)
        self.assertEqual(54, stock.count)

    def test_undo_keeps_stock_intact_when_a_carryover_depends_on_the_plan(self):
        source = self.upload_plan()
        config = json.loads(source.config_json)
        config['top_rail_extras'] = [
            {'id': 'extra', 'size': '6ft', 'colour': 'Black', 'quantity': 4},
        ]
        source.config_json = json.dumps(config)
        tracker.db.session.commit()
        self.process(source)
        dependent = self.upload_plan()
        self.generate(dependent, deduction=6)
        self.process(dependent)

        response = self.undo(source.id, source.stock_removed_at.isoformat())
        self.assertEqual(409, response.status_code, response.json)
        self.assertIn('dependent plan', response.json['error'])
        stock = tracker.TableStock.query.filter_by(type=self.rail_key).one()
        self.assertEqual(32, stock.count)
        self.assertIsNotNone(source.stock_removed_at)
        self.assertEqual(0, tracker.TableStockLog.query.filter_by(action_type='invoice_dispatch_undo').count())

        self.assertEqual(200, self.undo(dependent.id, dependent.stock_removed_at.isoformat()).status_code)
        self.assertEqual(200, self.undo(source.id, source.stock_removed_at.isoformat()).status_code)
        self.assertEqual(50, stock.count)
        self.assertEqual({('6ft', 'Black'): 5}, tracker.packaging_top_rail_balance())

    def test_undo_allows_a_plan_processed_without_stock_deductions(self):
        job = self.upload_plan()
        items = json.loads(job.items_json)
        items[0]['deduct_from_stock'] = False
        job.items_json = json.dumps(items)
        tracker.db.session.commit()
        self.process(job)
        self.assertEqual('[]', job.stock_removal_json)

        response = self.undo(job.id, job.stock_removed_at.isoformat())
        self.assertEqual(200, response.status_code, response.json)
        self.assertEqual(0, response.json['restored_quantity'])
        self.assertIsNone(job.stock_removed_at)
        self.assertEqual(50, tracker.TableStock.query.filter_by(type=self.rail_key).one().count)
        self.assertEqual(0, tracker.TableStockLog.query.count())

    def test_undo_rejects_unreadable_deductions_and_signed_out_requests(self):
        job = self.upload_plan()
        self.process(job)
        processed_at = job.stock_removed_at.isoformat()
        job.stock_removal_json = 'missing stock snapshot'
        tracker.db.session.commit()

        self.assertEqual(409, self.undo(job.id, processed_at).status_code)
        with self.client.session_transaction() as login:
            login.clear()
        self.assertEqual(401, self.undo(job.id, processed_at).status_code)
        self.assertIsNotNone(job.stock_removed_at)
        self.assertEqual(40, tracker.TableStock.query.filter_by(type=self.rail_key).one().count)


if __name__ == '__main__':
    unittest.main()
