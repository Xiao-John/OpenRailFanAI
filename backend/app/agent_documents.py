"""Bounded, offline document text extraction (stdlib only, including Android).

Word .doc support targets unencrypted Word 97–2003 compound documents. It reads
FIB/CLX piece tables, never guesses text by scanning arbitrary binary streams.
No macros, external references, images or embedded objects are evaluated.
"""
from __future__ import annotations

import io
import json
import re
import struct
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_CHARS = 60000
DOCUMENT_EXTENSIONS = frozenset({'.txt', '.md', '.tex', '.csv', '.json', '.docx', '.doc', '.rtf'})


class DocumentInputError(ValueError):
    pass


def _fail(message='文档格式损坏或不受支持'):
    raise DocumentInputError(message)


def _bounded(text):
    if len(text) > MAX_DOCUMENT_CHARS:
        _fail('文档文字超过 60000 字符，请拆分后导入')
    if not text.strip():
        _fail('文档没有可读取的文字（图片内容请使用图片输入/OCR）')
    return text


def read_document(name: str, content: bytes) -> str:
    """Extract text or raise DocumentInputError; never silently truncate."""
    if (not isinstance(name, str) or not name or len(name) > 160
            or re.search(r'[/\\\x00-\x1f\x7f]', name) or name in {'.', '..'}):
        _fail('文件名无效')
    if not isinstance(content, bytes) or not content or len(content) > MAX_DOCUMENT_BYTES:
        _fail('文件为空或超过 8 MiB')
    ext = PurePosixPath(name).suffix.lower()
    if ext not in DOCUMENT_EXTENSIONS:
        _fail('不支持此文档格式')
    try:
        if ext == '.docx':
            text = _docx(content)
        elif ext == '.doc':
            text = _doc(content)
        elif ext == '.rtf':
            text = _rtf(content)
        else:
            encoding = 'utf-16' if content.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
            text = content.decode(encoding)
            if '\x00' in text or any(ord(c) < 32 and c not in '\t\n\r' for c in text):
                _fail('文档不是受支持的纯文本编码')
            if ext == '.json':
                json.loads(text)
        return _bounded(text)
    except DocumentInputError:
        raise
    except (UnicodeError, ValueError, KeyError, IndexError, struct.error, ET.ParseError,
            zipfile.BadZipFile, RuntimeError, RecursionError, OverflowError, NotImplementedError, LookupError) as exc:
        raise DocumentInputError('文档格式损坏、编码不受支持或已加密') from exc


def _docx(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 2048 or sum(x.file_size for x in entries) > 32 * 1024 * 1024:
            _fail('DOCX 解压后过大')
        names = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            if (item.flag_bits & 1 or item.filename in names or path.is_absolute()
                    or '..' in path.parts or '\\' in item.filename
                    or item.file_size > 8 * 1024 * 1024):
                _fail('DOCX 包含不安全或过大的条目')
            names.add(item.filename)
        if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
            _fail('文件不是 DOCX 文档')
        xml = archive.read('word/document.xml')
        # stdlib XML expansion is additionally guarded before parsing.
        if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', xml.replace(b'\x00', b''), re.I):
            _fail('不支持包含实体声明的 DOCX')
        root = ET.fromstring(xml)
        namespace = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        body = root.find('w:body', namespace)
        if body is None:
            _fail('DOCX 缺少正文')
        w = '{' + namespace['w'] + '}'
        chunks = []
        count = 0
        for node in body.iter():
            if node.tag == w + 't':
                value = node.text or ''
            elif node.tag == w + 'tab':
                value = '\t'
            elif node.tag in {w + 'br', w + 'cr'}:
                value = '\n'
            elif node.tag == w + 'p':
                value = '\n' if chunks else ''
            else:
                continue
            count += len(value)
            if count > MAX_DOCUMENT_CHARS:
                _fail('文档文字超过 60000 字符，请拆分后导入')
            chunks.append(value)
        return ''.join(chunks).strip('\n')


class _Compound:
    """Read-only CFB FAT/miniFAT reader; all chains are bounded and cycle checked."""
    def __init__(self, data):
        self.data = data
        if data[:8] != bytes.fromhex('d0cf11e0a1b11ae1') or len(data) < 512:
            _fail('DOC 需要 Word 97–2003 二进制文档')
        version, byte_order, shift, mini_shift = struct.unpack_from('<4H', data, 26)
        if byte_order != 0xfffe or (version, shift) not in {(3, 9), (4, 12)} or mini_shift != 6:
            _fail('不支持的 DOC 容器版本')
        self.size = 1 << shift
        self.sectors = len(data) // self.size - 1
        if self.sectors < 1 or len(data) % self.size:
            _fail()
        fat_count, directory = struct.unpack_from('<II', data, 44)
        cutoff, mini_first, mini_count, dif_first, dif_count = struct.unpack_from('<5I', data, 56)
        if cutoff != 4096 or fat_count > self.sectors or dif_count > self.sectors:
            _fail()
        difat = list(struct.unpack_from('<109I', data, 76))
        seen = set()
        for _ in range(dif_count):
            if dif_first in seen:
                _fail()
            seen.add(dif_first)
            values = struct.unpack('<' + 'I' * (self.size // 4), self.sector(dif_first))
            difat.extend(values[:-1])
            dif_first = values[-1]
        fat_sectors = [n for n in difat if n != 0xffffffff]
        if len(fat_sectors) != fat_count or len(set(fat_sectors)) != len(fat_sectors):
            _fail()
        self.fat = []
        for sector in fat_sectors:
            self.fat.extend(struct.unpack('<' + 'I' * (self.size // 4), self.sector(sector)))
        directory_data = self.chain(directory, self.fat, self.sector, self.sectors)
        self.entries = {}
        root = None
        for offset in range(0, len(directory_data), 128):
            entry = directory_data[offset:offset + 128]
            name_size = struct.unpack_from('<H', entry, 64)[0]
            kind = entry[66]
            if not kind:
                continue
            if name_size < 2 or name_size > 64 or name_size % 2:
                _fail()
            name = entry[:name_size - 2].decode('utf-16-le')
            start = struct.unpack_from('<I', entry, 116)[0]
            size = struct.unpack_from('<Q', entry, 120)[0]
            if version == 3:
                size &= 0xffffffff
            if size > MAX_DOCUMENT_BYTES:
                _fail('DOC 内部流过大')
            if kind == 5:
                root = (start, size)
            elif kind == 2:
                if name in self.entries:
                    _fail()
                self.entries[name] = (start, size)
        if root is None:
            _fail()
        self.mini_data = self.chain(root[0], self.fat, self.sector, self.sectors, root[1]) if root[1] else b''
        mini_fat_data = self.chain(mini_first, self.fat, self.sector, self.sectors, mini_count * self.size) if mini_count else b''
        self.mini_fat = list(struct.unpack('<' + 'I' * (len(mini_fat_data) // 4), mini_fat_data))

    def sector(self, index):
        if not 0 <= index < self.sectors:
            _fail()
        return self.data[(index + 1) * self.size:(index + 2) * self.size]

    @staticmethod
    def chain(first, fat, reader, limit, size=None):
        parts, seen = [], set()
        current = first
        while current != 0xfffffffe:
            if current in seen or not 0 <= current < len(fat) or len(seen) >= limit:
                _fail('DOC 流链损坏')
            seen.add(current)
            parts.append(reader(current))
            current = fat[current]
        result = b''.join(parts)
        if size is not None:
            if len(result) < size:
                _fail('DOC 流被截断')
            result = result[:size]
        return result

    def stream(self, name):
        first, size = self.entries[name]
        if size == 0:
            return b''
        if size >= 4096:
            return self.chain(first, self.fat, self.sector, self.sectors, size)
        def mini(index):
            if (index + 1) * 64 > len(self.mini_data):
                _fail()
            return self.mini_data[index * 64:(index + 1) * 64]
        return self.chain(first, self.mini_fat, mini, len(self.mini_data) // 64, size)


def _doc(content):
    container = _Compound(content)
    if 'EncryptedPackage' in container.entries or 'EncryptionInfo' in container.entries:
        _fail('不支持加密文档')
    word = container.stream('WordDocument')
    ident, version = struct.unpack_from('<HH', word)
    flags = struct.unpack_from('<H', word, 10)[0]
    if ident != 0xa5ec or version < 0xc1:
        _fail('仅支持 Word 97–2003 DOC；较旧格式请另存为 DOCX/RTF')
    if flags & 0x8100:
        _fail('不支持加密或混淆的 DOC')
    table = container.stream('1Table' if flags & 0x200 else '0Table')
    offset = 32
    csw = struct.unpack_from('<H', word, offset)[0]
    offset += 2 + csw * 2
    cslw = struct.unpack_from('<H', word, offset)[0]
    offset += 2
    if cslw < 4:
        _fail()
    text_length = struct.unpack_from('<I', word, offset + 12)[0]
    if text_length > MAX_DOCUMENT_CHARS + 1:
        _fail('文档文字超过 60000 字符，请拆分后导入')
    offset += cslw * 4
    pair_count = struct.unpack_from('<H', word, offset)[0]
    if pair_count <= 33:
        _fail('DOC 缺少正文 piece table')
    clx_offset, clx_size = struct.unpack_from('<II', word, offset + 2 + 33 * 8)
    clx = table[clx_offset:clx_offset + clx_size]
    if len(clx) != clx_size:
        _fail()
    offset = 0
    while offset < len(clx) and clx[offset] == 1:
        offset += 3 + struct.unpack_from('<H', clx, offset + 1)[0]
    if offset >= len(clx) or clx[offset] != 2:
        _fail('DOC 缺少正文 piece table')
    length = struct.unpack_from('<I', clx, offset + 1)[0]
    plc = clx[offset + 5:offset + 5 + length]
    if len(plc) != length or length < 4 or (length - 4) % 12:
        _fail()
    count = (length - 4) // 12
    cps = struct.unpack_from('<' + 'I' * (count + 1), plc)
    if cps[0] != 0 or any(b < a for a, b in zip(cps, cps[1:])) or cps[-1] < text_length:
        _fail()
    chunks = []
    for index in range(count):
        if cps[index] >= text_length:
            break
        chars = min(cps[index + 1], text_length) - cps[index]
        encoded = struct.unpack_from('<I', plc, (count + 1) * 4 + index * 8 + 2)[0]
        compressed = bool(encoded & 0x40000000)
        file_offset = encoded & 0x3fffffff
        file_offset = file_offset // 2 if compressed else file_offset
        byte_count = chars if compressed else chars * 2
        piece = word[file_offset:file_offset + byte_count]
        if len(piece) != byte_count:
            _fail('DOC 正文被截断')
        chunks.append(piece.decode('cp1252' if compressed else 'utf-16-le'))
    text = ''.join(chunks)
    # Field instructions are not visible document content; keep field results.
    text = re.sub(r'\x13[^\x13\x14\x15]*\x14', '', text)
    text = re.sub(r'\x13[^\x13\x15]*\x15', '', text)
    return text.replace('\x15', '').replace('\x07', '\t').replace('\r', '\n').replace('\x0b', '\n').strip('\n')


def _rtf(data):
    if not data.startswith(b'{\\rtf'):
        _fail('文件不是 RTF 文档')
    skip_words = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'object', 'filetbl',
                  'datastore', 'themedata', 'colorschememapping', 'xmlnstbl', 'listtable',
                  'listoverridetable', 'generator', 'fldinst', 'header', 'footer'}
    output, pending = [], bytearray()
    codec = 'cp1252'
    stack = []
    skip, uc, fallback, star = False, 1, 0, False
    length = 0
    def flush():
        nonlocal length
        if pending:
            value = bytes(pending).decode(codec)
            output.append(value)
            length += len(value)
            pending.clear()
        if length > MAX_DOCUMENT_CHARS:
            _fail('文档文字超过 60000 字符，请拆分后导入')
    def emit(value):
        nonlocal length
        flush()
        if not skip:
            output.append(value)
            length += len(value)
    control = re.compile(br'([a-zA-Z]+)(-?\d+)? ?')
    i = 0
    while i < len(data):
        char = data[i]
        if char == 123:
            flush()
            if len(stack) >= 256:
                _fail('RTF 嵌套层数过多')
            stack.append((skip, uc, star, codec))
            star = False
            i += 1
        elif char == 125:
            flush()
            if not stack:
                _fail()
            skip, uc, star, codec = stack.pop()
            i += 1
            if not stack and data[i:].strip():
                _fail('RTF 存在正文外数据')
        elif char != 92:
            if char not in (10, 13) and not skip:
                if fallback:
                    fallback -= 1
                else:
                    pending.append(char)
            i += 1
        else:
            i += 1
            if i >= len(data):
                _fail()
            char = data[i]
            if char == 39:
                value = int(data[i + 1:i + 3], 16)
                if len(data[i + 1:i + 3]) != 2:
                    _fail()
                if not skip:
                    if fallback:
                        fallback -= 1
                    else:
                        pending.append(value)
                i += 3
            elif char in (92, 123, 125):
                if not skip:
                    if fallback:
                        fallback -= 1
                    else:
                        pending.append(char)
                i += 1
            elif not (65 <= char <= 90 or 97 <= char <= 122):
                if char == 42:
                    star = True
                elif char in (10, 13):
                    emit('\n')
                    if char == 13 and i + 1 < len(data) and data[i + 1] == 10:
                        i += 1
                elif char in (126, 95, 45) and not fallback:
                    emit({126: '\u00a0', 95: '\u2011', 45: '\u00ad'}[char])
                elif fallback:
                    fallback -= 1
                i += 1
            else:
                match = control.match(data, i)
                word = match[1].decode('ascii')
                number = int(match[2]) if match[2] else None
                i = match.end()
                if word in skip_words or star:
                    flush()
                    skip, star = True, False
                elif word == 'bin':
                    if number is None or number < 0 or i + number > len(data):
                        _fail()
                    i += number
                elif word == 'ansicpg':
                    flush()
                    codec = 'utf-8' if number == 65001 else f'cp{number}'
                    try:
                        b''.decode(codec)
                    except LookupError:
                        _fail('不支持 RTF 字符编码')
                elif word == 'uc':
                    if number is None or not 0 <= number <= 16:
                        _fail()
                    uc = number
                elif word == 'u':
                    if number is None or not -32768 <= number <= 65535:
                        _fail()
                    emit(chr(number & 0xffff))
                    fallback = uc
                elif word in {'par', 'line', 'tab', 'emdash', 'endash', 'bullet', 'lquote', 'rquote', 'ldblquote', 'rdblquote'}:
                    emit({'par': '\n', 'line': '\n', 'tab': '\t', 'emdash': '—', 'endash': '–',
                          'bullet': '•', 'lquote': '‘', 'rquote': '’', 'ldblquote': '“', 'rdblquote': '”'}[word])
    flush()
    if stack:
        _fail('RTF 组未闭合')
    return ''.join(output).encode('utf-16-le', 'surrogatepass').decode('utf-16-le').strip('\n')
