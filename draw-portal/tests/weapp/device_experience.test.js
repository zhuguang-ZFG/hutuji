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
  h.page.setData({ updateAvailable: true, latest: "hutuji.1.0.33" });
  h.page._startUpgrade(); reply(h.requests.at(-1), { status: "started" });
  return { h, clock };
}
test("升级结果未确认可只读核对原设备版本，不重复下发OTA", async () => {
  const { h } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "failed", reason: "status_timeout" }); await flush();
  assert.equal(h.page.data.resultUncertain, true);
  h.page._deviceMac = "11:22:33:44:55:66";
  h.page.onRecheckResult();
  assert.match(h.requests.at(-1).url, /firmware\/check\?device_mac=aa%3Abb/);
  reply(h.requests.at(-1), { current: "hutuji.1.0.33", reason: "up_to_date" }); await flush();
  assert.equal(h.page.data.resultUncertain, false); assert.equal(h.page.data.progressLabel, "升级完成");
  assert.equal(h.requests.filter((r) => r.url.endsWith("/firmware/upgrade")).length, 1);
});
test("重新核对读到旧版本时继续标待确认，不显示升级成功", async () => {
  const { h } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "failed", reason: "status_timeout" }); await flush();
  h.page.onRecheckResult(); reply(h.requests.at(-1), { current: "hutuji.1.0.32" }); await flush();
  assert.equal(h.page.data.resultUncertain, true); assert.equal(h.page.data.updateAvailable, false);
  assert(!h.toasts.some((item) => /升级成功/.test(item.title)));
});
test("升级等待有绝对时间上限，离页不能延长", async () => {
  const { h, clock } = firmware(); await flush();
  h.page.onHide(); clock.advance(10 * 60 * 1000 + 1);
  h.page._visible = true; h.page._pollOnce();
  assert.equal(h.page.data.upgrading, false); assert.equal(h.page.data.resultUncertain, true);
});
test("离开升级页后迟到的版本核对不显示完成", async () => {
  const { h } = firmware(); await flush();
  reply(h.requests.at(-1), { state: "failed", reason: "status_timeout" }); await flush();
  h.page.onRecheckResult(); const request = h.requests.at(-1);
  h.page.onHide(); reply(request, { current: "hutuji.1.0.33" }); await flush();
  assert.equal(h.page.data.resultUncertain, true);
  assert.notEqual(h.page.data.progressLabel, "升级完成");
});
