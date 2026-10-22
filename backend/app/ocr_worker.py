"""One-shot PaddleOCR worker, isolated so callers can enforce a hard timeout."""
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR, EngineType, LangDet, LangRec, OCRVersion, ModelType

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_TEXT_CHARS = 60_000


def recognize(path: Path) -> str:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_PIXELS:
            raise ValueError('图片超过 2400 万像素')
        # RapidOCR follows OpenCV's BGR convention. Convert only after applying
        # EXIF orientation so desktop and Android recognize the same pixels.
        rgb = np.asarray(image.convert('RGB'))
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])

    root = Path(__file__).resolve().parents[1] / 'resources' / 'ocr' / 'paddle' / 'v5'
    if not all((root / name).is_file() for name in ('det.onnx', 'rec.onnx', 'dict.txt')):
        raise FileNotFoundError('缺少本地 PaddleOCR 资源，禁止自动下载')
    engine = RapidOCR(params={
        'Global.log_level': 'error',
        'Global.use_cls': False,
        'Global.max_side_len': 2000,
        'EngineConfig.onnxruntime.intra_op_num_threads': 2,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
        'Det.engine_type': EngineType.ONNXRUNTIME,
        'Det.lang_type': LangDet.CH,
        'Det.ocr_version': OCRVersion.PPOCRV5,
        'Det.model_type': ModelType.MOBILE,
        'Det.model_path': str(root / 'det.onnx'),
        'Det.limit_side_len': 960,
        'Det.limit_type': 'max',
        'Rec.engine_type': EngineType.ONNXRUNTIME,
        'Rec.lang_type': LangRec.CH,
        'Rec.ocr_version': OCRVersion.PPOCRV5,
        'Rec.model_type': ModelType.MOBILE,
        'Rec.model_path': str(root / 'rec.onnx'),
        'Rec.rec_keys_path': str(root / 'dict.txt'),
    })
    result = engine(bgr, use_cls=False)
    text = '\n'.join(line.strip() for line in (result.txts or ()) if line and line.strip()).strip()
    if not text:
        raise ValueError('图片未识别出文字；请换清晰图片，或为支持看图的模型启用图片输入')
    if len(text) > MAX_TEXT_CHARS or '\x00' in text:
        raise ValueError('OCR 文字超出 60000 字符或内容无效')
    return text


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    path = Path(sys.argv[1])
    if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        return 2
    sys.stdout.write(recognize(path))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Keep library paths, private image names, and native diagnostics out of
        # the user-visible error. The parent reports one stable local failure.
        print(type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
