const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, createClock, flush } = require('./harness');

const mac = 'aa:bb:cc:dd:ee:ff';
const me = { phone_bound: true, active_device_mac: mac,
  binding: { bound: true, device_mac: 'aa:bb:cc…ff' } };
const state = { connected: true, ready: true, authorized: true, grbl_settings_ok: true,
  state: 'idle', grbl_state: 'Idle', plotter_sku: 'nopaper',
  speed: { axis_control: true, state: 'done' } };
const respond = (request, data) => request.success({ statusCode: 200, data });
const statusReply = (request, patch = {}) =>
  respond(request, { status: 'ok', result: JSON.stringify({ ...state, ...patch }) });

function ready() {
  const clock = createClock();
  const h = createWeapp({ clock });
  h.load('pages/control/control.js');
  h.page.setData({ bound: true, bindingChecked: true, bindingMac: mac,
    status: state, speedLoaded: true });
  h.page.setData(h.page._deriveControls(state));
  return { h, clock };
}

test('手动与试笔按 500ms 查状态，保持运动期间锁定且不展示上次绘制耗时', () => {
  const { h } = ready();
  const progress = h.load('utils/job_progress.js');
  for (const kind of ['manual', 'pen_test']) {
    const view = progress.deriveProgressView({ ...state, state: kind, elapsed_ms: 65000, eta_ms: 90000 });
    assert.equal(view.pollMs, 500, kind);
    assert.equal(view.elapsedLabel, '');
    assert.equal(view.etaLabel, '');
    assert.equal(h.page._deriveControls({ ...state, state: kind }).manualLocked, true);
    assert.notEqual(h.page._deriveControls({ ...state, state: kind }).stateLabel, '查询中');
  }
  assert.equal(progress.deriveProgressView(state).pollMs, 5000);
  assert.equal(progress.deriveProgressView({ ...state, state: 'streaming' }).pollMs, 1500);
});

test('步距被设备同步确认后立即恢复，再直接查询该设备状态', async () => {
  const { h } = ready();
  h.page.onStepToggle();
  assert.equal(h.page.data.manualLocked, true);
  respond(h.requests[0], { status: 'ok', result: '"ok"' });
  await flush();
  assert.equal(h.page.data.step, 1);
  assert.equal(h.page.data.manualLocked, false);
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].data.tool, 'hutuji.status');
  assert.equal(h.requests[1].data.device_mac, mac);
});

test('运动受理后立即查状态，manual 转 idle 的首个新回包恢复按钮', async () => {
  const { h, clock } = ready();
  h.page.fire('hutuji.manual', 'jog_x+');
  respond(h.requests[0], { status: 'ok', result: '"started"' });
  await flush();
  assert.equal(h.page.data.busyAction, '');
  assert.equal(h.page.data.manualLocked, true);
  assert.equal(h.requests[1].data.tool, 'hutuji.status');
  statusReply(h.requests[1], { state: 'manual' });
  await flush();
  assert.equal(h.page.data.manualLocked, true);
  clock.advance(499);
  assert.equal(h.requests.length, 2);
  clock.advance(1);
  if (h.requests.at(-1).url.endsWith('/api/auth/me')) {
    respond(h.requests.at(-1), me);
    await flush();
  }
  assert.equal(h.requests.at(-1).data.tool, 'hutuji.status');
  statusReply(h.requests.at(-1));
  await flush();
  assert.equal(h.page.data.manualLocked, false);
});

test('手动动作失败显示设备原因，下一次正常状态清除旧错误', async () => {
  const { h } = ready();
  h.page.fire('hutuji.manual', 'pen_up');
  respond(h.requests[0], { status: 'ok', result: '"started"' });
  await flush();
  statusReply(h.requests[1], { state: 'error', ready: false,
    control_error: '手动控制失败：等待写字机运动完成超时' });
  await flush();
  assert.equal(h.page.data.stateLabel, '需要处理');
  assert.equal(h.page.data.controlError, '手动控制失败：等待写字机运动完成超时');
  assert.equal(h.page.data.manualLocked, true);
  h.page.setData(h.page._deriveControls(state));
  assert.equal(h.page.data.controlError, '');
  assert.equal(h.page.data.manualLocked, false);
});

test('动作前的慢轮询不能解锁或替代动作后的新查询', async () => {
  const { h, clock } = ready();
  h.page.refresh();
  respond(h.requests[0], me);
  await flush();
  const stale = h.requests[1];
  h.page.fire('hutuji.manual', 'home');
  const action = h.requests[2];
  respond(action, { status: 'ok', result: '"started"' });
  await flush();
  assert.equal(h.requests.length, 3, '同页不能并行叠加状态查询');
  statusReply(stale);
  await flush();
  assert.equal(h.page.data.manualLocked, true, '旧 idle 不能解锁已经受理的运动');
  assert.equal(h.requests.length, 4, '旧查询结束后立即补查，不等 5 秒');
  assert.equal(h.requests[3].data.tool, 'hutuji.status');
  statusReply(h.requests[3], { state: 'manual' });
  await flush();
  assert.equal(h.page.data.manualLocked, true);
  assert.equal(clock.pending.size, 1);
});

test('动作在途时旧轮询不改变按钮，也不触发速度读取', async () => {
  const { h, clock } = ready();
  h.page.setData({ speedLoaded: false });
  h.page.refresh();
  respond(h.requests[0], me);
  await flush();
  h.page.fire('hutuji.manual', 'pen_up');
  statusReply(h.requests[1]);
  await flush();
  clock.advance(20000);
  assert.equal(h.requests.length, 3);
  assert.equal(h.page.data.manualLocked, true);
  assert.equal(h.page.data.canRepeat, false);
});

for (const boundary of ['hide', 'logout', 'device']) {
  test(`等待旧查询结束时发生 ${boundary}，不向旧设备续发`, async () => {
    const { h } = ready();
    h.page.refresh();
    respond(h.requests[0], me);
    await flush();
    h.page.fire('hutuji.manual', 'home');
    respond(h.requests[2], { status: 'ok', result: '"started"' });
    await flush();
    if (boundary === 'hide') h.page.onHide();
    if (boundary === 'logout') h.app.logout();
    if (boundary === 'device') h.page.setData({ bindingMac: '11:22:33:44:55:66' });
    statusReply(h.requests[1]);
    await flush();
    assert.equal(h.requests.length, 3);
  });
}

test('控制超时不按旧 idle 解锁，收到真实状态后再决定', async () => {
  const { h } = ready();
  h.page.fire('hutuji.manual', 'home');
  h.requests[0].fail({ errMsg: 'request:fail timeout' });
  await flush();
  assert.equal(h.page.data.manualLocked, true);
  assert.equal(h.requests[1].data.tool, 'hutuji.status');
  respond(h.requests[1], { status: 'error', detail: '设备状态暂不可读' });
  await flush();
  assert.equal(h.page.data.manualLocked, true);
  assert.match(h.page.data.statusError, /不可读/);
});
