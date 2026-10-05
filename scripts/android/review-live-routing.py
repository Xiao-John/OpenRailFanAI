#!/usr/bin/env python3
"""Collect a real installed-app routing walkthrough; screenshots come from acceptance.sh."""
import hashlib
import json
import os
import re
import struct
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
ADB = [str(ROOT / '.android-build/sdk/platform-tools/adb'), '-s', 'emulator-5562']
OUT = ROOT / 'acceptance/routing-live' / datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S')
OUT.mkdir(parents=True, exist_ok=False)
events = []

def adb(*args, timeout=30):
    return subprocess.check_output(ADB + list(args), timeout=timeout)

def ui():
    adb('shell', 'uiautomator', 'dump', '/sdcard/routing-ui.xml')
    raw = adb('shell', 'cat', '/sdcard/routing-ui.xml').decode(errors='replace')
    return raw

def capture(name):
    data = adb('exec-out', 'screencap', '-p')
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or struct.unpack('>II', data[16:24]) != (390, 844):
        raise RuntimeError('unexpected screenshot geometry; no scaling or cropping allowed')
    path = OUT / (name + '.png')
    path.write_bytes(data)
    hierarchy = ui()
    root = ET.fromstring(hierarchy)
    visible = []
    for node in root.iter('node'):
        label = node.get('text') or node.get('content-desc') or ''
        if not label:
            continue
        bounds = [int(x) for x in re.findall(r'\d+', node.get('bounds', ''))]
        sensitive = node.get('password') == 'true' or (
            name.startswith(('03-', '04-', '05-')) and len(bounds) == 4
            and bounds[1] >= 420 and bounds[1] < 550
        )
        visible.append('[masked API Key input]' if sensitive else label)
    events.append({'scene': name, 'file': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(data).hexdigest(),
                   'visible_text': visible})
    (OUT / 'index.json').write_text(json.dumps(events, ensure_ascii=False, indent=2))
    return hierarchy

def tap(x, y, label):
    adb('shell', 'input', 'tap', str(x), str(y)); events.append({'action':'tap','label':label,'xy':[x,y]}); time.sleep(.6)

def tap_text(label, partial=False):
    root = ET.fromstring(ui())
    for node in root.iter('node'):
        value = node.get('text') or node.get('content-desc') or ''
        if value == label or (partial and label in value):
            bounds = [int(x) for x in re.findall(r'\d+', node.get('bounds', ''))]
            if len(bounds) == 4 and bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                tap((bounds[0]+bounds[2])//2, (bounds[1]+bounds[3])//2, label)
                return
    raise RuntimeError('visible control not found: ' + label)

def main():
    serials = adb('devices', '-l').decode(errors='replace')
    if 'emulator-5562' not in serials or 'device' not in serials.split('emulator-5562',1)[1].splitlines()[0]:
        raise RuntimeError('emulator-5562 is unavailable')
    if adb('shell','getprop','sys.boot_completed').decode().strip() != '1':
        raise RuntimeError('device is not booted')
    info = adb('shell','dumpsys','package','org.openrailfanai.app').decode(errors='replace')
    vals = {}
    for k in ('versionName','versionCode','firstInstallTime','lastUpdateTime'):
        m = re.search(rf'\b{k}=([^\s]+)', info)
        vals[k] = m.group(1) if m else None
    vals['avd'] = adb('emu','avd','name').decode(errors='replace').strip()
    vals['apiLevel'] = adb('shell','getprop','ro.build.version.sdk').decode().strip()
    adb('shell','wm','size','390x844')
    adb('shell','wm','density','160')
    vals['screenOverride'] = adb('shell','wm','size').decode().strip()
    (OUT/'device.json').write_text(json.dumps(vals,ensure_ascii=False,indent=2))
    mode = os.environ.get('LUNA_ROUTING_MODE', 'settings')
    if mode == 'settings':
        adb('shell','am','start','-W','-n','org.openrailfanai.app/.MainActivity')
        time.sleep(7)
        capture('01-empty-conversation')
        tap(39, 73, 'settings icon (top left)')
        time.sleep(1)
        capture('02-settings-entry')
    elif mode == 'configure':
        key = os.environ.get('SILICONFLOW_KEY', '')
        if not key:
            raise RuntimeError('SILICONFLOW_KEY was not supplied to the process')
        capture('03-settings-before-key')
        adb('shell','input','tap','190','500')
        adb('shell','input','text',key)
        del key
        adb('shell','input','keyevent','4')
        time.sleep(.5)
        adb('shell','input','tap','190','596')
        adb('shell','input','text','deepseek-ai/DeepSeek-V4-Flash')
        adb('shell','input','keyevent','4')
        time.sleep(.5)
        capture('04-settings-filled-key-hidden')
        adb('logcat','-c')
        adb('shell','input','tap','280','657')
        time.sleep(.5)
        capture('05-model-connection-testing')
        deadline = time.time() + 100
        while time.time() < deadline:
            xml = ui()
            if '连接成功' in xml or '连接失败' in xml:
                break
            time.sleep(1)
        capture('06-model-connection-feedback')
        adb('shell','input','tap','342','77')
        time.sleep(2)
        capture('07-settings-saved')
    elif mode == 'connection':
        key = os.environ.get('SILICONFLOW_KEY', '')
        tap(39, 73, 'settings icon')
        time.sleep(1)
        capture('08-settings-check-remember-off')
        tap_text('编辑')
        time.sleep(1)
        screen = ui()
        if '本次运行仍可使用' not in screen:
            raise RuntimeError('remember-key-off state was not confirmed')
        if '填写 API Key' in screen:
            if not key:
                raise RuntimeError('session key absent; no in-memory key supplied')
            tap_text('填写 API Key')
            adb('shell','input','text',key)
            del key
            adb('shell','input','keyevent','4')
            time.sleep(.5)
        else:
            del key
        capture('09-provider-fields-key-hidden')
        adb('logcat','-c')
        tap_text('测试连接')
        time.sleep(.5)
        capture('10-model-connection-testing')
        deadline = time.time() + 100
        feedback = ''
        while time.time() < deadline:
            feedback = ui()
            if '连接成功' in feedback or '连接失败' in feedback:
                break
            time.sleep(1)
        capture('11-model-connection-feedback')
        events.append({'connection_feedback': 'success' if '连接成功' in feedback else 'failure' if '连接失败' in feedback else 'timeout'})
    elif mode == 'queries':
        if '消息输入框' not in ui():
            tap_text('返回')
            time.sleep(1)
        capture('12-query-chat-ready')
        tap_text('查时刻')
        time.sleep(.7)
        capture('13-query-1-draft-from-example')
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'device':vals,'events':events},ensure_ascii=False))

if __name__ == '__main__':
    try: main()
    except Exception as e:
        events.append({'status':'incomplete','error':str(e)})
        if OUT.exists(): (OUT/'index.json').write_text(json.dumps(events,ensure_ascii=False,indent=2))
        raise
