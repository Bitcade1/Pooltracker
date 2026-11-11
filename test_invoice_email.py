import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfReader
import requests

import flask_app as tracker
import invoice_email as mail
import test_invoice_generator as generator_tests


class InvoiceEmailTests(unittest.TestCase):
    setUpClass = classmethod(generator_tests.InvoiceGeneratorTests.setUpClass.__func__)
    save_invoice = generator_tests.InvoiceGeneratorTests.save_invoice

    def setUp(self):
        generator_tests.InvoiceGeneratorTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.directory.name, "config.json")
        self.store = Path(self.directory.name, "private")
        self.store.mkdir()
        self.config = {"tenant_id": "11111111-1111-1111-1111-111111111111", "client_id": "22222222-2222-2222-2222-222222222222",
                       "client_secret": "test-client-secret", "redirect_uri": "https://pooltabletracker.com/invoice_email/callback",
                       "mailbox": "jack.england@bitcade.co.uk", "data_dir": str(self.store)}
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.app.config.update(SECRET_KEY="test-secret-" * 5, INVOICE_EMAIL_CONFIG_PATH=str(self.config_path))
        # Existing cookie was signed with the fixture's short key. Log in again under the email key.
        with self.client.session_transaction() as login:
            login["worker"] = "Invoice Operator"
            login["invoice_generator_csrf"] = self.token
        self.save_invoice("100.00", {"customer_details": "Customer\naccounts@example.com", "vat_rate": "20"})
        self.invoice_id = self.invoice.id
        self.url = f"/invoice_generator/{self.invoice_id}/email"
        self.pdf = b"%PDF-1.7\nexample snapshot"
        self.post_guard = patch("invoice_email.requests.post", side_effect=AssertionError("Real email sending is forbidden in tests"))
        self.get_guard = patch("invoice_email.requests.get", side_effect=AssertionError("Unexpected external request"))
        self.post_guard.start()
        self.get_guard.start()

    def tearDown(self):
        self.post_guard.stop()
        self.get_guard.stop()
        tracker.db.session.remove()
        tracker.InvoiceEmailAttempt.__table__.drop(tracker.db.engine, checkfirst=True)
        generator_tests.InvoiceGeneratorTests.tearDown(self)
        self.directory.cleanup()

    def connect(self):
        (self.store / "account.json").write_text(json.dumps({"mailbox": self.config["mailbox"], "home_account_id": "account-id"}))
        (self.store / "tokens.json").write_text("{}")

    def form(self):
        response = self.client.get(self.url)
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        return {"csrf_token": self.token, "send_token": re.search(r'name="send_token" value="([a-f0-9]+)"', html)[1],
                "invoice_version": tracker.invoice_record_version(self.invoice), "recipient": "accounts@example.com", "cc": "copy@example.com",
                "subject": "Invoice INV-400", "body": "Hello,\nPlease find your invoice attached.\nBitCade"}

    def send(self, data, status=202):
        with patch("invoice_email.access_token", return_value="private-access-token"), patch("invoice_email.render_pdf", return_value=self.pdf), \
                patch("invoice_email.requests.post", return_value=Mock(status_code=status)) as post:
            response = self.client.post(self.url, data=data)
        return response, post

    def test_composer_defaults_setup_message_and_authentication(self):
        response = self.client.get(self.url)
        html = response.get_data(as_text=True)
        self.assertIn('name="recipient" value="accounts@example.com"', html)
        self.assertIn("Invoice INV-400 - Delivery 04/12/26", html)
        self.assertIn("\u00a34,956.00", html)
        self.assertIn("Connect Microsoft 365", html)
        self.assertRegex(html, r'id="send-invoice"\s+disabled')
        anonymous = self.app.test_client()
        for path in (self.url, f"/invoice_generator/{self.invoice_id}/pdf", f"/invoice_generator/{self.invoice_id}/emails/missing/pdf", "/invoice_email/callback"):
            self.assertEqual(302, anonymous.get(path).status_code)
        self.config_path.unlink()
        self.assertIn(b"one-time server setup", self.client.get(self.url).data)

    def test_send_payload_pdf_audit_and_double_submit(self):
        self.connect()
        data = self.form()
        response, post = self.send(data)
        self.assertEqual(303, response.status_code)
        self.assertEqual(1, post.call_count)
        kwargs = post.call_args.kwargs
        self.assertEqual("https://graph.microsoft.com/v1.0/me/sendMail", post.call_args.args[0])
        self.assertEqual("Bearer private-access-token", kwargs["headers"]["Authorization"])
        payload = kwargs["json"]
        self.assertIs(True, payload["saveToSentItems"])
        self.assertEqual("Text", payload["message"]["body"]["contentType"])
        self.assertEqual(self.pdf, base64.b64decode(payload["message"]["attachments"][0]["contentBytes"]))
        self.assertEqual("INV-400.pdf", payload["message"]["attachments"][0]["name"])
        attempt = tracker.db.session.get(tracker.InvoiceEmailAttempt, data["send_token"])
        self.assertEqual("accepted", attempt.status)
        self.assertEqual("Invoice Operator", attempt.worker)
        self.assertEqual(self.pdf, attempt.pdf_bytes)
        self.assertNotIn("private-access-token", self.client.get(self.url).get_data(as_text=True))
        self.assertEqual(303, self.client.post(self.url, data=data).status_code)
        self.assertEqual(1, tracker.InvoiceEmailAttempt.query.count())
        self.save_invoice("200.00")
        snapshot = self.client.get(f"/invoice_generator/{self.invoice_id}/emails/{attempt.id}/pdf")
        self.assertEqual(self.pdf, snapshot.data)
        self.assertIn("no-store", snapshot.headers["Cache-Control"])

    def test_duplicate_new_form_requires_deliberate_resend(self):
        self.connect()
        self.send(self.form())
        response, post = self.send(self.form())
        self.assertEqual(400, response.status_code)
        self.assertIn(b"already has a send request", response.data)
        post.assert_not_called()
        data = self.form()
        data["send_another"] = "1"
        response, post = self.send(data)
        self.assertEqual(303, response.status_code)
        self.assertEqual(1, post.call_count)
        self.assertEqual(2, tracker.InvoiceEmailAttempt.query.count())

    def test_timeout_is_uncertain_and_never_automatically_retried(self):
        self.connect()
        data = self.form()
        with patch("invoice_email.access_token", return_value="token"), patch("invoice_email.render_pdf", return_value=self.pdf), \
                patch("invoice_email.requests.post", side_effect=requests.Timeout()) as post:
            self.assertEqual(303, self.client.post(self.url, data=data).status_code)
            self.assertEqual(1, post.call_count)
        attempt = tracker.db.session.get(tracker.InvoiceEmailAttempt, data["send_token"])
        self.assertEqual("unknown", attempt.status)
        self.assertIn("Check Sent Items", attempt.detail)
        response, post = self.send(self.form())
        self.assertEqual(400, response.status_code)
        post.assert_not_called()

    def test_rejected_send_and_pdf_failure_are_recorded_without_false_success(self):
        self.connect()
        data = self.form()
        self.send(data, status=403)
        self.assertEqual("failed", tracker.db.session.get(tracker.InvoiceEmailAttempt, data["send_token"]).status)
        data = self.form()
        with patch("invoice_email.access_token", return_value="token"), patch("invoice_email.render_pdf", side_effect=mail.InvoiceEmailError("PDF is unavailable")):
            self.assertEqual(400, self.client.post(self.url, data=data).status_code)
        self.assertEqual("failed", tracker.db.session.get(tracker.InvoiceEmailAttempt, data["send_token"]).status)

    def test_invalid_message_csrf_connection_and_stale_invoice_never_send(self):
        self.connect()
        for key, invalid in (("recipient", "two@example.com,three@example.com"), ("recipient", "x@example.com\nBcc:bad@example.com"),
                             ("cc", "invalid"), ("subject", "hello\r\nBcc: bad@example.com"), ("body", ""),
                             ("invoice_version", "stale"), ("send_token", "0" * 32), ("csrf_token", "wrong")):
            data = dict(self.form(), **{key: invalid})
            response, post = self.send(data)
            self.assertEqual(400, response.status_code, key)
            post.assert_not_called()
        self.assertEqual(0, tracker.InvoiceEmailAttempt.query.count())
        (self.store / "tokens.json").unlink()
        response, post = self.send(self.form())
        self.assertEqual(400, response.status_code)
        post.assert_not_called()

    def test_draft_unpriced_deleted_and_pending_invoices_cannot_be_emailed(self):
        self.invoice.saved_at = None
        tracker.db.session.commit()
        self.assertEqual(302, self.client.get(self.url).status_code)
        self.save_invoice("")
        self.assertEqual(302, self.client.get(self.url).status_code)
        self.save_invoice("0.00")
        state = tracker.db.session.get(tracker.InvoiceSheetSyncState, 1)
        data = json.loads(state.data_json)
        data["pending"][str(self.invoice_id)] = {"change": "test"}
        state.data_json = json.dumps(data)
        tracker.db.session.commit()
        self.assertEqual(302, self.client.get(self.url).status_code)
        data["deleted"] = {str(self.invoice_id): {"by": "test"}}
        state.data_json = json.dumps(data)
        tracker.db.session.commit()
        self.assertEqual(404, self.client.get(self.url).status_code)

    def test_invoice_edited_during_pdf_creation_is_not_sent(self):
        self.connect()
        data = self.form()

        def edit(*args):
            self.invoice.notes = "Changed while rendering"
            tracker.db.session.commit()
            return self.pdf

        with patch("invoice_email.access_token", return_value="token"), patch("invoice_email.render_pdf", side_effect=edit):
            response = self.client.post(self.url, data=data)
        self.assertEqual(400, response.status_code)
        self.assertIn(b"changed while", response.data)
        self.assertEqual("failed", tracker.db.session.get(tracker.InvoiceEmailAttempt, data["send_token"]).status)

    def test_oauth_verifier_stays_server_side_state_expires_and_wrong_mailbox_is_rejected(self):
        client = Mock()
        client.initiate_auth_code_flow.return_value = {"auth_uri": "https://login.microsoftonline.com/signin", "state": "state", "code_verifier": "private-pkce-verifier"}
        with patch("invoice_email.microsoft_client", return_value=(client, Mock())):
            response = self.client.post("/invoice_email/connect", data={"csrf_token": self.token, "invoice_id": self.invoice_id})
        self.assertEqual(303, response.status_code)
        with self.client.session_transaction() as cookie:
            self.assertNotIn("private-pkce-verifier", str(dict(cookie)))
            flow_id = cookie["invoice_email_flow"]
        path = self.store / ("flow-" + flow_id + ".json")
        self.assertIn("private-pkce-verifier", path.read_text())
        client.acquire_token_by_auth_code_flow.return_value = {"access_token": "temporary-token"}
        profile = Mock()
        profile.json.return_value = {"mail": "wrong@example.com", "userPrincipalName": "wrong@example.com"}
        with patch("invoice_email.microsoft_client", return_value=(client, Mock())), patch("invoice_email.requests.get", return_value=profile):
            self.client.get("/invoice_email/callback?code=code&state=state")
        self.assertFalse((self.store / "tokens.json").exists())
        self.assertFalse(path.exists())
        client.acquire_token_by_auth_code_flow.reset_mock()
        flow_id = "a" * 48
        (self.store / ("flow-" + flow_id + ".json")).write_text(json.dumps({"flow": {}, "created": time.time() - 700}))
        with self.client.session_transaction() as cookie:
            cookie["invoice_email_flow"] = flow_id
        with patch("invoice_email.microsoft_client", return_value=(client, Mock())):
            self.client.get("/invoice_email/callback?code=code&state=wrong")
        client.acquire_token_by_auth_code_flow.assert_not_called()
        self.assertFalse((self.store / "tokens.json").exists())

    def test_oauth_success_disconnect_and_strong_session_required(self):
        flow_id = "b" * 48
        (self.store / ("flow-" + flow_id + ".json")).write_text(json.dumps({"flow": {"state": "state"}, "created": time.time()}))
        with self.client.session_transaction() as cookie:
            cookie["invoice_email_flow"] = flow_id
            cookie["invoice_email_return"] = self.invoice_id
        client, cache = Mock(), Mock()
        client.acquire_token_by_auth_code_flow.return_value = {"access_token": "secret-token"}
        client.get_accounts.return_value = [{"home_account_id": "connected-id"}]
        cache.serialize.return_value = "private-cache-content"
        profile = Mock()
        profile.json.return_value = {"mail": self.config["mailbox"]}
        with patch("invoice_email.microsoft_client", return_value=(client, cache)), patch("invoice_email.requests.get", return_value=profile):
            self.assertEqual(302, self.client.get("/invoice_email/callback?code=code&state=state").status_code)
        self.assertTrue(mail.connection_status(self.config))
        self.assertEqual("private-cache-content", (self.store / "tokens.json").read_text())
        with self.client.session_transaction() as cookie:
            self.assertNotIn("secret-token", str(dict(cookie)))
        self.assertEqual(400, self.client.post("/invoice_email/disconnect", data={}).status_code)
        self.client.post("/invoice_email/disconnect", data={"csrf_token": self.token, "invoice_id": self.invoice_id})
        self.assertFalse(mail.connection_status(self.config))
        self.app.secret_key = "your_secret_key"
        with self.assertRaises(mail.InvoiceEmailError):
            mail.load_config()

    def test_pdf_uses_real_template_logo_custom_items_delivery_and_vat(self):
        executable = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        if not executable.is_file():
            self.skipTest("Local Chrome is not installed; installer tests Linux Chromium separately")
        self.app.config["INVOICE_PDF_EXECUTABLE"] = str(executable)
        self.save_invoice("100.00", {"vat_rate": "20", "custom_items_present": "1", "custom_description": "Extra fitting service", "custom_quantity": "2", "custom_cost": "50.00"})
        try:
            response = self.client.get(f"/invoice_generator/{self.invoice_id}/pdf")
        finally:
            self.app.config.pop("INVOICE_PDF_EXECUTABLE", None)
        self.assertEqual("application/pdf", response.headers["Content-Type"])
        pdf = PdfReader(BytesIO(response.data))
        content = "\n".join(page.extract_text() for page in pdf.pages)
        self.assertIn("Extra fitting service", content)
        self.assertIn("Delivery (Per trip)", content)
        self.assertIn("5,076.00", content)
        self.assertIn("04/12/26", content)
        self.assertTrue(any(page.images for page in pdf.pages), "The logo must be embedded")
        self.assertLess(content.index("Extra fitting service"), content.index("Delivery (Per trip)"))
        self.assertLess(content.index("Delivery (Per trip)"), content.index("VAT"))


if __name__ == "__main__":
    unittest.main()
