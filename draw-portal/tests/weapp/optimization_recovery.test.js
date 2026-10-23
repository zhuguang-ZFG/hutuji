// 完整回包验证恢复入口，不用计时器或旧成功展示推断设备操作已完成。
const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, createClock, flush } = require('./harness');
const MAC = 'aa:bb:cc:dd:ee:01';
const ME = { phone_bound: true, active_device_mac: MAC, binding: { bound: true, device_mac: 'aa:bb:cc:**:**:**' } };
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
function firmwareBusy() {
  const clock = createClock(); const h = createWeapp({ clock });
  h.load('pages/firmware/firmware.js'); h.page._deviceMac = MAC;
  h.page.setData({ updateAvailable: true, latest: 'hutuji.1.0.50' });
  h.page._startUpgrade();
  reply(h.requests.at(-1), { status: 'error', detail: 'busy' });
  return { h, clock };
}
test('设备忙只核对一次原设备状态，重复点击不重复下发', async () => {
  const { h, clock } = firmwareBusy(); await flush();
  const posts = () => h.requests.filter(r => r.method === 'POST');
  const statuses = () => h.requests.filter(r => /firmware\/ota_status/.test(r.url));
  assert.equal(posts().length, 1);
  assert.equal(posts()[0].data.device_mac, MAC);
  assert.equal(statuses().length, 1);
  assert.match(statuses()[0].url, /ota_status\?device_mac=aa%3Abb/);
  assert.equal(h.page.data.upgrading, true);
  assert.match(h.page.data.progressLabel, /核对/);
  assert.match(h.page.data.statusTip, /设备忙|已有升级/);
  assert.equal(h.page.data.updateAvailable, false);
  h.page.onUpgrade(); h.page._startUpgrade(); h.page.onQueryUpgrade();
  assert.equal(posts().length, 1);
  assert.equal(statuses().length, 1);
  statuses()[0].success({ statusCode: 200, data: { state: 'upgrading', progress: 55, target_version: 'hutuji.1.0.50' } }); await flush();
  assert.equal(posts().length, 1);
  assert.equal(h.page.data.upgrading, true);
  assert.equal(h.page.data.progress, 55);
  assert.equal(h.page.data.targetVersion, 'hutuji.1.0.50');
  assert.equal(h.page.data.resultUncertain, false);
  clock.advance(2000);
  assert.equal(statuses().length, 2);
  h.page.onHide();
});
test('设备忙核对后设备并未升级时给忙碌提示，须重新检查才能再升级', async () => {
  const { h } = firmwareBusy(); await flush();
  const status = h.requests.filter(r => /firmware\/ota_status/.test(r.url)).at(-1);
  status.success({ statusCode: 200, data: { state: 'idle' } }); await flush();
  assert.equal(h.page.data.upgrading, false);
  assert.equal(h.page.data.resultUncertain, false);
  assert.equal(h.page.data.updateAvailable, false);
  assert.match(h.page.data.statusTip, /设备忙/);
  h.page.onUpgrade(); h.page._startUpgrade();
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 1);
  h.page.onCheck();
  const checks = h.requests.filter(r => /firmware\/check/.test(r.url));
  assert.equal(checks.length, 1);
  reply(checks[0], { update_available: true, latest: 'hutuji.1.0.50' }); await flush();
  assert.equal(h.page.data.updateAvailable, true);
  h.page.onHide();
});
test('设备忙核对失败回到设备忙，重新检查后才能再次确认下发', async () => {
  const { h } = firmwareBusy(); await flush();
  const status = h.requests.filter(r => /firmware\/ota_status/.test(r.url)).at(-1);
  status.fail({ errMsg: 'request:fail' }); await flush();
  assert.equal(h.page.data.upgrading, false);
  assert.equal(h.page.data.resultUncertain, false);
  assert.equal(h.page.data.updateAvailable, false);
  assert.equal(h.page._upgradeMac, '');
  assert.equal(h.page.data.statusTipClass, 'warn');
  assert.match(h.page.data.statusTip, /设备忙/);
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 1);
  // 未重新确认前不能再下发
  h.page._startUpgrade();
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 1);
  // 重新检查更新可用，结果仍可进入确认流程
  h.page.onCheck();
  const check = h.requests.filter(r => /firmware\/check/.test(r.url)).at(-1);
  reply(check, { update_available: true, latest: 'hutuji.1.0.50' }); await flush();
  assert.equal(h.page.data.updateAvailable, true);
  assert.equal(h.page.data.resultUncertain, false);
  h.page.onHide();
});
test('设备忙核对被离页打断后回屏仍按设备忙收口', async () => {
  const { h } = firmwareBusy(); await flush();
  const status = h.requests.filter(r => /firmware\/ota_status/.test(r.url)).at(-1);
  h.page.onHide();
  status.fail({ errMsg: 'request:fail' }); await flush();
  h.page.onShow(); await flush();
  reply(h.requests.at(-1), ME); await flush();
  const resumed = h.requests.filter(r => /firmware\/ota_status/.test(r.url));
  assert.equal(resumed.length, 2);
  reply(resumed.at(-1), { state: 'idle' }); await flush();
  assert.equal(h.page.data.upgrading, false);
  assert.equal(h.page.data.resultUncertain, false);
  assert.equal(h.page._upgradeMac, '');
  assert.match(h.page.data.statusTip, /设备忙/);
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 1);
  h.page.onHide();
});
test('下发未结算前不启动状态查询，避免读到上一次完成账本', async () => {
  const h = createWeapp({ clock: createClock() });
  h.load('pages/firmware/firmware.js'); h.page._deviceMac = MAC;
  h.page.setData({ updateAvailable: true, latest: 'hutuji.1.0.50' });
  h.page._startUpgrade();
  const post = h.requests.filter(r => r.method === 'POST').at(-1);
  const statuses = () => h.requests.filter(r => /firmware\/ota_status/.test(r.url)).length;
  assert.equal(statuses(), 0);
  h.page.onQueryUpgrade();
  h.page._pollOnce();
  assert.equal(statuses(), 0);
  reply(post, { status: 'started' }); await flush();
  assert.equal(statuses(), 1);
  assert.equal(h.page.data.resultUncertain, false);
  h.page.onHide();
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
