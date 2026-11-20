"""Read PO attachments and delivery replies. This module never changes email."""

import base64
import calendar
import hashlib
import io
import json
import re
import zipfile
from datetime import date
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

import requests

from packaging_planner import SUPPORTED_EXTENSIONS, extract_invoice_bytes


GRAPH = 'https://graph.microsoft.com/v1.0/'
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class POEmailError(ValueError):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'div', 'tr'):
            self.parts.append('\n')


def body_text(body):
    content = body.get('content') or ''
    if body.get('contentType', '').lower() == 'html':
        parser = _Text()
        parser.feed(content)
        content = ''.join(parser.parts)
    return content


def subject_po(subject):
    match = re.match(r'^New\s+Order\s*[-:]\s*PO\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]*)', subject or '', re.I)
    return 'PO' + match[1].upper().rstrip('/_-') if match else ''


def reply_delivery(text, delivery_dates):
    """Resolve only a delivery statement containing one unambiguous date."""
    # Quoted correspondence is not evidence for the author's new promise.
    text = re.split(r'(?im)^\s*(?:From:|Sent:|On .+wrote:|[-_]{4,})', text)[0]
    if re.search(r'\b(?:cancel(?:led)?|postpon(?:e|ed)|not\s+(?:able|deliver)|cannot|can\x27t)\b', text, re.I):
        return None, 'The latest reply mentions a cancellation or change. Review the delivery date.'
    if not re.search(r'\b(?:deliver(?:y|ing)?|collection|collect|booked|scheduled)\b', text, re.I):
        return None, ''
    months = {name.lower(): number for number in range(1, 13)
              for name in (calendar.month_name[number], calendar.month_abbr[number])}
    month_pattern = '|'.join(sorted(months, key=len, reverse=True))
    date_pattern = (
        rf'\b(?:(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<month>{month_pattern})'
        rf'|(?P<month_first>{month_pattern})\s+(?P<day_last>\d{{1,2}})(?:st|nd|rd|th)?)'
        rf'(?:[ ,]+(?P<year>20\d{{2}}))?\b'
    )
    found = []
    for match in re.finditer(date_pattern, text, re.I):
        month = months[(match['month'] or match['month_first']).lower()]
        found.append((int(match['day'] or match['day_last']), month, int(match['year']) if match['year'] else None))
    for match in re.finditer(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b', text):
        year = int(match[3]) if match[3] else None
        if year is not None and year < 100:
            year += 2000
        found.append((int(match[1]), int(match[2]), year))
    for match in re.finditer(r'\b(20\d{2})-(\d{2})-(\d{2})\b', text):
        found.append((int(match[3]), int(match[2]), int(match[1])))
    if not found:
        return None, 'A delivery reply was found, but its date needs review.'
    if len(set(found)) != 1 or re.search(r'\b(?:maybe|might|possibly|provisional|instead|rather|not)\b', text, re.I):
        return None, 'The delivery reply contains an uncertain or changed date. Review it before adding the PO.'
    day, month, year = found[0]
    if year:
        try:
            return date(year, month, day), ''
        except ValueError:
            return None, 'The delivery reply contains an invalid date.'
    matches = {value for value in delivery_dates if value.day == day and value.month == month}
    if len(matches) == 1:
        return matches.pop(), ''
    return None, 'No single invoice delivery date matches the reply. Choose the invoice below.'


def attachment_rows(filename, contents, catalogue, po_number=''):
    """Offer parsed rows, but never silently assume the quantity is one."""
    extension = '.' + filename.rsplit('.', 1)[-1].lower()
    if extension not in SUPPORTED_EXTENSIONS or len(contents) > MAX_ATTACHMENT_BYTES:
        raise POEmailError('The PO attachment type or size needs manual review (maximum 10 MB).')
    if extension in ('.xlsx', '.xlsm', '.docx'):
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 30 * 1024 * 1024:
                raise POEmailError('The expanded PO attachment is too large to read automatically.')
    items, warnings = extract_invoice_bytes(filename, contents)
    if extension in ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'):
        warnings.append('Check the quantities read from this image before adding the PO.')
    rows = {}
    colours = {'Black': 'BLA', 'Rustic Oak': 'ROAK', 'Grey Oak': 'GOAK', 'Rustic Black': 'RBLACK', 'Stone': 'STONE'}
    available = {product['sku'] for product in catalogue}
    for item in items:
        if po_number and item.get('po_number'):
            normalise_po = lambda value: re.sub(r'[^A-Z0-9]', '', value.upper()).removeprefix('PO')
            if normalise_po(po_number) != normalise_po(item['po_number']):
                warnings.append('The PO number inside the attachment differs from the email subject. Review the attachment.')
        sku_match = re.search(r'\bBIL/(?:CHAM|LEAG)/(?:6FT|7FT)/(?:RBLACK|ROAK|GOAK|STONE|BLA)\b', item['description'], re.I)
        model = {'Champion': 'CHAM', 'League': 'LEAG', 'Lite': 'LEAG'}.get(item['model'])
        sku = sku_match[0].upper() if sku_match else (
            f"BIL/{model}/{item['size'].upper()}/{colours.get(item['colour'])}" if model else ''
        )
        if sku not in available or item['item_type'] != 'complete_table':
            warnings.append(f"Review this PO line: {item['description']}")
            continue
        # Text extraction defaults missing quantities to one. Require explicit
        # evidence before allowing automatic import, even when the guess is one.
        explicit = re.search(r'(?:^\s*(?P<leading>\d+)\s*[x*]\s+|\bqty\.?\s*[:x-]?\s*(?P<labelled>\d+)\b)', item['raw_text'], re.I)
        if not explicit:
            warnings.append(f"Confirm the quantity for {sku}; the attachment layout needs review.")
        quantity = int(explicit['leading'] or explicit['labelled']) if explicit else item['quantity']
        if not isinstance(quantity, int) or not 1 <= quantity <= 200:
            warnings.append(f"Confirm the quantity for {sku} (1 to 200 tables per PO row).")
            continue
        rows[sku] = rows.get(sku, 0) + quantity
        if rows[sku] > 200:
            warnings.append(f'Confirm the combined quantity for {sku}; it exceeds 200.')
    if not rows:
        warnings.append('No complete table lines could be read. Enter the PO quantities below.')
    return rows, warnings


class POReader:
    def __init__(self, token, mailbox):
        self.token = token
        self.mailbox = mailbox.lower()

    def get(self, path, params=None):
        url = path if path.startswith('https://') else GRAPH + path
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != 'graph.microsoft.com' or not parsed.path.startswith('/v1.0/'):
            raise POEmailError('Microsoft returned an unexpected paging URL.')
        try:
            response = requests.get(url, params=params, headers={
                'Authorization': 'Bearer ' + self.token, 'Prefer': 'outlook.body-content-type="text"',
            }, timeout=(10, 30), allow_redirects=False)
            if response.status_code in (401, 403):
                raise POEmailError('Enable delegated Mail.Read in Microsoft 365, then reconnect with PO email access.')
            if response.status_code != 200:
                raise POEmailError(f'Microsoft could not read PO email (HTTP {response.status_code}). Try again later.')
            if len(response.content) > 20 * 1024 * 1024:
                raise POEmailError('The Microsoft response is too large. Review this PO manually.')
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, POEmailError):
                raise
            raise POEmailError('The PO email connection did not complete. Existing invoices are unchanged.') from None

    def pages(self, path, params=None):
        for _ in range(10):
            payload = self.get(path, params)
            yield from payload.get('value', [])
            path = payload.get('@odata.nextLink')
            if not path:
                return
            params = None
        raise POEmailError('Too many matching email results. Narrow the PO email search before syncing.')

    def read(self, catalogue, delivery_dates):
        originals = {}
        for message in self.pages('me/messages', {
            '$search': '"subject:New Order"', '$top': 100,
            '$select': 'id,subject,conversationId,internetMessageId,from,receivedDateTime,hasAttachments,webLink',
        }):
            po = subject_po(message.get('subject'))
            sender = message.get('from', {}).get('emailAddress', {}).get('address', '').lower()
            if not po or sender == self.mailbox:
                continue
            # Several originals/revisions for one PO require human review.
            originals.setdefault(po, []).append(message)
        results = []
        for po, messages in originals.items():
            message = max(messages, key=lambda value: value.get('receivedDateTime', ''))
            warnings = []
            if len(messages) > 1:
                warnings.append('More than one order email uses this PO number. Review its latest attachment.')
            conversation = message.get('conversationId', '')
            escaped = conversation.replace("'", "''")
            replies = list(self.pages('me/mailFolders/sentitems/messages', {
                '$filter': f"conversationId eq '{escaped}'", '$top': 100,
                '$select': 'id,sentDateTime,uniqueBody,isDraft,from',
            })) if conversation else []
            delivery, reply_id, evidence = None, '', ''
            for reply in sorted(replies, key=lambda value: value.get('sentDateTime', ''), reverse=True):
                sender = reply.get('from', {}).get('emailAddress', {}).get('address', '').lower()
                if reply.get('isDraft') or sender != self.mailbox:
                    continue
                text = body_text(reply.get('uniqueBody') or {})
                candidate, warning = reply_delivery(text, delivery_dates)
                if candidate or warning:
                    delivery, reply_id, evidence = candidate, reply['id'], text[:500]
                    if warning:
                        warnings.append(warning)
                    break
            if not delivery:
                warnings.append('No clear delivery-date reply was found in Sent Items. Choose the invoice below.')
            rows, attachment_names, digests = {}, [], []
            for attachment in self.pages(f"me/messages/{quote(message['id'], safe='')}/attachments", {'$top': 50}):
                if attachment.get('isInline'):
                    continue
                if len(attachment_names) >= 5:
                    warnings.append('More than five PO attachments need manual review.')
                    break
                filename = attachment.get('name') or 'PO attachment'
                attachment_names.append(filename)
                if attachment.get('size', 0) > MAX_ATTACHMENT_BYTES:
                    warnings.append(f'{filename}: attachment exceeds 10 MB.')
                    continue
                if not attachment.get('contentBytes'):
                    warnings.append(f'{filename}: attachment content could not be read.')
                    continue
                try:
                    contents = base64.b64decode(attachment['contentBytes'], validate=True)
                    digest = hashlib.sha256(contents).hexdigest()
                    if digest in digests:
                        continue
                    digests.append(digest)
                    parsed, problems = attachment_rows(filename, contents, catalogue, po)
                    warnings.extend(problems)
                    for sku, quantity in parsed.items():
                        rows[sku] = rows.get(sku, 0) + quantity
                except Exception:
                    warnings.append(f'{filename}: the attachment needs manual review.')
            if len(digests) > 1:
                warnings.append('Multiple different attachments need review to avoid counting the same PO twice.')
            if not rows:
                warnings.append('Enter the ordered table quantities from the PO attachment.')
            proposal = {'items': rows, 'delivery': delivery.isoformat() if delivery else '',
                        'warnings': sorted(set(warnings)), 'reply': evidence, 'attachments': attachment_names,
                        'attachment_hashes': sorted(digests)}
            results.append({
                'po_number': po, 'message_id': message['id'], 'conversation_id': conversation,
                'subject': message.get('subject', ''), 'sender': message.get('from', {}).get('emailAddress', {}).get('address', ''),
                'proposal': proposal, 'reply_id': reply_id,
            })
        return results


def proposal_hash(proposal):
    return hashlib.sha256(json.dumps(proposal, sort_keys=True).encode('utf-8')).hexdigest()
