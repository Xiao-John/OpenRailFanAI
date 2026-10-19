"""Attachment/API integration with real office bytes and mocked paid boundaries."""
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.agent import router
from app.agent_documents import MAX_DOCUMENT_BYTES
from app.agent_images import LocalOCRUnavailable
from app.llm import client as llm

APP = FastAPI()
APP.include_router(router, prefix='/api')
CLIENT = TestClient(APP)
FIXTURES = Path(__file__).parent / 'fixtures'
PNG = (FIXTURES / 'agent_images' / 'sample.png').read_bytes()
PNG_SECOND = (FIXTURES / 'agent_images' / 'ocr-chinese.png').read_bytes()
ANSWER = dict(operation='answer', format='md', answer='已读取资料。', columns=[], rows=[], content='', events=[])


def attachment(name, data, **kwargs):
    return dict(name=name, content_base64=base64.b64encode(data).decode(), **kwargs)


def request(files, **kwargs):
    return CLIENT.post('/api/agent/run', json=dict(message='请阅读附件并简述', actions=['files'], files=files, **kwargs))


def payload(model, index=-1):
    return json.loads(model.call_args_list[index].kwargs['prompt_tail'])


class AgentAttachmentRoutes(unittest.TestCase):
    def test_actual_office_files_and_tex_reach_model_as_text(self):
        for ext in ('doc', 'docx', 'rtf', 'tex'):
            content = ((FIXTURES / 'agent_documents' / ('sample.' + ext)).read_bytes()
                       if ext != 'tex' else '\\begin{document}北京 G1\\end{document}'.encode())
            model = AsyncMock(return_value=ANSWER)
            with self.subTest(ext=ext), patch.object(llm, 'chat_structured', model):
                response = request([attachment('sample.' + ext, content)])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('北京', payload(model)['files'][0]['text'])
                self.assertEqual(payload(model)['files'][0]['name'], 'sample.' + ext)
                self.assertNotIn('images', model.call_args.kwargs)
                self.assertEqual(response.json()['input_processing'], [{'name': 'sample.' + ext, 'mode': 'document_text'}])

    def test_client_ocr_metadata_reaches_prompt_without_image_transport(self):
        model = AsyncMock(return_value=ANSWER)
        file = attachment('picture-ocr.txt', '北京南站'.encode())
        file['source'] = 'local_ocr'
        with patch.object(llm, 'chat_structured', model), patch('app.agent.local_ocr') as ocr:
            response = request([file])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(payload(model)['files'][0]['source'], 'local_ocr')
        self.assertEqual(response.json()['input_processing'][0]['mode'], 'local_ocr')
        self.assertNotIn('images', model.call_args.kwargs)
        ocr.assert_not_called()

    def test_image_enabled_sends_original_bytes_and_order_without_ocr(self):
        files = [attachment('a.png', PNG, mime='image/png'), attachment('b.png', PNG_SECOND)]
        model = AsyncMock(return_value=ANSWER)
        with patch.object(llm, 'chat_structured', model), patch('app.agent.local_ocr') as ocr:
            response = request(files, input_types=['text', 'image'])
        self.assertEqual(response.status_code, 200, response.text)
        ocr.assert_not_called()
        self.assertEqual(model.call_args.kwargs['images'], [dict(mime='image/png', content_base64=f['content_base64']) for f in files])
        self.assertEqual([f['image_index'] for f in payload(model)['files']], [0, 1])
        self.assertNotIn('content_base64', model.call_args.kwargs['prompt_tail'])

    def test_image_disabled_or_unspecified_sends_only_local_ocr(self):
        for options in ({}, {'input_types': ['text']}):
            model = AsyncMock(return_value=ANSWER)
            with self.subTest(options=options), patch.object(llm, 'chat_structured', model), \
                    patch('app.agent.local_ocr', return_value='北京南站 G1') as ocr:
                response = request([attachment('a.png', PNG)], **options)
                self.assertEqual(response.status_code, 200, response.text)
                ocr.assert_called_once_with(PNG, 'image/png')
                self.assertNotIn('images', model.call_args.kwargs)
                self.assertEqual(payload(model)['files'], [{'name': 'a.png', 'text': '北京南站 G1', 'source': 'local_ocr'}])
                self.assertNotIn(base64.b64encode(PNG).decode(), model.call_args.kwargs['prompt_tail'])
                self.assertEqual(response.json()['input_processing'][0]['mode'], 'local_ocr')

    def test_explicit_unsupported_falls_back_once_and_preserves_document(self):
        model = AsyncMock(side_effect=[llm.ImageInputUnsupported('not supported'), ANSWER])
        with patch.object(llm, 'chat_structured', model), patch('app.agent.local_ocr', return_value='图中 G2') as ocr:
            response = request([attachment('notes.tex', b'TeX G1'), attachment('a.png', PNG)], input_types=['text', 'image'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.await_count, 2)
        ocr.assert_called_once()
        self.assertEqual(payload(model)['files'], [{'name': 'notes.tex', 'text': 'TeX G1'},
                                                    {'name': 'a.png', 'text': '图中 G2', 'source': 'local_ocr'}])
        self.assertNotIn('images', model.call_args.kwargs)
        self.assertEqual([x['mode'] for x in response.json()['input_processing']], ['document_text', 'local_ocr'])

    def test_same_name_images_do_not_overwrite_each_other_during_fallback(self):
        model = AsyncMock(side_effect=[llm.ImageInputUnsupported('not supported'), ANSWER])
        with patch.object(llm, 'chat_structured', model), patch('app.agent.local_ocr', side_effect=['第一张 G1', '第二张 G2']):
            response = request([attachment('photo.png', PNG), attachment('photo.png', PNG_SECOND)], input_types=['text', 'image'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([x['text'] for x in payload(model)['files']], ['第一张 G1', '第二张 G2'])

    def test_auth_and_quota_failures_do_not_trigger_ocr(self):
        class UpstreamError(Exception):
            def __init__(self, status):
                self.status_code = status
                super().__init__('model does not support image input')
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                self.assertFalse(llm._image_input_unsupported(UpstreamError(status)))
                model = AsyncMock(side_effect=llm.LLMUnavailable('private-authentication-or-quota-detail'))
                with patch.object(llm, 'chat_structured', model), patch('app.agent.local_ocr') as ocr:
                    response = request([attachment('a.png', PNG)], input_types=['text', 'image'])
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('private-', response.text)
                self.assertEqual(model.await_count, 1)
                ocr.assert_not_called()

    def test_missing_ocr_is_explicit_and_never_uploads_image(self):
        for enabled in (False, True):
            model = AsyncMock(side_effect=llm.ImageInputUnsupported('unsupported'))
            with self.subTest(enabled=enabled), patch.object(llm, 'chat_structured', model), \
                    patch('app.agent.local_ocr', side_effect=LocalOCRUnavailable('本地 OCR 不可用')):
                response = request([attachment('a.png', PNG)], input_types=['text', 'image'] if enabled else ['text'])
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()['needs_local_ocr'])
                self.assertEqual(response.json()['files'], [])
                self.assertEqual(model.await_count, int(enabled))

    def test_fallback_model_failure_never_tries_a_third_model_call(self):
        model = AsyncMock(side_effect=[llm.ImageInputUnsupported('unsupported'), llm.LLMUnavailable('private-failure')])
        with patch.object(llm, 'chat_structured', model), patch('app.agent.local_ocr', return_value='G1'):
            response = request([attachment('a.png', PNG)], input_types=['text', 'image'])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(model.await_count, 2)
        self.assertNotIn('private-', response.text)

    def test_malformed_documents_images_and_base64_do_not_reach_model(self):
        invalid = [attachment('x.doc', b'not a word file'), attachment('x.docx', b'not a zip'),
                   attachment('x.rtf', br'{\rtf1 unclosed'), attachment('x.tex', b'a\x00b'),
                   attachment('x.json', b'{'), attachment('../x.txt', b'a'),
                   attachment('x.png', b'not an image'), attachment('x.png', PNG, mime='image/jpeg'),
                   dict(name='x.txt', content_base64='not!base64')]
        with patch.object(llm, 'chat_structured', AsyncMock()) as model, patch('app.agent.local_ocr') as ocr:
            for file in invalid:
                with self.subTest(name=file['name']):
                    self.assertEqual(request([file], input_types=['text', 'image']).status_code, 422)
            model.assert_not_called()
            ocr.assert_not_called()

    def test_per_file_and_combined_text_budgets(self):
        invalid = [[attachment('x.tex', b'a' * 60001)],
                   [attachment('x.txt', b'a' * 50000), attachment('y.txt', b'b' * 50000)],
                   [dict(name='x.txt', content_base64='A' * 11184813)],
                   [attachment('x.doc', b'x' * (MAX_DOCUMENT_BYTES + 1))]]
        with patch.object(llm, 'chat_structured', AsyncMock()) as model:
            for files in invalid:
                with self.subTest(lengths=[len(f['content_base64']) for f in files]):
                    self.assertEqual(request(files).status_code, 422)
            model.assert_not_called()

    def test_text_is_required_and_file_count_is_bounded(self):
        with patch.object(llm, 'chat_structured', AsyncMock()) as model:
            for types in ([], ['image'], ['video'], ['text', 'image', 'text']):
                self.assertEqual(request([attachment('a.txt', b'G1')], input_types=types).status_code, 422)
            self.assertEqual(request([attachment('x.txt', b'G1')] * 3).status_code, 422)
            model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
