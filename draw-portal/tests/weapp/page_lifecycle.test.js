const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, createClock, flush } = require("./harness");
const me = { phone_bound: true, active_device_mac: "aa:bb:cc:dd:ee:ff",
  binding: { bound: true, device_mac: "aa:bb:cc…ff" } };
const state = { connected: true, ready: true, authorized: true, grbl_settings_ok: true,
  state: "done", grbl_state: "Idle", plotter_sku: "nopaper",
  speed: { axis_control: true, state: "done" } };
const reply = (h, data, statusCode = 200) => h.requests.at(-1).success({ statusCode, data });

test("完成后不把固件继续累加的时间显示为绘制耗时，进行中的时间仍显示", () => {
  const h = createWeapp();
  h.load("pages/control/control.js");
  for (const jobState of ["streaming", "paused"]) {
    const view = h.page._deriveControls({ ...state, state: jobState, elapsed_ms: 65000, eta_ms: 120000 });
    assert.equal(view.elapsedLabel, "1:05");
    assert.equal(view.etaLabel, "2:00");
  }
  for (const jobState of ["idle", "done", "error", "aborted", "previewing", "awaiting_confirmation"]) {
    const view = h.page._deriveControls({ ...state, state: jobState, elapsed_ms: 9247310, eta_ms: 9247310 });
    assert.equal(view.elapsedLabel, "", jobState);
    assert.equal(view.etaLabel, "", jobState);
  }
});

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
  reply(h, me); // 页面手机号闸；控制目标直接复用该 /me，不再重复请求
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

test("升级会话超时停止轮询并如实提示结果未确认", async () => {
  const clock = createClock();
  const h = createWeapp({ clock });
  h.load("pages/firmware/firmware.js");
  h.page.setData({ upgrading: true });
  h.page._pollOnce();
  reply(h, { state: "failed", reason: "status_timeout", progress: 20 });
  await flush();
  assert.equal(h.page.data.upgrading, false);
  assert.equal(h.page.data.progressLabel, "结果待确认");
  assert.match(h.toasts.at(-1).title, /未确认/);
  clock.advance(30000);
  assert.equal(h.requests.length, 1);
});

test("固件下发超时保留待确认状态，轮询继续锁定原设备", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/firmware/firmware.js");
  h.page._deviceMac = me.active_device_mac;
  h.page.setData({ updateAvailable: true, latest: "hutuji.1.0.17" });
  h.page._startUpgrade();
  const request = h.requests.at(-1);
  request.fail({ errMsg: "request:fail timeout" });
  await flush();
  assert.equal(h.page.data.upgrading, true);
  assert.match(h.requests.at(-1).url, /ota_status/);
  assert.match(h.requests.at(-1).url, /device_mac=aa%3Abb/);
  assert.equal(request.data.device_mac, me.active_device_mac);
  assert.equal(h.toasts.some((toast) => /失败/.test(toast.title)), false);
  h.page.onHide();
});

test("固件页正常加载使用明确目标，检查失败清除旧升级按钮", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/firmware/firmware.js");
  h.page.onShow();
  await flush();
  reply(h, me); // 手机号闸 /me；绑定信息随闸复用，不再二次请求
  await flush();
  assert.match(h.requests.at(-1).url, /firmware\/check\?device_mac=aa%3Abb/);
  reply(h, { update_available: true, current: "hutuji.1.0.16", latest: "hutuji.1.0.17" });
  await flush();
  assert.equal(h.page.data.updateAvailable, true);
  h.page.onCheck();
  reply(h, { detail: "设备绑定已变化，请刷新后重试" }, 409);
  await flush();
  assert.equal(h.page.data.updateAvailable, false);
  h.page.onHide();
});

test("升级期间切换活动设备再回屏，仍查询原升级设备而不丢进度", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/firmware/firmware.js");
  h.page.onShow(); await flush(); reply(h, me); await flush();
  reply(h, { update_available: true, latest: "hutuji.1.0.17" }); await flush();
  h.page._startUpgrade(); reply(h, { status: "started" }); await flush();
  h.page.onHide(); h.page.onShow(); await flush();
  reply(h, { ...me, active_device_mac: "11:22:33:44:55:66" }); await flush();
  assert.equal(h.page.data.upgrading, true);
  assert.match(h.requests.at(-1).url, /ota_status\?device_mac=aa%3Abb/);
  h.page.onHide();
});

for (const failureAt of ['account', 'binding']) {
  test(`固件页 ${failureAt} 读取失败可明确重试，等待期间不展示旧升级入口`, async () => {
    const h = createWeapp({ clock: createClock() });
    h.load("pages/firmware/firmware.js");
    h.page._firmwareSession = h.app._sessionGeneration;
    h.page.setData({ updateAvailable: true });
    h.page.onShow(); await flush();
    assert.equal(h.page.data.updateAvailable, false);
    if (failureAt === 'binding') {
      // 绑定信息随手机号闸的 /me 一起来（同程复用，不再二次请求）；此场景的失败点即固件检查请求。
      reply(h, me); await flush();
      assert.equal(h.requests.length, 2);
      h.requests.at(-1).fail(); await flush();
      assert.equal(h.page.data.checking, false);
      assert.equal(h.page.data.updateAvailable, false);
      assert.equal(h.page.data.statusTipClass, "warn");
      assert.notEqual(h.page.data.statusTip, "");
      return;
    }
    h.requests.at(-1).fail(); await flush();
    assert.match(h.page.data.loadError, /重试/);
    assert.equal(h.page.data.checking, false);
  });
}

test("固件页缺少明确目标不检查，升级确认窗口不能切换到另一设备", () => {
  const h = createWeapp();
  h.load("pages/firmware/firmware.js");
  h.page.onCheck();
  assert.equal(h.requests.length, 0);
  h.page._deviceMac = me.active_device_mac;
  h.page.setData({ updateAvailable: true, latest: "hutuji.1.0.17" });
  h.page.onUpgrade();
  h.page._deviceMac = "11:22:33:44:55:66";
  h.modals.at(-1).success({ confirm: true });
  assert.equal(h.requests.length, 0);
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

test("全量刷新切换设备后调速按钮不被视图 diff 缓存卡住", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/control/control.js");
  h.page.onShow();
  await flush();
  reply(h, me);
  await flush();
  reply(h, { status: "ok", result: JSON.stringify(state) });
  await flush();
  assert.equal(h.page.data.canSetSpeed, true);
  // 不经过 onShow 的全量刷新（下拉）换到另一台同样可调的设备：
  // 设备切换直写 canSetSpeed=false，新状态仍推导 true，diff 缓存若不失效会漏发。
  const me2 = Object.assign({}, me, { active_device_mac: "11:22:33:44:55:66" });
  h.page.refresh(false, false, me2);
  await flush();
  reply(h, { status: "ok", result: JSON.stringify(state) });
  await flush();
  assert.equal(h.page.data.canSetSpeed, true);
  h.page.onHide();
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
