// 完整回包验证恢复入口，不用计时器或旧成功展示推断设备操作已完成。
const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, createClock, flush } = require('./harness');
const MAC = 'aa:bb:cc:dd:ee:01';
const reply = (r, data) => r.success({ statusCode: 200, data });
async function firmware() {
  const h = createWeapp({ clock: createClock() });
  h.load('pages/firmware/firmware.js'); h.page._deviceMac = MAC;
  h.page.setData({ updateAvailable: true, latest: 'hutuji.1.0.47' });
  h.page._startUpgrade(); reply(h.requests.at(-1), { status: 'unknown' }); await flush();
  return h;
}
test('下发不明但设备报告下载失败时解释核对状态，不宣称失败或重发', async () => {
  const h = await firmware();
  reply(h.requests.at(-1), { state: 'upgrading', progress: 0, firmware_version: 'hutuji.1.0.46',
    ota: { state: 'failed', reason: 'download_failed' } }); await flush();
  assert.equal(h.page.data.upgrading, true);
  assert.match(h.page.data.progressLabel, /核对/);
  assert.match(h.page.data.statusTip, /下载失败/);
  assert.equal(h.page.data.updateAvailable, false);
  const count = h.requests.length;
  h.page.onQueryUpgrade(); h.page.onQueryUpgrade();
  assert.equal(h.requests.length, count + 1);
  assert.match(h.requests.at(-1).url, /ota_status\?device_mac=aa%3Abb/);
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 1);
});
test('明确下载失败给联网与设备重启指引，须重新检查后才能再升级', async () => {
  const h = await firmware();
  reply(h.requests.at(-1), { state: 'failed', reason: 'download_failed', firmware_version: 'hutuji.1.0.46' }); await flush();
  assert.equal(h.page.data.upgrading, false);
  assert.equal(h.page.data.updateAvailable, false);
  assert.match(h.page.data.statusTip, /联网/);
  assert.match(h.page.data.statusTip, /重启/);
  const count = h.requests.length; h.page._startUpgrade();
  assert.equal(h.requests.length, count);
  h.page.onCheck(); assert.match(h.requests.at(-1).url, /firmware\/check/);
});
test('真实进度与重启阶段有区分，无效百分比不污染显示', async () => {
  const h = await firmware();
  reply(h.requests.at(-1), { state: 'upgrading', progress: 100, ota: { state: 'upgrading', progress: 100 } }); await flush();
  assert.match(h.page.data.progressLabel, /重启|版本/);
  h.page.onQueryUpgrade(); reply(h.requests.at(-1), { state: 'upgrading', progress: NaN }); await flush();
  assert.equal(h.page.data.progress, 100);
  h.page.onHide(); const count = h.requests.length; h.page.onQueryUpgrade();
  assert.equal(h.requests.length, count);
});
test('控制页显示升级失败原因而非裸英文OTA状态', () => {
  const h = createWeapp(); const progress = h.load('utils/job_progress.js');
  const view = progress.deriveProgressView({ state: 'idle', ota: { state: 'failed', reason: 'download_failed', progress: 0 } });
  assert.match(view.otaLine, /下载失败/);
  assert.doesNotMatch(view.otaLine, /OTA failed/);
});
test('选择开放Wi-Fi不沿用密码，手动输入网络恢复密码入口', () => {
  const h = createWeapp(); h.load('pages/wifi/wifi.js');
  h.page.setData({ authReady: true, apList: [{ ssid: '开放网络', authmode: 0 }],
    homeSsid: '开放网络', homePassword: 'old-password', showPassword: true });
  h.page.onPickAp({ currentTarget: { dataset: { ssid: '开放网络' } } });
  assert.equal(h.page.data.wifiOpen, true); assert.equal(h.page.data.homePassword, '');
  h.page.onManualSsid(); assert.equal(h.page.data.wifiOpen, false);
});
test('开放Wi-Fi发送及手机回连不带旧密码，切换加密网络恢复密码输入', () => {
  const h = createWeapp(); h.load('pages/wifi/wifi.js');
  h.page.setData({ authReady: true, apList: [{ ssid: '开放网络', authmode: 0 }, { ssid: '加密网络', authmode: 3 }] });
  h.page.onPickAp({ currentTarget: { dataset: { ssid: '开放网络' } } });
  h.page.onPwdInput({ detail: { value: 'late-old-input' } });
  assert.equal(h.page.data.homePassword, '');
  let request, connection;
  h.load('utils/wifi_transport.js').request = options => { request = options; };
  h.wx.connectWifi = options => { connection = options; };
  const generation = h.page._begin(); h.page.submitCreds(generation, 0);
  assert.equal(request.body.password, '');
  h.page.restoreHome(generation, 0); assert.equal(connection.password, '');
  h.page.onPickAp({ currentTarget: { dataset: { ssid: '加密网络' } } });
  assert.equal(h.page.data.wifiOpen, false);
  h.page.onPwdInput({ detail: { value: 'new-password' } });
  assert.equal(h.page.data.homePassword, 'new-password');
});
test('配网提交处理中重复触发不取消连接或再发凭据', () => {
  const h = createWeapp(); h.load('pages/wifi/wifi.js');
  h.page.setData({ authReady: true, step: 'creds', homeSsid: 'home' });
  let calls = 0; h.wx.getConnectedWifi = () => { calls++; };
  h.page.onSubmitCreds(); const generation = h.page._generation;
  h.page.onSubmitCreds();
  assert.equal(calls, 1); assert.equal(h.page._generation, generation);
});
