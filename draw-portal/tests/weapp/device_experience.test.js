const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, createClock, flush } = require("./harness");
const paper = require("../../weapp/utils/paper_config");
const MAC = "aa:bb:cc:dd:ee:ff";
const ME = { phone_bound: true, active_device_mac: MAC, binding: { bound: true, device_mac: "aa:bb:cc:**:**:**" } };
const config = paper.defaultFor("nopaper");
const response = (value = config) => ({ config: value, marker: paper.marker(value), device_mac: MAC, capable: true, online: true });
const reply = (r, value) => r.success({ statusCode: 200, data: value });
async function showPaper(h, result = response()) {
  h.page.onShow(); await flush(); reply(h.requests.at(-1), ME); await flush(); reply(h.requests.at(-1), result); await flush();
}

test("纸张设置未改变不提交，编辑可还原", async () => {
  const h = createWeapp(); h.load("pages/paper/paper.js"); await showPaper(h);
  const count = h.requests.length; h.page.onSave(); assert.equal(h.requests.length, count);
  h.page.onInput({ currentTarget: { dataset: { field: "margin_mm" } }, detail: { value: "12" } });
  assert.equal(h.page.data.dirty, true); h.page.onResetForm();
  assert.equal(h.page.data.form.margin_mm, 10); assert.equal(h.page.data.dirty, false);
});
test("纸张编辑离页后保留，设备配置变化则不套用旧草稿", async () => {
  const h = createWeapp(); h.load("pages/paper/paper.js"); await showPaper(h);
  h.page.onInput({ currentTarget: { dataset: { field: "margin_mm" } }, detail: { value: "12" } });
  h.page.onHide(); await showPaper(h);
  assert.equal(Number(h.page.data.form.margin_mm), 12); assert.equal(h.page.data.dirty, true);
  h.page.onHide(); await showPaper(h, response({ ...config, margin_mm: 15 }));
  assert.equal(h.page.data.form.margin_mm, 15); assert.equal(h.page.data.dirty, false);
  assert.match(h.page.data.hint, /变化/);
});

test("Wi-Fi密码显隐不落存储，离页自动收起", () => {
  const h = createWeapp(); h.load("pages/wifi/wifi.js");
  h.page.setData({ step: "creds", homePassword: "sensitive-password" });
  h.page.onTogglePassword(); assert.equal(h.page.data.showPassword, true);
  h.page.onHide(); assert.equal(h.page.data.showPassword, false);
  assert.equal(h.page.data.homePassword, "sensitive-password");
  assert(!JSON.stringify([...h.storage]).includes("sensitive-password"));
});
test("Wi-Fi绑定完成后可以直接进入创作，未完成不能跳过绑定", () => {
  const h = createWeapp(); h.load("pages/wifi/wifi.js");
  h.page.onGoCreate(); assert.equal(h.tabs.length, 0);
  h.page.setData({ bindState: "bound" }); h.page.onGoCreate();
  assert.equal(h.tabs.at(-1).url, "/pages/index/index");
});
test("未扫码时Wi-Fi初始化失败不弹空热点名", () => {
  const h = createWeapp(); h.load("pages/wifi/wifi.js"); h.page.onConnectFail();
  assert.equal(h.modals.length, 0); assert.match(h.page.data.wifiError, /Wi-Fi/);
  assert.equal(h.page.data.step, "scan");
});
test("附近Wi-Fi去重取强信号，未知信号不排最前", () => {
  const h = createWeapp(); h.load("pages/wifi/wifi.js"); let request;
  h.load("utils/wifi_transport.js").request = (options) => { request = options; };
  const generation = h.page._begin(); h.page.scanAps(generation);
  request.done({ status: 200, data: { aps: [{ ssid: "无信号" }, { ssid: "家庭", rssi: -80 }, { ssid: "家庭", rssi: -30 }] } });
  assert.equal(h.page.data.apList.length, 2); assert.equal(h.page.data.apList[0].rssi, -30);
});
test("选择另一个Wi-Fi清除旧密码并恢复隐藏", () => {
  const h = createWeapp(); h.load("pages/wifi/wifi.js");
  h.page.setData({ homeSsid: "旧网络", homePassword: "old-secret", showPassword: true });
  h.page.onPickAp({ currentTarget: { dataset: { ssid: "新网络" } } });
  assert.equal(h.page.data.homePassword, ""); assert.equal(h.page.data.showPassword, false);
});

function firmware() {
  const clock = createClock(); const h = createWeapp({ clock });
  h.load("pages/firmware/firmware.js"); h.page._deviceMac = MAC;
  // 回屏时不因首帧会话初始化清掉本测试手工建立的升级目标。
  h.page._firmwareSession = h.app._sessionGeneration;
  h.page.setData({ updateAvailable: true, latest: "hutuji.1.0.33" });
  h.page._startUpgrade(); reply(h.requests.at(-1), { status: "started" });
  return { h, clock };
}
test("升级结果未确认可只读核对原设备版本，不重复下发OTA", async () => {
  const { h } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "failed", reason: "status_timeout" }); await flush();
  assert.equal(h.page.data.resultUncertain, true);
  // 活动设备已切走也只用原升级目标查询，绝不走只允许当前设备的 check。
  h.page._deviceMac = "11:22:33:44:55:66";
  h.page.onRecheckResult();
  const recheck = h.requests.at(-1);
  assert.match(recheck.url, /firmware\/ota_status\?device_mac=aa%3Abb/);
  reply(recheck, { state: "ok", target_version: "hutuji.1.0.33", firmware_version: "hutuji.1.0.33" }); await flush();
  assert.equal(h.page.data.resultUncertain, false); assert.equal(h.page.data.progressLabel, "升级完成");
  assert.equal(h.page.data.current, "hutuji.1.0.33");
  assert.equal(h.requests.filter((r) => r.url.endsWith("/firmware/upgrade")).length, 1);
  assert.equal(h.requests.filter((r) => /firmware\/check/.test(r.url)).length, 0);
});
test("重新核对读到旧版本时继续标待确认，不显示升级成功", async () => {
  const { h } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "failed", reason: "status_timeout" }); await flush();
  h.page.onRecheckResult();
  const recheck = h.requests.at(-1);
  assert.match(recheck.url, /firmware\/ota_status/);
  reply(recheck, { state: "idle", firmware_version: "hutuji.1.0.32" }); await flush();
  assert.equal(h.page.data.resultUncertain, true); assert.equal(h.page.data.updateAvailable, false);
  assert.equal(h.page.data.current, "hutuji.1.0.32");
  assert.equal(h.page.data.progressLabel, "结果待确认");
  assert(!h.toasts.some((item) => /升级成功/.test(item.title)));
});
test("升级等待有绝对时间上限，离页不能延长", async () => {
  const { h, clock } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "upgrading", progress: 10 }); await flush();
  assert.equal(h.page.data.upgrading, true);
  h.page.onHide(); clock.advance(10 * 60 * 1000 + 1);
  h.page.onShow(); await flush();
  reply(h.requests.at(-1), ME); await flush();
  assert.equal(h.page.data.upgrading, false); assert.equal(h.page.data.resultUncertain, true);
  assert.match(h.page.data.statusTip, /未确认/);
});
test("轮询回包不能续期升级等待上限", async () => {
  const { h, clock } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "upgrading", progress: 10 }); await flush();
  clock.advance(2000);
  const poll = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).at(-1);
  // 慢查询跨过绝对上限后才回包：这条观察不得重开窗口。
  clock.advance(10 * 60 * 1000);
  reply(poll, { state: "upgrading", progress: 60 }); await flush();
  const before = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).length;
  clock.advance(2000); await flush();
  assert.equal(h.page.data.upgrading, false);
  assert.equal(h.page.data.resultUncertain, true);
  assert.equal(h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).length, before);
  h.page.onHide();
});
test("换代后旧升级POST收尾不能打开新POST的查询门", async () => {
  const clock = createClock(); const h = createWeapp({ clock });
  h.load("pages/firmware/firmware.js"); h.page._deviceMac = MAC;
  h.page._firmwareSession = h.app._sessionGeneration;
  h.page.setData({ updateAvailable: true, latest: "hutuji.1.0.33" });
  h.page._startUpgrade();
  const first = h.requests.at(-1);
  assert.match(first.url, /firmware\/upgrade/);
  // 登录换代后回屏，页面复位并重新检查，再由用户确认发起第二次下发。
  h.app._sessionGeneration++;
  h.page.onShow(); await flush();
  reply(h.requests.at(-1), ME); await flush();
  const cold = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).at(-1);
  reply(cold, { state: "idle" }); await flush();
  reply(h.requests.filter((r) => /firmware\/check/.test(r.url)).at(-1),
    { update_available: true, current: "hutuji.1.0.32", latest: "hutuji.1.0.33" }); await flush();
  h.page._startUpgrade();
  const second = h.requests.at(-1);
  assert.notEqual(second, first);
  // 旧 POST 此刻才回包：不得清掉新 POST 的门，也不得提前发状态查询。
  const before = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).length;
  reply(first, { status: "started" }); await flush();
  h.page.onQueryUpgrade(); await flush();
  assert.equal(h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).length, before);
  // 新 POST 自己结算后才进入状态核对。
  reply(second, { status: "started" }); await flush();
  assert.equal(h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).length, before + 1);
  h.page.onHide();
});
test("离开升级页后迟到的版本核对不显示完成", async () => {
  const { h } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "failed", reason: "status_timeout" }); await flush();
  h.page.onRecheckResult(); const request = h.requests.at(-1);
  assert.match(request.url, /firmware\/ota_status/);
  h.page.onHide();
  reply(request, { state: "ok", firmware_version: "hutuji.1.0.33", target_version: "hutuji.1.0.33" }); await flush();
  assert.equal(h.page.data.resultUncertain, true);
  assert.notEqual(h.page.data.progressLabel, "升级完成");
});
test("超时后重新核对到仍在升级时重新开窗并恢复轮询", async () => {
  const { h, clock } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "upgrading", progress: 10 }); await flush();
  assert.equal(h.page.data.upgrading, true);
  h.page._pollDeadline = Date.now() - 1;
  h.page.onQueryUpgrade(); await flush();
  assert.equal(h.page.data.resultUncertain, true);
  h.page.onRecheckResult();
  const recheck = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).at(-1);
  reply(recheck, { state: "upgrading", progress: 40 }); await flush();
  assert.equal(h.page.data.upgrading, true);
  assert.equal(h.page.data.resultUncertain, false);
  const before = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).length;
  clock.advance(2000);
  assert.equal(h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).length, before + 1);
  assert.equal(h.page.data.upgrading, true);
  h.page.onHide();
});
test("升级完成后的版本校对使用当前活动设备", async () => {
  const { h, clock } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "upgrading", progress: 90 }); await flush();
  clock.advance(2000);
  const poll = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).at(-1);
  reply(poll, { state: "ok", target_version: "hutuji.1.0.33", firmware_version: "hutuji.1.0.33" }); await flush();
  assert.equal(h.page.data.progressLabel, "升级完成");
  clock.advance(800); await flush();
  const me = h.requests.filter((r) => /\/api\/auth\/me/.test(r.url)).at(-1);
  assert.ok(me, "完成校对前应重读当前活动设备");
  reply(me, { ...ME, active_device_mac: "11:22:33:44:55:66" }); await flush();
  const check = h.requests.filter((r) => /firmware\/check/.test(r.url)).at(-1);
  assert.ok(check);
  assert.match(check.url, /device_mac=11%3A22%3A33%3A44%3A55%3A66/);
  h.page.onHide();
});
test("冷启动状态缺字段时不盲发检查更新", async () => {
  const h = createWeapp(); h.load("pages/firmware/firmware.js");
  h.page.onShow(); await flush();
  reply(h.requests.at(-1), ME); await flush();
  const status = h.requests.filter((r) => /firmware\/ota_status/.test(r.url)).at(-1);
  assert.ok(status);
  reply(status, {}); await flush();
  assert.equal(h.requests.filter((r) => /firmware\/check/.test(r.url)).length, 0);
  assert.equal(h.page.data.checking, false);
  assert.match(h.page.data.statusTip, /状态/);
});
