# PO orders from Microsoft 365 email

The Invoice Generator can read original messages titled `New Order - 251367`
or `New Order - PO251367`. Both identify the same PO, so repeat scans or a
subject-format change do not duplicate the order. The reader can
extract table lines from their attachments and look through Sent Items in the
same conversation for your delivery-date reply. For example, `Great, we will
deliver on Dec 4th` matches the existing invoice for 4 December. A date without a
year is used only when one existing delivery date matches it. Your sent reply's
unique body is used so quoted customer dates do not become delivery promises.

Messages titled `Special Orders` are also scanned. Each PO attachment becomes
its own order, using the PO number in its contents or filename. A copy of a PO
already received through a `New Order` email reuses the same mailbox/PO identity.
If a delivery reply names only one PO, its date is not assigned to the others.
Missing or ambiguous numbers appear as **PO number needed** with the attachment
filename. Enter the actual PO number in review before applying the order; repeat
scans keep that assignment without recreating the pending attachment.

The reader uses GET requests to Microsoft Graph. It does not send replies,
change read/unread status, move mail or delete messages.

## One-time Microsoft setup

1. In the existing Microsoft Entra app registration, add Microsoft Graph
   **Delegated > Mail.Read** alongside the existing `User.Read` and `Mail.Send`.
   Grant consent if the tenant requires an administrator to do so.
2. Back up the live database using the existing backup command before deploying
   these source files. This feature adds the `invoice_special_order` table.
3. Restart the web service using the normal deployment process, open Invoice
   Generator and select **Connect PO email access**. Sign into the same mailbox
   used for invoices and approve the read permission.
   If Microsoft returns to the tracker login, sign in as the same worker to
   finish the pending connection within 10 minutes. Start a fresh connection if
   the approval was made before the callback recovery update was deployed.
4. Select **Sync PO emails**. Check the extracted quantities against a real PO
   attachment before enabling the timer below. Layouts without an explicit
   `Qty: 2` or `2 x ...` quantity need review rather than being assumed to be one.

Reading message bodies and attachments requires `Mail.Read`; `Mail.ReadBasic`
does not cover them. The normal **Email invoice** connection still requests its
existing sending permissions. PO access is requested by the separate PO button.
See Microsoft's [permission reference](https://learn.microsoft.com/en-us/graph/permissions-reference#mailread)
and [attachment API](https://learn.microsoft.com/en-us/graph/api/message-list-attachments?view=graph-rest-1.0).

## Matching and review

Clear, complete matches add table quantities to draft invoices automatically.
Already saved invoices, ambiguous dates, multiple matching invoices, changed
POs and uncertain attachment extraction go under **PO orders > Pending**.
Review the attachment, choose the delivery invoice, adjust quantities and select
**Apply PO to invoice**. A later change does not remove previously applied PO
quantities until the new proposal is reviewed. Acknowledgements without a new
delivery statement leave the previous confirmation available.

The importer identifies complete League/Champion tables using catalogue SKUs or
their model, size and colour. Other products, unreadable/scanned documents and
layouts it cannot read reliably need review. Attachments are limited to 10 MB
each, five per `New Order` message or twenty per `Special Orders` message.
Multiple different attachments for the same PO require review to avoid counting
the PO and its copy twice. The search covers up to 1,000 messages for each of
`New Order` and `Special Orders`; exceptionally large histories require a narrower
search or folder-specific adaptation before enabling automatic sync.

PO numbers are unique per mailbox. Repeating a sync or saving an invoice does
not add the order again. Quantities are stored separately from Google Sheets,
so sheet sync cannot erase them. These are additional special orders: check
that the same quantities have not also been entered into the sheet.

PO quantities use the invoice's product prices, not prices guessed from the PO.
Review costs before sending. Unknown prices still block invoice printing/email.
The printed invoice labels each included PO and quantity. Monthly body lists
include these quantities and display the PO references. Moving/removing an
applied PO updates the lists while retaining completed ticks and warnings when
the new quantities conflict with completed work. Invoice and stock counts are
not dispatched by the reader.

## Optional automatic sync every five minutes

After checking the first real import, run on the Droplet:

```bash
cd /var/www/pooltabletracker.com
sudo bash deploy/po-email/install.sh
sudo journalctl -u pooltracker-po-email.service -n 30 --no-pager
```

The installer verifies the database owner and Python environment, takes a
verified local backup, creates the additive PO table, and installs a separate
timer. It uses the existing private Microsoft email configuration/token store;
no credentials are copied into Git or systemd arguments. Confirm that the
database owner can access the same token store as the web service.

For a manual server run:

```bash
venv/bin/python sync_po_email_orders.py --database /var/www/pooltabletracker.com/pool_table_tracker.db
```

Pause automatic reads with:

```bash
sudo systemctl disable --now pooltracker-po-email.timer
```

This implementation has automated tests using simulated email and attachments.
The user's actual PO layout must be checked before relying on automatic import.
