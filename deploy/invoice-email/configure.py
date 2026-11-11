"""Run on the Droplet as root; never paste the resulting secret file into chat."""
import getpass
import json
import os
from pathlib import Path
import secrets
import sys
import uuid


def configure():
    destination = Path("/etc/pooltracker-invoice-email/config.json")
    if destination.exists():
        data = json.loads(destination.read_text(encoding="utf-8"))
        print("Keeping the existing private Microsoft 365 configuration.")
    else:
        print("Enter the values from Microsoft Entra > App registrations > your invoice app.")
        tenant = str(uuid.UUID(input("Directory (tenant) ID: ").strip()))
        client = str(uuid.UUID(input("Application (client) ID: ").strip()))
        secret = getpass.getpass("Client secret VALUE (hidden): ").strip()
        if len(secret) < 10:
            raise SystemExit("Expected the client secret value, not the secret ID.")
        data = {"tenant_id": tenant, "client_id": client, "client_secret": secret,
                "redirect_uri": "https://pooltabletracker.com/invoice_email/callback",
                "mailbox": "jack.england@bitcade.co.uk", "session_secret": secrets.token_urlsafe(48),
                "data_dir": "/var/lib/pooltracker-invoice-email",
                "browser_path": "/opt/pooltracker-invoice-email/browsers"}
    for key in ("tenant_id", "client_id"):
        uuid.UUID(data[key])
    if (not data.get("client_secret") or len(data.get("session_secret", "")) < 43
            or data.get("redirect_uri") != "https://pooltabletracker.com/invoice_email/callback"
            or data.get("mailbox") != "jack.england@bitcade.co.uk"
            or data.get("data_dir") != "/var/lib/pooltracker-invoice-email"
            or data.get("browser_path") != "/opt/pooltracker-invoice-email/browsers"):
        raise SystemExit("The private email configuration needs correcting. See INVOICE_EMAIL.md.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not destination.exists():
        descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2)
    print("Private email configuration is ready. No credentials were printed.")


if __name__ == "__main__":
    try:
        configure()
    except (ValueError, KeyError):
        sys.exit("The Microsoft app IDs or existing configuration are invalid. Check INVOICE_EMAIL.md.")
