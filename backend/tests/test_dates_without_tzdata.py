"""Android缺少IANA时区数据时，日期解析和未来日期判断仍须可用。"""
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from app import dates

code = '''
import zoneinfo
zoneinfo.reset_tzpath([])
try:
    zoneinfo.ZoneInfo("Asia/Shanghai")
except zoneinfo.ZoneInfoNotFoundError:
    pass
else:
    raise AssertionError("隔离环境意外存在tzdata")
import importlib.util
spec = importlib.util.spec_from_file_location("isolated_dates", "backend/app/dates.py")
dates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dates)
future_railway_date, normalize_date = dates.future_railway_date, dates.normalize_date
assert future_railway_date("明天") == normalize_date("明天")
assert future_railway_date("今天") == ""
print("无时区数据库的日期路径通过")
'''
subprocess.run([sys.executable, '-S', '-c', code], env={**os.environ, 'PYTHONPATH': 'backend'}, check=True)

class UtcClock:
    @classmethod
    def now(cls, tz):
        return datetime(2026, 10, 4, 16, 30, tzinfo=timezone.utc).astimezone(tz)

with patch('app.dates.datetime', UtcClock):
    assert dates.railway_today().isoformat() == '2026-10-05'
    assert dates.normalize_date('明天') == '2026-10-06'
    assert dates.future_railway_date('明天') == '2026-10-06'
    assert dates.future_railway_date('2026-10-05') == ''
print('中国时区跨日判断通过')
