# Office input fixtures

`sample.doc`, `sample.docx`, and `sample.rtf` were generated on 2026-10-18
with the macOS `/usr/bin/textutil` converter from the same owned UTF-8 input:

```text
RailFan 文档输入验证
北京 → 上海虹桥
G1 2026-10-18 09:00
```

They are real office containers, not text renamed with office suffixes. Tests
read their committed bytes and do not call textutil, Word, LibreOffice, network,
or platform services. The fixture content is project-authored test data.

The runtime extraction module uses only Python's standard library, including
on Chaquopy. It extracts document text, not page geometry, macros, pictures,
embedded objects or external links. DOC means unencrypted Word 97–2003 CFB
with a CLX piece table. Earlier Word binaries must be saved as DOCX or RTF.
DOCX currently extracts body paragraphs and table-cell text; headers, footers,
comments and page layout are outside the current extraction contract. Scanned
pictures embedded in Office files require a separate image/OCR input.
