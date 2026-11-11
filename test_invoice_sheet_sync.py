import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import flask_app as tracker
from invoice_sheet_sync import InvoiceSyncError, parse_order_values, source_order_identity, fetch_invoice_source
import test_invoice_generator as invoice_tests


def source_values(source):
    orders = source["invoices"]
    rows = [[], ["", ""] + [order["month"] for order in orders],
            ["Model", "Sku"] + [order["collection_date"] for order in orders],
            ["", ""] + [str(order["expected_quantity"]) + " Tables" for order in orders],
            ["", ""] + [order["reference"] for order in orders]]
    for product in source["products"]:
        rows.append([product["description"], product["sku"]] + [str(order["quantities"][product["sku"]]) for order in orders])
    rows.append(["Total", ""] + [str(sum(order["quantities"].values())) for order in orders])
    return rows


class SheetParserTests(unittest.TestCase):
    def setUp(self):
        self.source = tracker.invoice_order_seed_source()
        self.values = source_values(self.source)

    def test_mixed_date_formats_and_blank_future_columns(self):
        self.values[2][2] = "08/14"
        self.values[2][6] = "2/10"
        self.values[1].extend(["January", "", "February"])
        result = parse_order_values(self.values, self.source)
        self.assertEqual(12, len(result["invoices"]))
        self.assertEqual("14/08", result["invoices"][0]["collection_date"])
        self.assertEqual("02/10", result["invoices"][4]["collection_date"])
        self.assertEqual("04/12", result["invoices"][10]["collection_date"])
        self.assertEqual(self.source["products"], result["products"])

    def test_duplicate_dates_and_invalid_quantities_are_rejected(self):
        self.values[2][13] = "04/12"
        with self.assertRaisesRegex(InvoiceSyncError, "Duplicate"):
            parse_order_values(self.values, self.source)
        self.values = source_values(self.source)
        for quantity in ("-1", "1.5", "#REF!", "NaN"):
            self.values[5][2] = quantity
            with self.assertRaisesRegex(InvoiceSyncError, "whole number"):
                parse_order_values(self.values, self.source)

    def test_partial_or_empty_sheet_fails_without_accepting_an_empty_snapshot(self):
        for rows in ([], self.values[:-1]):
            with self.assertRaises(InvoiceSyncError):
                parse_order_values(rows, self.source)
        self.values[2][2] = ""
        with self.assertRaisesRegex(InvoiceSyncError, "no collection date"):
            parse_order_values(self.values, self.source)

    def test_explicit_order_ids_allow_duplicate_dates(self):
        self.values[2][13] = "04/12"
        self.values.insert(5, ["Order ID", ""] + ["order-" + str(i) for i in range(12)])
        result = parse_order_values(self.values, self.source)
        self.assertEqual("id:order-11", source_order_identity(result["invoices"][11], result))

    @patch("google.oauth2.service_account.Credentials.from_service_account_file")
    @patch("google.auth.transport.requests.AuthorizedSession")
    def test_read_only_client_uses_metadata_bounds_and_read_only_scope(self, session_type, credentials_type):
        connection = session_type.return_value.__enter__.return_value
        metadata = MagicMock(status_code=200)
        metadata.json.return_value = {"properties": {"title": self.source["source_title"]}, "sheets": [{"properties": {
            "title": "Orders", "gridProperties": {"rowCount": 1006, "columnCount": 29},
        }}]}
        cells = MagicMock(status_code=200)
        cells.json.return_value = {"values": self.values}
        connection.get.side_effect = [metadata, cells]
        result = fetch_invoice_source(self.source, "test-key.json")
        self.assertEqual(12, len(result["invoices"]))
        self.assertEqual(["https://www.googleapis.com/auth/spreadsheets.readonly"], credentials_type.call_args.kwargs["scopes"])
        self.assertIn("A1%3AAC200", connection.get.call_args.args[0])
        self.assertEqual(2, connection.get.call_count)


class InvoiceSyncTests(unittest.TestCase):
    setUpClass = classmethod(invoice_tests.InvoiceGeneratorTests.setUpClass.__func__)
    setUp = invoice_tests.InvoiceGeneratorTests.setUp
    tearDown = invoice_tests.InvoiceGeneratorTests.tearDown
    save_prices = invoice_tests.InvoiceGeneratorTests.save_prices
    save_invoice = invoice_tests.InvoiceGeneratorTests.save_invoice

    def incoming(self):
        return copy.deepcopy(tracker.invoice_order_seed_source())

    def change_order(self, source):
        order = next(order for order in source["invoices"] if order["collection_date"] == "04/12")
        order["quantities"][self.products[0]["sku"]] += 3
        return order

    def delete_invoice(self, invoice=None, version=None):
        invoice = invoice or self.invoice
        return self.client.post(f"/invoice_generator?invoice={invoice.id}", data={
            "action": "delete_invoice", "invoice_id": invoice.id, "csrf_token": self.token,
            "invoice_version": version or tracker.invoice_record_version(invoice),
        })

    def restore_invoice(self, invoice_id=None):
        return self.client.post("/invoice_generator", data={
            "action": "restore_invoice", "invoice_id": invoice_id or self.invoice_id, "csrf_token": self.token,
        })

    def review(self, decision, version=None):
        data = json.loads(tracker.db.session.get(tracker.InvoiceSheetSyncState, 1).data_json)
        change = data["pending"][str(self.invoice_id)]
        token = hashlib.sha256(json.dumps(change, sort_keys=True).encode()).hexdigest()
        return self.client.post("/invoice_generator", data={
            "action": "review_sheet_change", "decision": decision, "invoice_id": self.invoice_id,
            "csrf_token": self.token, "change_version": version or token,
        })

    def test_sync_updates_drafts_and_does_not_change_saved_sku_prices(self):
        self.save_prices("123.45")
        source = self.incoming()
        self.change_order(source)
        result = tracker.apply_invoice_sheet_source(source)
        self.assertEqual(1, result["updated"])
        self.assertEqual(38, tracker.generated_invoice_payload(self.invoice, {})["quantity"])
        self.assertEqual({12345}, {price.unit_cost_pence for price in tracker.InvoiceItemPrice.query.all()})
        self.client.get("/invoice_generator")
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())

    def test_moved_columns_preserve_the_invoice_ids_and_saved_price_snapshot(self):
        self.save_invoice("100.10")
        source = self.incoming()
        columns = [order["source_column"] for order in source["invoices"]][::-1]
        for order, column in zip(source["invoices"], columns):
            order["source_column"] = column
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())
        self.assertEqual("D", self.invoice.source_column)
        self.assertEqual(350350, tracker.generated_invoice_payload(self.invoice, {})["total_pence"])
        self.assertEqual({}, json.loads(tracker.db.session.get(tracker.InvoiceSheetSyncState, 1).data_json)["pending"])

    def test_new_columns_add_once_and_missing_columns_never_delete_invoices(self):
        source = self.incoming()
        new_order = copy.deepcopy(source["invoices"][-1])
        new_order.update(source_column="O", month="January", collection_date="08/01")
        source["invoices"].append(new_order)
        self.assertEqual(1, tracker.apply_invoice_sheet_source(source)["created"])
        self.assertEqual(0, tracker.apply_invoice_sheet_source(source)["created"])
        source["invoices"].pop(0)
        self.assertEqual(1, tracker.apply_invoice_sheet_source(source)["missing"])
        self.assertEqual(13, tracker.GeneratedInvoice.query.count())

    def test_saved_invoice_change_is_flagged_until_applied_and_prices_are_kept(self):
        self.save_invoice("100.10")
        original = self.invoice.lines_json
        source = self.incoming()
        self.change_order(source)
        self.assertEqual(1, tracker.apply_invoice_sheet_source(source)["review"])
        self.assertEqual(original, self.invoice.lines_json)
        response = self.client.get(f"/invoice_generator?invoice={self.invoice_id}")
        self.assertIn(b"The sheet has changed", response.data)
        self.assertEqual(302, self.client.get(f"/invoice_generator/{self.invoice_id}/print").status_code)
        self.assertEqual(303, self.review("apply").status_code)
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(38, payload["quantity"])
        self.assertEqual(380380, payload["total_pence"])
        self.assertEqual("Customer\nAddress", self.invoice.customer_details)
        self.assertEqual(0, tracker.apply_invoice_sheet_source(source)["review"])

    def test_keeping_saved_invoice_suppresses_only_the_reviewed_change(self):
        self.save_invoice("10.00")
        source = self.incoming()
        self.change_order(source)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(303, self.review("keep").status_code)
        self.assertEqual(35, tracker.generated_invoice_payload(self.invoice, {})["quantity"])
        self.assertEqual(0, tracker.apply_invoice_sheet_source(source)["review"])
        self.change_order(source)
        self.assertEqual(1, tracker.apply_invoice_sheet_source(source)["review"])
        self.assertEqual(400, self.review("apply", "outdated-review-token").status_code)

    def test_old_invoice_form_cannot_overwrite_quantities_after_background_sync(self):
        version = tracker.invoice_record_version(self.invoice)
        source = self.incoming()
        self.change_order(source)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(400, self.save_invoice(overrides={"invoice_version": version}).status_code)
        self.assertIsNone(self.invoice.saved_at)
        self.assertEqual(38, tracker.generated_invoice_payload(self.invoice, {})["quantity"])

    def test_date_replacement_aborts_the_entire_sync(self):
        source = self.incoming()
        self.change_order(source)
        source["invoices"][0]["collection_date"] = "15/08"
        with self.assertRaisesRegex(InvoiceSyncError, "replaced"):
            tracker.apply_invoice_sheet_source(source)
        self.assertEqual(35, tracker.generated_invoice_payload(self.invoice, {})["quantity"])

    def test_sync_failure_keeps_last_success_and_records_status(self):
        tracker.apply_invoice_sheet_source(self.incoming())
        state = tracker.db.session.get(tracker.InvoiceSheetSyncState, 1)
        success, snapshot = state.last_success_at, state.data_json
        with patch.object(tracker, "fetch_invoice_source", side_effect=InvoiceSyncError("Google connection failed")):
            with self.assertRaises(InvoiceSyncError):
                tracker.sync_invoice_sheet()
        self.assertEqual(snapshot, state.data_json)
        self.assertEqual(success, state.last_success_at)
        self.assertEqual("Google connection failed", state.last_error)
        response = self.client.get("/invoice_generator")
        self.assertIn(b"Google connection failed", response.data)

    def test_explicit_order_ids_keep_identity_when_a_collection_date_changes(self):
        source = self.incoming()
        for index, order in enumerate(source["invoices"]):
            order["order_id"] = "order-" + str(index)
        tracker.apply_invoice_sheet_source(source)
        source["invoices"][10]["collection_date"] = "05/12"
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())
        self.assertEqual("05/12", self.invoice.collection_date)

    def test_new_product_keeps_saved_prices_and_needs_pricing_only_if_ordered(self):
        self.save_invoice("100.10")
        source = self.incoming()
        product = {"sku": "NEW-SKU", "description": "New product"}
        source["products"].append(product)
        for order in source["invoices"]:
            order["quantities"][product["sku"]] = 0
        self.assertEqual(0, tracker.apply_invoice_sheet_source(source)["review"])
        source["invoices"][10]["quantities"][product["sku"]] = 2
        self.assertEqual(1, tracker.apply_invoice_sheet_source(source)["review"])
        self.assertEqual(303, self.review("apply").status_code)
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(1, payload["missing_prices"])
        self.assertEqual(350350, payload["total_pence"])
        self.assertEqual(302, self.client.get(f"/invoice_generator/{self.invoice_id}/print").status_code)

    def test_existing_invoices_adopt_sync_state_without_resetting_prices(self):
        self.save_invoice("100.10")
        tracker.db.session.remove()
        tracker.InvoiceSheetSyncState.__table__.drop(tracker.db.engine)
        tracker.ensure_invoice_generator_tables()
        invoice = tracker.db.session.get(tracker.GeneratedInvoice, self.invoice_id)
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())
        self.assertEqual("INV-400", invoice.reference)
        self.assertEqual(350350, tracker.generated_invoice_payload(invoice, {})["total_pence"])

    def test_deleted_invoice_disappears_and_cannot_be_printed_or_saved(self):
        self.save_invoice("10.00")
        original = self.invoice.lines_json
        self.assertEqual(303, self.delete_invoice().status_code)
        response = self.client.get("/invoice_generator")
        self.assertEqual(11, response.data.count(b'class="invoice-link '))
        self.assertIn(b"Deleted invoices (1)", response.data)
        self.assertEqual(404, self.client.get(f"/invoice_generator?invoice={self.invoice_id}").status_code)
        self.assertEqual(404, self.client.get(f"/invoice_generator/{self.invoice_id}/print").status_code)
        self.assertEqual(404, self.save_invoice("20.00").status_code)
        self.assertEqual(original, self.invoice.lines_json)
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())
        self.assertEqual(1000, tracker.db.session.get(tracker.InvoiceItemPrice, self.products[0]["sku"]).unit_cost_pence)

    def test_sync_does_not_restore_or_update_deleted_invoice_after_columns_move(self):
        self.save_invoice("10.00")
        original = self.invoice.lines_json
        self.delete_invoice()
        source = self.incoming()
        self.change_order(source)
        columns = [order["source_column"] for order in source["invoices"]][::-1]
        for index, (order, column) in enumerate(zip(source["invoices"], columns)):
            order["source_column"] = column
            order["order_id"] = "order-" + str(index)
        for _ in range(2):
            result = tracker.apply_invoice_sheet_source(source)
            self.assertEqual(0, result["created"])
            self.assertEqual(0, result["review"])
        self.assertEqual(original, self.invoice.lines_json)
        self.assertEqual(12, tracker.GeneratedInvoice.query.count())
        self.assertEqual(11, self.client.get("/invoice_generator").data.count(b'class="invoice-link '))

    def test_restoring_saved_invoice_reviews_sheet_changes_and_keeps_prices(self):
        self.save_invoice("10.00")
        original = self.invoice.lines_json
        self.delete_invoice()
        source = self.incoming()
        self.change_order(source)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(303, self.restore_invoice().status_code)
        self.assertEqual(original, self.invoice.lines_json)
        response = self.client.get(f"/invoice_generator?invoice={self.invoice_id}")
        self.assertIn(b"The sheet has changed", response.data)
        self.assertEqual(303, self.review("apply").status_code)
        self.assertEqual(38000, tracker.generated_invoice_payload(self.invoice, {})["total_pence"])

    def test_delete_every_invoice_keeps_empty_page_usable_and_restore_refreshes_draft(self):
        for invoice in tracker.GeneratedInvoice.query.all():
            self.assertEqual(303, self.delete_invoice(invoice).status_code)
        source = self.incoming()
        self.change_order(source)
        tracker.apply_invoice_sheet_source(source)
        response = self.client.get("/invoice_generator")
        self.assertEqual(200, response.status_code)
        self.assertEqual(0, response.data.count(b'class="invoice-link '))
        self.assertIn(b"Deleted invoices (12)", response.data)
        self.assertEqual(303, self.restore_invoice().status_code)
        self.assertEqual(38, tracker.generated_invoice_payload(self.invoice, {})["quantity"])

    def test_delete_requires_login_csrf_and_an_unchanged_invoice(self):
        data = {"action": "delete_invoice", "invoice_id": self.invoice_id}
        self.assertEqual(303, self.app.test_client().post("/invoice_generator", data=data).status_code)
        self.assertEqual(400, self.client.post("/invoice_generator", data=data).status_code)
        version = tracker.invoice_record_version(self.invoice)
        source = self.incoming()
        self.change_order(source)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(400, self.delete_invoice(version=version).status_code)
        self.assertEqual({}, json.loads(tracker.db.session.get(tracker.InvoiceSheetSyncState, 1).data_json).get("deleted", {}))

    def test_automatic_references_start_at_391_for_13_november(self):
        expected = {"K": "INV-391", "L": "INV-392", "M": "INV-393", "N": "INV-394"}
        for column, reference in expected.items():
            self.assertEqual(reference, tracker.GeneratedInvoice.query.filter_by(source_column=column).one().reference)
        self.assertEqual("TBC", tracker.GeneratedInvoice.query.filter_by(source_column="J").one().reference)
        self.assertEqual("INV-380", tracker.GeneratedInvoice.query.filter_by(source_column="C").one().reference)

    def test_sheet_sync_and_review_keep_the_allocated_reference(self):
        self.save_invoice("10.00", overrides={"reference": "INV-393"})
        source = self.incoming()
        self.change_order(source)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual("INV-393", self.invoice.reference)
        self.assertEqual(303, self.review("apply").status_code)
        self.assertEqual("INV-393", self.invoice.reference)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual("INV-393", self.invoice.reference)

    def test_moving_columns_does_not_change_allocated_references(self):
        references = {invoice.id: invoice.reference for invoice in tracker.GeneratedInvoice.query.all()}
        source = self.incoming()
        for order, column in zip(source["invoices"], [order["source_column"] for order in source["invoices"]][::-1]):
            order["source_column"] = column
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(references, {invoice.id: invoice.reference for invoice in tracker.GeneratedInvoice.query.all()})

    def test_deleted_references_are_reserved_for_future_orders(self):
        first = tracker.GeneratedInvoice.query.filter_by(source_column="K").one()
        self.delete_invoice(first)
        source = self.incoming()
        order = copy.deepcopy(source["invoices"][-1])
        order.update(source_column="O", month="January", collection_date="08/01")
        source["invoices"].append(order)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual("INV-395", tracker.GeneratedInvoice.query.filter_by(source_column="O").one().reference)
        self.restore_invoice(first.id)
        self.assertEqual("INV-391", first.reference)

    def test_inserted_collection_gets_next_number_without_renumbering(self):
        source = self.incoming()
        order = copy.deepcopy(source["invoices"][-1])
        order.update(source_column="O", month="November", collection_date="27/11")
        source["invoices"].append(order)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual("INV-395", tracker.GeneratedInvoice.query.filter_by(source_column="O").one().reference)
        self.assertEqual("INV-393", self.invoice.reference)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual("INV-395", tracker.GeneratedInvoice.query.filter_by(source_column="O").one().reference)

    def test_manual_references_are_not_replaced_or_issued_to_new_orders(self):
        earlier = tracker.GeneratedInvoice.query.filter_by(source_column="C").one()
        earlier.reference = "INV-395"
        earlier.saved_at = tracker.london_now()
        tracker.db.session.commit()
        source = self.incoming()
        order = copy.deepcopy(source["invoices"][-1])
        order.update(source_column="O", month="January", collection_date="08/01")
        source["invoices"].append(order)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual("INV-395", earlier.reference)
        self.assertEqual("INV-396", tracker.GeneratedInvoice.query.filter_by(source_column="O").one().reference)

    def test_existing_saved_tbc_invoice_receives_number_without_other_changes(self):
        self.save_invoice("10.00", overrides={"reference": "TBC"})
        original = (self.invoice.lines_json, self.invoice.saved_at, self.invoice.presentation_json)
        state = tracker.db.session.get(tracker.InvoiceSheetSyncState, 1)
        data = json.loads(state.data_json)
        del data["reference_assignments"]
        del data["next_reference_number"]
        state.data_json = json.dumps(data)
        tracker.db.session.commit()
        tracker.ensure_invoice_generator_tables()
        self.assertEqual("INV-393", self.invoice.reference)
        self.assertEqual(original, (self.invoice.lines_json, self.invoice.saved_at, self.invoice.presentation_json))

    def test_custom_items_survive_sync_review_delete_and_restore(self):
        self.save_invoice("10.00", overrides={"custom_items_present": "1", "custom_description": ["Extra top rail"],
                                             "custom_quantity": ["2"], "custom_cost": ["320.00"]})
        presentation = self.invoice.presentation_json
        source = self.incoming()
        self.change_order(source)
        tracker.apply_invoice_sheet_source(source)
        self.assertEqual(presentation, self.invoice.presentation_json)
        self.assertEqual(303, self.review("apply").status_code)
        self.assertEqual(presentation, self.invoice.presentation_json)
        self.delete_invoice()
        tracker.apply_invoice_sheet_source(source)
        self.restore_invoice()
        self.assertEqual(presentation, self.invoice.presentation_json)
        self.assertEqual(64000, tracker.generated_invoice_payload(self.invoice, {})["custom_pence"])


class SyncCommandTests(unittest.TestCase):
    def test_missing_database_does_not_create_an_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            result = subprocess.run([sys.executable, "-B", "sync_invoice_orders.py", "--database", str(path)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(1, result.returncode)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
