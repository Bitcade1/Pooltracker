import copy
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

import flask_app as tracker
import test_invoice_generator as generator_tests


class InvoiceBuildListSyncTests(unittest.TestCase):
    setUpClass = classmethod(generator_tests.InvoiceGeneratorTests.setUpClass.__func__)
    save_invoice = generator_tests.InvoiceGeneratorTests.save_invoice

    def setUp(self):
        self.clock = patch('flask_app.london_now', return_value=datetime(2026, 11, 19, 12))
        self.clock.start()
        generator_tests.InvoiceGeneratorTests.setUp(self)

    def tearDown(self):
        generator_tests.InvoiceGeneratorTests.tearDown(self)
        self.clock.stop()

    def link(self):
        return tracker.db.session.get(tracker.InvoiceBuildOrder, self.invoice_id)

    def first_row(self):
        sku, item_id = next(iter(json.loads(self.link().item_ids_json).items()))
        return sku, tracker.db.session.get(tracker.MonthlyBuildItem, item_id)

    def changed_source(self, sku, quantity):
        source = copy.deepcopy(tracker.invoice_order_source())
        order = next(order for order in source['invoices'] if order['source_column'] == 'M')
        order['quantities'][sku] = quantity
        order['expected_quantity'] = sum(order['quantities'].values())
        return source

    def test_all_drafts_feed_monthly_lists_and_reloading_never_duplicates(self):
        invoices = tracker.GeneratedInvoice.query.all()
        self.assertTrue(all(invoice.saved_at is None for invoice in invoices))
        self.assertEqual(len(invoices), tracker.InvoiceBuildOrder.query.count())
        for invoice in invoices:
            link = tracker.db.session.get(tracker.InvoiceBuildOrder, invoice.id)
            rows, _ = tracker.invoice_body_rows(invoice)
            self.assertEqual(sum(row['quantity'] for row in rows.values()),
                             sum(item.quantity for item in link.deadline.items))
        counts = (tracker.MonthlyBuildList.query.count(), tracker.MonthlyBuildDeadline.query.count(),
                  tracker.MonthlyBuildItem.query.count())
        for path in ('/invoice_generator', '/monthly_build_list', '/invoice_generator'):
            self.assertEqual(200, self.client.get(path).status_code)
        self.assertEqual(counts, (tracker.MonthlyBuildList.query.count(), tracker.MonthlyBuildDeadline.query.count(),
                                 tracker.MonthlyBuildItem.query.count()))
        november = tracker.db.session.get(tracker.InvoiceBuildMonth, date(2026, 11, 1)).build_list
        august = tracker.db.session.get(tracker.InvoiceBuildMonth, date(2026, 8, 1)).build_list
        self.assertFalse(november.archived)
        self.assertTrue(august.archived)
        self.assertIn(november.name, self.client.get('/monthly_build_list').get_data(as_text=True))

    def test_sheet_updates_rows_in_place_and_keeps_completed_ticks(self):
        sku, item = self.first_row()
        item_id, previous = item.id, item.quantity
        tracker.db.session.add(tracker.MonthlyBuildCompletion(
            item=item, unit_number=1, completed_by='Alice', completed_at=datetime(2026, 11, 19),
        ))
        tracker.db.session.commit()
        tracker.apply_invoice_sheet_source(self.changed_source(sku, previous + 3))
        item = tracker.db.session.get(tracker.MonthlyBuildItem, item_id)
        self.assertEqual(previous + 3, item.quantity)
        self.assertEqual('Alice', item.completions[0].completed_by)
        self.assertEqual(item_id, json.loads(self.link().item_ids_json)[sku])

    def test_reduction_below_a_completed_unit_keeps_tick_and_shows_warning(self):
        sku, item = self.first_row()
        tracker.db.session.add(tracker.MonthlyBuildCompletion(item=item, unit_number=3, completed_by='Alice'))
        tracker.db.session.commit()
        tracker.apply_invoice_sheet_source(self.changed_source(sku, 2))
        self.assertEqual(3, item.quantity)
        self.assertEqual(3, item.completions[0].unit_number)
        self.assertIn('order now needs 2', self.link().warning)
        page = self.client.get(f'/monthly_build_list?list_id={item.deadline.build_list_id}').get_data(as_text=True)
        self.assertIn('Completed bodies have been kept', page)

    def test_reviewed_sheet_changes_only_update_when_applied(self):
        sku, item = self.first_row()
        previous = item.quantity
        self.assertEqual(303, self.save_invoice().status_code)
        tracker.apply_invoice_sheet_source(self.changed_source(sku, previous + 4))
        self.assertEqual(previous, item.quantity)
        data = json.loads(tracker.db.session.get(tracker.InvoiceSheetSyncState, 1).data_json)
        change = data['pending'][str(self.invoice_id)]
        version = tracker.hashlib.sha256(json.dumps(change, sort_keys=True).encode('utf-8')).hexdigest()
        result = self.client.post('/invoice_generator', data={
            'csrf_token': self.token, 'action': 'review_sheet_change', 'invoice_id': self.invoice_id,
            'change_version': version, 'decision': 'apply',
        })
        self.assertEqual(303, result.status_code)
        self.assertEqual(previous + 4, item.quantity)

    def test_deleted_order_removes_unfinished_rows_and_restore_reuses_link(self):
        link = self.link()
        self.client.post('/invoice_generator', data={
            'csrf_token': self.token, 'action': 'delete_invoice', 'invoice_id': self.invoice_id,
            'invoice_version': tracker.invoice_record_version(self.invoice),
        })
        self.assertIsNone(link.deadline)
        self.assertEqual({}, json.loads(link.item_ids_json))
        self.client.post('/invoice_generator', data={
            'csrf_token': self.token, 'action': 'restore_invoice', 'invoice_id': self.invoice_id,
        })
        self.assertIsNotNone(link.deadline)
        rows, _ = tracker.invoice_body_rows(self.invoice)
        self.assertEqual(len(rows), len(link.deadline.items))

    def test_deleted_order_preserves_completed_history(self):
        _, item = self.first_row()
        tracker.db.session.add(tracker.MonthlyBuildCompletion(item=item, unit_number=1, completed_by='Alice'))
        tracker.db.session.commit()
        self.client.post('/invoice_generator', data={
            'csrf_token': self.token, 'action': 'delete_invoice', 'invoice_id': self.invoice_id,
            'invoice_version': tracker.invoice_record_version(self.invoice),
        })
        self.assertEqual(1, item.quantity)
        self.assertEqual(1, len(item.completions))
        self.assertIn('Removed order', self.link().deadline.label)
        self.assertIn('Completed bodies have been kept', self.link().warning)

    def test_archived_list_is_frozen_until_reopened(self):
        sku, item = self.first_row()
        previous = item.quantity
        build_list = item.deadline.build_list
        build_list.archived = True
        tracker.db.session.commit()
        tracker.apply_invoice_sheet_source(self.changed_source(sku, previous + 2))
        self.assertEqual(previous, item.quantity)
        self.assertIn('archived', self.link().warning)
        self.client.post('/monthly_build_list', data={'action': 'reopen_list', 'build_list_id': build_list.id})
        self.client.get(f'/monthly_build_list?list_id={build_list.id}')
        self.assertEqual(previous + 2, item.quantity)
        self.assertEqual('', self.link().warning)

    def test_delivery_moves_between_months_without_losing_completed_ticks(self):
        _, item = self.first_row()
        tracker.db.session.add(tracker.MonthlyBuildCompletion(item=item, unit_number=1, completed_by='Alice'))
        tracker.db.session.commit()
        self.invoice.month = 'January'
        self.invoice.collection_date = '08/01/27'
        tracker.sync_invoice_build_lists()
        tracker.db.session.commit()
        self.assertEqual(date(2027, 1, 1), item.deadline.build_list.month_start)
        self.assertEqual(datetime(2027, 1, 8, 17), item.deadline.due_at)
        self.assertEqual(1, len(item.completions))

    def test_extra_services_are_not_bodies_and_unknown_catalogue_skus_are_reported(self):
        self.invoice.presentation_json = json.dumps({'custom_items': [
            {'description': 'Delivery service', 'quantity': 20, 'unit_cost_pence': 100},
        ]})
        rows, _ = tracker.invoice_body_rows(self.invoice)
        lines = json.loads(self.invoice.lines_json)
        lines.append({'sku': 'UNKNOWN/TABLE', 'quantity': 3, 'description': 'New product'})
        self.invoice.lines_json = json.dumps(lines)
        tracker.sync_invoice_build_lists()
        tracker.db.session.commit()
        self.assertEqual(sum(row['quantity'] for row in rows.values()), sum(item.quantity for item in self.link().deadline.items))
        self.assertIn('UNKNOWN/TABLE', self.link().warning)

    def test_linked_rows_cannot_be_manually_removed_but_ticks_still_work(self):
        _, item = self.first_row()
        build_list_id = item.deadline.build_list_id
        result = self.client.post('/monthly_build_list', data={
            'action': 'delete_item', 'build_list_id': build_list_id, 'item_id': item.id,
        })
        self.assertEqual(303, result.status_code)
        self.assertIsNotNone(tracker.db.session.get(tracker.MonthlyBuildItem, item.id))
        result = self.client.post(f'/api/monthly_build_list/items/{item.id}/units/1', json={'completed': True})
        self.assertEqual(200, result.status_code)
        self.assertEqual(1, len(item.completions))

    def test_new_sheet_order_creates_a_build_section_without_saving_an_invoice(self):
        source = copy.deepcopy(tracker.invoice_order_source())
        quantities = {product['sku']: 0 for product in source['products']}
        quantities['BIL/CHAM/6FT/GOAK'] = 7
        source['invoices'].append(dict(
            source_column='P', collection_date='22/01/27', month='January',
            reference='TBC', expected_quantity=7, quantities=quantities,
        ))
        tracker.apply_invoice_sheet_source(source)
        invoice = tracker.GeneratedInvoice.query.filter_by(source_column='P').one()
        self.assertIsNone(invoice.saved_at)
        link = tracker.db.session.get(tracker.InvoiceBuildOrder, invoice.id)
        self.assertEqual(date(2027, 1, 1), link.deadline.build_list.month_start)
        self.assertEqual([('Signature Champion', '6FT', 'Grey Oak', 7)],
                         [(row.model_name, row.size, row.colour, row.quantity) for row in link.deadline.items])

    def test_manual_lists_and_inventory_stay_unchanged(self):
        tracker.TableStock.__table__.create(tracker.db.engine, checkfirst=True)
        try:
            tracker.db.session.add(tracker.TableStock(type='body_6ft_black', count=20))
            manual = tracker.MonthlyBuildList(name='Manual work', month_start=date(2026, 12, 1), created_by='Alice')
            section = tracker.MonthlyBuildDeadline(build_list=manual, label='Extra stock', position=1)
            item = tracker.MonthlyBuildItem(deadline=section, model_name='Signature Champion', size='6FT', colour='Black', quantity=4)
            tracker.db.session.add(item)
            tracker.db.session.add(tracker.MonthlyBuildCompletion(item=item, unit_number=1, completed_by='Alice'))
            tracker.db.session.commit()
            self.client.get('/invoice_generator')
            self.assertEqual('Manual work', manual.name)
            self.assertEqual(4, item.quantity)
            self.assertEqual(1, len(item.completions))
            self.assertEqual(20, tracker.TableStock.query.one().count)
        finally:
            tracker.db.session.remove()
            tracker.TableStock.__table__.drop(tracker.db.engine)


if __name__ == '__main__':
    unittest.main()
