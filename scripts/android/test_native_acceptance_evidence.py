"""Regression checks for stale evidence, preserved results and basis validation."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from native_capture_basis import validate_basis
from native_copy_evidence import compare_copy
import yaml

spec = importlib.util.spec_from_file_location("acceptance_runner", Path(__file__).with_name("run-native-acceptance.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class EvidenceTests(unittest.TestCase):
    def copy_case(self, actual_text="hello", visible=None, full=None, clips=None, overlays=None):
        design = [{"selector": ".copy-label", "mode": "text", "expected": "hello"}]
        binding = [{**design[0], "native_tag": "copy-label-node", "native_property": "text",
                    "required_ancestor": "copy-card"}]
        visible = visible or [10, 10, 40, 22]
        full = full or [10, 10, 40, 22]
        node = {"match_count": 1, "node_id": 11, "visible_rect_px": visible,
                "unclipped_rect_px": full, "unclipped_unit": "local_dp_at_density_1",
                "ancestor_tags": ["copy-card", "scroll-viewport"],
                "text_children_count": 0, "text": [actual_text]}
        return compare_copy(design, binding, {"copy-label-node": node}, True,
                            clips if clips is not None else {"capture_root": [0, 0, 100, 100],
                                                             "scroll-viewport": [0, 0, 100, 100]},
                            overlays or {})

    def test_copy_binding_requires_complete_unique_selector_coverage(self):
        design = [{"selector": ".a", "mode": "text", "expected": "A"},
                  {"selector": ".b", "mode": "text", "expected": "B"}]
        one_binding = [{**design[0], "native_tag": "a", "native_property": "text"}]
        duplicate_design = [design[0], design[0]]
        nodes = {}
        self.assertTrue(compare_copy(design, one_binding, nodes, True)["unmeasured"])
        self.assertTrue(compare_copy(duplicate_design, duplicate_design, nodes, True)["unmeasured"])

    def test_copy_leaf_exact_text_passes_without_glyph_inference(self):
        evidence = self.copy_case()
        self.assertEqual(len(evidence["checked"]), 1)
        self.assertTrue(evidence["checked"][0]["exact"])
        self.assertTrue(evidence["checked"][0]["visibility"]["complete"])
        self.assertEqual(evidence["failed"], [])
        self.assertEqual(evidence["unmeasured"], [])
        self.assertIn("no typography or glyph inference", evidence["scope"])

    def test_copy_leaf_partial_line_box_clipping_is_unmeasured_not_glyph_failure(self):
        evidence = self.copy_case(visible=[10, 10, 40, 18], full=[10, 10, 40, 22],
                                  clips={"capture_root": [0, 0, 100, 100],
                                         "scroll-viewport": [0, 0, 100, 18]})
        self.assertTrue(evidence["checked"][0]["exact"])
        self.assertEqual(evidence["failed"], [])
        self.assertEqual(evidence["failed_visibility"], [])
        self.assertTrue(evidence["unmeasured"])
        self.assertFalse(evidence["checked"][0]["visibility"]["complete"])

    def test_copy_leaf_wrong_text_is_failed(self):
        evidence = self.copy_case(actual_text="wrong")
        self.assertEqual(len(evidence["checked"]), 1)
        self.assertFalse(evidence["checked"][0]["exact"])
        self.assertEqual(len(evidence["failed"]), 1)

    def test_copy_leaf_without_capture_root_clip_is_unmeasured(self):
        evidence = self.copy_case(clips={"scroll-viewport": [0, 0, 100, 100]})
        self.assertEqual(len(evidence["checked"]), 1)
        self.assertEqual(evidence["failed"], [])
        self.assertTrue(evidence["unmeasured"])
        self.assertFalse(evidence["checked"][0]["visibility"]["complete"])

    def test_copy_leaf_overlay_overlap_is_unmeasured_not_confirmed_glyph_cut(self):
        evidence = self.copy_case(overlays={"dialog": [30, 15, 50, 24]})
        self.assertTrue(evidence["checked"][0]["exact"])
        self.assertEqual(evidence["failed"], [])
        self.assertEqual(evidence["failed_visibility"], [])
        self.assertTrue(evidence["unmeasured"])
        self.assertIn("no typography or glyph inference", evidence["scope"])

    def continuous_basis(self):
        return {"schema_version": 1, "coordinate_origin": "capture_root", "phase": "before_interaction",
                "root_rect_px": [0, 0, 390, 844], "content_rect_px": [0, 24, 390, 772],
                "system_insets_platform_px": [0, 66, 0, 132], "system_insets_applied_px": [0, 24, 0, 48],
                "platform_density": 2.75, "local_density": 1, "font_scale": 1,
                "content_tag": "history-content", "scroll_mode": "continuous",
                "scroll": {"scroll_offset_px": 0, "scroll_max_px": 0,
                           "viewport_start_px": 24, "viewport_end_px": 740}}

    def test_continuous_column_accepts_offset_and_viewport_without_list_indices(self):
        basis = self.continuous_basis()
        measurements = {"history-content": {"x": 0, "y": 24, "width": 390, "height": 772}}
        self.assertEqual(validate_basis(basis, measurements), [])
        scroll = {**basis["scroll"], "scroll_offset_px": 37, "scroll_max_px": 100}
        self.assertEqual(validate_basis({**basis, "scroll": scroll}, measurements), [])
        self.assertTrue(validate_basis({**basis, "scroll_mode": "list"}, measurements))

    def test_continuous_column_rejects_invalid_ranges_and_fabricated_indices(self):
        basis = self.continuous_basis()
        measurements = {"history-content": {"x": 0, "y": 24, "width": 390, "height": 772}}
        for changes in ({"scroll_offset_px": -1}, {"scroll_offset_px": 1}, {"scroll_max_px": -1},
                        {"scroll_offset_px": True}, {"scroll_offset_px": 0.5}, {"scroll_max_px": None},
                        {"viewport_start_px": 740}, {"viewport_start_px": -1}, {"viewport_end_px": 845},
                        {"visible_item_indices": [0]}, {"first_visible_item_index": 0}):
            with self.subTest(changes=changes):
                self.assertTrue(validate_basis({**basis, "scroll": {**basis["scroll"], **changes}}, measurements))
        scroll = dict(basis["scroll"])
        del scroll["viewport_end_px"]
        self.assertTrue(validate_basis({**basis, "scroll": scroll}, measurements))

    def test_scroll_mode_requires_an_explicit_supported_container_type(self):
        basis = self.continuous_basis()
        measurements = {"history-content": {"x": 0, "y": 24, "width": 390, "height": 772}}
        self.assertTrue(validate_basis({**basis, "scroll_mode": "unknown"}, measurements))
        self.assertTrue(validate_basis({**basis, "scroll_mode": "not_scrollable"}, measurements))
        self.assertEqual(validate_basis({**basis, "scroll_mode": "not_scrollable", "scroll": None}, measurements), [])

    def test_archive_excludes_stale_logs_and_survives_focused_test_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "results"
            source.mkdir()
            old = source / "old.log"
            old.write_text("old capture")
            os.utime(old, ns=(1, 1))
            started = time.time_ns()
            current = source / "TEST.xml"
            current.write_text('<testsuite><testcase classname="A" name="capture"/><testcase classname="B" name="live"><skipped/></testcase></testsuite>')
            copied = runner.archive_files(source, root / "archive", started)
            self.assertEqual([path.name for path in copied], ["TEST.xml"])
            current.write_text('<testsuite><testcase classname="A" name="diagnostic"/></testsuite>')
            summary = runner.test_summary(copied)
            self.assertEqual((summary["total"], summary["passed"], summary["skipped"]), (2, 1, 1))
            self.assertEqual(summary["cases"][0]["name"], "capture")

    def test_ambiguous_test_results_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a.xml", Path(directory) / "b.xml"
            for path in (first, second):
                path.write_text('<testsuite><testcase classname="A" name="capture"/></testsuite>')
            with self.assertRaises(ValueError):
                runner.test_summary([first, second])

    def _run_mocked_formal_acceptance(self, capture_case=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "android/app/build/outputs/androidTest-results/connected/debug"
            captures = root / "android/app/build/native-captures"
            acceptance = root / "acceptance"
            calls = []
            generated = [f"acceptance/ACC-CT8-{i:02d}__state__20261001.png" for i in range(1, 10)]

            def command(arguments, **kwargs):
                calls.append(arguments)
                code = 0
                if arguments[0] == "bash":
                    token_arg = next(arg for arg in arguments if arg.startswith(
                        "-Pandroid.testInstrumentationRunnerArguments.acceptanceRunToken="))
                    token = token_arg.split("=", 1)[1]
                    results.mkdir(parents=True)
                    cases = '<testcase classname="org.openrailfanai.app.NativeAcceptanceCaptureTest" name="captureNineFixedMainStatesAt390By844Px"/>' if capture_case else '<testcase classname="org.openrailfanai.app.NativeAcceptanceCaptureTest" name="diagnostic"/>'
                    (results / "TEST.xml").write_text(f"<testsuite>{cases}</testsuite>")
                    log_line = f"NATIVE_RUN|{token}\n"
                    (results / runner.CAPTURE_LOG).write_text(log_line)
                elif "extract-native-captures.py" in arguments[1]:
                    self.assertIn("/test-results/", arguments[arguments.index("--log") + 1])
                    self.assertIn("--expected-run-token", arguments)
                    token = arguments[arguments.index("--expected-run-token") + 1]
                    log_text = Path(arguments[arguments.index("--log") + 1]).read_text()
                    if f"NATIVE_RUN|{token}" not in log_text:
                        code = 1
                    else:
                        captures.mkdir(parents=True)
                        (captures / "verification.json").write_text(json.dumps({
                            "capture_run_token": token, "run_token_verified": True}))
                        for state in ("train_schedule", "emu_routing", "query_loading", "batch_partial",
                                      "routing_empty", "connection_error", "reading_followup", "query_details", "history"):
                            (captures / f"{state}.png").write_bytes(b"fixture image")
                elif "build-native-acceptance.py" in arguments[1]:
                    if "--failure" in arguments:
                        entries = [{"acc_id": f"ACC-CT8-{i:02d}", "status": "unmeasured", "file": None,
                                    "generated_at": runner.datetime.now(runner.ZoneInfo("Asia/Shanghai")).isoformat(),
                                    "script_version": "2.8.0",
                                    "capture_run_token": arguments[arguments.index("--expected-run-token") + 1]}
                                   for i in range(1, 10)]
                        acceptance.mkdir(parents=True, exist_ok=True)
                        (acceptance / "_failed").mkdir(exist_ok=True)
                        for name in ("index.yaml", "_failed/index.yaml"):
                            (acceptance / name).write_text(yaml.safe_dump(entries))
                    else:
                        code = 1
                        token = arguments[arguments.index("--expected-run-token") + 1]
                        (acceptance / "_failed").mkdir(parents=True, exist_ok=True)
                        entries = []
                        for i, path in enumerate(generated, 1):
                            (root / path).parent.mkdir(parents=True, exist_ok=True)
                            (root / path).write_bytes(b"comparison fixture")
                            state = ["train_schedule", "emu_routing", "query_loading", "batch_partial", "routing_empty",
                                     "connection_error", "reading_followup", "query_details", "history"][i - 1]
                            entries.append({"acc_id": f"ACC-CT8-{i:02d}", "status": "failed" if i == 5 else "passed",
                                "module": state, "file": path,
                                "impl_ref": f"android/app/build/native-captures/{state}.png",
                                "generated_at": runner.datetime.now(runner.ZoneInfo("Asia/Shanghai")).isoformat(),
                                "script_version": "2.8.0", "capture_run_token": token})
                        (acceptance / "index.yaml").write_text(yaml.safe_dump(entries, allow_unicode=True))
                        (acceptance / "_failed/index.yaml").write_text(yaml.safe_dump([entries[4]], allow_unicode=True))
                        kwargs["stdout"].write(json.dumps({"generated": generated, "status": {"failed": 1}}) + "\n")
                return runner.subprocess.CompletedProcess(arguments, code)

            with patch.object(runner.subprocess, "run", side_effect=command):
                self.assertEqual(runner.run(root), 1)
            manifest_path = next((acceptance / "runs").glob("*/manifest.yaml"))
            manifest = yaml.safe_load(manifest_path.read_text())
            if not capture_case:
                self.assertEqual(manifest["status"], "capture_failed")
                self.assertFalse(any("extract-native-captures.py" in call[1] for call in calls))
                return
            self.assertEqual(manifest["status"], "not_accepted")
            self.assertEqual(manifest["tests"]["cases"][0]["class"], "org.openrailfanai.app.NativeAcceptanceCaptureTest")
            self.assertEqual(manifest["tests"]["cases"][0]["name"], "captureNineFixedMainStatesAt390By844Px")
            self.assertEqual(manifest["capture_run_token"], manifest["run_id"])
            index = yaml.safe_load((acceptance / "index.yaml").read_text())
            self.assertEqual(len(index), 9)
            self.assertEqual({entry["acc_id"] for entry in index}, {f"ACC-CT8-{i:02d}" for i in range(1, 10)})
            self.assertTrue(all(entry["run_id"] == manifest["run_id"] for entry in index))
            self.assertTrue(all(entry["capture_run_token"] == manifest["run_id"] for entry in index))
            for entry in index:
                self.assertTrue((root / entry["archived_file"]).exists())
                self.assertTrue((root / entry["archived_impl_ref"]).exists())
            gradle_call = next(call for call in calls if call[0] == "bash")
            self.assertIn(f"-Pandroid.testInstrumentationRunnerArguments.acceptanceRunToken={manifest['run_id']}", gradle_call)
            extract_call = next(call for call in calls if "extract-native-captures.py" in call[1])
            self.assertEqual(extract_call[extract_call.index("--expected-run-token") + 1], manifest["run_id"])
            compare_call = next(call for call in calls if "build-native-acceptance.py" in call[1] and "--failure" not in call)
            self.assertEqual(compare_call[compare_call.index("--expected-run-token") + 1], manifest["run_id"])
            (results / "TEST.xml").write_text("overwritten by diagnostic")
            self.assertEqual(runner.test_summary(list((manifest_path.parent / "test-results").glob("*.xml")))["total"], 1)

    def test_run_archives_nine_failed_visual_entries_without_claiming_pass(self):
        self._run_mocked_formal_acceptance()

    def test_run_rejects_missing_or_wrong_capture_token(self):
        for token_mode in ("missing", "wrong"):
            with self.subTest(token_mode=token_mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                results = root / "android/app/build/outputs/androidTest-results/connected/debug"
                acceptance = root / "acceptance"
                calls = []

                def command(arguments, **kwargs):
                    calls.append(arguments)
                    if arguments[0] == "bash":
                        token_arg = next(arg for arg in arguments if arg.startswith(
                            "-Pandroid.testInstrumentationRunnerArguments.acceptanceRunToken="))
                        token = token_arg.split("=", 1)[1]
                        results.mkdir(parents=True)
                        (results / "TEST.xml").write_text('<testsuite><testcase classname="org.openrailfanai.app.NativeAcceptanceCaptureTest" name="captureNineFixedMainStatesAt390By844Px"/></testsuite>')
                        emitted = "wrong-token" if token_mode == "wrong" else ""
                        (results / runner.CAPTURE_LOG).write_text(f"NATIVE_RUN|{emitted}\n" if emitted else "")
                    elif "extract-native-captures.py" in arguments[1]:
                        expected = arguments[arguments.index("--expected-run-token") + 1]
                        log = Path(arguments[arguments.index("--log") + 1]).read_text()
                        self.assertNotIn(f"NATIVE_RUN|{expected}", log)
                        return runner.subprocess.CompletedProcess(arguments, 1)
                    elif "build-native-acceptance.py" in arguments[1]:
                        entries = [{"acc_id": f"ACC-CT8-{i:02d}", "status": "unmeasured", "file": None,
                                    "generated_at": runner.datetime.now(runner.ZoneInfo("Asia/Shanghai")).isoformat(),
                                    "script_version": "2.8.0",
                                    "capture_run_token": arguments[arguments.index("--expected-run-token") + 1]}
                                   for i in range(1, 10)]
                        acceptance.mkdir(parents=True, exist_ok=True)
                        (acceptance / "_failed").mkdir(exist_ok=True)
                        for name in ("index.yaml", "_failed/index.yaml"):
                            (acceptance / name).write_text(yaml.safe_dump(entries))
                    return runner.subprocess.CompletedProcess(arguments, 0)

                with patch.object(runner.subprocess, "run", side_effect=command):
                    self.assertEqual(runner.run(root), 1)
                manifest = yaml.safe_load(next((root / "acceptance/runs").glob("*/manifest.yaml")).read_text())
                self.assertEqual(manifest["status"], "capture_failed")
                self.assertFalse(any("build-native-acceptance.py" in call[1] and "--failure" not in call for call in calls))

    def test_run_rejects_xml_without_the_unique_capture_case(self):
        self._run_mocked_formal_acceptance(capture_case=False)

    def test_successful_command_cannot_reuse_old_capture_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "android/app/build/outputs/androidTest-results/connected/debug"
            acceptance = root / "acceptance"
            results.mkdir(parents=True)
            acceptance.mkdir(parents=True)
            old_index = [{"acc_id": f"ACC-CT8-{i:02d}", "status": "passed",
                          "file": f"acceptance/old-{i}.png", "script_version": "2.8.0",
                          "capture_run_token": "old-run", "generated_at": "2000-01-01T00:00:00+08:00"}
                         for i in range(1, 10)]
            (acceptance / "index.yaml").write_text(yaml.safe_dump(old_index))
            for name in ("TEST.xml", runner.CAPTURE_LOG):
                path = results / name
                path.write_text("old results")
                os.utime(path, ns=(1, 1))
            calls = []

            def command(arguments, **kwargs):
                calls.append(arguments)
                return runner.subprocess.CompletedProcess(arguments, 0)

            with patch.object(runner.subprocess, "run", side_effect=command):
                self.assertEqual(runner.run(root), 1)
            self.assertFalse(any("extract-native-captures.py" in str(call) for call in calls))
            manifest = yaml.safe_load(next((root / "acceptance/runs").glob("*/manifest.yaml")).read_text())
            self.assertEqual(manifest["status"], "capture_failed")
            kept_index = yaml.safe_load((acceptance / "index.yaml").read_text())
            self.assertEqual(len(kept_index), 9)
            self.assertTrue(all(entry.get("capture_run_token") == "old-run" for entry in kept_index))
            self.assertTrue(all("run_id" not in entry for entry in kept_index))

    def test_stamp_indices_skips_naive_and_invalid_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            acceptance = root / "acceptance"
            (acceptance / "_failed").mkdir(parents=True)
            entries = [
                {"acc_id": "ACC-CT8-01", "generated_at": "2026-10-01T11:00:00"},
                {"acc_id": "ACC-CT8-02", "generated_at": "not-a-timestamp"},
            ]
            (acceptance / "index.yaml").write_text(yaml.safe_dump(entries))
            (acceptance / "_failed/index.yaml").write_text("[]\n")
            files = runner.stamp_indices(root, "new-run", "acceptance/runs/new-run/manifest.yaml",
                                         runner.datetime.now(runner.ZoneInfo("Asia/Shanghai")))
            self.assertIn(acceptance / "index.yaml", files)
            kept = yaml.safe_load((acceptance / "index.yaml").read_text())
            self.assertTrue(all("run_id" not in entry for entry in kept))

    def test_missing_or_inconsistent_basis_is_not_accepted(self):
        self.assertTrue(validate_basis(None, {}))
        basis = {"schema_version": 1, "coordinate_origin": "capture_root", "phase": "before_interaction",
                 "root_rect_px": [0, 0, 390, 844], "content_rect_px": [0, 24, 390, 772],
                 "system_insets_platform_px": [0, 66, 0, 132], "system_insets_applied_px": [0, 24, 0, 48],
                 "platform_density": 2.75, "local_density": 1.0, "font_scale": 1.0,
                 "content_tag": "content", "scroll_mode": "list",
                 "scroll": {"first_visible_item_index": 0, "first_visible_item_offset_px": 0,
                            "viewport_start_px": 0, "viewport_end_px": 608, "visible_item_indices": [0, 1]}}
        measurements = {"content": {"x": 0, "y": 24, "width": 390, "height": 772}}
        self.assertEqual(validate_basis(basis, measurements), [])
        self.assertTrue(validate_basis({**basis, "system_insets_applied_px": [0, 66, 0, 132]}, measurements))
        self.assertTrue(validate_basis({**basis, "scroll": None}, measurements))
        self.assertTrue(validate_basis({**basis, "phase": "after_interaction"}, measurements))
        self.assertTrue(validate_basis(basis, {}))


if __name__ == "__main__":
    unittest.main()
