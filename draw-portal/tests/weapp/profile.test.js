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

test("隐私入口不可用时给出真实邮箱，只有用户确认才复制", () => {
  const h = createWeapp();
  h.load("pages/profile/profile.js");
  const clipboard = [];
  h.wx.setClipboardData = (options) => clipboard.push(options);
  h.page.onPrivacy();
  assert.match(h.modals[0].content, /17150303974@163\.com/);
  assert.equal(clipboard.length, 0);
  h.modals[0].success({ confirm: true });
  assert.equal(clipboard[0].data, "17150303974@163.com");
});

test("客服入口失败提供邮件帮助，不把打开会话报告为已接通", () => {
  const h = createWeapp();
  h.load("pages/profile/profile.js");
  h.page.onContactError();
  assert.match(h.modals[0].content, /微信客服暂时无法打开/);
  assert.match(h.modals[0].content, /17150303974@163\.com/);
  assert.equal(h.toasts.length, 0);
});

test("隐私迟到的错误不打扰其他页面，邮箱复制失败不报成功", () => {
  const h = createWeapp();
  h.load("pages/profile/profile.js");
  let request;
  h.wx.openPrivacyContract = (options) => { request = options; };
  h.page.onPrivacy();
  h.page.onHide();
  request.fail();
  assert.equal(h.modals.length, 0);
  h.wx.setClipboardData = (options) => options.fail();
  h.page.onShow();
  h.page.onCopySupportEmail();
  assert.match(h.toasts.at(-1).title, /复制失败/);
  assert.equal(h.toasts.at(-1).icon, "none");
});

test("视觉致谢可复制 handraw-style 原仓库地址", () => {
  const h = createWeapp();
  h.load("pages/profile/profile.js");
  const clipboard = [];
  h.wx.setClipboardData = (options) => clipboard.push(options);
  h.page.onCopyHandrawCredit();
  assert.equal(clipboard[0].data, "https://github.com/yang0/handraw-style");
});
