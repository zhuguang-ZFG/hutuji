// 2026-10-08 照片续作批第 5 项（控制）：透出设备 last_error 与完整操作失败原因；
// 速度读写等待覆盖 Portal 40 秒后台事务，失败不自动重复写，不把未确认当已完成。
const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

const initial = { x: 10000, y: 10000, z: 1200 };
const MAC = "aa:bb:cc:dd:ee:ff";

function readyControl() {
  const h = createWeapp();
  h.load("pages/control/control.js");
  h.page.setData({ bound: true, bindingChecked: true, linkOk: true, bindingMac: MAC,
    canSetSpeed: true, speedLoaded: true, speedRates: { ...initial },
    status: { state: "idle", plotter_sku: "nopaper" } });
  return h;
}
const change = (h, axis = "x", value = 8000) =>
  h.page.onSpeedChange({ currentTarget: { dataset: { axis } }, detail: { value } });
const plain = (v) => JSON.parse(JSON.stringify(v));

test("速度写入与读取的等待都覆盖 Portal 40 秒设备事务", () => {
  const h = readyControl();
  change(h);
  const post = h.requests.at(-1);
  assert.equal(post.method, "POST");
  assert.ok(post.timeout >= 60000, `速度写入超时 ${post.timeout}ms 覆盖不了 40s 后台事务`);
  h.page.setData({ speedSaving: false, speedLoaded: false });
  h.page._loadMachineSpeed();
  const get = h.requests.at(-1);
  assert.ok(get.url.includes("/api/settings/machine-speed?device_mac="));
  assert.ok(get.timeout >= 60000, `速度读取超时 ${get.timeout}ms 覆盖不了 40s 后台事务`);
});

test("速度写入超时后不自动重复写，也不把未确认状态当作已完成", async () => {
  const h = readyControl();
  change(h);
  assert.equal(h.requests.length, 1);
  h.requests[0].fail({ errMsg: "request:fail timeout" });
  await flush();
  assert.equal(h.requests.length, 1, "超时后不得自动重发速度写入");
  assert.equal(h.page.data.speedSaving, false);
  assert.equal(h.page.data.speedLoaded, false, "未确认的速度不能显示为已读回");
  assert.deepEqual(plain(h.page.data.speedRates), initial);
  assert.match(h.page.data.speedError, /尚未确认/);
  assert.match(h.toasts.at(-1).title, /尚未确认/);
  change(h);
  assert.equal(h.requests.length, 1, "未重新读回前滑块不可再写");
});

test("设备 error 态把 last_error 与 control_error 分别透出，非 error 态不显示", () => {
  const h = readyControl();
  const errored = h.page._deriveControls({ state: "error", connected: true,
    last_error: "出图失败：下载校验 CRC 不符", control_error: "归位未完成" });
  assert.equal(errored.deviceError, "出图失败：下载校验 CRC 不符");
  assert.equal(errored.controlError, "归位未完成");
  const idle = h.page._deriveControls({ state: "idle", connected: true, last_error: "陈旧原因" });
  assert.equal(idle.deviceError, "");
  assert.equal(h.page._deriveControls(null).deviceError, "");
});

test("状态轮询把 last_error 落屏，恢复后清除", () => {
  const h = readyControl();
  const errored = { state: "error", connected: true, last_error: "出图失败：换纸超时" };
  h.page._applyView(Object.assign({ status: errored }, h.page._deriveControls(errored)));
  assert.equal(h.page.data.deviceError, "出图失败：换纸超时");
  const idle = { state: "idle", connected: true };
  h.page._applyView(Object.assign({ status: idle }, h.page._deriveControls(idle)));
  assert.equal(h.page.data.deviceError, "");
});

test("操作失败透出完整原因到页面，下一次操作开始时清除", async () => {
  const h = readyControl();
  h.page.setData({ canAbort: true, manualLocked: false });
  const detail = "设备忙：正在整理纸张，请等本次换纸完成后再试（设备返回 busy，未执行停止）";
  h.page.fire("hutuji.abort");
  const req = h.requests.at(-1);
  assert.equal(req.data.tool, "hutuji.abort");
  req.success({ statusCode: 200, data: { status: "error", detail } });
  await flush();
  assert.equal(h.page.data.actionError, detail, "页面须显示完整失败原因，不止 toast");
  assert.equal(h.page.data.busyAction, "");
  h.page.setData({ manualLocked: false });
  h.page.fire("hutuji.manual", "pen_up");
  assert.equal(h.page.data.actionError, "", "新操作开始即清除上一条失败原因");
  h.requests.at(-1).success({ statusCode: 200, data: { status: "ok" } });
  await flush();
  assert.equal(h.page.data.actionError, "");
});

test("网络中断的操作按未确认透出，不自动重发", async () => {
  const h = readyControl();
  h.page.setData({ canAbort: true });
  h.page.fire("hutuji.abort");
  const count = h.requests.length;
  h.requests.at(-1).fail({ errMsg: "request:fail timeout" });
  await flush();
  assert.match(h.page.data.actionError, /尚未确认/);
  assert.equal(h.requests.filter((r) => r.data && r.data.tool === "hutuji.abort").length, 1);
  assert.ok(h.requests.length >= count);
});
