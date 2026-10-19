"""Archive focused native checks and generate unscaled comparisons via acceptance.sh."""
import datetime
import hashlib
import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from PIL import Image

root = Path(__file__).resolve().parents[2]
if len(sys.argv) == 3 and sys.argv[1] == '--review-run':
    run = (root / sys.argv[2]).resolve()
    assert run.is_relative_to(root / 'acceptance/runs')
    manifest = yaml.safe_load((run / 'manifest.yaml').read_text())
    review = yaml.safe_load((run / 'visual-review.yaml').read_text())
    assert review['reviewer'] == 'H' and all(review['checks'].values())
    for entry in manifest['entries']:
        assert review['comparison_sha256'][entry['acc_id']] == hashlib.sha256((root / 'acceptance' / entry['file']).read_bytes()).hexdigest()
        entry['status'] = 'passed'
        entry['visual_review'] = str((run / 'visual-review.yaml').relative_to(root))
        entry['verification']['scope'] = '定向原生交互、离线图片处理、密钥隔离及默认布局协调性通过；不代表云端准确率或完整真机验收'
    ids = {entry['acc_id'] for entry in manifest['entries']}
    for target in [root / 'acceptance/index.yaml', root / 'acceptance/_failed/index.yaml']:
        rows = yaml.safe_load(target.read_text()) or []
        rows = [row for row in rows if row.get('acc_id') not in ids]
        if target.parent.name != '_failed': rows.extend(manifest['entries'])
        target.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False))
    manifest['status'] = 'passed'
    (run / 'manifest.yaml').write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
    print('acceptance/index.yaml')
    raise SystemExit(0)
reports = root / 'android/app/build/outputs/androidTest-results/connected/debug'
xmls = sorted(reports.glob('TEST-*.xml'), key=lambda p: p.stat().st_mtime, reverse=True)
assert xmls, '没有原生检查结果'
xml = xmls[0]
cases = ET.parse(xml).getroot().findall('testcase')
required = {'ComposeInputsSettingsTest', 'MainSettingsRepositoryTest', 'AgentInputIntegrationTest'}
assert {c.attrib['classname'].split('.')[-1] for c in cases} == required
assert len(cases) == 17 and all(c.find('failure') is None and c.find('skipped') is None and c.find('error') is None for c in cases)
now = datetime.datetime.now(ZoneInfo('Asia/Shanghai'))
assert abs(now.timestamp() - xml.stat().st_mtime) < 3600, '请先重新运行定向原生检查'
run = root / 'acceptance/runs' / ('inputs-' + now.strftime('%Y%m%dT%H%M%S'))
run.mkdir(parents=True)
(run / 'test-results.xml').write_bytes(xml.read_bytes())
spec = importlib.util.spec_from_file_location('native_input_capture', root / 'scripts/android/run-native-acceptance.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
captures = module.extract_usability_captures(list(reports.rglob('logcat-*.txt')), run / 'captures')
entries = []
for number, slug, title in [(1, 'attachment-composer', '图片与文档导入'), (2, 'fuck-cr-settings', '车次规则兼容按钮')]:
    actual = run / 'captures' / (slug + '.png')
    assert actual in captures, f'缺少真实原生采集：{slug}'
    design = root / 'frontend/tests/visual/designs' / (slug + '.html')
    reference = run / (slug + '-design.png')
    with (run / (slug + '-render.log')).open('w') as log:
        subprocess.run(['node', str(root / 'scripts/android/render-photo-design.mjs'), str(design), str(reference)], cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=45)
    left = Image.open(reference).convert('RGB')
    right = Image.open(actual).convert('RGB')
    assert left.size == right.size == (390, 844)
    comparison = Image.new('RGB', (780, 844))
    comparison.paste(left, (0, 0)); comparison.paste(right, (390, 0))
    acc_id = f'ACC-INPUT-{number:02}'
    filename = f'{acc_id}__{slug}__{now:%Y%m%d}.png'
    comparison.save(root / 'acceptance' / filename)
    entries.append(dict(acc_id=acc_id, module=title, design_ref=str(design.relative_to(root)), impl_ref=str(actual.relative_to(root)),
        viewport=dict(width=390, height=844), diff_px=None, status='review_pending', file=filename, generated_at=now.isoformat(), script_version='inputs-1.0.0',
        verification=dict(tests=17, failed=0, skipped=0, scope='定向原生交互、图片处理及密钥隔离检查通过；视觉对照待审阅，非真实云端准确率验收'),
        run_manifest=str((run / 'manifest.yaml').relative_to(root))))
for target in [root / 'acceptance/index.yaml', root / 'acceptance/_failed/index.yaml']:
    old = yaml.safe_load(target.read_text()) if target.exists() else []
    assert old is None or isinstance(old, list), '未知索引格式，禁止覆盖'
    rows = [r for r in (old or []) if r.get('acc_id') not in {e['acc_id'] for e in entries}]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(rows + entries, allow_unicode=True, sort_keys=False))
(run / 'manifest.yaml').write_text(yaml.safe_dump(dict(status='review_pending', entries=entries,
    test_source=str(xml.relative_to(root)), test_xml_sha256=hashlib.sha256(xml.read_bytes()).hexdigest()), allow_unicode=True, sort_keys=False))
print(json.dumps(dict(index='acceptance/index.yaml', failed_index='acceptance/_failed/index.yaml', new_files=[e['file'] for e in entries], run=str(run.relative_to(root))), ensure_ascii=False))
