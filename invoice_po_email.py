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
    match = re.match(
        r'^\s*New\s+Order(?:\s*[-:\u2013\u2014]\s*|\s+)'
        r'(?:PO\s*[:#-]?\s*(?P<prefixed>[A-Z0-9][A-Z0-9/_-]*)|(?P<numeric>\d[A-Z0-9/_-]*))',
        subject or '', re.I,
    )
    if not match:
        return ''
    number = match['prefixed'] or match['numeric']
    if not re.search(r'\d', number):
        return ''
    return 'PO' + number.upper().rstrip('/_-')


def is_special_orders_subject(subject):
    return bool(re.match(r'^\s*Special\s+Orders\b', subject or '', re.I))


def canonical_po_number(value):
    value = re.sub(r'^PO\s*[:#-]?\s*', '', str(value or '').strip(), flags=re.I)
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9/_-]{1,79}', value, re.I):
        return ''
    return 'PO' + value.upper().rstrip('/_-')


def attachment_filename_po(filename):
    stem = filename.rsplit('.', 1)[0]
    patterns = (
        r'(?<![A-Z0-9])purchase[\s_-]+order(?:[\s_-]+(?:number|no\.?|ref(?:erence)?))?'
        r'[\s_:#\-\u2013\u2014]*(?:PO[\s_:#-]*)?(\d[A-Z0-9/-]*)',
        r'(?<![A-Z0-9])PO[\s_:#\-\u2013\u2014]*(\d[A-Z0-9/-]*)',
    )
    references = {canonical_po_number(match[1]) for pattern in patterns
                  for match in re.finditer(pattern, stem, re.I)}
    if len(references) == 1:
        return references.pop()
    if references:
        return ''
    if re.fullmatch(r'\d{5,}', stem):
        return canonical_po_number(stem)
    return ''


TABLE_SKU_PATTERN = r'BIL/(LEAG|CHAM)/(6FT|7FT)/(BLA|ROAK|GOAK|RBLACK|STONE)(/SPEC)?'


def table_product(sku, description=''):
    """Recognise supported table variants without assigning a price."""
    if not isinstance(sku, str):
        return None
    match = re.fullmatch(TABLE_SKU_PATTERN, sku.upper())
    if not match:
        return None
    colours = {'BLA': 'Black', 'ROAK': 'Rustic Oak', 'GOAK': 'Grey Oak',
               'RBLACK': 'Rustic Black', 'STONE': 'Stone'}
    model = 'Signature League' if match[1] == 'LEAG' else 'Signature Champion'
    if match[1] == 'LEAG' and re.search(r'\bplayers?\s+edition\b', description, re.I):
        model += ' Players Edition'
    name = f'{model} {match[2]} {colours[match[3]]}'
    if match[4]:
        name += ' (Special order)'
    return {'sku': sku.upper(), 'description': name, 'special_order': True}


def unquoted_reply(text):
    return re.split(r'(?im)^\s*(?:From:|Sent:|On .+wrote:|[-_]{4,})', text)[0]


def reply_delivery(text, delivery_dates):
    """Resolve only a delivery statement containing one unambiguous date."""
    # Quoted correspondence is not evidence for the author's new promise.
    text = unquoted_reply(text)
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


def attachment_details(filename, contents, catalogue, po_number=''):
    """Offer parsed rows, but never silently assume the quantity is one."""
    extension = '.' + filename.rsplit('.', 1)[-1].lower()
    if extension not in SUPPORTED_EXTENSIONS or len(contents) > MAX_ATTACHMENT_BYTES:
        raise POEmailError('The PO attachment type or size needs manual review (maximum 10 MB).')
    if extension in ('.xlsx', '.xlsm', '.docx'):
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 30 * 1024 * 1024:
                raise POEmailError('The expanded PO attachment is too large to read automatically.')
    items, warnings = extract_invoice_bytes(filename, contents)
    references = sorted({reference for item in items
                         if (reference := canonical_po_number(item.get('po_number')))})
    if extension in ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff'):
        warnings.append('Check the quantities read from this image before adding the PO.')
    rows, products = {}, {}
    colours = {'Black': 'BLA', 'Rustic Oak': 'ROAK', 'Grey Oak': 'GOAK', 'Rustic Black': 'RBLACK', 'Stone': 'STONE'}
    available = {product['sku']: product for product in catalogue}
    for item in items:
        if re.match(r'^(?:size|finish|cloth\s+colours?|coin\s+mechanism\??|delivery\s+method)\s*[-:]',
                    item['description'], re.I):
            continue
        if po_number and item.get('po_number'):
            normalise_po = lambda value: re.sub(r'[^A-Z0-9]', '', value.upper()).removeprefix('PO')
            if normalise_po(po_number) != normalise_po(item['po_number']):
                warnings.append('The PO number inside the attachment differs from the email subject. Review the attachment.')
        sku_match = re.search(r'\b' + TABLE_SKU_PATTERN + r'(?=$|[^\w/])', item['description'], re.I)
        raw_sku = re.search(r'\bBIL/[A-Z0-9/_-]+', item['description'], re.I)
        if raw_sku and not sku_match:
            warnings.append(f"Review this PO SKU: {raw_sku[0]}")
            continue
        model = {'Champion': 'CHAM', 'League': 'LEAG', 'Lite': 'LEAG'}.get(item['model'])
        sku = sku_match[0].upper() if sku_match else (
            f"BIL/{model}/{item['size'].upper()}/{colours.get(item['colour'])}" if model else ''
        )
        definition = table_product(sku, item['description']) if sku_match else None
        if (sku not in available and not definition) or item['item_type'] != 'complete_table':
            warnings.append(f"Review this PO line: {item['description']}")
            continue
        if definition and (sku not in available or available[sku].get('special_order') or sku.endswith('/SPEC')):
            products[sku] = available.get(sku, definition)
        # Text extraction defaults missing quantities to one. Require explicit
        # evidence before allowing automatic import, even when the guess is one.
        explicit = re.search(
            r'(?:^\s*(?P<leading>\d+)\s*[x*]\s+|\bqty\.?\s*[:x-]?\s*(?P<labelled>\d+)\b'
            r'|^\s*(?P<sku_quantity>\d+)\s+(?=BIL/))', item['raw_text'], re.I)
        if not explicit:
            warnings.append(f"Confirm the quantity for {sku}; the attachment layout needs review.")
        quantity = int(explicit['leading'] or explicit['labelled'] or explicit['sku_quantity']) if explicit else item['quantity']
        if not isinstance(quantity, int) or not 1 <= quantity <= 200:
            warnings.append(f"Confirm the quantity for {sku} (1 to 200 tables per PO row).")
            continue
        rows[sku] = rows.get(sku, 0) + quantity
        if rows[sku] > 200:
            warnings.append(f'Confirm the combined quantity for {sku}; it exceeds 200.')
    if not rows:
        warnings.append('No complete table lines could be read. Enter the PO quantities below.')
    return rows, warnings, references, list(products.values())


def attachment_rows(filename, contents, catalogue, po_number=''):
    rows, warnings, _, _ = attachment_details(filename, contents, catalogue, po_number)
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
        special_messages, seen_ids = [], set()
        for search in ('"subject:New Order"', '"subject:Special Orders"'):
            for message in self.pages('me/messages', {
                '$search': search, '$top': 100,
                '$select': 'id,subject,conversationId,internetMessageId,from,receivedDateTime,hasAttachments,webLink',
            }):
                if message['id'] in seen_ids:
                    continue
                seen_ids.add(message['id'])
                po = subject_po(message.get('subject'))
                sender = message.get('from', {}).get('emailAddress', {}).get('address', '').lower()
                if sender == self.mailbox:
                    continue
                if po:
                    originals.setdefault(po, []).append(message)
                elif is_special_orders_subject(message.get('subject')):
                    special_messages.append(message)
        results = []
        for po, messages in originals.items():
            message = max(messages, key=lambda value: value.get('receivedDateTime', ''))
            warnings = []
            if len(messages) > 1:
                warnings.append('More than one order email uses this PO number. Review its latest attachment.')
            results.extend(self.read_order_message(message, catalogue, delivery_dates, po, warnings))
        for message in special_messages:
            results.extend(self.read_order_message(message, catalogue, delivery_dates))
        return results

    def read_order_message(self, message, catalogue, delivery_dates, subject_reference='', warnings=None):
        warnings = list(warnings or [])
        conversation = message.get('conversationId', '')
        escaped = conversation.replace("'", "''")
        replies = list(self.pages('me/mailFolders/sentitems/messages', {
            '$filter': f"conversationId eq '{escaped}'", '$top': 100,
            '$select': 'id,sentDateTime,uniqueBody,isDraft,from',
        })) if conversation else []
        delivery, reply_id, evidence, reply_text = None, '', '', ''
        for reply in sorted(replies, key=lambda value: value.get('sentDateTime', ''), reverse=True):
            sender = reply.get('from', {}).get('emailAddress', {}).get('address', '').lower()
            if reply.get('isDraft') or sender != self.mailbox:
                continue
            text = body_text(reply.get('uniqueBody') or {})
            candidate, warning = reply_delivery(text, delivery_dates)
            if candidate or warning:
                delivery, reply_id, evidence = candidate, reply['id'], text[:500]
                reply_text = unquoted_reply(text)
                if warning:
                    warnings.append(warning)
                break
        if not delivery:
            warnings.append('No clear delivery-date reply was found in Sent Items. Choose the invoice below.')
        groups, name_counts = {}, {}
        attachment_count = 0
        maximum = 5 if subject_reference else 20
        for attachment in self.pages(f"me/messages/{quote(message['id'], safe='')}/attachments", {'$top': 50}):
            if attachment.get('isInline'):
                continue
            if attachment_count >= maximum:
                warnings.append(f'More than {maximum} PO attachments need manual review.')
                break
            attachment_count += 1
            filename = attachment.get('name') or 'PO attachment'
            name_counts[filename] = name_counts.get(filename, 0) + 1
            identity = [message.get('internetMessageId') or message['id'], filename, name_counts[filename]]
            source_key = hashlib.sha256(json.dumps(identity).encode('utf-8')).hexdigest()[:32]
            reference = subject_reference or attachment_filename_po(filename)
            rows, problems, references, products, digest = {}, [], [], [], ''
            if attachment.get('size', 0) > MAX_ATTACHMENT_BYTES:
                problems.append(f'{filename}: attachment exceeds 10 MB.')
            elif not attachment.get('contentBytes'):
                problems.append(f'{filename}: attachment content could not be read.')
            else:
                try:
                    contents = base64.b64decode(attachment['contentBytes'], validate=True)
                    digest = hashlib.sha256(contents).hexdigest()
                    rows, problems, references, products = attachment_details(filename, contents, catalogue, subject_reference)
                except Exception:
                    problems.append(f'{filename}: the attachment needs manual review.')
            if not subject_reference:
                if len(references) == 1:
                    if reference and reference != references[0]:
                        problems.append('The PO number inside the attachment differs from its filename. Review the attachment.')
                    reference = references[0]
                elif len(references) > 1:
                    reference = ''
                    problems.append('This attachment contains several PO numbers. Review its contents and quantities.')
            missing_reference = not reference
            reference = reference or 'MISSING-' + source_key
            group = groups.setdefault(reference, {
                'items': {}, 'attachments': [], 'attachment_hashes': [], 'warnings': [],
                'source_key': source_key, 'reference_missing': missing_reference, 'products': {},
            })
            if digest and digest in group['attachment_hashes']:
                # The same document can be attached twice; other POs remain separate.
                continue
            group['attachments'].append(filename)
            if digest:
                group['attachment_hashes'].append(digest)
            group['warnings'].extend(problems)
            group['products'].update({product['sku']: product for product in products})
            for sku, quantity in rows.items():
                group['items'][sku] = group['items'].get(sku, 0) + quantity

        if not groups:
            key = subject_reference or 'MISSING-' + hashlib.sha256(
                (message.get('internetMessageId') or message['id']).encode('utf-8')).hexdigest()[:32]
            groups[key] = {'items': {}, 'attachments': [], 'attachment_hashes': [], 'warnings': [],
                           'source_key': key, 'reference_missing': not subject_reference, 'products': {}}
        results = []
        mentioned_pos = set()
        if not subject_reference:
            mentioned_pos = {canonical_po_number(match[1]) for match in re.finditer(
                r'\bPO\s*[:#-]?\s*(\d[A-Z0-9/_-]*)', reply_text, re.I)}
            for reference in groups:
                if not reference.startswith('MISSING-') and re.search(
                        r'(?<!\w)' + re.escape(reference[2:]) + r'(?!\w)', reply_text, re.I):
                    mentioned_pos.add(reference)
        for po, group in groups.items():
            problems = warnings + group['warnings']
            po_delivery = delivery
            if mentioned_pos and po not in mentioned_pos:
                po_delivery = None
                problems.append('The delivery reply names other POs. Choose the delivery invoice for this attachment.')
            if len(group['attachment_hashes']) > 1:
                problems.append('Multiple different attachments for this PO need review to avoid counting it twice.')
            if group['reference_missing']:
                problems.append('No single PO number was found. Enter the actual PO number before adding this attachment to an invoice.')
            if not group['items']:
                problems.append('Enter the ordered table quantities from the PO attachment.')
            if any(quantity > 200 for quantity in group['items'].values()):
                problems.append('A combined PO quantity exceeds 200. Check the quantities before adding it.')
            proposal = {
                'items': group['items'], 'delivery': po_delivery.isoformat() if po_delivery else '',
                'warnings': sorted(set(problems)), 'reply': evidence, 'attachments': group['attachments'],
                'attachment_hashes': sorted(group['attachment_hashes']),
            }
            if not subject_reference:
                proposal['source_key'] = group['source_key']
                proposal['reference_missing'] = group['reference_missing']
            if group['products']:
                proposal['products'] = list(group['products'].values())
            results.append({
                'po_number': po, 'message_id': message['id'], 'conversation_id': conversation,
                'subject': message.get('subject', ''), 'sender': message.get('from', {}).get('emailAddress', {}).get('address', ''),
                'proposal': proposal, 'reply_id': reply_id,
            })
        return results


def proposal_hash(proposal):
    return hashlib.sha256(json.dumps(proposal, sort_keys=True).encode('utf-8')).hexdigest()
