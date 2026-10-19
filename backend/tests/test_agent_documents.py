"""Real office fixtures and hostile bounded inputs; no office/OCR subprocesses."""
import io
import struct
import unittest
import zipfile
from pathlib import Path

from app.agent_documents import (
    DOCUMENT_EXTENSIONS, DocumentInputError, MAX_DOCUMENT_BYTES,
    MAX_DOCUMENT_CHARS, read_document,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'agent_documents'
EXPECTED = 'RailFan 文档输入验证\n北京 → 上海虹桥\nG1 2026-10-18 09:00'


def docx(xml, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as out:
        out.writestr('[Content_Types].xml', '<Types/>')
        out.writestr('word/document.xml', xml)
        if extra:
            out.writestr(*extra)
    return buffer.getvalue()


class DocumentInputTests(unittest.TestCase):
    def test_real_office_documents(self):
        for ext in ('doc', 'docx', 'rtf'):
            with self.subTest(ext=ext):
                path = FIXTURES / ('sample.' + ext)
                self.assertEqual(read_document(path.name, path.read_bytes()), EXPECTED)

    def test_pure_text_extensions_and_encodings(self):
        for ext in ('txt', 'md', 'tex', 'csv', 'json'):
            text = '{"车次":"G1"}' if ext == 'json' else '北京,上海虹桥\n\\begin{document}'
            for encoding in ('utf-8-sig', 'utf-16'):
                with self.subTest(ext=ext, encoding=encoding):
                    self.assertEqual(read_document('sample.' + ext, text.encode(encoding)), text)
        self.assertEqual(len(DOCUMENT_EXTENSIONS), 8)

    def test_rejects_unsafe_names_and_binary_disguise(self):
        for name in ('../x.txt', '/x.txt', 'C:\\x.txt', 'x\x00.txt', 'x\n.md', 'x.pdf', 'a' * 161 + '.txt'):
            with self.subTest(name=name), self.assertRaises(DocumentInputError):
                read_document(name, b'hello')
        for name, content in [('x.txt', b'abc\x00bad'), ('x.txt', b'\xff'), ('x.json', b'{broken'),
                              ('x.doc', b'plain text'), ('x.rtf', b'plain text'), ('x.docx', b'plain text')]:
            with self.subTest(name=name), self.assertRaises(DocumentInputError):
                read_document(name, content)

    def test_empty_and_limits_never_silently_truncate(self):
        for content in (b'', b' \n\t', b'a' * (MAX_DOCUMENT_BYTES + 1), b'a' * (MAX_DOCUMENT_CHARS + 1)):
            with self.assertRaises(DocumentInputError):
                read_document('x.txt', content)
        self.assertEqual(len(read_document('x.txt', b'a' * MAX_DOCUMENT_CHARS)), MAX_DOCUMENT_CHARS)

    def test_docx_paragraph_table_tab_and_line_break(self):
        xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>D</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        self.assertEqual(read_document('x.docx', docx(xml)), 'A\tB\nC\nD')

    def test_docx_rejects_entities_paths_and_bombs(self):
        for content in (docx(b'<!DOCTYPE x [<!ENTITY a "bad">]><x/>'),
                        docx('<!DOCTYPE x [<!ENTITY a "bad">]><x/>'.encode('utf-16')),
                        docx(b'<x/>', ('../escape', 'bad')),
                        docx(b'<x/>', ('large.bin', b'x' * (8 * 1024 * 1024 + 1)))):
            with self.assertRaises(DocumentInputError):
                read_document('x.docx', content)

    def test_rtf_destinations_unicode_surrogates_and_ansi(self):
        source = br"{\rtf1\ansi\ansicpg936{\fonttbl hidden}{\*\private hidden}A\par \'d6\'d0\tab \uc1\u-10179?\u-8704?{\field{\*\fldinst HYPERLINK hidden}{\fldrslt link}}}"
        self.assertEqual(read_document('x.rtf', source), 'A\n中\t😀link')

    def test_rtf_binary_ignored_and_malformed_rejected(self):
        self.assertEqual(read_document('x.rtf', br'{\rtf1 A{\pict\bin4 {}\\}B}'), 'AB')
        for value in (br'{\rtf1 never closes', br'{\rtf1 x}extra', b"{\\rtf1 \\'zz}",
                      br'{\rtf1 \bin999 x}', br'{\rtf1 \uc999\u1?}',
                      b'{\\rtf1 ' + b'{' * 257 + b'x' + b'}' * 258):
            with self.subTest(value=value[:30]), self.assertRaises(DocumentInputError):
                read_document('x.rtf', value)

    def test_doc_encryption_and_corrupt_fat_rejected(self):
        data = bytearray((FIXTURES / 'sample.doc').read_bytes())
        # Locate WordDocument stream through the actual CFB reader.
        from app.agent_documents import _Compound
        container = _Compound(bytes(data))
        word_start, size = container.entries['WordDocument']
        self.assertGreaterEqual(size, 4096)
        flag_offset = (word_start + 1) * container.size + 10
        struct.pack_into('<H', data, flag_offset, struct.unpack_from('<H', data, flag_offset)[0] | 0x100)
        with self.assertRaisesRegex(DocumentInputError, '加密'):
            read_document('x.doc', bytes(data))
        data = bytearray((FIXTURES / 'sample.doc').read_bytes())
        struct.pack_into('<I', data, 48, 0xfffffff0)
        with self.assertRaises(DocumentInputError):
            read_document('x.doc', bytes(data))


if __name__ == '__main__':
    unittest.main()
