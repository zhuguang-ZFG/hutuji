const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

test("退出后迟到的资料读取不会复活账号", async () => {
  const h = createWeapp();
  h.load("pages/profile/profile.js");
  h.page.refresh();
  await flush();
  const stale = h.requests.at(-1);
  h.page.onLogout();
  h.modals[0].success({ confirm: true });
  stale.success({ statusCode: 200, data: { nickname: "旧昵称", phone_bound: true } });
  await flush();
  assert.equal(h.page.data.me, null);
  assert.equal(h.app.globalData.user, null);
  assert.equal(h.page.data.loggedOut, true);
});

test("隐藏后的昵称保存回包不弹提示也不继续读取资料", async () => {
  const h = createWeapp();
  h.load("pages/profile/profile.js");
  h.page.setData({ nicknameDraft: "昵称" });
  h.page.onSaveNick();
  h.page.onHide();
  h.requests[0].success({ statusCode: 200, data: {} });
  await flush();
  assert.equal(h.toasts.length, 0);
  assert.equal(h.requests.length, 1);
});

test("当前会话过期显示重新登录，隐私指引调用官方入口", async () => {
  const h = createWeapp();
  h.load("pages/profile/profile.js");
  h.page.refresh();
  await flush();
  h.requests[0].success({ statusCode: 401, data: {} });
  await flush();
  assert.equal(h.page.data.loggedOut, true);
  assert.match(h.page.data.loadError, /登录/);
  let opened = 0;
  h.wx.openPrivacyContract = () => opened++;
  h.page.onPrivacy();
  assert.equal(opened, 1);
});
