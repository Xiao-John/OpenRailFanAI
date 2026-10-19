"""FUCK_CR mode boundary and syntax shadow; no live railway/model calls."""
import json
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from app.config import Settings
from app.fuck_cr import Mode, selected_mode
from app.fuck_cr.shadow import candidates
from app import metrics
from app.pipeline import service_dispatch


class FuckCR(unittest.TestCase):
    def settings(self, mode='legacy'):
        return Settings(_env_file=None, fuck_cr_mode=mode)

    def test_default_and_environment_switch(self):
        with patch.dict(os.environ, {'FUCK_CR_MODE':'shadow'}):
            self.assertEqual(Settings(_env_file=None).fuck_cr_mode,'shadow')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings(_env_file=None).fuck_cr_mode,'legacy')

    def test_reserved_new_mode_and_typos_fail_explicitly(self):
        for mode in ['reform_2026','2026','automatic','SHADOW','']:
            with self.assertRaises(ValidationError):self.settings(mode)
        with self.assertRaisesRegex(ValidationError,'not ready'):
            self.settings('reform_2026')

    def test_lm_keeps_legacy(self):
        with patch.dict(os.environ,APP_VARIANT='lm'):
            self.assertIs(selected_mode(self.settings('shadow')),Mode.LEGACY)

    def test_intact_candidates_do_not_claim_passenger_admission(self):
        known,unknown=candidates('G1 X1 V6001 DH1 DJ55001 71001次 AB123次 12306次',['G1'])
        self.assertEqual(known,['G1','X1','V6001','DH1','DJ55001','71001'])
        self.assertEqual(unknown,['AB123'])
        self.assertNotIn('123',known)
        self.assertNotIn('12306',known)

    def test_shadow_cannot_change_codes_or_selected_service(self):
        texts=['G1明天时刻表','X1明天时刻表','V6001次余票','DH1次票价',
               'G1、G2明天北京南到上海虹桥票价','CR400AF今天交路','2026年10月18日G1时刻表']
        with patch.dict(os.environ,APP_VARIANT='main'):
            with patch('app.config.get_settings',return_value=self.settings('legacy')):
                baseline=[(service_dispatch.train_codes(t),service_dispatch.decide(t)) for t in texts]
            with patch('app.config.get_settings',return_value=self.settings('shadow')):
                preview=[(service_dispatch.train_codes(t),service_dispatch.decide(t)) for t in texts]
            self.assertEqual(baseline,preview)

    def test_metrics_are_counts_not_inputs_and_legacy_is_silent(self):
        with metrics._LOCK:metrics._RULE_COMPARISONS.clear()
        with patch.dict(os.environ,APP_VARIANT='main'):
            with patch('app.config.get_settings',return_value=self.settings('legacy')):
                service_dispatch.train_codes('G1 X1 secret-private-input')
            self.assertEqual(metrics.snapshot()['rule_comparisons'],{})
            with patch('app.config.get_settings',return_value=self.settings('shadow')):
                service_dispatch.train_codes('G1')
                service_dispatch.train_codes('X1 secret-private-input')
                service_dispatch.train_codes('CR400A车型')
            counters=metrics.snapshot()['rule_comparisons']
            self.assertEqual(counters,{'fuck_cr.syntax':{'calls':3,'same':2,'different':1}})
            serialized=json.dumps(counters)
            for secret in ['G1','X1','secret-private-input','CR400A']:
                self.assertNotIn(secret,serialized)

    def test_settings_mode_partitions_existing_configuration_scope(self):
        from app.query_cache import query_scope
        with patch('app.config.get_settings',return_value=self.settings('legacy')):
            before=query_scope()
        with patch('app.config.get_settings',return_value=self.settings('shadow')):
            after=query_scope()
        self.assertNotEqual(before,after)


if __name__=='__main__':unittest.main()
