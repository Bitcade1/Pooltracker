"""Add/replace the OpenAI key privately without changing the Microsoft connection."""
import getpass
import json
import os
from pathlib import Path
import secrets


def configure(path=Path("/etc/pooltracker-invoice-email/config.json")):
    original = path.stat()
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not config.get("client_id") or not config.get("session_secret"):
        raise ValueError("Complete the Microsoft 365 email setup first.")
    key = getpass.getpass("OpenAI API key (hidden; leave blank to cancel): ").strip()
    if not key:
        print("Cancelled; existing configuration unchanged.")
        return
    if not key.startswith("sk-") or len(key) < 20 or any(char.isspace() for char in key):
        raise ValueError("Expected an OpenAI API key. No configuration was changed.")
    config["openai_api_key"] = key
    config.setdefault("openai_model", "gpt-4.1-mini")
    temporary = path.with_name(path.name + ".ai-" + secrets.token_hex(8) + ".tmp")
    try:
        descriptor = os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(config, output, indent=2)
        if os.name == "posix":
            os.chown(temporary, original.st_uid, original.st_gid)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print("AI improvement configured. Reload the email page; the Microsoft connection is unchanged.")
    print("No request was made to OpenAI and no email was sent.")


if __name__ == "__main__":
    try:
        configure()
    except (OSError, ValueError):
        raise SystemExit("Could not update the private email configuration. Run with sudo after completing Microsoft 365 setup; check the API key format.") from None
