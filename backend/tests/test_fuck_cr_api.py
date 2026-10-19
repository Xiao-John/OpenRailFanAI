"""Button preparation contract: readiness never invents production behavior."""
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.fuck_cr import router
from app.config import Settings

app = FastAPI()
app.include_router(router, prefix='/api')
client = TestClient(app)


class ButtonContractTest(unittest.TestCase):
    def configuration(self, mode):
        return patch('app.config.get_settings', return_value=Settings(_env_file=None, fuck_cr_mode=mode))

    def test_legacy_and_shadow_are_not_new_rules(self):
        for mode in ('legacy', 'shadow'):
            with self.subTest(mode=mode), patch.dict(os.environ, APP_VARIANT='main'), self.configuration(mode):
                result = client.get('/api/fuck-cr/status')
                self.assertEqual(result.status_code, 200)
                body = result.json()
                self.assertEqual(body['mode'], mode)
                self.assertEqual(body['button_label'], '一键FUCK_CR')
                self.assertEqual(body['schema_version'], 1)
                self.assertFalse(body['enabled'])
                self.assertFalse(body['new_rules_ready'])
                self.assertFalse(body['can_enable'])

    def test_enable_is_explicitly_refused_and_readback_is_unchanged(self):
        for mode in ('legacy', 'shadow'):
            with self.subTest(mode=mode), patch.dict(os.environ, APP_VARIANT='main'), self.configuration(mode):
                before = client.get('/api/fuck-cr/status').json()
                response = client.post('/api/fuck-cr/activation', json={'enabled': True})
                self.assertEqual(response.status_code, 409)
                detail = response.json()['detail']
                self.assertEqual(detail['code'], 'fuck_cr_not_ready')
                self.assertEqual(detail['status'], before)
                self.assertEqual(client.get('/api/fuck-cr/status').json(), before)

    def test_disable_is_idempotent_and_does_not_disable_shadow(self):
        with patch.dict(os.environ, APP_VARIANT='main'), self.configuration('shadow'):
            for _ in range(2):
                response = client.post('/api/fuck-cr/activation', json={'enabled': False})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()['changed'])
                self.assertFalse(response.json()['requested_enabled'])
                self.assertEqual(response.json()['status']['mode'], 'shadow')

    def test_malformed_requests_cannot_select_modes_or_coerce_truth(self):
        for body in ({}, {'enabled': 'true'}, {'enabled': 1}, {'enabled': None},
                     {'enabled': True, 'mode': 'shadow'}, {'enabled': False, 'api_key': 'private'}):
            with self.subTest(body=body):
                self.assertEqual(client.post('/api/fuck-cr/activation', json=body).status_code, 422)

    def test_main_mounts_routes_without_changing_chat_paths(self):
        from app.main import app as main
        paths = {route.path for route in main.routes}
        self.assertTrue({'/api/fuck-cr/status', '/api/fuck-cr/activation',
                         '/api/chat', '/api/chat/stream'}.issubset(paths))


if __name__ == '__main__':
    unittest.main()
