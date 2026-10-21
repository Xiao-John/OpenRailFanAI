import assert from 'node:assert/strict';
import { safeHttpSource, routingSourceUrl } from '../src/source-links.js';
for (const value of ['javascript:alert(1)', 'data:text/html,test', 'file:///tmp/test', '//rail.re', '', null]) {
  assert.equal(safeHttpSource(value), '');
}
assert.equal(safeHttpSource('https://kyfw.12306.cn/otn/'), 'https://kyfw.12306.cn/otn/');
assert.equal(safeHttpSource('http://example.org/a'), 'http://example.org/a');
assert.equal(routingSourceUrl(['javascript:rail.re', 'https://rail.re.evil.org', 'https://api.rail.re/emu/CR400AF']), 'https://api.rail.re/emu/CR400AF');
assert.equal(routingSourceUrl(['https://example.org/rail.re']), 'https://rail.re');
console.log('[PASS] 来源链接：网页协议与交路域名边界');
