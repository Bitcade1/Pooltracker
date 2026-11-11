import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch

import requests

import flask_app as tracker
import test_invoice_email as email_tests


class InvoiceEmailAITests(unittest.TestCase):
    setUpClass = classmethod(email_tests.InvoiceEmailTests.setUpClass.__func__)
    save_invoice = email_tests.InvoiceEmailTests.save_invoice

    def setUp(self):
        email_tests.InvoiceEmailTests.setUp(self)
        self.key_env = patch.dict("os.environ", {"OPENAI_API_KEY": "", "POOLTRACKER_EMAIL_AI_MODEL": ""})
        self.key_env.start()
        self.config["openai_api_key"] = "sk-test-private-ai-key-never-display"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.client.get(self.url)
        self.ai_url = self.url + "/improve"
        self.draft = {"subject": "invoice INV-400 for 04/12/26", "body": "Hi, attached is INV-400. 35 tables for 04/12/26. Total GBP 4,956.00. thanks Jack",
                      "invoice_version": tracker.invoice_record_version(self.invoice)}

    def tearDown(self):
        self.key_env.stop()
        email_tests.InvoiceEmailTests.tearDown(self)

    def response(self, **overrides):
        suggestion = {"subject": "Invoice INV-400 - Delivery 04/12/26", "body": "Hi,\n\nPlease find attached INV-400 for 35 tables, for delivery on 04/12/26.\nTotal: GBP 4,956.00.\n\nThanks,\nJack"}
        suggestion.update(overrides)
        return Mock(status_code=200, json=Mock(return_value={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(suggestion)}]}]}))

    def improve(self, data=None, headers=None):
        return self.client.post(self.ai_url, json=self.draft if data is None else data,
                                headers={"X-CSRF-Token": self.token} if headers is None else headers)

    def test_improvement_sends_only_draft_to_ai_and_does_not_send_or_save_invoice(self):
        before = tracker.invoice_record_version(self.invoice)
        with patch("invoice_email_ai.requests.post", return_value=self.response()) as post, \
                patch("invoice_email.submit_message") as send, patch("invoice_email.access_token") as microsoft:
            result = self.improve()
        self.assertEqual(200, result.status_code)
        self.assertIn("Please find attached", result.json["suggestion"]["body"])
        self.assertEqual(1, post.call_count)
        self.assertEqual("https://api.openai.com/v1/responses", post.call_args.args[0])
        api_data = post.call_args.kwargs["json"]
        self.assertEqual({"subject": self.draft["subject"], "body": self.draft["body"]}, json.loads(api_data["input"][0]["content"]))
        self.assertIs(False, api_data["store"])
        self.assertTrue(api_data["text"]["format"]["strict"])
        self.assertNotIn("accounts@example.com", json.dumps(api_data))
        self.assertNotIn(self.config["client_secret"], json.dumps(api_data))
        self.assertNotIn(self.config["openai_api_key"], result.get_data(as_text=True))
        self.assertIn("no-store", result.headers["Cache-Control"])
        self.assertEqual(before, tracker.invoice_record_version(self.invoice))
        self.assertEqual(0, tracker.InvoiceEmailAttempt.query.count())
        send.assert_not_called()
        microsoft.assert_not_called()

    def test_authentication_csrf_validation_and_invoice_changes_prevent_ai_calls(self):
        anonymous = self.app.test_client()
        self.assertEqual(401, anonymous.post(self.ai_url, json=self.draft).status_code)
        self.assertEqual(400, self.improve(headers={}).status_code)
        for change, status in (({"body": " "}, 400), ({"subject": "Bad\nsubject"}, 400),
                               ({"body": "x" * 10001}, 400), ({"invoice_version": "old"}, 409)):
            with self.subTest(change=list(change)):
                result = self.improve(dict(self.draft, **change))
                self.assertEqual(status, result.status_code)
                self.assertIn("error", result.json)
        self.assertEqual(0, tracker.InvoiceEmailAttempt.query.count())

    def test_missing_key_disables_button_and_endpoint_preserves_original(self):
        del self.config["openai_api_key"]
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        page = self.client.get(self.url)
        self.assertRegex(page.get_data(as_text=True), r'id="improve-email"\s+disabled')
        response = self.improve()
        self.assertEqual(503, response.status_code)
        self.assertIn("original message is unchanged", response.json["error"])

    def test_provider_failures_never_retry_or_include_private_provider_details(self):
        for status, expected in ((401, 503), (403, 503), (400, 503), (404, 503), (429, 429), (500, 502)):
            with self.subTest(status=status), patch("invoice_email_ai.requests.post", return_value=Mock(status_code=status, text="private-provider-response")) as post:
                response = self.improve()
                self.assertEqual(expected, response.status_code)
                self.assertNotIn("private-provider-response", response.get_data(as_text=True))
                self.assertNotIn("suggestion", response.json)
                self.assertEqual(1, post.call_count)
        with patch("invoice_email_ai.requests.post", side_effect=requests.Timeout("private request")) as post:
            response = self.improve()
            self.assertEqual(502, response.status_code)
            self.assertIn("original message is unchanged", response.json["error"])
            self.assertEqual(1, post.call_count)
        self.assertEqual(0, tracker.InvoiceEmailAttempt.query.count())

    def test_malformed_incomplete_or_refused_output_is_not_offered_as_a_suggestion(self):
        for result in ({"status": "incomplete", "output": []},
                       {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]},
                       {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}]}):
            with patch("invoice_email_ai.requests.post", return_value=Mock(status_code=200, json=Mock(return_value=result))):
                self.assertEqual(502, self.improve().status_code)
        for fields in ({"subject": "Bad\r\nBcc: extra@example.com"}, {"body": ""}, {"body": "x" * 10001}, {"extra": "not allowed"}):
            with patch("invoice_email_ai.requests.post", return_value=self.response(**fields)):
                self.assertEqual(502, self.improve().status_code)

    def test_rate_limit_is_shared_on_disk_and_contains_no_draft_or_api_key(self):
        with patch("invoice_email_ai.requests.post", return_value=self.response()) as post:
            for _ in range(10):
                self.assertEqual(200, self.improve().status_code)
            response = self.improve()
        self.assertEqual(429, response.status_code)
        self.assertEqual("60", response.headers["Retry-After"])
        self.assertEqual(10, post.call_count)
        stored = (self.store / "ai-request-times.json").read_text()
        self.assertNotIn(self.draft["body"], stored)
        self.assertNotIn(self.config["openai_api_key"], stored)
        self.assertNotIn("Invoice Operator", stored)

    def test_private_setup_preserves_microsoft_settings_and_does_not_print_key(self):
        self.config["session_secret"] = "persistent-session-key-unchanged"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        configure = runpy.run_path(str(Path(tracker.basedir, "deploy", "invoice-email", "configure_ai.py")))["configure"]
        with patch("getpass.getpass", return_value="sk-replacement-private-openai-key"), patch("builtins.print") as output:
            configure(self.config_path)
        updated = json.loads(self.config_path.read_text())
        for key, value in self.config.items():
            if key != "openai_api_key":
                self.assertEqual(value, updated[key])
        self.assertEqual("sk-replacement-private-openai-key", updated["openai_api_key"])
        self.assertNotIn("sk-replacement-private-openai-key", str(output.call_args_list))
        saved_config = self.config_path.read_bytes()
        with patch("getpass.getpass", return_value=""), patch("builtins.print"):
            configure(self.config_path)
        self.assertEqual(saved_config, self.config_path.read_bytes())
        with patch("getpass.getpass", return_value="not-an-api-key"), self.assertRaises(ValueError):
            configure(self.config_path)
        self.assertEqual(saved_config, self.config_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
