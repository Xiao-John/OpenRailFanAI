"""The relaxed visual gate must still require authentic review and hard evidence."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from native_harmony_acceptance import CHECKS, evaluate_harmony


class HarmonyAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.image = Path(self.directory.name) / "fixture.png"
        self.image.write_bytes(b"fixed screenshot fixture")
        self.policy = {"schema_version": 1, "id": "HARMONY-TEST", "mode": "harmony_first"}
        self.review = {"schema_version": 1, "policy_id": "HARMONY-TEST", "reviewer": "H",
                       "reviewed_at": "2026-10-03T10:00:00+08:00", "states": {"history": {
                           "image_sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
                           "checks": {key: True for key in CHECKS}, "notes": ["标题、列表和操作层级清晰。"]}}}

    def result(self, review=None, failures=None, missing=None):
        return evaluate_harmony("history", self.image, self.policy,
                                self.review if review is None else review,
                                failures or [], missing or [])

    def test_complete_review_passes(self):
        self.assertEqual(self.result()["status"], "passed")

    def test_hard_failure_still_fails(self):
        self.assertEqual(self.result(failures=["触控范围不足"])["status"], "failed")

    def test_missing_operation_still_unmeasured(self):
        self.assertEqual(self.result(missing=["缺少复制操作证据"])["status"], "unmeasured")

    def test_changed_screenshot_does_not_pass(self):
        self.image.write_bytes(b"different screenshot")
        self.assertEqual(self.result()["status"], "unmeasured")

    def test_malformed_review_never_passes(self):
        for review in [[], {}, {"states": []}, {"states": {"history": []}},
                       {"states": {"history": {"checks": []}}}]:
            with self.subTest(review=review):
                self.assertEqual(self.result(review)["status"], "unmeasured")
        self.assertEqual(evaluate_harmony("history", self.image, None, None, [], [])["status"], "unmeasured")

    def test_bool_checks_and_timezone_required(self):
        for key, value in [("checks", {name: 1 for name in CHECKS}), ("notes", [""])]:
            review = copy.deepcopy(self.review)
            review["states"]["history"][key] = value
            self.assertEqual(self.result(review)["status"], "unmeasured")
        review = copy.deepcopy(self.review)
        review["reviewed_at"] = "2026-10-03T10:00:00"
        self.assertEqual(self.result(review)["status"], "unmeasured")

    def test_visual_failure_is_not_ignored(self):
        self.review["states"]["history"]["checks"]["no_control_overlap"] = False
        self.assertEqual(self.result()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
