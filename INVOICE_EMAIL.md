# Invoice email through GoDaddy Microsoft 365

The Invoice Generator has an **Email invoice** button on saved, fully priced
invoices. Review To, CC, subject and message, view the PDF, then select **Send
invoice**. The attachment uses the same BitCade logo, addresses, custom items,
delivery charge and VAT as the print page. Changes from Sheets must be reviewed
before sending. Unsaved invoice edits hide the email button.

Sending uses Microsoft Graph over HTTPS with delegated `Mail.Send` and
`User.Read`. The connected mailbox is `jack.england@bitcade.co.uk`. This works
with a Microsoft 365 mailbox sold through GoDaddy, subject to your tenant's app
registration and consent policies. It does not use the Google service account.

## 1. Register the Microsoft app

Sign into [Microsoft Entra](https://entra.microsoft.com) as your Microsoft 365
administrator. Select the directory containing the BitCade mailbox.

1. Open **Entra ID > App registrations > New registration** (older menus show
   **Identity > Applications**).
2. Name: `Pooltracker invoice email`.
3. Supported accounts: **Accounts in this organizational directory only**.
4. Redirect URI: choose **Web** and enter exactly:
   `https://pooltabletracker.com/invoice_email/callback`.
5. Register. Keep the **Application (client) ID** and **Directory (tenant) ID**.
6. Under **API permissions**, add **Microsoft Graph > Delegated permissions >
   Mail.Send**. Keep/add delegated **User.Read**. Do not add application
   permissions. Grant admin consent for your organization if required.
7. Under **Certificates & secrets > Client secrets**, create a secret. Keep the
   **Value**, not its ID. You will enter it directly into the Droplet's hidden
   prompt. Record its expiry so you can replace it before it expires.

If app registration or consent is blocked in your GoDaddy-managed tenant,
the Microsoft 365 administrator/GoDaddy support must resolve that before the
mailbox can connect. Do not enable legacy password authentication as a workaround.

## 2. Install on the Droplet

Deploy these source changes using your normal deployment process first. In the
Droplet terminal, run:

```bash
cd /var/www/pooltabletracker.com
sudo bash deploy/invoice-email/install.sh
```

Enter the tenant ID, client ID, then the secret **Value** at the hidden prompt.
Do not paste secrets into chat, Git, or command-line arguments. The installer
keeps an existing configuration when rerun. To change it, edit the private JSON
using `sudo nano /etc/pooltracker-invoice-email/config.json`.

The installer uses the database owner's account (currently `root2`) and the app
virtual environment. It makes a verified local database backup, installs MSAL
and Chromium, tests PDF creation, and creates one additive email-history table.
It sends no email. If your web service uses a different Linux user, align the
private directory ownership with that user before connecting the mailbox.

Restart the existing Flask/Gunicorn web service using your usual restart
command. The service name is not recorded in this repository. If necessary,
identify it with:

```bash
systemctl list-units --type=service --all | rg -i 'pool|gunicorn|flask'
```

The app reads `/etc/pooltracker-invoice-email/config.json` automatically at
startup. No systemd environment change is needed for the standard install.
The install generates a strong Flask session key and enables secure cookies.
Everyone will need to sign into the tracker again after the first restart.
If you already set `POOLTRACKER_SECRET_KEY`, it takes precedence and must be at
least 32 characters long. HTTPS must remain enabled.

## 3. Connect the mailbox and send

1. Sign into the tracker again.
2. Open a saved, fully priced invoice and select **Email invoice**.
3. Select **Connect Microsoft 365**, sign in as `jack.england@bitcade.co.uk`,
   and accept the requested permissions (or arrange admin consent).
4. Review **View PDF**, To, CC, subject and message. Send a test to your own
   address first. Select **Send invoice** only when the message is ready.
5. Check the mailbox's **Sent Items**. Email history on the page records the
   recipient, subject, operator, UTC time, status and the exact attached PDF.

Microsoft's HTTP 202 response means **accepted for sending**, not confirmed
delivery. This is the status shown in the tracker. If the response is lost, the
tracker says **Check Sent Items** and does not retry automatically. Double-clicks
and repeat submissions use the same request ID and cannot send another copy.
To deliberately resend, check **Send another copy** on a new form.

All logged-in tracker workers can access the composer and connected mailbox.
Recipient/message edits are not saved as drafts; send history records submitted
messages. The recipient defaults to the email address in this invoice's Bill To
details and remains editable.

## Private files and maintenance

- `/etc/pooltracker-invoice-email/config.json`: tenant/client IDs, client secret,
  fixed callback, mailbox, random session key, token/browser directories. Mode 600;
  containing directory mode 700. Never commit it.
- `/var/lib/pooltracker-invoice-email/`: OAuth token cache and temporary sign-in
  flows. Mode 700, private files mode 600. Tokens and PKCE verifiers are not in
  browser cookies or the invoice database.
- `/opt/pooltracker-invoice-email/browsers/`: installed Chromium binaries.
- SQLite `invoice_email_attempt`: audit records and PDF snapshots. Included in
  normal database backups; keep those backups private too.

The delegated connection refreshes automatically while Microsoft permits it.
After a password/security policy change or revoked consent, use **Reconnect
Microsoft 365**. Before a client secret expires, replace the secret Value in the
private configuration and restart the web service. **Disconnect mailbox** removes
the local tokens; revoke the app's consent in Entra as well if retiring it.
Record retention follows your existing database/backup policy.

## Troubleshoot a connection that returns to the tracker

The callback now reports the stage that failed rather than guessing that
`Mail.Send` consent is missing. If you still see the old message, deploy the
updated `invoice_email.py` and restart the web service first. Start a fresh
**Connect Microsoft 365** attempt in one browser tab at
`https://pooltabletracker.com` and complete it within 10 minutes.

The page displays safe Microsoft error codes when returned. A private diagnostic
also records the latest callback failure. After trying to connect again, read it
on the Droplet with:

```bash
sudo cat /var/lib/pooltracker-invoice-email/last-sign-in-error.json
```

This specific diagnostic is safe to share: it contains a UTC time, failed stage,
our explanatory message, numeric Microsoft error codes and a correlation ID. It
does not contain tokens, authorization codes, PKCE verifiers, client secrets or
Microsoft's raw error response. It is removed after a successful connection. Do
not share `config.json`, `tokens.json`, or `flow-*.json`.

- `AADSTS7000215` / `invalid_client`: check the client secret **Value**, not the
  Secret ID, in `/etc/pooltracker-invoice-email/config.json`.
- `AADSTS7000222`: replace the expired secret Value.
- `AADSTS65001` / `AADSTS90094`: check delegated `Mail.Send` and `User.Read`, and
  grant the required administrator consent in the matching app registration.
- A mailbox identity check refused with HTTP 401/403: check delegated `User.Read`
  and consent; this is distinct from permission to send mail.
- A missing, expired, or unverifiable sign-in request: start a new connection at
  the exact HTTPS domain above, use one browser tab, and check that all web
  workers use the same persistent Flask session key.
- Private file errors: check that the web service user can read the configuration
  and write `/var/lib/pooltracker-invoice-email/` (keep the private permissions).

For Microsoft error meanings, see the
[Microsoft error reference](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes).
An empty Entra log does not establish which stage failed; use the tracker's
diagnostic to distinguish a server/session failure from Microsoft's rejection.

PDF creation has a 2 MB attachment limit and needs Chromium's Linux dependencies.
The installer checks this as the app user. On a restricted systemd unit, ensure
it can read the browser directory, write its token directory and start Chromium.
Allow outbound HTTPS to `login.microsoftonline.com` and `graph.microsoft.com`.
Microsoft sign-in may redirect through your organization's GoDaddy federation.
An existing deployment's proxy/Gunicorn request timeout may need increasing to
120 seconds to allow PDF generation plus a bounded Microsoft API request. No
background job automatically retries a send.

Official references: [Microsoft Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0),
[MSAL token acquisition](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens),
[Playwright browser installation](https://playwright.dev/python/docs/browsers).
