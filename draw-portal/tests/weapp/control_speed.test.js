const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

const initial = { x: 10000, y: 10000, z: 1200 };

function readyControl() {
  const h = createWeapp();
  h.load("pages/control/control.js");
  h.page.setData({ bound: true, linkOk: true, bindingMac: "aa:bb:cc:dd:ee:ff",
    canSetSpeed: true, speedLoaded: true, speedRates: { ...initial },
    status: { state: "idle", plotter_sku: "nopaper" } });
  return h;
}
const change = (h, axis = "x", value = 8000) =>
  h.page.onSpeedChange({ currentTarget: { dataset: { axis } }, detail: { value } });
const reply = (h, data, statusCode = 200) => h.requests.at(-1).success({ statusCode, data });
const confirmed = (axis = "x", rate = 8000) =>
  ({ status: "ok", axis, rate, rates: { ...initial, [axis]: rate } });
const plain = (v) => JSON.parse(JSON.stringify(v));

test("X/Y 默认 10000，读回前不显示为实机值", () => {
  const h = createWeapp();
  h.load("pages/control/control.js");
  assert.equal(h.page.data.speedRates.x, 10000);
  assert.equal(h.page.data.speedRates.y, 10000);
  assert.equal(h.page.data.speedLoaded, false);
  assert.equal(h.page.data.canSetSpeed, false);
});

test("速度被设备拒绝时显示原因并显式还原滑块", async () => {
  const h = readyControl();
  const updates = [];
  const setData = h.page.setData;
  h.page.setData = (data) => { updates.push(data); setData(data); };
  change(h);
  h.requests.at(-1).success({ statusCode: 502, data: { detail: "写字机未完成速度调整" } });
  await flush();
  assert.deepEqual(plain(h.page.data.speedRates), initial);
  assert.equal(h.page.data.speedSaving, false);
  assert.equal(h.page.data.speedLoaded, false);
  assert(updates.some((x) => x.speedRates && x.speedRates.x === 10000));
  assert.equal(h.toasts.at(-1).title, "写字机未完成速度调整");
});

test("速度回包必须含本次设备确认值", async () => {
  const h = readyControl();
  change(h);
  h.requests.at(-1).success({ statusCode: 200, data: { status: "ok", result: '{"error":"busy"}' } });
  await flush();
  assert.deepEqual(plain(h.page.data.speedRates), initial);
  assert.equal(h.toasts.at(-1).icon, "none");
});

test("退出登录后旧速度回包不能影响新会话", async () => {
  const h = readyControl();
  change(h);
  h.app.logout();
  reply(h, confirmed());
  await flush();
  assert.deepEqual(plain(h.page.data.speedRates), initial);
  assert.equal(h.toasts.length, 0);
});

test("页面隐藏后旧请求不能弹成功提示", async () => {
  const h = readyControl();
  change(h);
  h.page.onHide();
  reply(h, confirmed());
  await flush();
  assert.deepEqual(plain(h.page.data.speedRates), initial);
  assert.equal(h.toasts.length, 0);
});

test("设备确认速度后才显示成功", async () => {
  const h = readyControl();
  change(h);
  reply(h, confirmed());
  await flush();
  assert.equal(h.page.data.speedRates.x, 8000);
  assert.equal(h.toasts.at(-1).icon, "success");
});

for (const [axis, rate] of [["x", 8000], ["y", 9000], ["z", 1000]]) {
  test(`拖动 ${axis} 只提交该轴，确认后保留另外两轴`, async () => {
    const h = readyControl();
    change(h, axis, rate);
    assert.deepEqual(plain(h.requests.at(-1).data), { axis, rate, device_mac: "aa:bb:cc:dd:ee:ff" });
    assert.deepEqual(plain(h.page.data.speedRates), initial);
    reply(h, confirmed(axis, rate));
    await flush();
    assert.deepEqual(plain(h.page.data.speedRates), { ...initial, [axis]: rate });
    assert.equal(h.toasts.at(-1).icon, "success");
  });
}

for (const bad of [
  { ...confirmed(), axis: "y" },
  { ...confirmed(), rates: { ...initial, x: 9000 } },
  { ...confirmed(), rates: { x: 8000, y: 10000 } },
]) {
  test("速度回包缺少匹配轴与三轴读回时不报成功 " + JSON.stringify(bad), async () => {
    const h = readyControl();
    change(h);
    reply(h, bad);
    await flush();
    assert.deepEqual(plain(h.page.data.speedRates), initial);
    assert.equal(h.toasts.at(-1).icon, "none");
  });
}

test("设备切换后旧调速回包不更新新状态", async () => {
  const h = readyControl();
  change(h);
  h.page.setData({ bindingMac: "11:22:33:44:55:66" });
  reply(h, confirmed());
  await flush();
  assert.deepEqual(plain(h.page.data.speedRates), initial);
  assert.equal(h.toasts.length, 0);
});

test("调速期间再次拖动不会覆盖另外一个轴", () => {
  const h = readyControl();
  change(h);
  change(h, "z", 1000);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].data.axis, "x");
});

test("加载使用只读接口并显示三轴实际值", async () => {
  const h = readyControl();
  h.page.setData({ speedLoaded: false });
  h.page._loadMachineSpeed();
  assert(h.requests.at(-1).url.includes("machine-speed?device_mac=aa%3Abb"));
  assert.equal(h.requests.at(-1).method, "GET");
  assert.equal(h.page.data.speedLoading, true);
  reply(h, { status: "ok", rates: { x: 9500, y: 10500, z: 600 } });
  await flush();
  assert.deepEqual(plain(h.page.data.speedRates), { x: 9500, y: 10500, z: 600 });
  assert.equal(h.page.data.speedLoaded, true);
});

test("退出页面后晚到的读回不会恢复参数", async () => {
  const h = readyControl();
  h.page.setData({ speedLoaded: false });
  h.page._loadMachineSpeed();
  h.page.onHide();
  reply(h, { status: "ok", rates: { x: 9500, y: 10500, z: 600 } });
  await flush();
  assert.equal(h.page.data.speedLoaded, false);
  assert.deepEqual(plain(h.page.data.speedRates), initial);
});

test("旧固件、未就绪或正在调速时禁用三轴控制", () => {
  const h = readyControl();
  const status = { connected: true, ready: true, authorized: true, grbl_settings_ok: true,
    state: "idle", plotter_sku: "nopaper", speed: { axis_control: true, state: "done" } };
  assert.equal(h.page._deriveControls(status).canSetSpeed, true);
  for (const patch of [{ speed: {} }, { ready: false }, { grbl_settings_ok: false },
    { plotter_sku: "paper" }, { state: "streaming" }, { speed: { axis_control: true, state: "pending" } }]) {
    assert.equal(h.page._deriveControls({ ...status, ...patch }).canSetSpeed, false);
  }
});
