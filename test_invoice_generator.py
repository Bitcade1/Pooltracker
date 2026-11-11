import os
import unittest

from flask import Flask

import flask_app as tracker


class InvoiceGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__, template_folder=os.path.join(tracker.basedir, "templates"))
        cls.app.config.update(
            TESTING=True, SECRET_KEY="test-only",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
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
        self.client = self.app.test_client()
        with self.client.session_transaction() as login:
            login["worker"] = "Invoice Operator"
        self.assertEqual(200, self.client.get("/invoice_generator").status_code)
        with self.client.session_transaction() as login:
            self.token = login["invoice_generator_csrf"]
        self.invoice = tracker.GeneratedInvoice.query.filter_by(source_column="M").one()
        self.invoice_id = self.invoice.id
        self.products = tracker.invoice_order_source()["products"]

    def tearDown(self):
        tracker.db.session.remove()
        tracker.InvoiceSheetSyncState.__table__.drop(tracker.db.engine)
        tracker.GeneratedInvoice.__table__.drop(tracker.db.engine)
        tracker.InvoiceItemPrice.__table__.drop(tracker.db.engine)
        self.context.pop()

    def save_prices(self, value="100.10", overrides=None):
        data = {"action": "save_prices", "csrf_token": self.token,
                "catalogue_version": tracker.invoice_catalogue_version(tracker.invoice_order_source())}
        data.update({"price_" + product["sku"]: value for product in self.products})
        data.update(overrides or {})
        return self.client.post("/invoice_generator", data=data)

    def save_invoice(self, value="100.10", overrides=None):
        data = {
            "action": "save_invoice", "csrf_token": self.token,
            "invoice_id": self.invoice.id, "reference": "INV-400",
            "invoice_version": tracker.invoice_record_version(self.invoice),
            "collection_date": "04/12", "seller_details": "Seller\nAddress",
            "customer_details": "Customer\nAddress", "notes": "Payment on collection",
        }
        data.update({"cost_" + product["sku"]: value for product in self.products})
        data.update(overrides or {})
        return self.client.post("/invoice_generator", data=data)

    def test_each_dated_column_is_a_distinct_invoice_and_import_is_idempotent(self):
        self.client.get("/invoice_generator")
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual("04/12", self.invoice.collection_date)
        self.assertEqual(35, payload["quantity"])
        self.assertEqual([5, 10, 5, 15], [line["quantity"] for line in payload["lines"] if line["quantity"]])
        self.assertEqual(4, payload["missing_prices"])

    def test_prices_persist_across_sessions_and_default_other_drafts(self):
        self.assertEqual(303, self.save_prices().status_code)
        tracker.db.session.remove()
        client = self.app.test_client()
        with client.session_transaction() as login:
            login["worker"] = "Another Operator"
        response = client.get(f"/invoice_generator?invoice={self.invoice_id}")
        self.assertEqual(200, response.status_code)
        self.assertIn(b'value="100.10"', response.data)
        prices = {price.sku: price.unit_cost_pence for price in tracker.InvoiceItemPrice.query.all()}
        self.assertEqual({10010}, set(prices.values()))
        other = tracker.GeneratedInvoice.query.filter_by(source_column="N").one()
        self.assertEqual(350350, tracker.generated_invoice_payload(other, prices)["total_pence"])

    def test_saved_invoice_keeps_snapshot_when_defaults_change(self):
        self.save_prices()
        self.assertEqual(303, self.save_invoice("0.10").status_code)
        tracker.db.session.remove()
        self.invoice = tracker.db.session.get(tracker.GeneratedInvoice, self.invoice_id)
        self.save_prices("500.00")
        payload = tracker.generated_invoice_payload(self.invoice, {product["sku"]: 50000 for product in self.products})
        self.assertEqual(350, payload["total_pence"])
        self.assertEqual("INV-400", self.invoice.reference)
        self.assertEqual("Customer\nAddress", self.invoice.customer_details)
        self.assertEqual("Invoice Operator", self.invoice.saved_by)
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())
        response = self.client.get(f"/invoice_generator/{self.invoice.id}/print")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"GBP 3.50", response.data)
        self.assertEqual(4, response.data.count(b'<span class="sku">'))

    def test_blank_cost_is_unpriced_and_explicit_zero_is_valid(self):
        self.save_invoice("")
        self.assertEqual(4, tracker.generated_invoice_payload(self.invoice, {})["missing_prices"])
        self.assertEqual(302, self.client.get(f"/invoice_generator/{self.invoice.id}/print").status_code)
        self.save_invoice("0.00")
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(0, payload["missing_prices"])
        self.assertEqual(0, payload["total_pence"])
        self.assertEqual(200, self.client.get(f"/invoice_generator/{self.invoice.id}/print").status_code)

    def test_invalid_prices_are_rejected_atomically_and_edits_are_retained(self):
        self.save_prices("100.00")
        sku = self.products[-1]["sku"]
        for invalid in ("-1", "NaN", "Infinity", "1e3", "1.001", "100000000", "1,000"):
            response = self.save_prices("200.00", {"price_" + sku: invalid})
            self.assertEqual(400, response.status_code, invalid)
            self.assertEqual({10000}, {price.unit_cost_pence for price in tracker.InvoiceItemPrice.query.all()})
        self.save_invoice("1.00")
        previous_lines = self.invoice.lines_json
        response = self.save_invoice("2.00", {"cost_" + sku: "-5", "notes": "Keep my edits"})
        self.assertEqual(400, response.status_code)
        self.assertEqual(previous_lines, self.invoice.lines_json)
        self.assertIn(b"Keep my edits", response.data)

    def test_authentication_csrf_and_missing_invoice(self):
        client = self.app.test_client()
        self.assertEqual(302, client.get("/invoice_generator").status_code)
        self.assertEqual(303, client.post("/invoice_generator", data={"action": "save_prices"}).status_code)
        self.assertEqual(302, client.get(f"/invoice_generator/{self.invoice.id}/print").status_code)
        self.assertEqual(400, self.client.post("/invoice_generator", data={"action": "save_prices"}).status_code)
        self.assertEqual(404, self.client.get("/invoice_generator?invoice=99999").status_code)
        self.assertEqual(404, self.client.get("/invoice_generator/99999/print").status_code)

    def test_source_mismatch_is_visible_and_uses_product_quantities(self):
        invoice = tracker.GeneratedInvoice.query.filter_by(source_column="J").one()
        response = self.client.get(f"/invoice_generator?invoice={invoice.id}")
        self.assertIn(b"column heading says 50 tables", response.data)
        self.assertIn(b"quantities add up to 52", response.data)
        self.assertEqual(52, tracker.generated_invoice_payload(invoice, {})["quantity"])


if __name__ == "__main__":
    unittest.main()
