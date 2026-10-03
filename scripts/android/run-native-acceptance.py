#!/usr/bin/env python3
"""Run native acceptance with immutable evidence for this invocation."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_LOG = "logcat-org.openrailfanai.app.NativeAcceptanceCaptureTest-captureNineFixedMainStatesAt390By844Px.txt"


def archive_files(source: Path, destination: Path, started_ns: int | None = None) -> list[Path]:
    copied: list[Path] = []
    if not source.exists():
        return copied
    for path in sorted(source.rglob("*")):
        if path.is_file() and (started_ns is None or path.stat().st_mtime_ns >= started_ns):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append(target)
    return copied


def test_summary(paths: list[Path]) -> dict:
    cases: dict[tuple[str, str], dict] = {}
    for path in paths:
        tree = ET.parse(path)
        for case in tree.iter("testcase"):
            key = (case.get("classname", ""), case.get("name", ""))
            if key in cases:
                raise ValueError(f"Ambiguous repeated test result: {key}")
            cases[key] = {"class": key[0], "name": key[1],
                          "status": "failed" if case.find("failure") is not None or case.find("error") is not None
                          else "skipped" if case.find("skipped") is not None else "passed"}
    return {"total": len(cases), **{status: sum(case["status"] == status for case in cases.values())
            for status in ("passed", "failed", "skipped")}, "cases": list(cases.values())}


def stamp_indices(root: Path, run_id: str, manifest_ref: str, generated_after: datetime) -> list[Path]:
    files: list[Path] = []
    for relative in ("acceptance/index.yaml", "acceptance/_failed/index.yaml"):
        path = root / relative
        if not path.exists():
            continue
        entries = yaml.safe_load(path.read_text()) or []
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            raise ValueError(f"Invalid acceptance index structure: {relative}")
        for entry in entries:
            if str(entry.get("acc_id", "")).startswith("ACC-CT8-"):
                try:
                    generated_at = datetime.fromisoformat(entry.get("generated_at", ""))
                except (TypeError, ValueError):
                    continue
                if generated_at.tzinfo is None:
                    continue
                if generated_at < generated_after.replace(microsecond=0):
                    continue
                entry["run_id"] = run_id
                entry["run_manifest"] = manifest_ref
                if entry.get("file"):
                    entry["archived_file"] = str(Path(manifest_ref).parent / "acceptance" / Path(entry["file"]).name)
                    if entry.get("impl_ref", "").startswith("android/app/build/native-captures/"):
                        entry["archived_impl_ref"] = str(Path(manifest_ref).parent / "captures" / Path(entry["impl_ref"]).name)
                    files.append(root / entry["file"])
        path.write_text(yaml.safe_dump(entries, allow_unicode=True, sort_keys=False))
        files.append(path)
    return list(dict.fromkeys(files))


def run(root: Path = ROOT) -> int:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    run_id = f"{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    directory = root / "acceptance/runs" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    manifest_path = directory / "manifest.yaml"
    python = str(root / "backend/.venv/bin/python")
    results = root / "android/app/build/outputs/androidTest-results/connected/debug"
    manifest: dict = {"run_id": run_id, "started_at": now.isoformat(timespec="seconds"),
                      "script_version": "2.8.0", "capture_run_token": run_id,
                      "status": "running", "steps": [], "tests": None}
    final_code = 1
    started_ns = time.time_ns()

    def save() -> None:
        manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))

    def command(label: str, arguments: list[str]) -> int:
        log = directory / f"{label}.log"
        with log.open("w") as stream:
            result = subprocess.run(arguments, cwd=root, stdout=stream, stderr=subprocess.STDOUT)
        manifest["steps"].append({"id": label, "command": arguments, "exit_code": result.returncode,
                                  "log": str(log.relative_to(root))})
        save()
        return result.returncode

    def failure(message: str) -> None:
        manifest["failure"] = message
        command("record-failure", [python, "scripts/android/build-native-acceptance.py", "--failure", message,
                                   "--expected-run-token", run_id])

    save()
    try:
        build_code = command("instrumentation", ["bash", "scripts/android/build.sh", "connectedDebugAndroidTest",
                            f"-Pandroid.testInstrumentationRunnerArguments.acceptanceRunToken={run_id}"])
        # Archive immediately: later focused tests overwrite Gradle's connected result directory.
        archived = archive_files(results, directory / "test-results", started_ns)
        xmls = [path for path in archived if path.suffix == ".xml"]
        manifest["tests"] = test_summary(xmls)
        save()
        logs = [path for path in archived if path.name == CAPTURE_LOG]
        capture_passed = any(case["class"] == "org.openrailfanai.app.NativeAcceptanceCaptureTest"
                             and case["name"] == "captureNineFixedMainStatesAt390By844Px"
                             and case["status"] == "passed" for case in manifest["tests"]["cases"])
        if build_code or not manifest["tests"]["total"] or manifest["tests"]["failed"]:
            failure("本次原生测试失败或缺少本次运行的测试结果，未使用旧采集文件")
        elif len(logs) != 1 or not capture_passed:
            failure("本次运行缺少唯一的九状态采集日志，未使用旧采集文件")
        elif command("extract", [python, "scripts/android/extract-native-captures.py", "--log", str(logs[0]),
                                  "--expected-run-token", run_id]):
            failure("本次原生截图提取失败")
        else:
            archive_files(root / "android/app/build/native-captures", directory / "captures")
            final_code = command("compare", [python, "scripts/android/build-native-acceptance.py",
                                             "--expected-run-token", run_id])
            compare_output = (directory / "compare.log").read_text().splitlines()
            comparison_complete = False
            try:
                summary = json.loads(compare_output[-1])
                if not isinstance(summary, dict):
                    raise ValueError("Comparison summary must be an object")
                expected_ids = {f"ACC-CT8-{i:02d}" for i in range(1, 10)}
                raw_index = yaml.safe_load((root / "acceptance/index.yaml").read_text())
                if not isinstance(raw_index, list) or not all(isinstance(e, dict) for e in raw_index):
                    raise ValueError("Comparison index must contain entry objects")
                current = [e for e in raw_index
                           if e.get("acc_id") in expected_ids]
                comparison_complete = (final_code in (0, 1) and len(current) == 9
                    and len(summary.get("generated", [])) == 9
                    and {e["acc_id"] for e in current} == expected_ids
                    and all(e.get("script_version") == manifest["script_version"] and e.get("capture_run_token") == run_id and e.get("file")
                            and (root / e["file"]).is_file()
                            and datetime.fromisoformat(e["generated_at"]) >= now.replace(microsecond=0)
                            for e in current))
            except (ValueError, KeyError, IndexError, TypeError, OSError):
                pass
            if comparison_complete:
                manifest["status"] = "passed" if final_code == 0 else "not_accepted"
            else:
                final_code = 1
                failure("本次比较未生成完整的新索引；未将旧索引或未完成图片视为本次验收")
                manifest["status"] = "comparison_failed"
    except (OSError, ValueError, ET.ParseError) as error:
        manifest["error"] = str(error)
        failure("本次验收流程异常；详见运行归档")
    finally:
        if manifest["status"] == "running":
            manifest["status"] = "capture_failed"
        manifest["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        manifest_ref = str(manifest_path.relative_to(root))
        try:
            for path in stamp_indices(root, run_id, manifest_ref, now):
                target = directory / "acceptance" / path.relative_to(root / "acceptance")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        except (OSError, ValueError, TypeError) as error:
            manifest["status"] = "archive_failed"
            manifest["archive_error"] = str(error)
            final_code = 1
        try:
            manifest["artifacts"] = [{"file": str(path.relative_to(directory)),
                                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                                     for path in sorted(directory.rglob("*")) if path.is_file() and path != manifest_path]
            save()
        except OSError as error:
            manifest["status"] = "archive_failed"
            manifest["archive_error"] = str(error)
            final_code = 1
            try:
                save()
            except OSError:
                pass
    print(json.dumps({"index": "acceptance/index.yaml", "failed_index": "acceptance/_failed/index.yaml",
                      "run_id": run_id, "manifest": manifest_ref, "status": manifest["status"]}, ensure_ascii=False))
    return final_code


if __name__ == "__main__":
    raise SystemExit(run())
