"""Automated preliminary native evidence. Invoked only through acceptance.sh."""
import datetime, hashlib, importlib.util, json, re, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml
from PIL import Image

root = Path(__file__).resolve().parents[2]
if len(sys.argv) == 3 and sys.argv[1] == '--review-run':
    run = (root / sys.argv[2]).resolve()
    if not run.is_relative_to(root/'acceptance/runs'): raise ValueError('不是正式运行目录')
    review = yaml.safe_load((run/'visual-review.yaml').read_text())
    entry = yaml.safe_load((run/'manifest.yaml').read_text())
    file = root/'acceptance'/entry['file']
    assert review['comparison_sha256'] == hashlib.sha256(file.read_bytes()).hexdigest()
    assert review['reviewer'] == 'H' and all(review['checks'].values())
    assert entry['verification']['tests'] == 5 and entry['verification']['failed'] == 0
    entry['status'] = 'passed'
    entry['verification']['scope'] = '初步功能及默认卡片协调性通过；不表示完整真机验收或像素测量通过'
    entry['visual_review'] = str((run/'visual-review.yaml').relative_to(root))
    for target in [root/'acceptance/index.yaml',root/'acceptance/_failed/index.yaml']:
        rows = yaml.safe_load(target.read_text()) or []
        rows = [r for r in rows if r.get('acc_id') != entry['acc_id']]
        if target.parent.name != '_failed': rows.append(entry)
        target.write_text(yaml.safe_dump(rows,allow_unicode=True,sort_keys=False))
    (run/'manifest.yaml').write_text(yaml.safe_dump(entry,allow_unicode=True,sort_keys=False))
    print('acceptance/index.yaml')
    raise SystemExit(0)
now = datetime.datetime.now(ZoneInfo('Asia/Shanghai'))
run = root / 'acceptance' / 'runs' / ('photo-' + now.strftime('%Y%m%dT%H%M%S'))
run.mkdir(parents=True)
subprocess.run([str(root/'.android-build/sdk/platform-tools/adb'), 'reverse', 'tcp:8017', 'tcp:8017'], check=True)
with (run/'instrumentation.log').open('w') as log:
    result = subprocess.run(['bash','scripts/android/build.sh','connectedDebugAndroidTest',
        '-Pandroid.testInstrumentationRunnerArguments.class=org.openrailfanai.app.ComposePhotoSpotTest'], cwd=root, stdout=log, stderr=subprocess.STDOUT)
if result.returncode: raise SystemExit('原生测试未通过，保留运行日志：' + str(run))
report_root = root/'android/app/build/outputs/androidTest-results/connected/debug'
xml = ET.parse(next(report_root.glob('TEST-*.xml'))).getroot()
cases = xml.findall('testcase')
assert len(cases) == 5 and not xml.findall('.//failure') and not xml.findall('.//skipped')
module_spec = importlib.util.spec_from_file_location('native_capture', root/'scripts/android/run-native-acceptance.py')
module = importlib.util.module_from_spec(module_spec); module_spec.loader.exec_module(module)
captures = module.extract_usability_captures(list(report_root.rglob('logcat-*.txt')), run/'captures')
actual = run/'captures/photo-spot-default.png'
assert actual in captures
source = root/'frontend/tests/visual/designs/photo-spot-card.html'
fragment = source.read_text()
# Preserve the reference module; static icons come from the same fixed source, no hand drawing.
def icon(match):
    name = match.group(1)
    file = root/'frontend/assets/icons'/({'map-pin':'search','chevron-down':'chevron-right'}.get(name,name) + '.svg')
    return file.read_text() if file.exists() else match.group(0)
fragment = re.sub(r'<i[^>]*data-lucide="([a-z-]+)"[^>]*></i>', icon, fragment)
reference = run/'design.html'
reference.write_text('<!doctype html><html style="color-scheme:light"><head><meta charset="utf-8"></head><body style="margin:0;background:#F8FAFD">' + fragment + '</body></html>')
with (run/'design-render.log').open('w') as log:
    subprocess.run(['node',str(root/'scripts/android/render-photo-design.mjs'),str(reference),str(run/'design.png')], stdout=log,stderr=subprocess.STDOUT,check=True,timeout=40)

left=Image.open(run/'design.png').convert('RGB');right=Image.open(actual).convert('RGB')
assert left.size == right.size == (390,844)
out=Image.new('RGB',(780,844));out.paste(left,(0,0));out.paste(right,(390,0))
filename='ACC-PHOTO-01__photo-spot__'+now.strftime('%Y%m%d')+'.png'
out.save(root/'acceptance'/filename)
entry=dict(acc_id='ACC-PHOTO-01',module='机位卡片',design_ref=str(source.relative_to(root)),
    impl_ref=str(actual.relative_to(root)),viewport=dict(width=390,height=844),diff_px=None,
    status='review_pending',file=filename,generated_at=now.isoformat(),script_version='photo-1.0.0',
    verification=dict(tests=len(cases),failed=0,skipped=0,scope='初步功能验证通过；设计对照待人工审阅，不宣称像素验收通过'))
for target in [root/'acceptance/index.yaml',root/'acceptance/_failed/index.yaml']:
    rows=yaml.safe_load(target.read_text()) if target.exists() else []
    if isinstance(rows,dict):
        # Do not overwrite an unfamiliar historical schema.
        raise ValueError('索引格式需人工核对：'+str(target))
    rows=[r for r in (rows or []) if r.get('acc_id')!='ACC-PHOTO-01']
    rows.append(entry)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(yaml.safe_dump(rows,allow_unicode=True,sort_keys=False))
(run/'manifest.yaml').write_text(yaml.safe_dump(entry,allow_unicode=True,sort_keys=False))
print(json.dumps(dict(index='acceptance/index.yaml',failed_index='acceptance/_failed/index.yaml',new_files=[filename],run=str(run.relative_to(root))),ensure_ascii=False))
