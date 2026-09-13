const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, createClock, flush } = require("./harness");
const me = { phone_bound: true, active_device_mac: "aa:bb:cc:dd:ee:ff",
  binding: { bound: true, device_mac: "aa:bb:cc…ff" } };
const state = { connected: true, ready: true, authorized: true, grbl_settings_ok: true,
  state: "done", grbl_state: "Idle", plotter_sku: "nopaper",
  speed: { axis_control: true, state: "done" } };
const reply = (h, data, statusCode = 200) => h.requests.at(-1).success({ statusCode, data });

test("冷启动控制页等待真实登录完成，再读状态与速度", async () => {
  const h = createWeapp({ clock: createClock() });
  h.app.onLaunch();
  h.load("pages/control/control.js");
  h.page.onShow();
  await flush();
  assert.equal(h.requests.length, 0);
  h.logins[0].success({ code: "code" });
  reply(h, { session: "current" });
  await flush();
  reply(h, me);
  await flush();
  reply(h, me); // 页面手机号闸
  await flush();
  reply(h, me); // 当前控制目标
  await flush();
  assert.equal(h.requests.at(-1).data.device_mac, me.active_device_mac);
  reply(h, { status: "ok", result: JSON.stringify(state) });
  await flush();
  assert(h.requests.at(-1).url.includes("machine-speed?device_mac=aa%3Abb"));
  reply(h, { status: "ok", rates: { x: 10000, y: 10000, z: 1200 } });
  await flush();
  assert.equal(h.page.data.canSetSpeed && h.page.data.speedLoaded, true);
});

test("慢网络不叠加轮询，读取失败清除旧的可操作状态", async () => {
  const clock = createClock();
  const h = createWeapp({ clock });
  h.load("pages/control/control.js");
  const first = h.page.refresh();
  assert.equal(h.page.refresh(), first);
  clock.advance(60000);
  assert.equal(h.requests.length, 1);
  reply(h, me);
  await flush();
  assert.equal(h.page.refresh(), first);
  clock.advance(60000);
  assert.equal(h.requests.length, 2);
  h.page.setData({ canPause: true, canConfirm: true, canRepeat: true, manualLocked: false });
  reply(h, { status: "error", detail: "设备未连接" });
  await flush();
  assert.equal(h.page.data.manualLocked, true);
  assert.equal(h.page.data.canPause || h.page.data.canConfirm || h.page.data.canRepeat, false);
  assert.match(h.page.data.statusError, /未连接/);
  assert.equal(clock.pending.size, 1);
  h.page.onHide();
  assert.equal(clock.pending.size, 0);
});

test("打码编号缺少明确目标时拒绝控制，不猜测设备", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/control/control.js");
  h.page.refresh();
  reply(h, { binding: me.binding, devices: [{ device_mac: "aa:bb:cc:dd:ee:ff" }] });
  await flush();
  assert.equal(h.requests.length, 1);
  assert.equal(h.page.data.bindingMac, "");
  assert.match(h.page.data.statusError, /设备信息/);
});

test("步距只有设备接受后才切换，离页的拒绝回包不弹提示", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/control/control.js");
  h.page.setData({ bindingMac: me.active_device_mac, manualLocked: false });
  h.page.onStepToggle();
  reply(h, { status: "error", detail: "设备拒绝" });
  await flush();
  assert.equal(h.page.data.step, 10);
  h.page.setData({ manualLocked: false });
  h.page.onStepToggle();
  h.page.onHide();
  const count = h.toasts.length;
  reply(h, { status: "error", detail: "旧错误" });
  await flush();
  assert.equal(h.toasts.length, count);
  assert.equal(h.page.data.step, 10);
});

test("固件页离开后的旧状态回包不能重新启动轮询", async () => {
  const clock = createClock();
  const h = createWeapp({ clock });
  h.load("pages/firmware/firmware.js");
  h.page._pollOnce();
  h.page._pollOnce();
  assert.equal(h.requests.length, 1);
  h.page.onHide();
  reply(h, { state: "upgrading", progress: 10 });
  await flush();
  clock.advance(30000);
  assert.equal(h.requests.length, 1);
  assert.equal(clock.pending.size, 0);
});

test("旧手机号不足回包不会把新登录踢到授权页", async () => {
  const h = createWeapp();
  const pending = h.load("utils/api.js").request("/api/draw/saved");
  const rejected = assert.rejects(pending);
  h.app.logout();
  reply(h, { detail: { code: "phone_required", message: "请授权" } }, 403);
  await rejected;
  assert.equal(h.tabs.length, 0);
});

for (const route of ["chat", "xiaopai"]) {
  test(`${route} 账号读取失败显示重试，不永久加载或展示上个账号设置`, async () => {
    const h = createWeapp();
    h.load(`pages/${route}/${route}.js`);
    h.page.setData({ selectedVoice: "previous-user", selectedModel: "previous-model" });
    h.page.onShow();
    await flush();
    h.requests.at(-1).fail({ errMsg: "request:fail" });
    await flush();
    assert.match(h.page.data.loadError || h.page.data.error, /重试/);
    if (route === "xiaopai") {
      assert.equal(h.page.data.loading, false);
      assert.equal(h.page.data.selectedVoice, "");
      assert.equal(h.page.data.selectedModel, "");
    }
    h.page.onShow();
    await flush();
    reply(h, me);
    await flush();
    if (route === "chat") assert.equal(h.page.data.enabled, true);
    else assert(h.requests.at(-1).url.endsWith("/api/settings/xiaopai"));
  });
}
