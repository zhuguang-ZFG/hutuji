const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

function readyControl() {
  const h = createWeapp();
  h.load("pages/control/control.js");
  h.page.setData({ bound: true, linkOk: true, status: { state: "idle", plotter_sku: "nopaper" } });
  return h;
}

test("速度被设备拒绝时显示原因并显式还原滑块", async () => {
  const h = readyControl();
  const updates = [];
  const setData = h.page.setData;
  h.page.setData = (data) => { updates.push(data); setData(data); };
  h.page.onSpeedChange({ detail: { value: 8000 } });
  h.requests.at(-1).success({ statusCode: 502, data: { detail: "写字机未完成速度调整" } });
  await flush();
  assert.equal(h.page.data.speedRate, 16000);
  assert.equal(h.page.data.speedSaving, false);
  assert(updates.some((x) => x.speedRate === 16000));
  assert.equal(h.toasts.at(-1).title, "写字机未完成速度调整");
});

test("速度回包必须含本次设备确认值", async () => {
  const h = readyControl();
  h.page.onSpeedChange({ detail: { value: 8000 } });
  h.requests.at(-1).success({ statusCode: 200, data: { status: "ok", result: '{"error":"busy"}' } });
  await flush();
  assert.equal(h.page.data.speedRate, 16000);
  assert.equal(h.toasts.at(-1).icon, "none");
});

test("退出登录后旧速度回包不能影响新会话", async () => {
  const h = readyControl();
  h.page.onSpeedChange({ detail: { value: 8000 } });
  h.app.logout();
  h.requests.at(-1).success({ statusCode: 200, data: { status: "ok", rate: 8000 } });
  await flush();
  assert.equal(h.page.data.speedRate, 16000);
  assert.equal(h.toasts.length, 0);
});

test("页面隐藏后旧请求不能弹成功提示", async () => {
  const h = readyControl();
  h.page.onSpeedChange({ detail: { value: 8000 } });
  h.page.onHide();
  h.requests.at(-1).success({ statusCode: 200, data: { status: "ok", rate: 8000 } });
  await flush();
  assert.equal(h.page.data.speedRate, 16000);
  assert.equal(h.toasts.length, 0);
});

test("设备确认速度后才显示成功", async () => {
  const h = readyControl();
  h.page.onSpeedChange({ detail: { value: 8000 } });
  h.requests.at(-1).success({ statusCode: 200, data: { status: "ok", rate: 8000 } });
  await flush();
  assert.equal(h.page.data.speedRate, 8000);
  assert.equal(h.toasts.at(-1).icon, "success");
});
