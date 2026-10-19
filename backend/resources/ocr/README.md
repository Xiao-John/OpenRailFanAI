# Desktop OCR language data

The desktop/Web backend adapter uses a locally installed Tesseract executable.
It never calls a hosted OCR service. Android uses its bundled ML Kit recognizer
instead and does not need Tesseract.

Included language data: Simplified Chinese and English, from
[tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast),
revision `87416418657359cb625c412a48b6e1d6d41c29bd`. `manifest.json` records the
SHA-256 of each downloaded file; the upstream Apache-2.0 license is in `LICENSE`.
No language data is downloaded at runtime.

Install Tesseract using the platform package manager (`brew install tesseract`
on macOS; `apt install tesseract-ocr` on Debian/Ubuntu). The adapter detects it
on PATH. `RAILFAN_TESSERACT` may specify its executable path, and
`RAILFAN_TESSDATA_DIR` may override this directory's `tessdata` path.
Keep both `chi_sim.traineddata` and `eng.traineddata` when deploying this backend.

References: [installation](https://tesseract-ocr.github.io/tessdoc/Installation.html),
[command line](https://github.com/tesseract-ocr/tessdoc/blob/main/Command-Line-Usage.md).
