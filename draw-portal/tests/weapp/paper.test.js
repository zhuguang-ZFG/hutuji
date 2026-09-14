const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");
const paper = require("../../weapp/utils/paper_config");
const MAC = "aa:bb:cc:dd:ee:ff";
const ME = { phone_bound: true, active_device_mac: MAC, binding: { bound: true, device_mac: "aa:bb:cc:**:**:**" } };
const response = (config = paper.defaultFor("nopaper"), extras = {}) =>
  Object.assign({ config, marker: paper.marker(config), device_mac: MAC, capable: true, online: true }, extras);
const reply = (request, data) => request.success({ statusCode: 200, data });
const event = (field, value) => ({ currentTarget: { dataset: { field } }, detail: { value } });

async function showSummary(h, me = ME) {
  h.page.onShow();
  await flush();
  const auth = h.requests.filter((request) => request.url.endsWith('/api/auth/me')).at(-1);
  reply(auth, me);
  await flush();
  const latest = h.requests.filter((request) => request.url.endsWith('/api/auth/me')).at(-1);
  if (latest !== auth) { reply(latest, me); await flush(); }
  return h.requests.filter((request) => request.url.includes('/api/settings/paper?')).at(-1);
}

for (const name of ['index', 'chat']) {
  const create = () => {
    const h = createWeapp();
    h.storage.set('session_token', 'owner');
    h.load('pages/' + name + '/' + name + '.js');
    h.page.onLoad();
    return h;
  };
  test(name + ' 当前纸张从正常登录链读取，完整 MAC 与打码展示值不混用', async () => {
    const h = create();
    const request = await showSummary(h);
    assert(request.url.includes(encodeURIComponent(MAC)));
    reply(request, response());
    await flush();
    assert.equal(h.page.data.paperSummary.title, 'A4 · 竖版');
    assert.match(h.page.data.paperSummary.detail, /190 × 277 mm/);
    assert.equal(h.page.data.paperSummary.state, 'ready');
    const offline = await showSummary(h);
    reply(offline, response(undefined, { online: false }));
    await flush();
    assert.match(h.page.data.paperSummary.detail, /上次确认/);
    assert.equal(h.page.data.paperSummary.state, 'offline');
  });
  test(name + ' 旧设备和离页回包不能覆盖新纸张，退出后清摘要', async () => {
    const h = create();
    const old = await showSummary(h);
    h.page.onHide();
    const nextMac = '11:22:33:44:55:66';
    const latest = await showSummary(h, Object.assign({}, ME, { active_device_mac: nextMac }));
    const config = { paper: 'A2', width_mm: 420, height_mm: 594, margin_mm: 10,
      landscape: true, swap_xy: true, max_x_mm: 400, max_y_mm: 574 };
    reply(latest, response(config, { device_mac: nextMac }));
    await flush();
    reply(old, response());
    await flush();
    assert.equal(h.page.data.paperSummary.title, 'A2 · 横版');
    assert.equal(h.page.data.paperSummary.landscape, true);
    h.app.logout();
    h.page.onShow();
    assert.equal(h.page.data.paperSummary.title, '纸张与方向');
  });
  test(name + ' 回包标记错误不显示默认 A4，未绑定不发设备请求', async () => {
    const h = create();
    const request = await showSummary(h);
    reply(request, response(undefined, { marker: 'different' }));
    await flush();
    assert.equal(h.page.data.paperSummary.state, 'unavailable');
    assert.equal(h.page.data.paperSummary.title, '纸张与方向');
    const count = h.requests.filter((item) => item.url.includes('/api/settings/paper?')).length;
    await showSummary(h, Object.assign({}, ME, { active_device_mac: '', binding: null }));
    assert.equal(h.requests.filter((item) => item.url.includes('/api/settings/paper?')).length, count);
    assert.equal(h.page.data.paperSummary.state, 'unbound');
  });
}
async function show(h, status = response()) {
  h.page.onShow();
  await flush();
  reply(h.requests.at(-1), ME);
  await flush();
  reply(h.requests.at(-1), status);
  await flush();
}
function setup() {
  const h = createWeapp();
  h.storage.set("session_token", "owner");
  h.load("pages/paper/paper.js");
  return h;
}

test("两种旧机型与十分之一毫米标记遵守现有边界", () => {
  const no = paper.defaultFor("nopaper");
  assert.equal(paper.marker(no), "p1:A4:2100:2970:0:100:0:2050:2900");
  assert.equal(paper.marker(paper.defaultFor("paper")), "p1:A4:2100:2970:0:100:1:2770:1900");
  assert.throws(() => paper.validate(Object.assign({}, no, { max_x_mm: true })));
  assert.throws(() => paper.validate(Object.assign({}, no, { max_y_mm: Infinity })));
  assert.throws(() => paper.validate(Object.assign({}, no, { margin_mm: 10.01 })));
  assert.throws(() => paper.validate(Object.assign({}, no, { paper: "A3", width_mm: 297, height_mm: 420 })), /放不下/);
  const large = { paper: "A2", width_mm: 420, height_mm: 594, margin_mm: 10,
    landscape: true, swap_xy: true, max_x_mm: 400, max_y_mm: 574 };
  assert.deepEqual(paper.dimensions(paper.validate(large)),
    { width: 594, height: 420, pageWidth: 574, pageHeight: 400, x: 400, y: 574 });
});

test("先验证手机号再用真实 active_device_mac 读取，纸张选择不改变行程", async () => {
  const h = setup();
  await show(h);
  assert(h.requests.at(-1).url.includes(encodeURIComponent(MAC)));
  h.page.onPreset({ currentTarget: { dataset: { paper: "A3" } } });
  assert.equal(h.page.data.form.max_x_mm, 205);
  assert.equal(h.page.data.form.max_y_mm, 290);
  assert.match(h.page.data.fitError, /放不下/);
  const before = h.requests.length;
  h.page.onSave();
  assert.equal(h.requests.length, before);
});

test("扩大行程须确认，写入携带旧标记并核对读回", async () => {
  const h = setup();
  await show(h);
  const before = response();
  h.page.onPreset({ currentTarget: { dataset: { paper: "A3" } } });
  h.page.onInput(event("max_x_mm", "277"));
  h.page.onInput(event("max_y_mm", "400"));
  assert.equal(h.page.data.fitError, "");
  h.page.onSave();
  assert.equal(h.modals.length, 1);
  const count = h.requests.length;
  h.modals[0].success({ confirm: true });
  assert.equal(h.requests.length, count + 1);
  const request = h.requests.at(-1);
  assert.equal(request.data.device_mac, MAC);
  assert.equal(request.data.expected_marker, before.marker);
  assert.equal(request.data.range_confirmed, true);
  reply(request, response(JSON.parse(JSON.stringify(request.data.config))));
  await flush();
  assert.equal(h.page.data.saving, false);
  assert.match(h.page.data.hint, /已保存/);
});

test("离开确认框后不下发，重新进入不会卡在保存中", async () => {
  const h = setup();
  await show(h);
  h.page.onInput(event("max_x_mm", "210"));
  h.page.onSave();
  const modal = h.modals.at(-1);
  h.page.onHide();
  const count = h.requests.length;
  modal.success({ confirm: true });
  assert.equal(h.requests.length, count);
  await show(h);
  assert.equal(h.page.data.saving, false);
});

test("旧固件、离线与错误读回不能冒保存成功", async () => {
  const h = setup();
  await show(h, response(undefined, { capable: false }));
  const count = h.requests.length;
  h.page.onSave();
  assert.equal(h.requests.length, count);
  await show(h, response(undefined, { online: false }));
  h.page.onInput(event("margin_mm", "11"));
  assert.equal(h.page.data.form.margin_mm, 10);
  await show(h);
  h.page.onInput(event("margin_mm", "11"));
  h.page.onSave();
  reply(h.requests.at(-1), response());
  await flush();
  assert.equal(h.page.data.saving, false);
  assert.match(h.page.data.hint, /未确认/);
  assert.equal(h.page.data.marker, response().marker);
});

test("退出登录后原设备保存回包不能复活表单", async () => {
  const h = setup();
  await show(h);
  h.page.onInput(event("margin_mm", "11"));
  h.page.onSave();
  const request = h.requests.at(-1);
  h.app.logout();
  h.storage.set("session_token", "next-user");
  h.page.onShow();
  reply(request, response(request.data.config));
  await flush();
  assert.equal(h.page.data.form, null);
  assert.equal(h.page.data.saving, false);
});
