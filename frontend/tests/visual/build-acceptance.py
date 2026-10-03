"""Build unscaled design/Edge acceptance pairs and YAML indices."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from PIL import Image


ROOT = Path(__file__).resolve().parents[3]
VISUAL = ROOT / "frontend/tests/visual"
OUT = ROOT / "acceptance"
FAILED = OUT / "_failed"
OUT.mkdir(exist_ok=True)
FAILED.mkdir(exist_ok=True)
VERSION = "1.0.0"
NOW = datetime.now(ZoneInfo("Asia/Shanghai"))
STATE_IDS = {
    "train_schedule": "ACC-T03", "emu_routing": "ACC-T04",
    "query_loading": "ACC-T05", "batch_partial": "ACC-T06",
    "routing_empty": "ACC-T07", "connection_error": "ACC-T08",
    "query_details": "ACC-T09", "reading_followup": "ACC-T10",
    "history": "ACC-T11",
}

verification = json.loads((VISUAL / "screenshots/verification.json").read_text())
targets = json.loads((VISUAL / "design-targets.json").read_text())["states"]
icons = json.loads((VISUAL / "icon-comparisons/measurements.json").read_text())["items"]
captures = {item["state"]: item for item in verification["results"] if "checks" in item}
assert verification["browser"] == "Microsoft Edge"
assert set(captures) == set(STATE_IDS)

index = []
failures = []
for state, acc_id in STATE_IDS.items():
    result = captures[state]
    assert result["viewport"]["width"] == 390 and result["viewport"]["height"] == 844
    assert result["viewport"]["dpr"] == 1
    design = targets[state]
    source = ROOT / design["source"]
    raw_x, raw_y, raw_width, raw_height = design["frame"]
    with Image.open(source) as original:
        assert 0 <= raw_x < original.width and 0 <= raw_y < original.height
        assert raw_x + raw_width <= original.width and raw_y + raw_height <= original.height
        # This is the user-approved extraction of a module from the composite
        # source. It retains the source pixels at their original 1:1 size.
        design_module = original.crop((raw_x, raw_y, raw_x + raw_width, raw_y + raw_height)).convert("RGB")
    with Image.open(result["path"]) as page:
        assert page.size == (390, 844), (state, page.size)
        page = page.convert("RGB")
        output = Image.new("RGB", (raw_width + 16 + 390, max(raw_height, 844)), "white")
        output.paste(design_module, (0, 0))
        output.paste(page, (raw_width + 16, 0))
    name = f"{acc_id}__{state.replace('_', '-')}__{NOW:%Y%m%d}.png"
    output.save(OUT / name)
    layout_diffs = [abs(float(v)) for item in result["checks"]
                    for v in item.get("deltaPx", {}).values()]
    icon_items = [item for item in icons if item["id"].startswith(state + "-")]
    icon_diffs = [abs(float(v)) for item in icon_items
                  for key in ("edgeDeltaPx", "centerDeltaPx")
                  for v in item.get(key, [])]
    bad_layout = [item.get("element", item.get("selector")) for item in result["checks"]
                  if item["result"] != "pass"]
    bad_copy = [item.get("key", item.get("selector")) for item in result["copyChecks"]
                if item["result"] != "pass"]
    bad_icons = [item["id"] for item in icon_items if item["result"] != "pass"]
    status = "passed" if not (bad_layout or bad_copy or bad_icons) else "failed"
    entry = {
        "acc_id": acc_id,
        "module": state,
        "design_ref": f"{design['source']}:{design['frame']}",
        "impl_ref": str(Path(result["path"]).relative_to(ROOT)),
        "viewport": {"width": 390, "height": 844, "unit": "css_px"},
        "diff_px": round(max(layout_diffs + icon_diffs, default=0), 2),
        "status": status,
        "file": f"acceptance/{name}",
        "generated_at": NOW.isoformat(timespec="seconds"),
        "script_version": VERSION,
    }
    index.append(entry)
    if status != "passed":
        failures.append({**entry, "failed_layout": bad_layout,
                         "failed_copy": bad_copy, "failed_icons": bad_icons})

(OUT / "index.yaml").write_text(yaml.safe_dump(index, allow_unicode=True, sort_keys=False))
(FAILED / "index.yaml").write_text(yaml.safe_dump(failures, allow_unicode=True, sort_keys=False))
print(json.dumps({"index": str(OUT / "index.yaml"),
                  "failed_index": str(FAILED / "index.yaml"),
                  "generated": [item["file"] for item in index],
                  "passed": len(index) - len(failures), "failed": len(failures)}, ensure_ascii=False))
