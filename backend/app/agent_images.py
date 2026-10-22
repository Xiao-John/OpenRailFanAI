"""Bounded image validation and entirely local PaddleOCR/ONNX recognition.

Android uses the same bundled PaddleOCR models through ONNX Runtime. This
backend adapter is for desktop deployments; it never sends an image to an OCR
service.
"""
from pathlib import Path
import importlib.util
import os
import sys
import struct
import subprocess
import tempfile

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
IMAGE_EXTENSIONS = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp', '.gif': 'image/gif'}


class LocalOCRUnavailable(RuntimeError):
    """A local OCR engine/language pack is missing; clients may use native OCR."""


def image_info(content: bytes, extension: str):
    """Validate the signature and encoded dimensions before decoding/uploading."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError('图片不能超过 8 MiB')
    width = height = 0
    mime = IMAGE_EXTENSIONS.get(extension.lower())
    try:
        if mime == 'image/png' and len(content) >= 33 and content[:8] == b'\x89PNG\r\n\x1a\n' and content[8:16] == b'\x00\x00\x00\rIHDR':
            width, height = struct.unpack('>II', content[16:24])
        elif mime == 'image/gif' and len(content) >= 13 and content[:6] in (b'GIF87a', b'GIF89a'):
            width, height = struct.unpack('<HH', content[6:10])
        elif mime == 'image/jpeg' and content[:2] == b'\xff\xd8':
            offset = 2
            while offset < len(content):
                if content[offset] != 255:
                    raise ValueError('JPEG 标记无效')
                while offset < len(content) and content[offset] == 255:
                    offset += 1
                marker = content[offset]; offset += 1
                if marker in (0xDA, 0xD9):
                    break
                if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
                    continue
                size = struct.unpack('>H', content[offset:offset+2])[0]
                if size < 2 or offset+size > len(content):
                    raise ValueError('JPEG 数据不完整')
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if size < 8:
                        raise ValueError('JPEG 尺寸标记损坏')
                    height, width = struct.unpack('>HH', content[offset+3:offset+7])
                    break
                offset += size
        elif mime == 'image/webp' and len(content) >= 25 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
            total = struct.unpack('<I', content[4:8])[0] + 8
            chunk_size = struct.unpack('<I', content[16:20])[0]
            if total != len(content) or chunk_size > len(content)-20:
                raise ValueError('WebP 数据不完整')
            kind = content[12:16]
            if kind == b'VP8X' and chunk_size >= 10 and len(content) >= 30:
                width = 1+int.from_bytes(content[24:27], 'little')
                height = 1+int.from_bytes(content[27:30], 'little')
            elif kind == b'VP8L' and chunk_size >= 5 and content[20:21] == b'\x2f':
                bits = int.from_bytes(content[21:25], 'little')
                width, height = (bits & 0x3FFF)+1, ((bits >> 14) & 0x3FFF)+1
            elif kind == b'VP8 ' and chunk_size >= 10 and len(content) >= 30 and content[23:26] == b'\x9d\x01\x2a':
                width, height = (n & 0x3FFF for n in struct.unpack('<HH', content[26:30]))
    except (IndexError, struct.error) as exc:
        raise ValueError('图片头部损坏') from exc
    if not width or not height or width*height > MAX_PIXELS or max(width, height) > 12000:
        raise ValueError('图片无效或超过 2400 万像素，请缩小后重试')
    return {'mime': mime, 'width': width, 'height': height}


def local_ocr(content: bytes, mime: str) -> str:
    if importlib.util.find_spec('rapidocr') is None:
        raise LocalOCRUnavailable('本地 PaddleOCR 未安装；请使用设备端 OCR')
    suffix = next((ext for ext, value in IMAGE_EXTENSIONS.items() if value == mime), None)
    if suffix is None:
        raise ValueError('不支持的图片类型')
    image_info(content, suffix)
    backend_root = Path(__file__).resolve().parents[1]
    command = [sys.executable, '-m', 'app.ocr_worker']
    with tempfile.TemporaryDirectory(prefix='railfan-ocr-') as directory:
        image = Path(directory)/('input'+suffix)
        image.write_bytes(content)
        command += [str(image)]
        # Bound reads before decoding: PIPE would first collect arbitrary OCR
        # output into memory, including verbose errors from malformed inputs.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run(command, stdout=output, stderr=errors,
                                        timeout=30, check=False, cwd=backend_root,
                                        env={**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'})
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LocalOCRUnavailable('本地 OCR 无法运行或处理超时') from exc
            if result.returncode:
                raise LocalOCRUnavailable('本地 PaddleOCR 失败，请确认 OCR 运行依赖可用')
            output.seek(0)
            raw = output.read(240001)
            if len(raw) > 240000:
                raise ValueError('OCR 文字超出 60000 字符或内容无效')
    try:
        text = raw.decode('utf-8').strip()
    except UnicodeDecodeError as exc:
        raise ValueError('OCR 文字编码无效') from exc
    if not text:
        raise ValueError('图片未识别出文字；可开启支持图片的模型输入，或换一张清晰图片')
    if len(text) > 60000 or '\x00' in text:
        raise ValueError('OCR 文字超出 60000 字符或内容无效')
    return text
