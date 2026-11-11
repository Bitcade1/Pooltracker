"""Microsoft 365 invoice mail. Credentials and OAuth tokens stay on the server."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import uuid

from flask import abort, current_app, flash, jsonify, make_response, redirect, render_template, request, session, url_for
import requests
from sqlalchemy import text
from sqlalchemy.orm import defer
from invoice_email_ai import EmailAIError, api_key as email_ai_key, improve_email, validate_draft

CONFIG_PATH = "/etc/pooltracker-invoice-email/config.json"
SCOPES = ["User.Read", "Mail.Send"]
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}")
_lock = threading.RLock()


class InvoiceEmailError(Exception):
    pass


class InvoiceSignInError(InvoiceEmailError):
    """A safe, actionable sign-in message; never include Microsoft's raw response."""
    def __init__(self, message, codes=(), correlation_id=None):
        self.codes = list(codes)
        self.correlation_id = correlation_id
        super().__init__(message)


def microsoft_sign_in_error(result):
    """Extract only allowlisted error names, numeric codes and a correlation UUID."""
    result = result if isinstance(result, dict) else {}
    error_codes = result.get("error_codes", [])
    error_codes = error_codes if isinstance(error_codes, (list, tuple)) else []
    codes = {str(code) for code in error_codes
             if isinstance(code, (str, int)) and re.fullmatch(r"[0-9]{4,10}", str(code))}
    description = result.get("error_description", "")
    if isinstance(description, str):
        codes.update(re.findall(r"\bAADSTS([0-9]{4,10})\b", description[:16000]))
    codes = sorted(codes)[:5]
    hints = {
        "7000215": "Microsoft rejected the client secret. Update client_secret in the server's private email configuration with the secret VALUE, not its Secret ID.",
        "7000222": "The Microsoft client secret has expired. Create a new secret in Entra and update client_secret in the server's private email configuration.",
        "7000218": "Microsoft did not receive the required client secret. Check the Web app registration and the server's private email configuration.",
        "700016": "Microsoft could not find this app in the selected tenant. Check the tenant ID and application (client) ID on the server.",
        "65001": "Microsoft consent is missing. In the app registration, check delegated Mail.Send and User.Read and have your administrator grant consent.",
        "65004": "The Microsoft permission request was declined. Connect again and approve the requested permissions, or ask your administrator to approve them.",
        "90094": "Microsoft requires administrator consent. Ask your Microsoft 365 administrator to approve delegated Mail.Send and User.Read for this app.",
        "54005": "This Microsoft sign-in response has already been used. Return to the email page and start a new connection.",
        "53003": "Your Microsoft organization blocked this sign-in under its access policy. Ask your administrator to check the Entra sign-in log.",
    }
    message = next((hints[code] for code in codes if code in hints), None)
    if not message:
        error = result.get("error")
        message = {
            "invalid_client": "Microsoft rejected the app credentials. Check the client secret VALUE and the tenant/client IDs in the server's private email configuration.",
            "invalid_grant": "The Microsoft sign-in response expired or could not be redeemed. Start a new connection; if it repeats, check the Entra sign-in log.",
            "access_denied": "Microsoft did not approve the connection. Check consent and your organization's sign-in policy in Entra.",
            "invalid_scope": "Microsoft rejected the requested permissions. Check delegated Mail.Send and User.Read in the app registration.",
            "temporarily_unavailable": "Microsoft sign-in is temporarily unavailable. Start a new connection in a few minutes.",
            "server_error": "Microsoft sign-in is temporarily unavailable. Start a new connection in a few minutes.",
        }.get(error if isinstance(error, str) else "", "Microsoft did not return a mailbox access token. Check the app's Entra sign-in log and start a new connection.")
    if codes:
        message += " (" + ", ".join("AADSTS" + code for code in codes) + ")"
    correlation_id = result.get("correlation_id")
    if not isinstance(correlation_id, str) or not re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", correlation_id):
        correlation_id = None
    return InvoiceSignInError(message, codes, correlation_id)


def record_sign_in_failure(config, stage, error):
    """Keep a shareable diagnostic without authorization codes, tokens or secrets."""
    diagnostic = {"time_utc": datetime.now(timezone.utc).isoformat(), "stage": stage,
                  "message": str(error), "microsoft_error_codes": getattr(error, "codes", []),
                  "correlation_id": getattr(error, "correlation_id", None)}
    # Log only our static stage and numeric error codes. Never log exception/response bodies.
    current_app.logger.warning("Invoice Microsoft sign-in failed: stage=%s microsoft_codes=%s", stage,
                               ",".join(diagnostic["microsoft_error_codes"]) or "none")
    if config:
        try:
            with private_store(config) as directory:
                _private_write(directory / "last-sign-in-error.json", json.dumps(diagnostic, indent=2))
        except OSError:
            pass


def session_secret():
    """Read only the private session key at startup, without loading MSAL."""
    try:
        data = json.loads(Path(os.environ.get("POOLTRACKER_INVOICE_EMAIL_CONFIG", CONFIG_PATH)).read_text(encoding="utf-8"))
        value = data.get("session_secret", "")
        return value if isinstance(value, str) and len(value) >= 43 else None
    except (OSError, ValueError, TypeError):
        return None


def load_config():
    try:
        path = current_app.config.get("INVOICE_EMAIL_CONFIG_PATH") or os.environ.get("POOLTRACKER_INVOICE_EMAIL_CONFIG", CONFIG_PATH)
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        for field in ("tenant_id", "client_id"):
            if str(uuid.UUID(config[field])) != config[field].lower():
                raise ValueError()
        if not config.get("client_secret") or not EMAIL_PATTERN.fullmatch(config.get("mailbox", "")):
            raise ValueError()
        if config.get("redirect_uri") != "https://pooltabletracker.com/invoice_email/callback":
            raise ValueError()
        if len(current_app.secret_key or "") < 32 or current_app.secret_key == "your_secret_key":
            raise ValueError()
        config["data_dir"] = str(Path(config.get("data_dir", "/var/lib/pooltracker-invoice-email")).resolve())
        return config
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise InvoiceEmailError("Microsoft 365 email needs its one-time server setup. See INVOICE_EMAIL.md.") from None


def _private_write(path, content):
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def private_store(config):
    directory = Path(config["data_dir"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Gunicorn processes share a POSIX lock; the thread lock also supports local Windows previews.
    with _lock, open(directory / "mail.lock", "a+b") as handle:
        if os.name == "posix":
            import fcntl
            os.chmod(handle.name, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield directory
        finally:
            if os.name == "posix":
                fcntl.flock(handle, fcntl.LOCK_UN)


def limit_email_ai_requests(config, worker):
    """Bound paid API requests across web workers without storing any draft text."""
    now = time.time()
    worker_key = hashlib.sha256(str(worker).encode("utf-8")).hexdigest()
    with private_store(config) as directory:
        path = directory / "ai-request-times.json"
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            recent = [entry for entry in previous if isinstance(entry, dict) and isinstance(entry.get("at"), (int, float))
                      and 0 <= now - entry["at"] < 60]
        except (FileNotFoundError, ValueError, TypeError):
            recent = []
        if len(recent) >= 30 or sum(entry.get("worker") == worker_key for entry in recent) >= 10:
            raise EmailAIError("Please wait a minute before requesting another AI improvement.", 429)
        recent.append({"at": now, "worker": worker_key})
        _private_write(path, json.dumps(recent))


class BoundedHttpClient(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (10, 30))
        return super().request(method, url, **kwargs)


def microsoft_client(config, directory, fresh=False):
    try:
        import msal
    except ImportError:
        raise InvoiceEmailError("Install the invoice email server dependencies first. See INVOICE_EMAIL.md.") from None
    cache = msal.SerializableTokenCache()
    path = directory / "tokens.json"
    if path.exists() and not fresh:
        cache.deserialize(path.read_text(encoding="utf-8"))
    client = msal.ConfidentialClientApplication(
        config["client_id"], authority="https://login.microsoftonline.com/" + config["tenant_id"],
        client_credential=config["client_secret"], token_cache=cache, http_client=BoundedHttpClient(),
    )
    return client, cache


def connection_status(config):
    with private_store(config) as directory:
        try:
            account = json.loads((directory / "account.json").read_text(encoding="utf-8"))
            return account.get("mailbox", "").lower() == config["mailbox"].lower() and (directory / "tokens.json").is_file()
        except (OSError, ValueError):
            return False


def access_token(config):
    with private_store(config) as directory:
        client, cache = microsoft_client(config, directory)
        try:
            account = json.loads((directory / "account.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            account = {}
        accounts = [item for item in client.get_accounts() if item.get("home_account_id") == account.get("home_account_id")]
        result = client.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        if cache.has_state_changed:
            _private_write(directory / "tokens.json", cache.serialize())
        if not result or "access_token" not in result:
            raise InvoiceEmailError("Reconnect Microsoft 365 before sending this invoice.")
        return result["access_token"]


def validate_message(form):
    recipient = form.get("recipient", "").strip()
    cc_text = form.get("cc", "").strip()
    cc = [value.strip() for value in cc_text.split(",")] if cc_text else []
    if len(recipient) > 254 or not EMAIL_PATTERN.fullmatch(recipient):
        raise InvoiceEmailError("Enter one valid recipient email address.")
    if len(cc) > 10 or any(len(value) > 254 or not EMAIL_PATTERN.fullmatch(value) for value in cc):
        raise InvoiceEmailError("Enter up to 10 CC email addresses, separated by commas.")
    subject = form.get("subject", "").strip()
    body = form.get("body", "").strip()
    if not subject or len(subject) > 180 or any(ord(char) < 32 for char in subject):
        raise InvoiceEmailError("Enter a subject of up to 180 characters, on one line.")
    if not body or len(body) > 10000 or "\x00" in body:
        raise InvoiceEmailError("Enter an email message of up to 10,000 characters.")
    return recipient, cc, subject, body


def render_pdf(html, config=None):
    """Use the same A4 print template as the browser, without fetching external URLs."""
    try:
        from playwright.sync_api import sync_playwright
        browser_path = (config or {}).get("browser_path")
        if browser_path:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_path
        with sync_playwright() as driver:
            options = {"headless": True, "timeout": 25000}
            if current_app.config.get("INVOICE_PDF_EXECUTABLE"):
                options["executable_path"] = current_app.config["INVOICE_PDF_EXECUTABLE"]
            with driver.chromium.launch(**options) as browser:
                page = browser.new_page(locale="en-GB")
                page.route("**/*", lambda route: route.abort())
                page.set_content(html, wait_until="load", timeout=15000)
                page.emulate_media(media="print")
                pdf = page.pdf(prefer_css_page_size=True, print_background=True)
        if not pdf.startswith(b"%PDF-") or len(pdf) > 2 * 1024 * 1024:
            raise InvoiceEmailError("The invoice PDF exceeds the 2 MB email attachment limit.")
        return pdf
    except InvoiceEmailError:
        raise
    except Exception:
        # Browser exceptions can contain HTML or server paths; keep these out of the UI.
        raise InvoiceEmailError("The invoice PDF could not be created. Check the Chromium installation in INVOICE_EMAIL.md.") from None


def submit_message(token, recipient, cc, subject, body, filename, pdf):
    payload = {"message": {
        "subject": subject, "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": recipient}}],
        "ccRecipients": [{"emailAddress": {"address": value}} for value in cc],
        "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": filename,
                         "contentType": "application/pdf", "contentBytes": base64.b64encode(pdf).decode("ascii")}],
    }, "saveToSentItems": True}
    # Exactly one POST. A timeout may occur after Microsoft accepted the message: never auto-retry.
    try:
        response = requests.post("https://graph.microsoft.com/v1.0/me/sendMail", json=payload,
                                 headers={"Authorization": "Bearer " + token}, timeout=(10, 30))
    except requests.RequestException:
        return "unknown", "Microsoft's response was not received. Check Sent Items before sending another copy."
    if response.status_code == 202:
        return "accepted", "Microsoft 365 accepted the email for sending. Delivery is not yet confirmed."
    if 400 <= response.status_code < 500 and response.status_code != 408:
        return "failed", f"Microsoft 365 rejected the email (HTTP {response.status_code}). Check the mailbox connection and recipient."
    return "unknown", "Sending could not be confirmed. Check Sent Items before sending another copy."


def register_invoice_email(app, db, helpers):
    class InvoiceEmailAttempt(db.Model):
        __tablename__ = "invoice_email_attempt"
        id = db.Column(db.String(32), primary_key=True)
        invoice_id = db.Column(db.Integer, nullable=False, index=True)
        invoice_version = db.Column(db.String(64), nullable=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        worker = db.Column(db.String(50), nullable=False)
        recipient = db.Column(db.String(254), nullable=False)
        cc_json = db.Column(db.Text, nullable=False, default="[]")
        subject = db.Column(db.String(180), nullable=False)
        body = db.Column(db.Text, nullable=False)
        status = db.Column(db.String(16), nullable=False, default="pending")
        detail = db.Column(db.Text, nullable=False, default="")
        filename = db.Column(db.String(160), nullable=False, default="invoice.pdf")
        pdf_bytes = db.Column(db.LargeBinary, nullable=True)
        pdf_sha256 = db.Column(db.String(64), nullable=True)

    def authenticated():
        if "worker" not in session:
            return redirect(url_for("login"))

    def csrf():
        expected = session.get("invoice_generator_csrf")
        if not expected or not secrets.compare_digest(request.form.get("csrf_token", ""), expected):
            abort(400, "This form has expired. Reload the page and try again.")

    def ready(invoice_id):
        helpers["ensure"]()
        invoice = helpers["active"](invoice_id)
        payload = helpers["payload"](invoice, {})
        state = db.session.get(helpers["state_model"], 1)
        if str(invoice_id) in json.loads(state.data_json)["pending"]:
            raise InvoiceEmailError("Review the changes from Google Sheets before emailing this invoice.")
        if invoice.saved_at is None or payload["missing_prices"]:
            raise InvoiceEmailError("Save the invoice with a price for every item before emailing it.")
        return payload

    def pdf_for(payload, config):
        logo = base64.b64encode(Path(app.root_path, "static", "invoice-bitcade-logo.png").read_bytes()).decode("ascii")
        html = render_template("generated_invoice_print.html", invoice=payload, invoice_logo_uri="data:image/png;base64," + logo)
        return render_pdf(html, config)

    def pdf_response(pdf, filename):
        response = make_response(pdf)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = 'inline; filename="' + filename + '"'
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def filename_for(payload):
        reference = re.sub(r"[^A-Za-z0-9_-]", "-", payload["record"].reference)[:80].strip("-") or "invoice"
        return reference + ".pdf"

    @app.route("/invoice_email/connect", methods=["POST"])
    def connect_invoice_email():
        if (login := authenticated()):
            return login
        csrf()
        try:
            config = load_config()
            with private_store(config) as directory:
                client, _ = microsoft_client(config, directory, fresh=True)
                flow = client.initiate_auth_code_flow(SCOPES, redirect_uri=config["redirect_uri"], login_hint=config["mailbox"], prompt="select_account")
                if "auth_uri" not in flow:
                    raise InvoiceEmailError("Microsoft sign-in is unavailable. Check the app registration.")
                flow_id = secrets.token_hex(24)
                # The PKCE verifier stays in a private file, never in Flask's readable session cookie.
                for old in directory.glob("flow-*.json"):
                    if time.time() - old.stat().st_mtime > 600:
                        old.unlink(missing_ok=True)
                _private_write(directory / ("flow-" + flow_id + ".json"), json.dumps({"flow": flow, "created": time.time()}))
                session["invoice_email_flow"] = flow_id
                session["invoice_email_return"] = request.form.get("invoice_id", type=int)
            return redirect(flow["auth_uri"])
        except (InvoiceEmailError, requests.RequestException, ValueError, OSError):
            flash("Microsoft 365 could not be connected. Check the server setup and Microsoft app registration.", "error")
            return redirect(url_for("invoice_generator"))

    @app.route("/invoice_email/callback")
    def invoice_email_callback():
        if (login := authenticated()):
            return login
        invoice_id = session.pop("invoice_email_return", None)
        config = None
        stage = "configuration"
        try:
            config = load_config()
            stage = "browser_session"
            flow_id = session.pop("invoice_email_flow", "")
            if not re.fullmatch(r"[a-f0-9]{48}", flow_id):
                raise InvoiceSignInError("The Microsoft sign-in session is missing. Open https://pooltabletracker.com, sign in to the tracker, and start Connect Microsoft 365 again in the same browser.")
            stage = "sign_in_request"
            with private_store(config) as directory:
                path = directory / ("flow-" + flow_id + ".json")
                try:
                    stored = json.loads(path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    raise InvoiceSignInError("This Microsoft sign-in request expired or has already been used. Return to the email page and start a new connection.") from None
                path.unlink()
                if not 0 <= time.time() - stored["created"] <= 600:
                    raise InvoiceSignInError("Microsoft sign-in took longer than 10 minutes. Start a new connection and finish the sign-in within 10 minutes.")
                stage = "microsoft_app"
                client, cache = microsoft_client(config, directory, fresh=True)
                stage = "token_exchange"
                result = client.acquire_token_by_auth_code_flow(stored["flow"], request.args.to_dict())
                if not isinstance(result, dict) or not result.get("access_token"):
                    raise microsoft_sign_in_error(result)
                stage = "mailbox_profile"
                response = requests.get("https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName",
                                        headers={"Authorization": "Bearer " + result["access_token"]}, timeout=(10, 30))
                if response.status_code in (401, 403):
                    raise InvoiceSignInError(f"Microsoft sign-in succeeded, but the mailbox identity check was refused (HTTP {response.status_code}). Check delegated User.Read and consent for this app, then reconnect.")
                response.raise_for_status()
                profile = response.json()
                stage = "mailbox_identity"
                if config["mailbox"].lower() not in {(profile.get("mail") or "").lower(), (profile.get("userPrincipalName") or "").lower()}:
                    raise InvoiceEmailError("Sign in with the configured invoice mailbox: " + config["mailbox"])
                stage = "mailbox_account"
                accounts = client.get_accounts()
                if len(accounts) != 1:
                    raise InvoiceSignInError("Microsoft sign-in succeeded, but no single reusable mailbox account was returned. Start a new connection and select the configured invoice mailbox.")
                stage = "save_connection"
                _private_write(directory / "tokens.json", cache.serialize())
                _private_write(directory / "account.json", json.dumps({"mailbox": config["mailbox"], "home_account_id": accounts[0]["home_account_id"]}))
                (directory / "last-sign-in-error.json").unlink(missing_ok=True)
            flash("Microsoft 365 is connected. Review the email, then select Send invoice.", "success")
        except InvoiceEmailError as exc:
            record_sign_in_failure(config, stage, exc)
            flash(str(exc), "error")
        except (ValueError, KeyError, OSError, requests.RequestException) as exc:
            if isinstance(exc, OSError) and not isinstance(exc, requests.RequestException):
                message = "The server could not read or save the private Microsoft connection files. Check the email configuration/token directory ownership for the web service user."
            elif isinstance(exc, requests.RequestException):
                message = "The server could not complete its HTTPS request to Microsoft. Start a new connection; if it repeats, check outbound HTTPS access and Microsoft service status."
            elif stage == "token_exchange":
                message = "The Microsoft sign-in response could not be verified. Start a fresh connection in one browser tab; do not reuse a previous sign-in link."
            else:
                message = "Microsoft connection data could not be read or verified. Check the server's email configuration and start a new connection."
            error = InvoiceSignInError(message)
            record_sign_in_failure(config, stage, error)
            flash(str(error), "error")
        return redirect(url_for("email_generated_invoice", invoice_id=invoice_id) if invoice_id else url_for("invoice_generator"))

    @app.route("/invoice_email/disconnect", methods=["POST"])
    def disconnect_invoice_email():
        if (login := authenticated()):
            return login
        csrf()
        try:
            config = load_config()
            with private_store(config) as directory:
                for filename in ("tokens.json", "account.json"):
                    (directory / filename).unlink(missing_ok=True)
                for path in directory.glob("flow-*.json"):
                    path.unlink(missing_ok=True)
            session.pop("invoice_email_flow", None)
            flash("Microsoft 365 disconnected from the tracker.", "success")
        except InvoiceEmailError as exc:
            flash(str(exc), "error")
        invoice_id = request.form.get("invoice_id", type=int)
        return redirect(url_for("email_generated_invoice", invoice_id=invoice_id) if invoice_id else url_for("invoice_generator"))

    @app.route("/invoice_generator/<int:invoice_id>/pdf")
    def invoice_email_pdf(invoice_id):
        if (login := authenticated()):
            return login
        try:
            payload = ready(invoice_id)
            config = load_config()
            return pdf_response(pdf_for(payload, config), filename_for(payload))
        except InvoiceEmailError as exc:
            flash(str(exc), "error")
            return redirect(url_for("email_generated_invoice", invoice_id=invoice_id))

    @app.route("/invoice_generator/<int:invoice_id>/emails/<attempt_id>/pdf")
    def invoice_email_snapshot(invoice_id, attempt_id):
        if (login := authenticated()):
            return login
        helpers["ensure"]()
        helpers["active"](invoice_id)
        InvoiceEmailAttempt.__table__.create(db.engine, checkfirst=True)
        attempt = db.get_or_404(InvoiceEmailAttempt, attempt_id)
        if attempt.invoice_id != invoice_id or not attempt.pdf_bytes:
            abort(404)
        return pdf_response(attempt.pdf_bytes, attempt.filename)

    @app.route("/invoice_generator/<int:invoice_id>/email", methods=["GET", "POST"])
    def email_generated_invoice(invoice_id):
        if (login := authenticated()):
            return login
        session.setdefault("invoice_generator_csrf", secrets.token_hex(24))
        try:
            payload = ready(invoice_id)
        except InvoiceEmailError as exc:
            flash(str(exc), "error")
            return redirect(url_for("invoice_generator", invoice=invoice_id))
        InvoiceEmailAttempt.__table__.create(db.engine, checkfirst=True)
        config = None
        connected = False
        notice = ""
        try:
            config = load_config()
            connected = connection_status(config)
        except (InvoiceEmailError, OSError) as exc:
            notice = str(exc) if isinstance(exc, InvoiceEmailError) else "The private mailbox token directory is unavailable. Check the server setup."
        matches = re.findall(EMAIL_PATTERN, payload["presentation"]["customer_details"])
        defaults = {"recipient": matches[-1] if matches else "", "cc": "",
                    "subject": "Invoice " + payload["record"].reference + " - Delivery " + payload["delivery_date"],
                    "body": "Hello,\n\nPlease find attached invoice " + payload["record"].reference + ".\n\nDelivery date: " + payload["delivery_date"] +
                            "\nTotal including VAT: " + helpers["money"](payload["grand_total_pence"]) + "\n\nThank you for your business.\n\nBitCade"}
        error = ""
        response_code = 200
        values = {key: request.form.get(key, value) for key, value in defaults.items()} if request.method == "POST" else defaults
        if request.method == "POST":
            csrf()
            attempt_id = request.form.get("send_token", "")
            attempt = None
            claimed = False
            try:
                if not config or not connected:
                    raise InvoiceEmailError(notice or "Connect Microsoft 365 before sending this invoice.")
                recipient, cc, subject, body = validate_message(request.form)
                if not re.fullmatch(r"[a-f0-9]{32}", attempt_id) or attempt_id not in session.get("invoice_email_send_tokens", []):
                    raise InvoiceEmailError("This email form has expired. Review it and try again.")
                db.session.rollback()
                db.session.execute(text("BEGIN IMMEDIATE"))
                existing = db.session.get(InvoiceEmailAttempt, attempt_id)
                if existing:
                    if existing.invoice_id != invoice_id:
                        abort(400)
                    db.session.rollback()
                    flash("This send request has already been recorded. Check the email history below.", "success")
                    return redirect(url_for("email_generated_invoice", invoice_id=invoice_id), code=303)
                payload = ready(invoice_id)
                if request.form.get("invoice_version") != payload["version"]:
                    raise InvoiceEmailError("This invoice changed. Reload the email page and review the latest PDF before sending.")
                previous = InvoiceEmailAttempt.query.with_entities(InvoiceEmailAttempt.id).filter_by(invoice_id=invoice_id, invoice_version=payload["version"], recipient=recipient).filter(
                    InvoiceEmailAttempt.status.in_(["pending", "accepted", "unknown"])).first()
                if previous and request.form.get("send_another") != "1":
                    raise InvoiceEmailError("This invoice already has a send request for this recipient. Check Sent Items and tick 'Send another copy' if needed.")
                attempt = InvoiceEmailAttempt(id=attempt_id, invoice_id=invoice_id, invoice_version=payload["version"],
                                              worker=session["worker"], recipient=recipient, cc_json=json.dumps(cc), subject=subject, body=body,
                                              filename=filename_for(payload), status="pending", detail="Sending started. If this remains pending, check Sent Items before sending again.")
                db.session.add(attempt)
                db.session.commit()
                claimed = True
                token = access_token(config)
                pdf = pdf_for(payload, config)
                # Re-read under a short write lock: do not send an invoice changed during PDF generation.
                db.session.rollback()
                db.session.execute(text("BEGIN IMMEDIATE"))
                db.session.expire_all()
                fresh = ready(invoice_id)
                if fresh["version"] != payload["version"]:
                    raise InvoiceEmailError("The invoice changed while its PDF was being created. Review it and send again.")
                attempt = db.session.get(InvoiceEmailAttempt, attempt_id)
                attempt.pdf_bytes = pdf
                attempt.pdf_sha256 = hashlib.sha256(pdf).hexdigest()
                db.session.commit()
                # No database lock spans the HTTPS send request.
                status, detail = submit_message(token, recipient, cc, subject, body, attempt.filename, pdf)
                attempt.status, attempt.detail = status, detail
                db.session.commit()
                flash(detail, "success" if status == "accepted" else "error")
                return redirect(url_for("email_generated_invoice", invoice_id=invoice_id), code=303)
            except (InvoiceEmailError, requests.RequestException, OSError, ValueError) as exc:
                db.session.rollback()
                error = str(exc) if isinstance(exc, InvoiceEmailError) else "The mailbox connection could not be used. Reconnect Microsoft 365 and try again."
                if claimed:
                    attempt = db.session.get(InvoiceEmailAttempt, attempt_id)
                    attempt.status, attempt.detail = "failed", error
                    db.session.commit()
                response_code = 400
        history = InvoiceEmailAttempt.query.options(defer(InvoiceEmailAttempt.pdf_bytes)).filter_by(invoice_id=invoice_id).order_by(InvoiceEmailAttempt.created_at.desc()).limit(50).all()
        send_token = secrets.token_hex(16)
        session["invoice_email_send_tokens"] = (session.get("invoice_email_send_tokens", []) + [send_token])[-12:]
        return render_template("invoice_email.html", invoice=payload, values=values, config_ready=bool(config), connected=connected,
                               mailbox=config["mailbox"] if config else "jack.england@bitcade.co.uk", notice=notice, error=error,
                               ai_configured=bool(config and email_ai_key(config)),
                               send_token=send_token, history=history), response_code

    @app.route("/invoice_generator/<int:invoice_id>/email/improve", methods=["POST"])
    def improve_invoice_email(invoice_id):
        def reply(data, status=200):
            response = jsonify(data)
            response.status_code = status
            response.headers["Cache-Control"] = "no-store"
            if status == 429:
                response.headers["Retry-After"] = "60"
            return response

        if "worker" not in session:
            return reply({"error": "Sign in to the tracker again before improving this email."}, 401)
        expected = session.get("invoice_generator_csrf", "")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
            return reply({"error": "This page has expired. Reload it before using AI improvement."}, 400)
        try:
            data = request.get_json(silent=True)
            subject, body = validate_draft(data)
            payload = ready(invoice_id)
            if data.get("invoice_version") != payload["version"]:
                return reply({"error": "The saved invoice changed. Reload and review the invoice before improving this email."}, 409)
            config = load_config()
            if not email_ai_key(config):
                raise EmailAIError("AI improvement needs its one-time server setup. Your original message is unchanged.", 503)
            limit_email_ai_requests(config, session["worker"])
            # No send request, PDF generation, saved invoice update or mailbox access occurs here.
            suggestion = improve_email(subject, body, config)
            return reply({"suggestion": suggestion})
        except EmailAIError as exc:
            return reply({"error": str(exc)}, exc.status)
        except InvoiceEmailError as exc:
            return reply({"error": str(exc)}, 400)
        except OSError:
            return reply({"error": "The AI server settings could not be read. Your original message is unchanged."}, 503)

    return InvoiceEmailAttempt
