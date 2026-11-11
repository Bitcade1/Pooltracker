import os
import json
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
            "invoice_date": "2026-11-05", "due_date": "2026-12-04", "vat_rate": "0",
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
        self.assertIn("\u00a3633.50", response.get_data(as_text=True))
        self.assertEqual(4, response.data.count(b'class="item-quantity"'))

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

    def test_reference_addresses_and_editable_vat_are_prefilled_for_drafts(self):
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(tracker.INVOICE_SELLER_DETAILS, payload["presentation"]["seller_details"])
        self.assertEqual(tracker.INVOICE_CUSTOMER_DETAILS, payload["presentation"]["customer_details"])
        self.assertEqual("Thanks for your business.", payload["presentation"]["notes"])
        self.assertEqual(2000, payload["presentation"]["vat_basis_points"])
        self.assertEqual("2026-12-04", payload["presentation"]["due_date"])

    def test_presentation_dates_vat_and_addresses_save_and_survive_reopening(self):
        self.assertEqual(303, self.save_invoice("100.10", overrides={"vat_rate": "20", "due_date": "2026-11-13"}).status_code)
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(82670, payload["vat_pence"])
        self.assertEqual(496020, payload["grand_total_pence"])
        self.assertEqual("13 NOV 2026", payload["due_date_label"])
        self.assertEqual("5 Nov 2026", payload["invoice_date_label"])
        self.assertEqual(2000, json.loads(self.invoice.presentation_json)["vat_basis_points"])
        printed = self.client.get(f"/invoice_generator/{self.invoice_id}/print").get_data(as_text=True)
        for text in ("Invoice #400", "BILL TO", "FROM", "VAT (20%)", "\u00a34,960.20", "invoice-bitcade-logo.png"):
            self.assertIn(text, printed)

    def test_reference_invoice_391_amounts_calculate_exactly(self):
        items = [(20, 72800), (15, 52500), (5, 52500), (10, 72800), (1, 32000), (3, 32000), (24, 150)]
        self.invoice.lines_json = json.dumps([
            {"sku": str(index), "description": "Reference item", "quantity": quantity, "unit_cost_pence": cost}
            for index, (quantity, cost) in enumerate(items)
        ])
        self.invoice.saved_at = tracker.london_now()
        self.invoice.presentation_json = json.dumps({"invoice_date": "2026-11-05", "due_date": "2026-11-13", "vat_basis_points": 2000})
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(3365600, payload["total_pence"])
        self.assertEqual(3428600, payload["taxable_subtotal_pence"])
        self.assertEqual(685720, payload["vat_pence"])
        self.assertEqual(4114320, payload["grand_total_pence"])

    def test_legacy_saved_item_prices_and_vat_are_preserved_with_delivery_added(self):
        self.save_invoice("0.03")
        self.invoice.presentation_json = "{}"
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(0, payload["vat_pence"])
        self.assertEqual(payload["total_pence"] + 63000, payload["grand_total_pence"])

    def test_invalid_dates_and_vat_do_not_modify_a_saved_invoice(self):
        self.save_invoice("10.00")
        original = self.invoice.presentation_json
        for overrides in ({"vat_rate": "-1"}, {"vat_rate": "100.01"}, {"vat_rate": "NaN"},
                          {"invoice_date": "2026-02-30"}, {"due_date": "not-a-date"}):
            self.assertEqual(400, self.save_invoice(overrides=overrides).status_code)
            self.assertEqual(original, self.invoice.presentation_json)

    def test_vat_rounds_half_pennies_up(self):
        self.assertEqual(303, self.save_invoice("0.05", overrides={"vat_rate": "6"}).status_code)
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(175, payload["total_pence"])
        self.assertEqual(3791, payload["vat_pence"])
        self.assertEqual(66966, payload["grand_total_pence"])

    def test_delivery_is_three_trips_at_210_and_uses_the_invoice_vat_rate(self):
        self.save_invoice("100.00", overrides={"vat_rate": "20"})
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(35, payload["quantity"])
        self.assertEqual(3, payload["delivery_trips"])
        self.assertEqual(21000, payload["delivery_unit_pence"])
        self.assertEqual(63000, payload["delivery_pence"])
        self.assertEqual(413000, payload["taxable_subtotal_pence"])
        self.assertEqual(82600, payload["vat_pence"])
        self.assertEqual(495600, payload["grand_total_pence"])
        printed = self.client.get(f"/invoice_generator/{self.invoice_id}/print").get_data(as_text=True)
        self.assertLess(printed.index("Delivery (Per trip)"), printed.index("VAT (20%)"))
        self.assertEqual(1, printed.count("Delivery (Per trip)"))
        self.client.get("/invoice_generator")
        self.assertEqual(495600, tracker.generated_invoice_payload(self.invoice, {})["grand_total_pence"])

    def test_delivery_label_has_a_full_date_and_the_invoice_reference(self):
        invoice = tracker.GeneratedInvoice.query.filter_by(source_column="K").one()
        payload = tracker.generated_invoice_payload(invoice, {})
        self.assertEqual("Delivery Date 13/11/26 - INV-391", payload["delivery_label"])
        response = self.client.get(f"/invoice_generator?invoice={invoice.id}")
        self.assertIn(b"Upcoming invoices", response.data)
        self.assertIn(b"Delivery Date 13/11/26 - INV-391", response.data)
        self.assertNotIn(b"Order columns", response.data)

    def test_saving_invoice_shares_ordered_prices_with_other_drafts(self):
        self.assertEqual(303, self.save_invoice("123.45").status_code)
        price = tracker.db.session.get(tracker.InvoiceItemPrice, self.products[0]["sku"])
        self.assertEqual(12345, price.unit_cost_pence)
        other = tracker.GeneratedInvoice.query.filter_by(source_column="N").one()
        prices = {p.sku: p.unit_cost_pence for p in tracker.InvoiceItemPrice.query.all()}
        line = next(line for line in tracker.generated_invoice_payload(other, prices)["lines"] if line["sku"] == price.sku)
        self.assertEqual(12345, line["unit_cost_pence"])
        self.assertIsNone(other.saved_at)
        self.assertIsNone(tracker.db.session.get(tracker.InvoiceItemPrice, "BIL/CHAM/6FT/ROAK"))

    def test_shared_invoice_prices_do_not_rewrite_other_saved_invoices(self):
        self.save_invoice("10.00")
        previous = self.invoice.lines_json
        self.invoice = tracker.GeneratedInvoice.query.filter_by(source_column="N").one()
        self.save_invoice("20.00")
        original = tracker.db.session.get(tracker.GeneratedInvoice, self.invoice_id)
        self.assertEqual(previous, original.lines_json)
        self.assertEqual(2000, tracker.db.session.get(tracker.InvoiceItemPrice, self.products[0]["sku"]).unit_cost_pence)

    def test_blank_invoice_prices_do_not_clear_defaults_and_zero_is_shared(self):
        self.save_invoice("10.00")
        self.save_invoice("")
        self.assertEqual(1000, tracker.db.session.get(tracker.InvoiceItemPrice, self.products[0]["sku"]).unit_cost_pence)
        self.save_invoice("0.00")
        self.assertEqual(0, tracker.db.session.get(tracker.InvoiceItemPrice, self.products[0]["sku"]).unit_cost_pence)

    def custom_fields(self, descriptions=None, quantities=None, costs=None):
        return {"custom_items_present": "1", "custom_description": descriptions or [],
                "custom_quantity": quantities or [], "custom_cost": costs or []}

    def test_custom_items_save_print_and_calculate_vat_without_changing_table_quantity(self):
        fields = self.custom_fields(["Latch", "Replacement top rail"], ["24", "1"], ["1.50", "320.00"])
        fields["vat_rate"] = "20"
        self.assertEqual(303, self.save_invoice("100.00", overrides=fields).status_code)
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(35, payload["quantity"])
        self.assertEqual(35600, payload["custom_pence"])
        self.assertEqual(385600, payload["items_subtotal_pence"])
        self.assertEqual(448600, payload["taxable_subtotal_pence"])
        self.assertEqual(89720, payload["vat_pence"])
        self.assertEqual(538320, payload["grand_total_pence"])
        self.assertEqual(4, tracker.InvoiceItemPrice.query.count())
        response = self.client.get(f"/invoice_generator/{self.invoice_id}/print")
        text = response.get_data(as_text=True)
        self.assertEqual(2, text.count('class="custom-print-row"'))
        self.assertLess(text.index("Latch"), text.index("Delivery (Per trip)"))
        self.assertIn("\u00a35,383.20", text)
        tracker.db.session.remove()
        invoice = tracker.db.session.get(tracker.GeneratedInvoice, self.invoice_id)
        self.assertEqual("Replacement top rail", json.loads(invoice.presentation_json)["custom_items"][1]["description"])
        page = self.client.get(f"/invoice_generator?invoice={self.invoice_id}").get_data(as_text=True)
        self.assertIn('value="Replacement top rail"', page)
        self.assertIn('value="320.00"', page)

    def test_custom_items_can_be_removed_and_blank_prices_block_printing(self):
        fields = self.custom_fields(["Spare latch"], ["2"], [""])
        self.assertEqual(303, self.save_invoice("100.00", overrides=fields).status_code)
        self.assertEqual(1, tracker.generated_invoice_payload(self.invoice, {})["missing_prices"])
        self.assertEqual(302, self.client.get(f"/invoice_generator/{self.invoice_id}/print").status_code)
        self.assertEqual(303, self.save_invoice("100.00", overrides=self.custom_fields()).status_code)
        self.assertEqual([], tracker.generated_invoice_payload(self.invoice, {})["custom_items"])
        self.assertEqual(200, self.client.get(f"/invoice_generator/{self.invoice_id}/print").status_code)

    def test_invalid_custom_items_are_atomic_and_retain_submitted_fields(self):
        self.save_invoice("10.00")
        original = (self.invoice.presentation_json, self.invoice.lines_json)
        cases = [self.custom_fields([""], ["1"], ["1.00"]),
                 self.custom_fields(["Spare rail"], ["1.5"], ["1.00"]),
                 self.custom_fields(["Spare rail"], ["0"], ["1.00"]),
                 self.custom_fields(["Spare rail"], ["10001"], ["1.00"]),
                 self.custom_fields(["Spare rail"], ["1"], ["-1.00"]),
                 self.custom_fields(["Spare rail"], ["1"], ["NaN"]),
                 self.custom_fields(["Spare rail"], [], ["1.00"]),
                 self.custom_fields(["Spare rail"] * 51, ["1"] * 51, ["1.00"] * 51)]
        for fields in cases:
            response = self.save_invoice("20.00", overrides=fields)
            self.assertEqual(400, response.status_code)
            self.assertEqual(original, (self.invoice.presentation_json, self.invoice.lines_json))
            self.assertEqual({1000}, {price.unit_cost_pence for price in tracker.InvoiceItemPrice.query.all()})
        self.assertIn(b'Spare rail', response.data)

    def test_custom_description_is_escaped_and_zero_price_is_valid(self):
        fields = self.custom_fields(['<script>alert("custom")</script>'], ["1"], ["0.00"])
        self.assertEqual(303, self.save_invoice("10.00", overrides=fields).status_code)
        response = self.client.get(f"/invoice_generator/{self.invoice_id}/print")
        self.assertEqual(200, response.status_code)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert(', response.data)
        self.assertEqual(0, tracker.generated_invoice_payload(self.invoice, {})["custom_pence"])


if __name__ == "__main__":
    unittest.main()
