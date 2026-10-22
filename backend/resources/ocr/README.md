# Desktop OCR language data

Android and desktop backend use the official PaddleOCR PP-OCRv5 mobile ONNX
models. Android bundles these assets and runs ONNX Runtime directly. The Python
backend uses RapidOCR and ONNX Runtime in a worker process with a hard timeout.
Neither path contacts a hosted OCR service or downloads a model at runtime.

Model origins, pinned revisions, SHA-256 values, and licenses are recorded in
`paddle/manifest.json`. The PaddleOCR Apache-2.0 license is included in
`paddle/LICENSE`. The two ONNX models total about 20.5 MiB and increase the APK
size by about that amount before compression.

Install the optional desktop backend OCR dependencies alongside the regular
backend requirements with `pip install -r backend/requirements.txt -r
backend/requirements-ocr.txt`. Android does not install these Python packages;
it uses the ONNX Runtime Android dependency in Gradle.

References: [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR),
[RapidOCR](https://github.com/RapidAI/RapidOCR).

Android uses a lightweight connected-component DB postprocessor and axis-aligned
crops rather than the full OpenCV polygon/perspective pipeline. EXIF rotations
and tall text crops are handled; severely tilted or curved text remains a known
limitation. Compilation is not evidence of recognition accuracy on real images.
