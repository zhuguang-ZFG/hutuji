// 2026-10-08 照片续作批第 4 项（聊天）：失败原文可恢复到输入框、不自动发送、不覆盖新草稿；
// 失败轮次与错误提示不混入下一轮模型历史；登录切换作废恢复入口。
const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

async function setup() {
  const h = createWeapp();
  h.wx.getSetting = (options) => options.success({ authSetting: { "scope.record": true } });
  h.load("pages/chat/chat.js");
  h.page.onLoad();
  h.requests[0].success({ statusCode: 200, data: {} });
  h.page.onShow();
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  return h;
}
const restore = (h, id) => h.page.onRestoreText({ currentTarget: { dataset: { id } } });
async function sendAndFail(h, text = "画一只抱着星星的小猫") {
  h.page.setData({ textInput: text });
  h.page.onSendText();
  assert(h.requests.at(-1).url.endsWith("/api/chat"));
  h.requests.at(-1).fail({ errMsg: "request:fail timeout" });
  await flush();
  return h.page.data.messages.find((m) => m.role === "user" && m.content === text);
}

test("发送失败标记回复未确认并落错误提示，两者都不进下一轮模型历史", async () => {
  const h = await setup();
  const user = await sendAndFail(h);
  const notice = h.page.data.messages.at(-1);
  assert.equal(user.failed, true);
  assert.equal(notice.role, "assistant");
  assert.equal(notice.error, true);
  assert.match(notice.content, /尚未确认/);
  assert.equal(h.page.data.busy, false);
  h.page.setData({ textInput: "换个说法再聊" });
  h.page.onSendText();
  const history = h.requests.at(-1).data.history;
  assert.deepEqual(history, [], "失败轮次与错误提示不得混入模型历史");
  h.requests.at(-1).success({ statusCode: 200, data: { reply: "好呀" } });
  await flush();
  h.page.setData({ textInput: "继续" });
  h.page.onSendText();
  const roles = h.requests.at(-1).data.history.map((m) => m.role + ":" + m.content);
  assert.deepEqual(roles, ["user:换个说法再聊", "assistant:好呀"]);
});

test("失败原文可恢复到输入框：不自动发送，也不覆盖新草稿", async () => {
  const h = await setup();
  const user = await sendAndFail(h);
  const before = h.requests.length;
  h.page.setData({ textInput: "新草稿", inputMode: "text" });
  restore(h, user.id);
  assert.equal(h.page.data.textInput, "新草稿", "有新草稿时不得覆盖");
  assert.match(h.toasts.at(-1).title, /先清空/);
  h.page.setData({ textInput: "   " });
  restore(h, user.id);
  assert.equal(h.page.data.textInput, "画一只抱着星星的小猫");
  assert.equal(h.page.data.inputMode, "text");
  assert.equal(h.page.data.inputFocus, true);
  assert.equal(h.requests.length, before, "恢复只填草稿，不得自动发送");
  assert.equal(h.page.data.messages.find((m) => m.id === user.id).restored, true);
  restore(h, user.id);
  assert.equal(h.page.data.textInput, "画一只抱着星星的小猫");
});

test("正常发出的消息与错误提示本身没有恢复入口", async () => {
  const h = await setup();
  h.page.setData({ textInput: "你好" });
  h.page.onSendText();
  h.requests.at(-1).success({ statusCode: 200, data: { reply: "你好呀" } });
  await flush();
  const ok = h.page.data.messages.find((m) => m.role === "user");
  assert.equal(ok.failed, undefined);
  restore(h, ok.id);
  assert.equal(h.page.data.textInput, "");
  await sendAndFail(h, "再来一句");
  const notice = h.page.data.messages.at(-1);
  restore(h, notice.id);
  assert.equal(h.page.data.textInput, "");
});

test("处理中不恢复，登录切换后失败轮次与恢复入口一并作废", async () => {
  const h = await setup();
  const user = await sendAndFail(h);
  h.page.setData({ textInput: "第二句" });
  h.page.onSendText();
  assert.equal(h.page.data.busy, true);
  restore(h, user.id);
  assert.equal(h.page.data.textInput, "", "回复等待中不得改写输入框");
  h.requests.at(-1).success({ statusCode: 200, data: { reply: "好" } });
  await flush();
  assert.equal(h.page.data.messages.length, 4);
  h.app.logout();
  h.page.onShow();
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  assert.equal(h.page.data.messages.length, 0);
  const before = h.requests.length;
  restore(h, user.id);
  assert.equal(h.page.data.textInput, "");
  assert.equal(h.requests.length, before);
});

test("隐藏期间失败的轮次回屏后仍可恢复", async () => {
  const h = await setup();
  h.page.setData({ textInput: "切页前发的" });
  h.page.onSendText();
  const pending = h.requests.at(-1);
  h.page.onHide();
  pending.fail({ errMsg: "request:fail timeout" });
  await flush();
  h.page.onShow();
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  const user = h.page.data.messages.find((m) => m.role === "user");
  assert.equal(user.failed, true);
  assert.equal(h.page.data.busy, false);
  restore(h, user.id);
  assert.equal(h.page.data.textInput, "切页前发的");
});

test("退出后即使尚未回屏，也不能恢复旧账号原文", async () => {
  const h = await setup();
  const user = await sendAndFail(h);
  h.app.logout();
  restore(h, user.id);
  assert.equal(h.page.data.textInput, "");
  assert.equal(h.page.data.messages.find((m) => m.id === user.id).restored, undefined);
});
