import base64
import copy
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch
from types import SimpleNamespace

import flask_app as tracker
import invoice_email
from invoice_po_email import POEmailError, POReader, attachment_filename_po, attachment_rows, body_text, reply_delivery, subject_po
import test_invoice_generator as generator_tests


SPECIAL_LEAGUE_SKU = 'BIL/LEAG/7FT/GOAK/SPEC'
SPECIAL_LEAGUE_PO = '''ty SKU Item name
1 BIL/LEAG/7FT/GOAK/SPEC Signature League Players Edition Pool Table
Size - 7ft
Coin Mechanism? - Freeplay
Finish - Grey Oak
Delivery Method - Installation Accessory Pack
Delivery Method - Pool Delivery & Installation
Cloth Colours - Duke Series Cloth - Black
Finish: Grey Oak Size: 7f'''


class POParsingTests(unittest.TestCase):
    def read_special_attachments(self, attachments, reply='We will deliver on Dec 4th', catalogue=None):
        reader = POReader('private-token', 'owner@example.com')
        def pages(path, params=None):
            if path == 'me/messages':
                if params['$search'] == '"subject:Special Orders"':
                    return iter([{'id': 'special-message', 'internetMessageId': '<special@example.com>',
                                  'subject': 'Special Orders', 'conversationId': 'thread',
                                  'from': {'emailAddress': {'address': 'customer@example.com'}}}])
                return iter([])
            if path.startswith('me/mailFolders/sentitems'):
                return iter([{'id': 'reply', 'sentDateTime': '2026-11-24T09:00:00Z',
                              'from': {'emailAddress': {'address': 'owner@example.com'}},
                              'uniqueBody': {'contentType': 'text', 'content': reply}}])
            return iter(attachments)
        with patch.object(reader, 'pages', side_effect=pages):
            return reader.read(catalogue if catalogue is not None else tracker.invoice_order_seed_source()['products'],
                               [date(2026, 12, 4)])

    @staticmethod
    def po_attachment(filename, text):
        return {'name': filename, 'contentBytes': base64.b64encode(text.encode('ascii')).decode('ascii')}

    def test_special_orders_attachments_create_separate_pos_using_document_references(self):
        results = self.read_special_attachments([
            self.po_attachment('first.txt', 'PO number: 251367\n2 x Signature Champion 6ft Grey Oak'),
            self.po_attachment('second.txt', 'PO number: 251368\n3 x Signature Champion 6ft Black'),
        ])
        self.assertEqual(['PO251367', 'PO251368'], [result['po_number'] for result in results])
        self.assertEqual({'BIL/CHAM/6FT/GOAK': 2}, results[0]['proposal']['items'])
        self.assertEqual({'BIL/CHAM/6FT/BLA': 3}, results[1]['proposal']['items'])
        self.assertTrue(all(not result['proposal']['warnings'] for result in results))

    def test_special_orders_filename_references_and_identical_copies_do_not_duplicate_quantities(self):
        contents = '2 x Signature Champion 6ft Grey Oak'
        results = self.read_special_attachments([
            self.po_attachment('PO251367.txt', contents),
            self.po_attachment('PO251367 copy.txt', contents),
            self.po_attachment('251368.txt', contents),
        ])
        self.assertEqual(['PO251367', 'PO251368'], [result['po_number'] for result in results])
        self.assertTrue(all(result['proposal']['items']['BIL/CHAM/6FT/GOAK'] == 2 for result in results))

    def test_customer_purchase_order_filename_supplies_the_po_without_a_document_header(self):
        filename = 'Home Leisure Direct Purchase Order - 251508.pdf'
        self.assertEqual('PO251508', attachment_filename_po(filename))
        results = self.read_special_attachments([
            self.po_attachment(filename.replace('.pdf', '.txt'), '2 x Signature Champion 6ft Grey Oak'),
        ])
        self.assertEqual('PO251508', results[0]['po_number'])
        self.assertEqual([], results[0]['proposal']['warnings'])
        self.assertFalse(results[0]['proposal']['reference_missing'])

    def test_real_special_league_sku_keeps_grey_oak_finish_and_explicit_quantity(self):
        candidate = self.read_special_attachments([
            self.po_attachment('PO251551.txt', SPECIAL_LEAGUE_PO),
        ])[0]
        self.assertEqual({SPECIAL_LEAGUE_SKU: 1}, candidate['proposal']['items'])
        self.assertEqual([], candidate['proposal']['warnings'])
        product = candidate['proposal']['products'][0]
        self.assertEqual(SPECIAL_LEAGUE_SKU, product['sku'])
        self.assertIn('Players Edition', product['description'])
        self.assertIn('7FT Grey Oak', product['description'])
        self.assertNotIn('Black', product['description'])
        self.assertNotIn('unit_cost_pence', product)

    def test_unknown_sku_suffix_and_replacement_components_cannot_become_standard_tables(self):
        for text in ('1 BIL/LEAG/7FT/GOAK/UNKNOWN Signature League 7ft Grey Oak',
                     '1 BIL/LEAG/7FT/GOAK/SPEC Replacement top rail for Signature League 7ft Grey Oak'):
            rows, warnings = attachment_rows('po.txt', text.encode('ascii'), tracker.invoice_order_seed_source()['products'])
            self.assertEqual({}, rows)
            self.assertTrue(warnings)

    def test_purchase_order_filename_labels_and_ambiguous_or_unlabelled_numbers(self):
        for filename in ('Home Leisure Direct Purchase Order No. 251508.pdf',
                         'Home_Leisure_Direct_Purchase_Order_-_251508.pdf',
                         'Home Leisure Direct Purchase Order \u2013 PO251508.pdf'):
            self.assertEqual('PO251508', attachment_filename_po(filename), filename)
        for filename in ('Delivery 2026-11-24.pdf', 'PO251508 and PO251509.pdf', 'Purchase Order.pdf'):
            self.assertEqual('', attachment_filename_po(filename), filename)

    def test_missing_po_number_keeps_each_attachment_for_review_with_stable_identity(self):
        attachments = [self.po_attachment('first.txt', '2 x Signature Champion 6ft Grey Oak'),
                       self.po_attachment('second.txt', '3 x Signature Champion 6ft Black')]
        first = self.read_special_attachments(attachments)
        second = self.read_special_attachments(attachments)
        self.assertEqual(2, len(first))
        self.assertNotEqual(first[0]['po_number'], first[1]['po_number'])
        self.assertEqual([result['po_number'] for result in first], [result['po_number'] for result in second])
        self.assertTrue(all(result['proposal']['reference_missing'] for result in first))
        self.assertTrue(all(result['proposal']['warnings'] for result in first))

    def test_one_unreadable_attachment_does_not_hide_other_special_orders(self):
        results = self.read_special_attachments([
            {'name': 'PO251367.pdf'},
            self.po_attachment('PO251368.txt', '2 x Signature Champion 6ft Black'),
        ])
        self.assertEqual(2, len(results))
        self.assertTrue(results[0]['proposal']['warnings'])
        self.assertEqual([], results[1]['proposal']['warnings'])

    def test_conflicting_document_and_filename_po_numbers_require_review(self):
        results = self.read_special_attachments([
            self.po_attachment('PO251367.txt', 'PO number: 251368\n2 x Signature Champion 6ft Black'),
        ])
        self.assertEqual('PO251368', results[0]['po_number'])
        self.assertTrue(any('differs' in warning for warning in results[0]['proposal']['warnings']))

    def test_reply_naming_one_po_does_not_schedule_the_other_attachments(self):
        results = self.read_special_attachments([
            self.po_attachment('PO251367.txt', '2 x Signature Champion 6ft Grey Oak'),
            self.po_attachment('PO251368.txt', '3 x Signature Champion 6ft Black'),
        ], reply='We will deliver PO251367 on Dec 4th\nFrom: customer\nPlease also check PO251368')
        self.assertEqual('2026-12-04', results[0]['proposal']['delivery'])
        self.assertEqual([], results[0]['proposal']['warnings'])
        self.assertEqual('', results[1]['proposal']['delivery'])
        self.assertTrue(results[1]['proposal']['warnings'])

    def test_subject_identifies_original_orders_and_preserves_distinct_po_numbers(self):
        self.assertEqual('PO12345', subject_po('New Order - PO12345 (customer)'))
        self.assertEqual('PO123-45', subject_po('New Order - PO #123-45'))
        self.assertEqual('', subject_po('RE: New Order - PO12345'))
        self.assertEqual('', subject_po('Other message PO12345'))

    def test_real_numeric_order_subject_and_prefixed_version_share_one_po_number(self):
        for subject in ('New Order - 251367', 'New Order - PO251367',
                        ' New Order - 251367 (customer)', 'New Order \u2013 251367'):
            self.assertEqual('PO251367', subject_po(subject), subject)
        self.assertEqual('', subject_po('New Order - Reminder'))
        self.assertEqual('', subject_po('RE: New Order - 251367'))

    def test_order_subject_accepts_a_missing_dash_without_matching_general_order_notices(self):
        for subject in ('New Order 251551', 'New Order PO251551', 'New Order - 251551',
                        'New Order: PO251551', '  new order   251551 (customer)'):
            self.assertEqual('PO251551', subject_po(subject), subject)
        for subject in ('New Order postponed', 'New Order postage', 'New Orders 251551',
                        'New Order Reminder', 'RE: New Order 251551'):
            self.assertEqual('', subject_po(subject), subject)

    def test_ellies_order_without_a_dash_is_found_and_queued_without_a_delivery_reply(self):
        reader = POReader('private-token', 'owner@example.com')
        def pages(path, params=None):
            if path == 'me/messages' and params['$search'] == '"subject:New Order"':
                return iter([{'id': 'ellie-order', 'subject': 'New Order 251551', 'conversationId': 'thread',
                              'from': {'emailAddress': {'address': 'ellie.de-ville@homeleisuredirect.com'}}}])
            return iter([])
        with patch.object(reader, 'pages', side_effect=pages):
            results = reader.read(tracker.invoice_order_seed_source()['products'], [date(2026, 12, 4)])
        self.assertEqual(1, len(results))
        self.assertEqual('PO251551', results[0]['po_number'])
        self.assertEqual('ellie.de-ville@homeleisuredirect.com', results[0]['sender'])
        self.assertTrue(results[0]['proposal']['warnings'])

    def test_numeric_subject_is_discovered_without_a_delivery_reply_or_readable_attachment(self):
        reader = POReader('private-token', 'owner@example.com')
        def pages(path, params=None):
            if path == 'me/messages':
                return iter([{'id': 'today-order', 'subject': 'New Order - 251367', 'conversationId': 'thread',
                              'receivedDateTime': '2026-11-20T09:00:00Z',
                              'from': {'emailAddress': {'address': 'customer@example.com'}}}])
            return iter([])
        with patch.object(reader, 'pages', side_effect=pages):
            results = reader.read(tracker.invoice_order_seed_source()['products'], [date(2026, 12, 4)])
        self.assertEqual(1, len(results))
        self.assertEqual('PO251367', results[0]['po_number'])
        self.assertEqual({}, results[0]['proposal']['items'])
        self.assertTrue(results[0]['proposal']['warnings'])

    def test_delivery_wording_and_dates_match_the_existing_delivery_year(self):
        dates = [date(2026, 12, 4), date(2027, 1, 8)]
        for text in ('Great, we will deliver on Dec 4th', 'Delivery on 4th December',
                     'We will delivery on 04/12', 'Booked for 2026-12-04'):
            self.assertEqual((date(2026, 12, 4), ''), reply_delivery(text, dates), text)
        self.assertEqual((date(2027, 1, 8), ''), reply_delivery('We will deliver on Jan 8th', dates))

    def test_ambiguous_negative_and_quoted_dates_are_never_automatically_matched(self):
        dates = [date(2026, 12, 4), date(2027, 12, 4)]
        self.assertIsNone(reply_delivery('We will deliver Dec 4th', dates)[0])
        for text in ('Maybe we can deliver Dec 4th', 'We cannot deliver Dec 4th',
                     'Delivery on Dec 4th or Dec 18th', 'Delivery on 31/02/2027'):
            value, warning = reply_delivery(text, dates)
            self.assertIsNone(value)
            self.assertTrue(warning)
        self.assertEqual((None, ''), reply_delivery('Thanks!\nFrom: customer\nDelivery Dec 4th', dates))

    def test_attachment_needs_explicit_quantities_and_flags_unknown_products(self):
        products = tracker.invoice_order_seed_source()['products']
        rows, warnings = attachment_rows('po.txt', b'2 x Signature Champion 6ft Grey Oak', products)
        self.assertEqual({'BIL/CHAM/6FT/GOAK': 2}, rows)
        self.assertEqual([], warnings)
        rows, warnings = attachment_rows('po.txt', b'Signature Champion 6ft Grey Oak', products)
        self.assertTrue(warnings)
        rows, warnings = attachment_rows('po.txt', b'0 x Signature Champion 6ft Grey Oak', products)
        self.assertEqual({}, rows)
        self.assertTrue(warnings)
        rows, warnings = attachment_rows('po.txt', b'2 x Signature Champion 6ft White', products)
        self.assertEqual({}, rows)
        self.assertTrue(warnings)

    def test_attachment_po_mismatch_and_html_reply(self):
        products = tracker.invoice_order_seed_source()['products']
        _, warnings = attachment_rows('po.txt', b'PO number: 99999\n2 x Signature Champion 6ft Black', products, 'PO12345')
        self.assertTrue(any('differs' in value for value in warnings))
        self.assertIn('Dec 4th', body_text({'contentType': 'html', 'content': '<p>Delivery Dec 4th</p>'}))

    def test_reader_uses_sent_unique_body_and_reads_attachments_without_writing_mail(self):
        reader = POReader('private-token', 'owner@example.com')
        attachment = base64.b64encode(b'2 x Signature Champion 6ft Black').decode('ascii')
        def pages(path, params=None):
            if path == 'me/messages':
                return iter([{'id': 'original', 'subject': 'New Order - PO12345', 'conversationId': 'thread',
                              'from': {'emailAddress': {'address': 'customer@example.com'}}}])
            if path.startswith('me/mailFolders/sentitems'):
                return iter([{'id': 'reply', 'sentDateTime': '2026-11-20T09:00:00Z',
                              'from': {'emailAddress': {'address': 'owner@example.com'}},
                              'uniqueBody': {'contentType': 'text', 'content': 'Great, we will deliver Dec 4th'}}])
            return iter([{'name': 'po.txt', 'contentBytes': attachment}])
        with patch.object(reader, 'pages', side_effect=pages):
            results = reader.read(tracker.invoice_order_seed_source()['products'], [date(2026, 12, 4)])
        self.assertEqual('2026-12-04', results[0]['proposal']['delivery'])
        self.assertEqual({'BIL/CHAM/6FT/BLA': 2}, results[0]['proposal']['items'])
        self.assertEqual([], results[0]['proposal']['warnings'])

    def test_graph_token_cannot_follow_an_untrusted_paging_url(self):
        reader = POReader('private-token', 'owner@example.com')
        with patch('invoice_po_email.requests.get') as get:
            for url in ('https://example.com/v1.0/me', 'https://graph.microsoft.com@evil.example/v1.0/me'):
                with self.assertRaises(POEmailError):
                    reader.get(url)
            get.assert_not_called()


class POSyncTests(unittest.TestCase):
    setUpClass = classmethod(generator_tests.InvoiceGeneratorTests.setUpClass.__func__)
    save_invoice = generator_tests.InvoiceGeneratorTests.save_invoice

    def setUp(self):
        self.clock = patch('flask_app.london_now', return_value=datetime(2026, 11, 20, 12))
        self.clock.start()
        generator_tests.InvoiceGeneratorTests.setUp(self)
        self.sku = 'BIL/CHAM/6FT/GOAK'
        self.candidate = {
            'po_number': 'PO12345', 'subject': 'New Order - PO12345', 'sender': 'customer@example.com',
            'message_id': 'original', 'conversation_id': 'thread',
            'proposal': {'items': {self.sku: 2}, 'delivery': '2026-12-04', 'warnings': [],
                         'reply': 'Great, we will deliver Dec 4th', 'attachments': ['po.txt'], 'attachment_hashes': ['digest']},
        }

    def tearDown(self):
        generator_tests.InvoiceGeneratorTests.tearDown(self)
        self.clock.stop()

    def sync(self, candidates=None):
        with patch('invoice_email.load_config', return_value={'mailbox': 'owner@example.com'}), \
             patch('invoice_email.access_token', return_value='private-token'), \
             patch('invoice_po_email.POReader.read', return_value=candidates if candidates is not None else [self.candidate]):
            return tracker.sync_special_po_emails()

    def order(self):
        return tracker.InvoiceSpecialOrder.query.one()

    def review(self, action='apply', invoice_id=None, fingerprint=None):
        order = self.order()
        return self.client.post(f'/invoice_generator/po_emails/{order.id}', data={
            'csrf_token': self.token, 'proposal_hash': fingerprint or order.proposal_hash, 'action': action,
            'invoice_id': invoice_id or self.invoice_id, 'po_qty_' + self.sku: 2,
        })

    def quantity(self):
        return next(line['quantity'] for line in tracker.generated_invoice_payload(self.invoice, {})['lines'] if line['sku'] == self.sku)

    def test_clear_draft_order_adds_once_and_survives_sheet_sync_and_invoice_saves(self):
        original_lines = self.invoice.lines_json
        previous = self.quantity()
        result = self.sync()
        self.assertEqual(1, result['added'])
        self.assertEqual(previous + 2, self.quantity())
        self.assertEqual(original_lines, self.invoice.lines_json)
        self.assertEqual(self.invoice_id, self.order().invoice_id)
        self.assertEqual(0, self.sync()['added'])
        self.assertEqual(previous + 2, self.quantity())
        tracker.apply_invoice_sheet_source(copy.deepcopy(tracker.invoice_order_source()))
        self.assertEqual(previous + 2, self.quantity())
        self.assertEqual(303, self.save_invoice().status_code)
        self.assertEqual(previous + 2, self.quantity())
        self.assertEqual(303, self.save_invoice().status_code)
        self.assertEqual(previous + 2, self.quantity())
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual((35 + 2) * 10010, payload['total_pence'])
        printed = self.client.get(f'/invoice_generator/{self.invoice_id}/print').get_data(as_text=True)
        self.assertIn('PO12345', printed)

    def test_numeric_and_prefixed_subjects_cannot_add_the_same_order_twice(self):
        previous = self.quantity()
        self.candidate['subject'] = 'New Order - 251367'
        self.candidate['po_number'] = subject_po(self.candidate['subject'])
        self.assertEqual(1, self.sync()['added'])
        self.candidate['subject'] = 'New Order - PO251367'
        self.candidate['po_number'] = subject_po(self.candidate['subject'])
        self.assertEqual(0, self.sync()['added'])
        self.assertEqual(1, tracker.InvoiceSpecialOrder.query.count())
        self.assertEqual(previous + 2, self.quantity())

    def test_dashless_subject_and_later_subject_format_change_do_not_duplicate_ellies_order(self):
        previous = self.quantity()
        self.candidate['sender'] = 'ellie.de-ville@homeleisuredirect.com'
        for index, subject in enumerate(('New Order 251551', 'New Order - PO251551')):
            self.candidate['subject'] = subject
            self.candidate['po_number'] = subject_po(subject)
            self.assertEqual(1 if index == 0 else 0, self.sync()['added'])
        self.assertEqual(1, tracker.InvoiceSpecialOrder.query.count())
        self.assertEqual('PO251551', self.order().po_number)
        self.assertEqual(previous + 2, self.quantity())

    def test_special_orders_split_into_independent_invoice_orders_and_body_quantities(self):
        parsing = POParsingTests()
        candidates = parsing.read_special_attachments([
            parsing.po_attachment('PO251367.txt', '2 x Signature Champion 6ft Grey Oak'),
            parsing.po_attachment('PO251368.txt', '3 x Signature Champion 6ft Black'),
        ])
        result = self.sync(candidates)
        self.assertEqual(2, result['added'])
        self.assertEqual(['PO251367', 'PO251368'], [order.po_number for order in tracker.InvoiceSpecialOrder.query.order_by(tracker.InvoiceSpecialOrder.po_number)])
        self.assertEqual(40, tracker.generated_invoice_payload(self.invoice, {})['quantity'])
        self.assertEqual(0, self.sync(candidates)['added'])
        self.assertEqual(40, tracker.generated_invoice_payload(self.invoice, {})['quantity'])
        link = tracker.db.session.get(tracker.InvoiceBuildOrder, self.invoice_id)
        self.assertEqual(40, sum(item.quantity for item in link.deadline.items))

    def test_missing_reference_cannot_apply_until_given_actual_po_and_rescan_reuses_it(self):
        self.candidate['po_number'] = 'MISSING-attachment-key'
        self.candidate['subject'] = 'Special Orders'
        self.candidate['proposal'].update(source_key='attachment-key', reference_missing=True,
                                         warnings=['Enter the actual PO number.'])
        previous = self.quantity()
        self.assertEqual(1, self.sync()['review'])
        order_id = self.order().id
        self.review()
        self.assertIsNone(self.order().invoice_id)
        self.assertEqual(previous, self.quantity())
        page = self.client.get('/invoice_generator').get_data(as_text=True)
        self.assertIn('PO number needed', page)
        self.assertNotIn('MISSING-attachment-key', page)
        response = self.client.post(f'/invoice_generator/po_emails/{order_id}', data={
            'csrf_token': self.token, 'proposal_hash': self.order().proposal_hash, 'action': 'apply',
            'invoice_id': self.invoice_id, 'po_number': '251367', 'po_qty_' + self.sku: '2',
        })
        self.assertEqual(303, response.status_code)
        self.assertEqual('PO251367', self.order().po_number)
        self.assertEqual(previous + 2, self.quantity())
        self.sync()
        self.assertEqual(1, tracker.InvoiceSpecialOrder.query.count())
        self.assertEqual(order_id, self.order().id)
        self.assertEqual(previous + 2, self.quantity())

    def test_missing_reference_cannot_be_assigned_to_an_already_imported_po(self):
        self.sync()
        original_order_id = self.order().id
        self.candidate['po_number'] = 'MISSING-other-attachment'
        self.candidate['proposal'].update(source_key='other-attachment', reference_missing=True,
                                         warnings=['Enter the actual PO number.'])
        self.sync()
        other = tracker.InvoiceSpecialOrder.query.filter(tracker.InvoiceSpecialOrder.id != original_order_id).one()
        previous = self.quantity()
        response = self.client.post(f'/invoice_generator/po_emails/{other.id}', data={
            'csrf_token': self.token, 'proposal_hash': other.proposal_hash, 'action': 'apply',
            'invoice_id': self.invoice_id, 'po_number': 'PO12345', 'po_qty_' + self.sku: '2',
        })
        self.assertEqual(303, response.status_code)
        self.assertIsNone(other.invoice_id)
        self.assertEqual(previous, self.quantity())

    def recognised_filename_candidates(self):
        parsing = POParsingTests()
        candidate = parsing.read_special_attachments([
            parsing.po_attachment('Home Leisure Direct Purchase Order - 251508.txt',
                                  '2 x Signature Champion 6ft Grey Oak'),
        ])[0]
        old_candidate = copy.deepcopy(candidate)
        old_candidate['po_number'] = 'MISSING-' + candidate['proposal']['source_key']
        old_candidate['proposal'].update(reference_missing=True, warnings=['Enter the actual PO number.'])
        return old_candidate, candidate

    def special_league_candidate(self):
        parser = POParsingTests()
        return parser.read_special_attachments([parser.po_attachment('PO251551.txt', SPECIAL_LEAGUE_PO)])[0]

    def test_special_table_is_added_to_local_catalogue_and_invoice_with_no_invented_price(self):
        self.candidate = self.special_league_candidate()
        self.assertEqual(1, self.sync()['added'])
        product = next(product for product in tracker.invoice_order_source()['products'] if product['sku'] == SPECIAL_LEAGUE_SKU)
        self.assertTrue(product['special_order'])
        self.assertNotIn(SPECIAL_LEAGUE_SKU, {product['sku'] for product in tracker.invoice_order_seed_source()['products']})
        line = next(line for line in tracker.generated_invoice_payload(self.invoice, {})['lines'] if line['sku'] == SPECIAL_LEAGUE_SKU)
        self.assertEqual(1, line['quantity'])
        self.assertIsNone(line['unit_cost_pence'])
        self.assertIsNone(tracker.db.session.get(tracker.InvoiceItemPrice, SPECIAL_LEAGUE_SKU))
        parser = POParsingTests()
        fresh_candidate = parser.read_special_attachments([
            parser.po_attachment('PO251551.txt', SPECIAL_LEAGUE_PO),
        ], catalogue=tracker.invoice_order_source()['products'])[0]
        self.assertEqual(self.candidate['proposal'], fresh_candidate['proposal'])
        self.assertEqual(0, self.sync([fresh_candidate])['added'])
        self.assertEqual('imported', self.order().status)

    def test_special_table_is_available_in_po_review_and_invoice_prices_can_be_saved(self):
        self.candidate = self.special_league_candidate()
        self.candidate['proposal']['warnings'] = ['Confirm the delivery date.']
        self.sync()
        page = self.client.get(f'/invoice_generator?invoice={self.invoice_id}').get_data(as_text=True)
        self.assertIn('po_qty_' + SPECIAL_LEAGUE_SKU, page)
        response = self.client.post(f'/invoice_generator/po_emails/{self.order().id}', data={
            'csrf_token': self.token, 'proposal_hash': self.order().proposal_hash, 'action': 'apply',
            'invoice_id': self.invoice_id, 'po_qty_' + SPECIAL_LEAGUE_SKU: '1',
        })
        self.assertEqual(303, response.status_code)
        self.assertEqual(303, self.save_invoice().status_code)
        self.assertEqual(302, self.client.get(f'/invoice_generator/{self.invoice_id}/print').status_code)
        self.assertEqual(303, self.save_invoice(overrides={'cost_' + SPECIAL_LEAGUE_SKU: '925.00'}).status_code)
        payload = tracker.generated_invoice_payload(self.invoice, {})
        self.assertEqual(36, payload['quantity'])
        self.assertEqual(35 * 10010 + 92500, payload['total_pence'])
        self.assertEqual(0, payload['missing_prices'])

    def test_special_league_creates_the_correct_body_row_and_can_be_auto_ticked(self):
        self.candidate = self.special_league_candidate()
        self.sync()
        link = tracker.db.session.get(tracker.InvoiceBuildOrder, self.invoice_id)
        item = tracker.db.session.get(tracker.MonthlyBuildItem, json.loads(link.item_ids_json)[SPECIAL_LEAGUE_SKU])
        self.assertEqual(('Signature League', '7FT', 'Grey Oak', 1),
                         (item.model_name, item.size, item.colour, item.quantity))
        completion = tracker.auto_tick_monthly_build_body(
            SimpleNamespace(worker='Alice', serial_number='1001GO'), tracker.TABLE_TYPE_LITE, 'grey_oak',
        )
        self.assertIsNotNone(completion)
        self.assertEqual(item.id, completion.item_id)

    def test_sheet_review_preserves_a_saved_special_table_price_and_its_quantity(self):
        self.candidate = self.special_league_candidate()
        self.sync()
        self.save_invoice(overrides={'cost_' + SPECIAL_LEAGUE_SKU: '925.00'})
        price = tracker.db.session.get(tracker.InvoiceItemPrice, SPECIAL_LEAGUE_SKU)
        price.unit_cost_pence = 110000
        tracker.db.session.commit()
        incoming = copy.deepcopy(tracker.invoice_order_seed_source())
        order = next(order for order in incoming['invoices'] if order['source_column'] == 'M')
        order['quantities']['BIL/LEAG/6FT/BLA'] += 1
        order['expected_quantity'] += 1
        tracker.apply_invoice_sheet_source(incoming)
        data = json.loads(tracker.db.session.get(tracker.InvoiceSheetSyncState, 1).data_json)
        change = data['pending'][str(self.invoice_id)]
        version = tracker.hashlib.sha256(json.dumps(change, sort_keys=True).encode('utf-8')).hexdigest()
        response = self.client.post('/invoice_generator', data={
            'csrf_token': self.token, 'action': 'review_sheet_change', 'invoice_id': self.invoice_id,
            'change_version': version, 'decision': 'apply',
        })
        self.assertEqual(303, response.status_code)
        payload = tracker.generated_invoice_payload(self.invoice, {})
        line = next(line for line in payload['lines'] if line['sku'] == SPECIAL_LEAGUE_SKU)
        self.assertEqual(1, line['quantity'])
        self.assertEqual(92500, line['unit_cost_pence'])
        self.assertIn(SPECIAL_LEAGUE_SKU, {product['sku'] for product in tracker.invoice_order_source()['products']})

    def test_better_filename_extraction_fills_existing_review_entry_without_recreating_it(self):
        old_candidate, candidate = self.recognised_filename_candidates()
        previous = self.quantity()
        self.assertEqual(1, self.sync([old_candidate])['review'])
        original_id = self.order().id
        self.assertEqual(1, self.sync([candidate])['added'])
        self.assertEqual(1, tracker.InvoiceSpecialOrder.query.count())
        self.assertEqual(original_id, self.order().id)
        self.assertEqual('PO251508', self.order().po_number)
        self.assertFalse(self.order().reference_missing)
        self.assertEqual(previous + 2, self.quantity())
        self.assertEqual(0, self.sync([candidate])['added'])
        self.assertEqual(previous + 2, self.quantity())

    def test_matching_an_already_imported_po_retires_its_old_missing_reference_entry(self):
        old_candidate, candidate = self.recognised_filename_candidates()
        self.candidate['po_number'] = 'PO251508'
        self.sync()
        previous = self.quantity()
        self.sync([old_candidate])
        placeholder = tracker.InvoiceSpecialOrder.query.filter_by(po_number=old_candidate['po_number']).one()
        self.assertEqual(0, self.sync([candidate])['added'])
        self.assertEqual('ignored', placeholder.status)
        self.assertIn('PO251508', placeholder.note)
        self.assertEqual(previous, self.quantity())

    def test_newly_detected_reference_does_not_double_add_a_manually_assigned_attachment(self):
        old_candidate, candidate = self.recognised_filename_candidates()
        self.sync([old_candidate])
        response = self.client.post(f'/invoice_generator/po_emails/{self.order().id}', data={
            'csrf_token': self.token, 'proposal_hash': self.order().proposal_hash, 'action': 'apply',
            'invoice_id': self.invoice_id, 'po_number': '251507', 'po_qty_' + self.sku: '2',
        })
        self.assertEqual(303, response.status_code)
        previous = self.quantity()
        self.assertEqual(0, self.sync([candidate])['added'])
        detected = tracker.InvoiceSpecialOrder.query.filter_by(po_number='PO251508').one()
        self.assertEqual('pending', detected.status)
        self.assertIn('PO251507', json.loads(detected.proposal_json)['warnings'][-1])
        self.assertEqual(previous, self.quantity())

    def test_saved_invoice_needs_review_before_quantities_change(self):
        self.save_invoice()
        previous = self.quantity()
        self.assertEqual(1, self.sync()['review'])
        self.assertEqual(previous, self.quantity())
        self.assertEqual(303, self.review().status_code)
        self.assertEqual(previous + 2, self.quantity())

    def test_uncertain_dates_and_attachment_quantities_stay_in_review(self):
        self.candidate['proposal']['warnings'] = ['Confirm the quantity.']
        previous = self.quantity()
        self.assertEqual(1, self.sync()['review'])
        self.assertIsNone(self.order().invoice_id)
        self.assertEqual(previous, self.quantity())
        self.assertEqual(303, self.review().status_code)
        self.assertEqual(previous + 2, self.quantity())

    def test_changed_po_keeps_imported_quantities_until_reviewed(self):
        self.sync()
        previous = self.quantity()
        self.candidate['proposal']['items'][self.sku] = 4
        self.assertEqual(1, self.sync()['review'])
        self.assertEqual(previous, self.quantity())
        self.assertEqual('pending', self.order().status)

    def test_body_list_includes_po_and_removal_preserves_completed_history(self):
        self.sync()
        link = tracker.db.session.get(tracker.InvoiceBuildOrder, self.invoice_id)
        item = tracker.db.session.get(tracker.MonthlyBuildItem, json.loads(link.item_ids_json)[self.sku])
        self.assertEqual(2, item.quantity)
        tracker.db.session.add(tracker.MonthlyBuildCompletion(item=item, unit_number=1, completed_by='Alice'))
        tracker.db.session.commit()
        self.review('remove')
        self.assertEqual(1, item.quantity)
        self.assertEqual(1, len(item.completions))
        self.assertIn('Completed bodies have been kept', link.warning)
        self.assertEqual('ignored', self.order().status)
        self.assertEqual(0, self.sync()['added'])

    def test_po_can_move_to_another_invoice_without_remaining_on_original(self):
        self.sync()
        original = self.quantity()
        target = tracker.GeneratedInvoice.query.filter_by(source_column='N').one()
        self.review('reopen')
        self.review(invoice_id=target.id)
        self.assertEqual(original - 2, self.quantity())
        self.assertEqual(target.id, self.order().invoice_id)
        self.assertTrue(any(line['po_quantities'] for line in tracker.generated_invoice_payload(target, {})['lines']))

    def test_stale_review_bad_quantities_and_missing_csrf_cannot_change_orders(self):
        self.candidate['proposal']['warnings'] = ['Review attachment.']
        self.sync()
        self.review(fingerprint='stale')
        self.assertIsNone(self.order().invoice_id)
        response = self.client.post(f'/invoice_generator/po_emails/{self.order().id}', data={
            'csrf_token': self.token, 'proposal_hash': self.order().proposal_hash,
            'action': 'apply', 'invoice_id': self.invoice_id, 'po_qty_' + self.sku: '201',
        })
        self.assertEqual(303, response.status_code)
        self.assertIsNone(self.order().invoice_id)
        self.assertEqual(400, self.client.post('/invoice_generator/po_emails/sync').status_code)

    def test_network_failure_preserves_existing_import(self):
        self.sync()
        previous = self.quantity()
        with patch('invoice_email.load_config', return_value={'mailbox': 'owner@example.com'}), \
             patch('invoice_email.access_token', side_effect=invoice_email.InvoiceEmailError('Reconnect Microsoft 365.')):
            response = self.client.post('/invoice_generator/po_emails/sync', data={'csrf_token': self.token})
        self.assertEqual(303, response.status_code)
        self.assertEqual(previous, self.quantity())

if __name__ == '__main__':
    unittest.main()
