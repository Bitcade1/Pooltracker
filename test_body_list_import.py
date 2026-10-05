import unittest
import zipfile
from datetime import date, datetime
from io import BytesIO

from werkzeug.datastructures import FileStorage

from flask_app import (
    BODY_LIST_IMPORT_MAX_BYTES,
    parse_body_list_import,
    read_body_list_import_text,
)


def odt_document(body_xml):
    content = (
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
        '<office:body><office:text>' + body_xml
        + '</office:text></office:body></office:document-content>'
    )
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as document:
        document.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
        document.writestr('content.xml', content)
    return output.getvalue()


class BodyListOdtImportTests(unittest.TestCase):
    def read_upload(self, contents, filename='body-list.odt'):
        return read_body_list_import_text(
            FileStorage(stream=BytesIO(contents), filename=filename), ''
        )

    def test_headings_bullets_and_inline_formatting(self):
        contents = odt_document(
            '<text:h>Ready for 5pm 10th of Oct</text:h>'
            '<text:p>Signature <text:span>League</text:span></text:p>'
            '<text:list><text:list-item>'
            '<text:p>6FT<text:s text:c="2"/>Black<text:tab/>- 8</text:p>'
            '</text:list-item><text:list-item>'
            '<text:p>7FT Black - 10</text:p>'
            '</text:list-item></text:list>'
        )
        sections = parse_body_list_import(self.read_upload(contents), date(2026, 10, 1))
        self.assertEqual([{
            'label': 'Ready for',
            'due_at': datetime(2026, 10, 10, 17),
            'items': [
                {'model_name': 'Signature League', 'size': '6FT', 'colour': 'Black', 'quantity': 8},
                {'model_name': 'Signature League', 'size': '7FT', 'colour': 'Black', 'quantity': 10},
            ],
        }], sections)

    def test_table_rows_stay_between_their_headings(self):
        contents = odt_document(
            '<text:h>Ready for 5pm 10th of Oct</text:h>'
            '<text:p>Signature Champion</text:p>'
            '<table:table><table:table-row>'
            '<table:table-cell><text:p>7FT</text:p></table:table-cell>'
            '<table:table-cell><text:p>Rustic Oak</text:p></table:table-cell>'
            '<table:table-cell><text:p>4</text:p></table:table-cell>'
            '</table:table-row></table:table>'
            '<text:h>STOCK FOR CHRISTMAS</text:h>'
            '<text:p>Signature League<text:line-break/>- 6FT Black - 2</text:p>'
        )
        sections = parse_body_list_import(self.read_upload(contents), date(2026, 10, 1))
        self.assertEqual(2, len(sections))
        self.assertEqual('Signature Champion', sections[0]['items'][0]['model_name'])
        self.assertEqual('Rustic Oak', sections[0]['items'][0]['colour'])
        self.assertEqual(4, sections[0]['items'][0]['quantity'])
        self.assertEqual('STOCK FOR CHRISTMAS', sections[1]['label'])
        self.assertIsNone(sections[1]['due_at'])
        self.assertEqual('Signature League', sections[1]['items'][0]['model_name'])

    def test_comments_and_footnotes_do_not_become_body_rows(self):
        contents = odt_document(
            '<text:p>STOCK</text:p><text:p>Signature League</text:p>'
            '<office:annotation><text:p>7FT Black - 99</text:p></office:annotation>'
            '<text:p>6FT Black - 2<text:note><text:note-body>'
            '<text:p>7FT Black - 99</text:p>'
            '</text:note-body></text:note></text:p>'
            '<table:table><table:table-row><table:table-cell>'
            '<office:annotation><text:p>7FT Black - 99</text:p></office:annotation>'
            '<text:p>7FT Black - 3</text:p>'
            '</table:table-cell></table:table-row></table:table>'
        )
        sections = parse_body_list_import(self.read_upload(contents), date(2026, 10, 1))
        self.assertEqual([
            {'model_name': 'Signature League', 'size': '6FT', 'colour': 'Black', 'quantity': 2},
            {'model_name': 'Signature League', 'size': '7FT', 'colour': 'Black', 'quantity': 3},
        ], sections[0]['items'])

    def test_uppercase_extension_and_pasted_text(self):
        self.assertEqual('STOCK', self.read_upload(odt_document('<text:p>STOCK</text:p>'), 'LIST.ODT'))
        self.assertEqual('STOCK', read_body_list_import_text(None, ' STOCK '))

    def test_invalid_odt_files_have_readable_errors(self):
        missing_content = BytesIO()
        with zipfile.ZipFile(missing_content, 'w') as document:
            document.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
        invalid_xml = BytesIO()
        with zipfile.ZipFile(invalid_xml, 'w') as document:
            document.writestr('content.xml', '<broken>')
        for contents in (b'Not a ZIP document', missing_content.getvalue(), invalid_xml.getvalue()):
            with self.subTest(contents=contents[:20]):
                with self.assertRaisesRegex(ValueError, r'OpenDocument \(.odt\) document could not be read'):
                    self.read_upload(contents)

    def test_compressed_document_text_has_size_limit(self):
        contents = odt_document('<text:p>' + 'x' * BODY_LIST_IMPORT_MAX_BYTES + '</text:p>')
        self.assertLess(len(contents), BODY_LIST_IMPORT_MAX_BYTES)
        with self.assertRaisesRegex(ValueError, 'document text is too large'):
            self.read_upload(contents)

    def test_existing_text_and_word_uploads(self):
        from docx import Document
        text = 'STOCK\nSignature League\n6FT Black - 2'
        for extension in ('.txt', '.md'):
            self.assertEqual(text, self.read_upload(text.encode('utf-8'), 'list' + extension))
        document = Document()
        for line in text.splitlines():
            document.add_paragraph(line)
        output = BytesIO()
        document.save(output)
        self.assertEqual(text, self.read_upload(output.getvalue(), 'list.docx'))


class BodyListDeadlineImportTests(unittest.TestCase):
    body_rows = 'Signature League\n6FT Black - 10\n7FT Black - 13\n6FT Rustic Black 4'

    def test_reported_month_first_deadline_and_body_rows(self):
        sections = parse_body_list_import(
            'Ready for NOVEMBER 5th 5pm\n' + self.body_rows, date(2026, 10, 1)
        )
        self.assertEqual(datetime(2026, 11, 5, 17), sections[0]['due_at'])
        self.assertEqual([
            {'model_name': 'Signature League', 'size': '6FT', 'colour': 'Black', 'quantity': 10},
            {'model_name': 'Signature League', 'size': '7FT', 'colour': 'Black', 'quantity': 13},
            {'model_name': 'Signature League', 'size': '6FT', 'colour': 'Rustic Black', 'quantity': 4},
        ], sections[0]['items'])

    def test_deadline_order_and_time_variations(self):
        headings = (
            ('Ready for 5pm 5th of November', datetime(2026, 11, 5, 17)),
            ('Ready for 5pm November 5th', datetime(2026, 11, 5, 17)),
            ('Ready for 5th November 5pm', datetime(2026, 11, 5, 17)),
            ('Ready for November 5th at 5:30 pm', datetime(2026, 11, 5, 17, 30)),
            ('Ready for Nov 5 2027 17:30', datetime(2027, 11, 5, 17, 30)),
            ('Ready for 5.30pm 5th of Nov 2027', datetime(2027, 11, 5, 17, 30)),
            ('Ready for November 5th', datetime(2026, 11, 5, 17)),
            ('Ready for 5th of November', datetime(2026, 11, 5, 17)),
            ('Ready for November 5th 12am', datetime(2026, 11, 5)),
            ('Ready for November 5th 12pm', datetime(2026, 11, 5, 12)),
        )
        for heading, expected_deadline in headings:
            with self.subTest(heading=heading):
                sections = parse_body_list_import(heading + '\n' + self.body_rows, date(2026, 10, 1))
                self.assertEqual(expected_deadline, sections[0]['due_at'])

    def test_invalid_deadlines_still_report_errors(self):
        for heading, error in (
            ('Ready for November 31st 5pm', 'Invalid deadline'),
            ('Ready for November 5th 13pm', 'Invalid time'),
            ('Ready for November 5th 5:60pm', 'Invalid deadline'),
            ('Ready for Notamonth 5th 5pm', 'Unknown month'),
        ):
            with self.subTest(heading=heading):
                with self.assertRaisesRegex(ValueError, error):
                    parse_body_list_import(heading + '\n' + self.body_rows, date(2026, 10, 1))

    def test_month_first_deadline_in_odt_upload(self):
        content = odt_document(
            '<text:h>Ready for NOVEMBER 5th 5pm</text:h>'
            '<text:p>Signature League</text:p>'
            '<text:p>6FT Black - 10</text:p>'
            '<text:p>7FT Black - 13</text:p>'
            '<text:p>6FT Rustic Black 4</text:p>'
        )
        text = read_body_list_import_text(
            FileStorage(stream=BytesIO(content), filename='November.odt'), ''
        )
        sections = parse_body_list_import(text, date(2026, 10, 1))
        self.assertEqual(datetime(2026, 11, 5, 17), sections[0]['due_at'])
        self.assertEqual(27, sum(item['quantity'] for item in sections[0]['items']))


if __name__ == '__main__':
    unittest.main()
