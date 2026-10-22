// 完成授权回包后再断言恢复，不把仅发起 /me 当成正确恢复。
const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, createClock, flush } = require('./harness');
async function open(kind, options = {}) {
  const clock = createClock(), h = createWeapp({ clock }); h.clock = clock;
  h.app.ensureSession = () => Promise.resolve({ phone_bound: true });
  h.connections = []; h.starts = 0;
  h.wx.startWifi = o => { h.starts++; };
  h.wx.connectWifi = o => h.connections.push(o);
  h.load(`pages/${kind}/${kind}.js`); h.page.onLoad(options); await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } }); await flush();
  return h;
}
async function reauth(h) {
  h.page.onHide(); h.app._sessionGeneration++; h.page.onShow(); await flush();
  assert(h.requests.at(-1).url.endsWith('/api/auth/me'));
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } }); await flush();
}
for (const status of ['bound', 'expired']) {
  test(`完成配网重新认证后只读核当前归属 ${status}，不切设备热点`, async () => {
    const h = await open('wifi', { s: 'test-device-ap', m: 'aa:bb:cc:dd:ee:01' });
    h.page.autoBind(); h.requests.at(-1).success({ statusCode: 200, data: { status: 'bound' } }); await flush();
    const before = h.requests.length, starts = h.starts;
    await reauth(h);
    const query = h.requests.at(-1);
    assert.equal(query.method, 'GET'); assert(query.url.includes('/bind/claim/status?'));
    query.success({ statusCode: 200, data: { status } }); await flush();
    assert.equal(h.page.data.bindState, status === 'bound' ? 'bound' : 'manual');
    assert.equal(h.page.data.step, 'done');
    assert.equal(h.connections.length, 0); assert.equal(h.starts, starts);
    assert.equal(h.requests.slice(before).filter(r => r.method === 'POST').length, 0);
    assert.equal(h.page.data.homePassword, '');
  });
}
test('已完成手输短码重新认证不重发，旧成功展示先失效', async () => {
  const h = await open('bind');
  h.page.onCodeInput({ detail: { value: 'ABC123' } }); h.page.onSubmit();
  h.requests.at(-1).success({ statusCode: 200, data: { device_mac: 'aa:bb:cc:dd:ee:01' } }); await flush();
  const before = h.requests.length;
  await reauth(h); h.clock.advance(60000);
  assert.equal(h.page.data.step, 'input'); assert.equal(h.page._boundMac, null);
  assert.equal(h.page.data.macSuffix, ''); assert.equal(h.page.data.submitting, false);
  assert.equal(h.requests.slice(before).filter(r => r.method === 'POST').length, 0);
});
for (const active of [false, true]) {
  test(`无短码完成展示重新认证取当前账号设备，active=${active}`, async () => {
    const h = await open('bind');
    h.requests.at(-1).success({ statusCode: 200, data: { devices: [{ active: true, device_mac: 'aa:bb:cc:dd:ee:01' }] } }); await flush();
    await reauth(h);
    h.requests.at(-1).success({ statusCode: 200, data: { devices: active ? [{ active: true, device_mac: 'aa:bb:cc:dd:ee:02' }] : [] } }); await flush();
    assert.equal(h.page.data.step, active ? 'bound' : 'input');
    assert.equal(h.page._boundMac, active ? 'aa:bb:cc:dd:ee:02' : null);
  });
}
test('同登录代次完成展示回屏保持完成且不重发', async () => {
  const h = await open('bind');
  h.page.onCodeInput({ detail: { value: 'ABC123' } }); h.page.onSubmit();
  h.requests.at(-1).success({ statusCode: 200, data: { device_mac: 'aa:bb:cc:dd:ee:01' } }); await flush();
  const before = h.requests.length;
  h.page.onHide(); h.page.onShow(); h.clock.advance(60000);
  assert.equal(h.page.data.step, 'bound'); assert.equal(h.requests.length, before);
});
