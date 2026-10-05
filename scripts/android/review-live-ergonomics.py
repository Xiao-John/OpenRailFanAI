#!/usr/bin/env python3
"""Automated, isolated live-use walkthrough; captures are observations, not acceptance passes."""
import argparse
import hashlib
import json
import struct
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import yaml

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--serial', default='emulator-5556')
args = parser.parse_args()
if args.serial != 'emulator-5556':
    raise SystemExit('此流程只允许使用隔离评测模拟器 emulator-5556')
ADB = [str(ROOT / '.android-build/sdk/platform-tools/adb'), '-s', args.serial]
now = datetime.now(ZoneInfo('Asia/Shanghai'))
OUT = ROOT / 'acceptance/ergonomics' / now.strftime('%Y%m%dT%H%M%S')
OUT.mkdir(parents=True)
records = []

def adb(*args):
    return subprocess.check_output(ADB + list(args), timeout=40)

def nodes():
    adb('shell', 'uiautomator', 'dump', '/sdcard/ergonomics.xml')
    return list(ET.fromstring(adb('shell', 'cat', '/sdcard/ergonomics.xml')).iter('node'))

def find(value, partial=False):
    for node in nodes():
        texts = [node.get('text', ''), node.get('content-desc', '')]
        if any(value in text if partial else value == text for text in texts):
            return node
    raise RuntimeError('未找到界面控件：' + value)

def tap(value, partial=False):
    import re
    node = find(value, partial)
    left, top, right, bottom = map(int, re.findall(r'\d+', node.get('bounds', '')))
    if right <= left or bottom <= top:
        raise RuntimeError('控件不可见：' + value)
    adb('shell', 'input', 'tap', str((left + right)//2), str((top + bottom)//2))
    records.append({'action': 'tap', 'target': value, 'bounds': [left, top, right, bottom]})
    time.sleep(.35)

def capture(slug):
    data = adb('exec-out', 'screencap', '-p')
    if data[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', data[16:24]) != (390,844):
        raise RuntimeError('实际截图尺寸不是390×844，拒绝缩放或裁切')
    path = OUT / (slug + '.png')
    path.write_bytes(data)
    visible = [{'text': node.get('text'), 'description': node.get('content-desc'),
                'bounds': node.get('bounds'), 'clickable': node.get('clickable')}
               for node in nodes() if node.get('text') or node.get('content-desc')]
    records.append({'scene': slug, 'file': str(path.relative_to(ROOT)),
                    'sha256': hashlib.sha256(data).hexdigest(), 'viewport': [390,844],
                    'status': 'observed_not_accepted', 'visible_controls': visible})
    (OUT / 'index.yaml').write_text(yaml.safe_dump(records, allow_unicode=True, sort_keys=False))

def fill(label, value):
    tap(label)
    adb('shell', 'input', 'text', value)
    adb('shell', 'input', 'keyevent', '4')
    time.sleep(.35)

def wait_feedback():
    for _ in range(20):
        if any('连接失败' in node.get('text', '') for node in nodes()):
            return
        time.sleep(.5)
    raise RuntimeError('连接失败反馈未在观察窗口内出现')

try:
    avd_name = adb('emu', 'avd', 'name').decode().splitlines()[0].strip()
    if avd_name != 'railfan_aesthetic':
        raise RuntimeError('拒绝清空非专用评测模拟器的数据')
    adb('shell', 'wm', 'size', '390x844')
    adb('shell', 'wm', 'density', '160')
    adb('shell', 'pm', 'clear', 'org.openrailfanai.app')
    adb('shell', 'am', 'force-stop', 'org.openrailfanai.app')
    adb('shell', 'am', 'start', '-n', 'org.openrailfanai.app/.MainActivity')
    time.sleep(8)
    if any("System UI isn't responding" == n.get('text', '') for n in nodes()):
        capture('00-system-ui-dialog')
        tap('Wait')
        time.sleep(2)
    capture('01-home')
    tap('设置')
    time.sleep(2)
    capture('02-settings-first-entry')
    tap('添加提供商', True)
    capture('03-provider-picker')
    adb('shell', 'input', 'swipe', '210', '580', '210', '300', '400')
    records.append({'action': 'scroll_provider_picker'})
    capture('03b-provider-picker-scrolled')
    tap('自定义提供商', True)
    fill('接口地址 输入框', 'http://127.0.0.1:1/v1')
    fill('API Key输入框', 'ergonomics-invalid-key')
    fill('模型 输入框', 'ergonomics-test-model')
    capture('04-settings-filled')
    tap('测试连接')
    capture('05-connection-testing')
    wait_feedback()
    capture('06-connection-failed')
    tap('保存')
    capture('07-saved-back-to-chat')
    tap('查时刻')
    capture('08-example-draft-keyboard')
    adb('shell', 'input', 'keyevent', '4')
    capture('09-draft-ready')
    tap('查询')
    time.sleep(6)
    capture('10-query-with-invalid-config')
    tap('对话历史')
    capture('11-history-drawer')
    adb('shell', 'input', 'tap', '380', '400')
    records.append({'action': 'tap_scrim', 'position': [380,400]})
    time.sleep(.35)
    tap('新建对话')
    capture('12-new-conversation')
except Exception as error:
    records.append({'status': 'incomplete', 'error': str(error)})
finally:
    (OUT / 'index.yaml').write_text(yaml.safe_dump(records, allow_unicode=True, sort_keys=False))
    print(json.dumps({'index': str((OUT/'index.yaml').relative_to(ROOT)), 'observations': len(records)}, ensure_ascii=False))
