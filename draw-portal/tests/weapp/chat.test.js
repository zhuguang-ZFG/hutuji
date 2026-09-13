const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

async function setup() {
  const h = createWeapp();
  h.load("pages/chat/chat.js");
  h.page.onLoad();
  h.requests[0].success({ statusCode: 200, data: {} });
  h.page.onShow();
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
  return h;
}

test("快速松手早于录音开始：迟到的 onStart 立即停止且不上传", async () => {
  const h = await setup();
  h.page.onHoldStart();
  h.page.onHoldEnd();
  h.recorder.emit("start");
  assert.equal(h.recorder.stops, 1);
  h.recorder.emit("stop", { tempFilePath: "wxfile://short.mp3" });
  assert.equal(h.uploads.length, 0);
  assert.equal(h.page.data.recording, false);
});

for (const event of ["onHide", "onUnload", "onHoldCancel"]) {
  test(`${event} 会停止录音，作废音频不能继续上传`, async () => {
    const h = await setup();
    h.page.onHoldStart();
    h.recorder.emit("start");
    h.page[event]();
    assert.equal(h.recorder.stops, 1);
    h.recorder.emit("stop", { tempFilePath: "wxfile://cancelled.mp3" });
    assert.equal(h.uploads.length, 0);
  });
}

test("正常松手只上传一次；旧 ASR 不能借新登录继续语音工具调用", async () => {
  const h = await setup();
  h.page.onHoldStart();
  h.recorder.emit("start");
  h.page.onHoldEnd();
  h.recorder.emit("stop", { tempFilePath: "wxfile://voice.mp3" });
  assert.equal(h.uploads.length, 1);
  const count = h.requests.length;
  h.app.logout();
  h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ text: "开始画" }) });
  await flush();
  assert.equal(h.requests.length, count);
  assert.equal(h.storage.has("voice_session_id"), false);
});

test("正常录音的 ASR、对话和回复形成完整链路", async () => {
  const h = await setup();
  h.page.onHoldStart();
  h.recorder.emit("start");
  h.page.onHoldEnd();
  h.recorder.emit("stop", { tempFilePath: "wxfile://voice.mp3" });
  h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ text: "你好" }) });
  await flush();
  assert(h.requests.at(-1).url.endsWith("/api/voice/turn"));
  assert.equal(h.requests.at(-1).data.session_id, h.page._sessionId);
  h.requests.at(-1).success({ statusCode: 200, data: { reply: "你好呀" } });
  await flush();
  assert.equal(h.page.data.messages.at(-1).content, "你好呀");
  assert.equal(h.page.data.busy, false);
});

test("退出后旧文字回包不复活聊天，新会话不带上一个账号的历史", async () => {
  const h = await setup();
  h.page.setData({ textInput: "旧用户内容" });
  h.page.onSendText();
  const stale = h.requests.at(-1);
  h.app.logout();
  h.page.onShow();
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  stale.success({ statusCode: 200, data: { reply: "旧回复" } });
  await flush();
  assert.equal(h.page.data.messages.some((m) => /旧/.test(m.content)), false);
});

test("收藏重复点击只发一次请求", async () => {
  const h = await setup();
  const id = h.page._pushMessage("assistant", "画好了");
  h.page._patchMessage(id, { draw: { jobId: "job-a", state: "ready" } });
  const event = { currentTarget: { dataset: { id } } };
  h.page.onDrawSave(event);
  h.page.onDrawSave(event);
  assert.equal(h.requests.filter((r) => r.url.endsWith("/save")).length, 1);
});

for (const beforeShow of [false, true]) {
  test(`文字回复在回屏${beforeShow ? "前" : "后"}完成仍可见，在途请求不重复提交`, async () => {
    const h = await setup();
    h.page.setData({ textInput: "你好" });
    h.page.onSendText();
    const chat = h.requests.at(-1);
    h.page.onHide();
    if (beforeShow) {
      chat.success({ statusCode: 200, data: { reply: "同一次对话的回复" } });
      await flush();
    }
    h.page.onShow();
    await flush();
    h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
    await flush();
    if (!beforeShow) {
      assert.equal(h.page.data.busy, true);
      h.page.setData({ textInput: "再点一次" });
      h.page.onSendText();
      assert.equal(h.requests.filter((r) => r.url.endsWith("/api/chat")).length, 1);
      chat.success({ statusCode: 200, data: { reply: "同一次对话的回复" } });
      await flush();
    }
    assert.equal(h.page.data.messages.at(-1).content, "同一次对话的回复");
    assert.equal(h.page.data.busy, false);
  });
}

test("聊天卡生成结果跨切页保留，不在回屏时重置成未生成", async () => {
  const h = await setup();
  const id = h.page._pushMessage("assistant", "可以画猫");
  h.page._patchMessage(id, { draw: { description: "猫", state: "idle" } });
  h.page.onDrawGenerate({ currentTarget: { dataset: { id } } });
  const generation = h.requests.at(-1);
  h.page.onHide();
  generation.success({ statusCode: 200, data: { job_id: "chat-picture", preview_url: "/picture.png" } });
  await flush();
  h.page.onShow();
  const draw = h.page.data.messages.find((m) => m.id === id).draw;
  assert.equal(draw.state, "ready");
  assert.equal(draw.jobId, "chat-picture");
});
