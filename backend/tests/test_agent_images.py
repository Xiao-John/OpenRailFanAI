"""Local fixture tests; no image service/network or Pillow runtime dependency."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import agent_images as images

FIXTURES = Path(__file__).parent/'fixtures'/'agent_images'
PNG = (FIXTURES/'sample.png').read_bytes()


class ImageValidationTest(unittest.TestCase):
    def test_real_container_variants(self):
        for name in ('sample.png', 'sample.jpg', 'sample.gif', 'sample-lossy.webp',
                     'sample-lossless.webp', 'sample-extended.webp'):
            with self.subTest(name=name):
                info = images.image_info((FIXTURES/name).read_bytes(), Path(name).suffix)
                self.assertEqual((info['width'], info['height']), (13, 7))
                self.assertTrue(info['mime'].startswith('image/'))

    def test_signature_mismatch_unknown_type_and_empty(self):
        for content, extension in ((PNG,'.jpg'),(PNG,'.svg'),(b'', '.png'),(b'hello', '.png')):
            with self.assertRaises(ValueError):
                images.image_info(content, extension)

    def test_truncated_headers_rejected(self):
        for name in ('sample.png','sample.jpg','sample.gif','sample-lossy.webp',
                     'sample-lossless.webp','sample-extended.webp'):
            content = (FIXTURES/name).read_bytes()
            limit = 33 if name.endswith('png') else 13 if name.endswith('gif') else 30
            for size in range(limit):
                with self.subTest(name=name,size=size), self.assertRaises(ValueError):
                    images.image_info(content[:size],Path(name).suffix)

    def test_pixel_axis_and_byte_limits(self):
        for width,height in ((5000,5000),(12001,1),(1,12001),(0,12)):
            patched=PNG[:16]+struct.pack('>II',width,height)+PNG[24:]
            with self.assertRaises(ValueError):
                images.image_info(patched,'.png')
        with self.assertRaises(ValueError):
            images.image_info(PNG+b'0'*(images.MAX_IMAGE_BYTES+1),'.png')

    def test_webp_container_and_chunk_lengths(self):
        data=(FIXTURES/'sample-extended.webp').read_bytes()
        for broken in (data[:-1],data[:16]+struct.pack('<I',99999999)+data[20:],
                       data[:4]+struct.pack('<I',1)+data[8:]):
            with self.assertRaises(ValueError):
                images.image_info(broken,'.webp')


class LocalOCRTest(unittest.TestCase):
    def invoke(self, runner):
        with patch.object(images.shutil,'which',return_value='/local/tesseract'), \
             patch.object(images.subprocess,'run',runner), \
             patch.object(__import__('app.config', fromlist=['get_settings']).get_settings(), 'railfan_tesseract', '/local/tesseract'):
            return images.local_ocr(PNG,'image/png')

    def test_command_is_local_bounded_and_temp_input_removed(self):
        seen=[]
        def runner(command,**kwargs):
            seen.append(Path(command[1]))
            self.assertTrue(seen[0].exists())
            self.assertNotIn('shell',kwargs)
            self.assertEqual(command[0],'/local/tesseract')
            self.assertEqual(command[2:7],['stdout','-l','chi_sim+eng','--psm','3'])
            self.assertEqual(kwargs['timeout'],30)
            self.assertEqual(kwargs['env']['OMP_THREAD_LIMIT'],'1')
            kwargs['stdout'].write('北京南站 G1'.encode())
            return SimpleNamespace(returncode=0)
        self.assertEqual(self.invoke(runner),'北京南站 G1')
        self.assertFalse(seen[0].exists())

    def test_timeout_and_missing_engine_are_explicit(self):
        def timeout(*args,**kwargs):
            raise subprocess.TimeoutExpired('private-image-path',30)
        with self.assertRaises(images.LocalOCRUnavailable) as ctx:
            self.invoke(timeout)
        self.assertNotIn('private-image-path',str(ctx.exception))
        with patch.dict(os.environ,{},clear=True), patch.object(images.shutil,'which',return_value=None):
            with self.assertRaises(images.LocalOCRUnavailable):
                images.local_ocr(PNG,'image/png')

    def test_output_size_utf8_empty_and_nul(self):
        for raw in (b'x'*240001,b'x'*60001,b'\xff',b'',b'a\x00b'):
            def runner(command,**kwargs):
                kwargs['stdout'].write(raw)
                return SimpleNamespace(returncode=0)
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                self.invoke(runner)

    def test_upstream_diagnostics_are_not_returned(self):
        def runner(command,**kwargs):
            kwargs['stderr'].write(b'private-path-and-secret')
            return SimpleNamespace(returncode=1)
        with self.assertRaises(images.LocalOCRUnavailable) as ctx:
            self.invoke(runner)
        self.assertNotIn('private-path-and-secret',str(ctx.exception))

    @unittest.skipUnless(shutil.which('tesseract') and
        (Path(__file__).resolve().parents[1]/'resources/ocr/tessdata/chi_sim.traineddata').exists(),
        'local Chinese Tesseract installation not available')
    def test_real_local_chinese_ocr(self):
        text=images.local_ocr((FIXTURES/'ocr-chinese.png').read_bytes(),'image/png')
        normalized=''.join(text.split())
        self.assertIn('北京南站',normalized)
        self.assertIn('上海虹桥',normalized)
        self.assertIn('G1',normalized)


if __name__=='__main__':
    unittest.main()
