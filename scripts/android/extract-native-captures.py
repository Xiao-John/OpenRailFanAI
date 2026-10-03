#!/usr/bin/env python3
"""Extract exact Compose test-node PNG bytes from the Android instrumentation log."""
from __future__ import annotations

import base64
import json
import re
import argparse
from pathlib import Path

from PIL import Image
from io import BytesIO

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "android/app/build/outputs/androidTest-results/connected/debug"
OUTPUT = ROOT / "android/app/build/native-captures"
TEST_NAME = "logcat-org.openrailfanai.app.NativeAcceptanceCaptureTest-captureNineFixedMainStatesAt390By844Px.txt"
STATES = ["train_schedule", "emu_routing", "query_loading", "batch_partial", "routing_empty", "connection_error", "reading_followup", "query_details", "history"]

parser = argparse.ArgumentParser()
parser.add_argument("--log", type=Path, help="use the capture log archived by the current acceptance run")
parser.add_argument("--expected-run-token", help="require a unique instrumentation token from this invocation")
args = parser.parse_args()
logs = [args.log] if args.log else list(RESULTS.glob(f"*/{TEST_NAME}"))
if not logs:
    raise SystemExit(f"Compose capture log missing: {RESULTS}/<device>/{TEST_NAME}")
log = max(logs, key=lambda path: path.stat().st_mtime)
lines = log.read_text(errors="replace").splitlines()
run_tokens = [match.group(1) for line in lines if (match := re.search(r"NATIVE_RUN\|([A-Za-z0-9-]+)$", line))]
if args.expected_run_token and run_tokens != [args.expected_run_token]:
    raise SystemExit("Capture log does not contain the unique token of this acceptance invocation")
if len(run_tokens) > 1:
    raise SystemExit("Capture log contains repeated invocation tokens")
capture_run_token = run_tokens[0] if run_tokens else None
chunks: dict[str, dict[int, str]] = {}
counts: dict[str, int] = {}
metadata: dict[str, dict[str, float | int]] = {}
measurements: dict[str, dict[str, dict[str, float]]] = {}
copy_checks: dict[str, list[dict[str, object]]] = {}
node_text: dict[str, dict[str, str]] = {}
node_semantics: dict[str, dict[str, dict]] = {}
interactions: list[str] = []
bases: dict[str, object] = {}
history_diagnostics: object = None
for line in lines:
    match = re.search(r"NATIVE_NODE\|([^|]+)\|([^|]+)\|([A-Za-z0-9+/=]+)$", line)
    if match:
        state, tag, encoded = match.groups()
        if tag in node_semantics.setdefault(state, {}):
            raise SystemExit(f"Duplicate semantics evidence for {state}/{tag}")
        node = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        if not isinstance(node, dict):
            raise SystemExit(f"Invalid semantics record for {state}/{tag}")
        node_semantics[state][tag] = node
        continue
    match = re.search(r"NATIVE_HISTORY_GEOMETRY\|([A-Za-z0-9+/=]+)$", line)
    if match:
        if history_diagnostics is not None:
            raise SystemExit("Duplicate history geometry diagnostic in formal capture log")
        history_diagnostics = json.loads(base64.b64decode(match.group(1), validate=True).decode("utf-8"))
        if not isinstance(history_diagnostics, dict):
            raise SystemExit("History geometry diagnostic must be a JSON object")
        continue
    match = re.search(r"NATIVE_BASIS\|([^|]+)\|([A-Za-z0-9+/=]+)$", line)
    if match:
        state, encoded = match.groups()
        if state in bases:
            raise SystemExit(f"Duplicate capture basis for {state}")
        bases[state] = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
        continue
    match = re.search(r"NATIVE_CAPTURE\|([^|]+)\|(\d+)\|(\d+)\|([A-Za-z0-9+/=]+)$", line)
    if match:
        state, index, count, data = match.groups()
        chunks.setdefault(state, {})[int(index)] = data
        counts[state] = int(count)
        continue
    match = re.search(r"NATIVE_CAPTURE_META\|([^|]+)\|(\d+)\|(\d+)\|([0-9.]+)\|([0-9.]+)\|(\d+)\|(\d+)$", line)
    if match:
        state, width, height, density, font_scale, width_dp, height_dp = match.groups()
        metadata[state] = {"width_px": int(width), "height_px": int(height), "density": float(density),
                           "font_scale": float(font_scale), "width_dp": int(width_dp), "height_dp": int(height_dp)}
        continue
    match = re.search(r"NATIVE_RECT\|([^|]+)\|([^|]+)\|([-0-9.]+)\|([-0-9.]+)\|([-0-9.]+)\|([-0-9.]+)$", line)
    if match:
        state, tag, left, top, right, bottom = match.groups()
        measurements.setdefault(state, {})[tag] = {"x": float(left), "y": float(top), "width": float(right) - float(left), "height": float(bottom) - float(top)}
        continue
    match = re.search(r"NATIVE_NODE_TEXT\|([^|]+)\|([^|]+)\|([A-Za-z0-9+/=]+)$", line)
    if match:
        state, tag, encoded = match.groups()
        if tag in node_text.setdefault(state, {}):
            raise SystemExit(f"Duplicate text leaf evidence for {state}/{tag}")
        node_text[state][tag] = base64.b64decode(encoded, validate=True).decode("utf-8")
        continue
    match = re.search(r"NATIVE_COPY\|([^|]+)\|([A-Za-z0-9+/=]+)\|(true|false)$", line)
    if match:
        state, encoded, passed = match.groups()
        expected = base64.b64decode(encoded).decode("utf-8")
        copy_checks.setdefault(state, []).append({"expected": expected, "exact_visible_match": passed == "true"})
        continue
    match = re.search(r"NATIVE_INTERACTION\|(.*)$", line)
    if match:
        interactions.append(match.group(1))

OUTPUT.mkdir(parents=True, exist_ok=True)
screens = []
for state in STATES:
    if state not in chunks or len(chunks[state]) != counts.get(state) or state not in metadata:
        raise SystemExit(f"Capture is incomplete for {state}: chunks={len(chunks.get(state, {}))}/{counts.get(state)}, metadata={state in metadata}")
    data = base64.b64decode("".join(chunks[state][index] for index in range(counts[state])), validate=True)
    image_path = OUTPUT / f"{state}.png"
    image_path.write_bytes(data)
    with Image.open(BytesIO(data)) as image:
        size = image.size
    if size != (390, 844) or (metadata[state]["width_px"], metadata[state]["height_px"]) != size:
        raise SystemExit(f"Unexpected native screenshot size for {state}: png={size}, measured={metadata[state]}")
    screens.append({"state": state, "file": str(image_path.relative_to(ROOT)), **metadata[state], "measurements": measurements.get(state, {}), "node_text": node_text.get(state, {}), "node_semantics": node_semantics.get(state, {}), "basis": bases.get(state)})
    if state == "history":
        screens[-1]["history_diagnostics"] = history_diagnostics

verification = {"platform": "Android Compose", "source": str(log.relative_to(ROOT)), "device": "railfan-arm64 API 35",
                "capture_run_token": capture_run_token, "run_token_verified": bool(args.expected_run_token),
                "interactions": interactions, "copy_checks": copy_checks, "results": screens}
(OUTPUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"verification": str(OUTPUT / "verification.json"), "screens": [item["file"] for item in screens]}, ensure_ascii=False))
