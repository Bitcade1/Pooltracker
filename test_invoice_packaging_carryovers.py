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


if __name__ == '__main__':
    unittest.main()
