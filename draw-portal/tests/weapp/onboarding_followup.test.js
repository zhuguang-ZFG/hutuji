// 复用真实页面、扫码与TCP实现，检验新板首次使用的恢复与身份边界。
const assert = require('node:assert/strict');
const test = require('node:test');
const crypto = require('node:crypto');
const { createWeapp, createClock, flush } = require('./harness');
const base = 'https://hutuji.donglicao.com/draw-upload/';
const key = crypto.createECDH('prime256v1');
key.setPrivateKey(Buffer.from('1'.padStart(64, '0'), 'hex'));
const pk = key.getPublicKey();
const fields = { m: 'aa:bb:cc:dd:ee:01', v: '2',
  d: crypto.createHash('sha256').update(pk).digest('hex').slice(0, 32),
  k: pk.toString('base64url'), n: Buffer.alloc(16, 7).toString('base64url') };
const qr = (kind, extra = {}) => base + kind + '?' + Object.entries({ ...fields,
  ...(kind === 'wifi' ? { s: 'test-device' } : { c: 'ABC123' }), ...extra })
  .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
async function open(kind = 'wifi', launch = {}) {
  const clock = createClock();
  const h = createWeapp({ clock }); h.clock = clock;
  h.app.ensureSession = () => Promise.resolve({ phone_bound: true });
  h.wx.startWifi = () => {};
  h.connections = [];
  h.wx.connectWifi = options => h.connections.push(options);
  h.load(`pages/${kind}/${kind}.js`); h.page.onLoad(launch);
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  return h;
}
for (const kind of ['wifi', 'bind']) {
  test(`${kind}误扫另一种合法设备码可转到正确入口并保留完整身份`, async () => {
    const h = await open(kind);
    const other = kind === 'wifi' ? 'bind' : 'wifi';
    if (kind === 'wifi') h.page.onScanAp(); else h.page.onScanBind();
    h.scans.at(-1).success({ result: qr(other) });
    const modal = h.modals.at(-1);
    assert.equal(modal.confirmText, other === 'wifi' ? '去配网' : '去绑定');
    assert.equal(h.navigations.length, 0);
    modal.success({ confirm: true });
    assert.equal(h.navigations.at(-1).url, `/pages/${other}/${other}`);
    const pending = h.load('utils/bind_identity.js').restore(other);
    assert.equal(pending.scan.mac, fields.m);
    assert.equal(pending.scan.identity.nonce, fields.n);
    assert.equal(pending.scan.identity.public_key, fields.k);
    assert.equal(h.requests.filter(r => r.method === 'POST').length, 0);
  });
  test(`${kind}误扫部分身份或其他域名不提供跳页入口`, async () => {
    const h = await open(kind);
    const other = kind === 'wifi' ? 'bind' : 'wifi';
    for (const value of [qr(other, { k: '' }), qr(other).replace('hutuji.donglicao.com', 'other.test')]) {
      if (kind === 'wifi') h.page.onScanAp(); else h.page.onScanBind();
      h.scans.at(-1).success({ result: value });
      assert.equal(h.modals.at(-1).showCancel, false);
      assert.equal(h.navigations.length, 0);
    }
  });
}
for (const operation of ['manual-ssid', 'edit-ssid', 'other-device', 'manual-device']) {
  test(`${operation}清除旧密码、显示与锁定状态`, async () => {
    const h = await open();
    h.page.setData({ homeSsid: 'old-router', homePassword: 'TEST-ONLY', apSsid: 'old-device',
      deviceMac: fields.m, wifiLocked: true, showPassword: true });
    if (operation === 'manual-ssid') h.page.onManualSsid();
    else if (operation === 'edit-ssid') h.page.onSsidInput({ detail: { value: 'new-router' } });
    else if (operation === 'other-device') {
      h.page.onScanAp(); h.scans.at(-1).success({ result: qr('wifi', { m: 'aa:bb:cc:dd:ee:02' }) });
    } else h.page.onManualAp();
    assert.equal(h.page.data.homePassword, '');
    assert.equal(h.page.data.showPassword, false);
    if (operation !== 'edit-ssid') assert.equal(h.page.data.wifiLocked, false);
  });
}
test('两次连接均未建立时不进入联网等待；已发后断流仍待确认', async () => {
  for (const sent of [false, true]) {
    const h = await open();
    h.page.setData({ homeSsid: 'test-router', homePassword: 'TEST-ONLY', apSsid: 'test-device' });
    const sockets = [];
    h.wx.createTCPSocket = () => {
      const handlers = {};
      const socket = { onConnect: f => { handlers.connect = f; }, onMessage: f => { handlers.message = f; },
        onClose: f => { handlers.close = f; }, onError: f => { handlers.error = f; },
        connect() {}, write() {}, close() {}, handlers };
      sockets.push(socket); return socket;
    };
    h.wx.startWifi = options => options.success();
    h.wx.getConnectedWifi = options => options.success({ wifi: { SSID: 'test-device' } });
    h.page.onSubmitCreds();
    if (sent) sockets[0].handlers.connect();
    sockets[0].handlers.error();
    if (!sent) {
      h.connections.at(-1).success(); h.clock.advance(1201);
      sockets[1].handlers.error();
      assert.equal(h.page.data.step, 'creds');
      assert.equal(h.page.data.bindState, '');
      assert.match(h.page.data.log, /未发送/);
      assert.equal(h.clock.pending.size, 0);
    } else {
      assert.equal(h.page.data.bindState, 'waiting');
      assert.equal(sockets.length, 1);
    }
  }
});
test('绑定页隐藏停止重试，回屏只读核原扫码且不重发POST', async () => {
  const h = await open('bind', { q: qr('bind') });
  const original = h.requests.at(-1);
  const before = h.requests.length;
  h.page.onHide(); original.fail({ errMsg: 'request:fail timeout' });
  await flush(); h.clock.advance(60000);
  assert.equal(h.requests.length, before);
  h.page.onShow();
  assert.equal(h.requests.at(-1).method, 'GET');
  assert(h.requests.at(-1).url.includes('/bind/claim/status?'));
  assert(h.requests.at(-1).url.includes('nonce=' + fields.n));
  h.requests.at(-1).success({ statusCode: 200, data: { status: 'bound' } });
  await flush();
  assert.equal(h.page.data.step, 'bound');
  assert.equal(h.page._boundMac, fields.m);
  assert.equal(h.clock.pending.size, 0);
});
test('手输码切后台后不以账号另一设备冒充成功', async () => {
  const h = await open('bind');
  h.page.onCodeInput({ detail: { value: 'ABC123' } }); h.page.onSubmit();
  const original = h.requests.at(-1);
  h.page.onHide(); original.success({ statusCode: 200, data: { device_mac: fields.m } });
  await flush();
  const count = h.requests.length; h.page.onShow();
  assert.equal(h.requests.length, count);
  assert.equal(h.page.data.step, 'input');
  assert.equal(h.tabs.length, 0);
});
test('扫码相机暂时遮住绑定页不会丢弃该次合法扫码', async () => {
  const h = await open('bind');
  h.page.onScanBind(); h.page.onHide(); h.page.onShow();
  h.scans.at(-1).success({ result: qr('bind') });
  assert.equal(h.requests.at(-1).data.bind_code, 'ABC123');
  assert.equal(h.requests.at(-1).data.nonce, fields.n);
});
test('误扫后取消跳页不保留上一设备二维码或密码', async () => {
  for (const kind of ['wifi', 'bind']) {
    const h = await open(kind);
    h.page._scan = { mac: fields.m, identity: { nonce: fields.n } };
    h.page.setData({ code: 'OLD123', homePassword: 'TEST-ONLY' });
    if (kind === 'wifi') h.page.onScanAp(); else h.page.onScanBind();
    h.scans.at(-1).success({ result: qr(kind === 'wifi' ? 'bind' : 'wifi') });
    h.modals.at(-1).success({ confirm: false });
    assert.equal(h.page._scan, null);
    assert.equal(h.storage.has('pending_device_binding'), false);
    if (kind === 'wifi') assert.equal(h.page.data.homePassword, '');
    else assert.equal(h.page.data.code, '');
  }
});
test('误扫跳页确认跨过离页或重扫后不会沿用旧二维码', async () => {
  const h = await open('bind');
  h.page.onScanBind(); h.scans.at(-1).success({ result: qr('wifi') });
  const modal = h.modals.at(-1);
  h.page.onHide(); modal.success({ confirm: true });
  assert.equal(h.navigations.length, 0);
});
test('已确认的手动绑定成功回屏后保持成功状态', async () => {
  const h = await open('bind');
  h.page.onCodeInput({ detail: { value: 'ABC123' } }); h.page.onSubmit();
  h.requests.at(-1).success({ statusCode: 200, data: { device_mac: fields.m } });
  await flush();
  assert.equal(h.page.data.step, 'bound');
  h.page.onHide(); const count = h.requests.length; h.page.onShow();
  assert.equal(h.page.data.step, 'bound');
  assert.equal(h.requests.length, count);
});

test('配网页隐藏取消TCP，迟到连接不发凭据、不切网，回屏核原身份', async () => {
  const h = await open('wifi', { q: qr('wifi') });
  const sockets = [];
  h.wx.getConnectedWifi = options => options.success({ wifi: { SSID: 'test-device' } });
  h.wx.createTCPSocket = () => {
    const handlers = {}, socket = { writes: 0, closes: 0, handlers,
      onConnect: f => { handlers.connect = f; }, onMessage: f => { handlers.message = f; },
      onClose: f => { handlers.close = f; }, onError: f => { handlers.error = f; },
      connect() {}, write() { this.writes++; }, close() { this.closes++; } };
    sockets.push(socket); return socket;
  };
  h.page.setData({ homeSsid: 'test-router', homePassword: 'TEST-ONLY' });
  h.page.onSubmitCreds(); h.page.onHide();
  sockets[0].handlers.connect(); sockets[0].handlers.error(); h.clock.advance(60000);
  assert.equal(sockets[0].writes, 0);
  assert.equal(sockets[0].closes, 1);
  assert.equal(h.connections.length, 0);
  assert.equal(h.clock.pending.size, 0);
  h.page.onShow();
  assert.equal(h.requests.at(-1).method, 'GET');
  assert(h.requests.at(-1).url.includes('/bind/claim/status?'));
  h.requests.at(-1).success({ statusCode: 200, data: { status: 'bound' } });
  await flush();
  assert.equal(h.page.data.bindState, 'bound');
});

test('配网页隐藏停止认领重试和延迟切网，失败回包不改隐藏页面', async () => {
  const h = await open('wifi', { q: qr('wifi') });
  h.page.autoBind(); const original = h.requests.at(-1);
  h.page._later(h.page._generation, () => h.page.restoreHome(h.page._generation, 0), 2500);
  h.page.onHide(); const before = h.requests.length;
  original.fail({ errMsg: 'request:fail timeout' }); await flush(); h.clock.advance(60000);
  assert.equal(h.requests.length, before);
  assert.equal(h.connections.length, 0);
  assert.equal(h.clock.pending.size, 0);
});

test('系统WiFi设置同次往返保留内存密码，隐藏时不续调控制', async () => {
  const h = await open('wifi');
  h.page.setData({ homeSsid: 'router', homePassword: 'TEST-ONLY', step: 'creds' });
  h.wx.openSystemWifiSetting = () => { h.page.onHide(); };
  h.page.openWifiSettings(); h.clock.advance(60000); h.page.onShow();
  assert.equal(h.page.data.homePassword, 'TEST-ONLY');
  assert.equal(h.page.data.step, 'creds');
  assert.equal(h.connections.length, 0);
  assert.equal(JSON.stringify([...h.storage]).includes('TEST-ONLY'), false);
});

for (const outcome of ['success', 'fail']) {
  test(`绑定扫码${outcome}跨登录代次后结算标记且回屏重新认证`, async () => {
    const h = await open('bind');
    h.page.onScanBind(); h.page.onHide();
    h.app._sessionGeneration++;
    const before = h.requests.length;
    h.scans.at(-1)[outcome](outcome === 'success' ? { result: qr('bind') } : {});
    assert.equal(h.page._scanning, null);
    assert.equal(h.requests.length, before);
    h.page.onShow(); await flush();
    assert.equal(h.requests.at(-1).method, 'GET');
    assert(h.requests.at(-1).url.endsWith('/api/auth/me'));
    assert.equal(h.page._sessionGeneration, h.app._sessionGeneration);
  });
}

test('旧扫码完成不清除更新扫码的所有者', async () => {
  const h = await open('bind');
  h.page.onScanBind(); const first = h.scans.at(-1);
  h.page.onScanBind(); const owner = h.page._scanning;
  first.fail({}); assert.equal(h.page._scanning, owner);
  h.scans.at(-1).success({ result: qr('bind') });
  assert.equal(h.page._scanning, null);
  assert.equal(h.requests.at(-1).data.bind_code, 'ABC123');
});

test('配网页扫码相机往返继续本次扫码，账号变化则拒绝旧码', async () => {
  for (const changed of [false, true]) {
    const h = await open('wifi');
    h.page.onScanAp(); h.page.onHide();
    if (changed) h.app._sessionGeneration++;
    else h.page.onShow();
    h.scans.at(-1).success({ result: qr('wifi') });
    assert.equal(h.page._scanning, null);
    assert.equal(!!h.page._scan, !changed);
  }
});

test('旧WIFI码已发送结果未知，回屏保留下一步绑定且不重发凭据', async () => {
  const h = await open('wifi');
  const scan = h.load('utils/bind_identity.js').parseQr('WIFI:T:nopass;S:old-device;;', 'wifi');
  h.page.applyScan(scan);
  h.page.setData({ step: 'done', homeSsid: 'router', bindState: 'waiting' });
  h.page._phase = 'waiting';
  const before = h.requests.length;
  h.page.onHide(); h.page.onShow();
  assert.equal(h.page.data.step, 'done');
  assert.equal(h.page.data.bindState, 'manual');
  assert.equal(h.page.data.deviceMac, '');
  assert.equal(h.requests.length, before);
  const fs = require('node:fs');
  const path = require('node:path');
  const wxml = fs.readFileSync(path.resolve(__dirname, '../../weapp/pages/wifi/wifi.wxml'), 'utf8');
  assert(wxml.includes(`wx:if="{{step === 'done'}}"`));
  assert(wxml.includes(`wx:if="{{bindState !== 'bound' && !deviceMac}}"`));
  assert(wxml.includes('bindtap="onGoBind">下一步：绑定设备'));
});

test('已确认绑定跨后台保留完成状态，不因网络不可用再请求绑定', async () => {
  const h = await open('wifi', { q: qr('wifi') });
  h.page.autoBind();
  h.requests.at(-1).success({ statusCode: 200, data: { status: 'bound' } });
  await flush();
  const before = h.requests.length;
  h.page.onHide(); h.page.onShow(); h.clock.advance(60000);
  assert.equal(h.page.data.bindState, 'bound');
  assert.equal(h.page.data.step, 'done');
  assert.equal(h.page._phase, 'bound');
  assert.equal(h.requests.length, before);
  h.page.onGoControl();
  assert.equal(h.tabs.at(-1).url, '/pages/control/control');
});

test('已确认绑定遇登录代次改变仍重新认证，不沿用旧账号完成状态', async () => {
  const h = await open('wifi', { q: qr('wifi') });
  h.page.autoBind(); h.requests.at(-1).success({ statusCode: 200, data: { status: 'bound' } });
  await flush();
  h.page.onHide(); h.app._sessionGeneration++; h.page.onShow(); await flush();
  assert.equal(h.page.data.bindState, '');
  assert.equal(h.page.data.authReady, false);
  assert(h.requests.at(-1).url.endsWith('/api/auth/me'));
});
