# Image input / local OCR fixtures

These small synthetic fixtures were generated locally for tests. They contain no
user uploads, personal data, remote assets, or provider credentials.

- `sample.png`, `sample.jpg`, `sample.gif`: 13 × 7 white image.
- `sample-lossy.webp`: simple lossy VP8 container.
- `sample-lossless.webp`: VP8L container.
- `sample-extended.webp`: VP8X container (alpha channel).
- `ocr-chinese.png`: rendered synthetic Chinese/Latin text: 北京南站 到 上海虹桥;
  旅客列车 时刻查询; G1 2026-10-18 09:00. This tests actual local recognition,
  not OCR accuracy on arbitrary user images.

Fixtures were exported once with Pillow 12.0.0; production image header parsing
and these tests do not import or require Pillow. The OCR fixture uses macOS STHeiti
Medium for Chinese and Arial for Latin. Tests use the saved pixels, not host fonts.
The real OCR test requires Tesseract plus local chi_sim and eng trained data;
other tests stub the executable and never use a cloud service.
