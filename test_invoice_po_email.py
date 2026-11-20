import base64
import copy
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

import flask_app as tracker
import invoice_email
from invoice_po_email import POEmailError, POReader, attachment_rows, body_text, reply_delivery, subject_po
import test_invoice_generator as generator_tests


class POParsingTests(unittest.TestCase):
    def test_subject_identifies_original_orders_and_preserves_distinct_po_numbers(self):
        self.assertEqual('PO12345', subject_po('New Order - PO12345 (customer)'))
        self.assertEqual('PO123-45', subject_po('New Order - PO #123-45'))
        self.assertEqual('', subject_po('RE: New Order - PO12345'))
        self.assertEqual('', subject_po('Other message PO12345'))

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

    def sync(self):
        with patch('invoice_email.load_config', return_value={'mailbox': 'owner@example.com'}), \
             patch('invoice_email.access_token', return_value='private-token'), \
             patch('invoice_po_email.POReader.read', return_value=[self.candidate]):
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
