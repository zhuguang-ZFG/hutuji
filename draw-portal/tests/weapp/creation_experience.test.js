const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, createClock, flush } = require("./harness");
const event = (value) => ({ detail: { value } });
const reply = (r, data) => r.success({ statusCode: 200, data });
function setup() {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/index/index.js"); h.page.onLoad(); h.requests.length = 0;
  return h;
}

for (const [action, field] of [["onGenerate", "description"], ["onLayoutPreview", "writeText"], ["onTeachGenerate", "teachText"], ["onAiGenerate", "writeText"]]) {
  test(`${action} 拒绝空白内容并为长任务留出完整时间`, () => {
    const h = setup(); h.page.setData({ [field]: "   \n " }); h.page[action]();
    assert.equal(h.requests.length, 0);
    h.page.setData({ [field]: "春天" }); h.page[action]();
    assert.equal(h.requests[0].timeout, 300000);
  });
}

test("生成进行中不能同时启动另一项创作覆盖结果", () => {
  const h = setup(); h.page.setData({ description: "猫", writeText: "春天", teachText: "春" });
  h.page.onGenerate(); h.page.onLayoutPreview(); h.page.onTeachGenerate(); h.page.onPickFile();
  assert.equal(h.requests.length, 1);
  h.page.onModeTap({ currentTarget: { dataset: { mode: "write" } } });
  assert.equal(h.page.data.mode, "draw");
});

test("精修等候超时后可只读取回结果，不重复生图", async () => {
  const h = setup(); h.page.adoptJob({ job_id: "photo", preview_url: "/base.png" });
  h.page.setData({ "job.refineFailed": "timeout" });
  h.requests.length = 0;
  h.page.onRetryRefined(); h.page.onRetryRefined();
  assert.equal(h.requests.length, 1); assert.equal(h.requests[0].method, "GET");
  reply(h.requests[0], { refined: { status: "ready", job_id: "refined", preview_url: "/refined.png" } });
  await flush(); assert.equal(h.page.data.job.useRefined, true);
  assert.equal(h.page.data.job.refineFailed, false); assert.equal(h.page.data.job.refineChecking, false);
  assert(!h.requests.some((r) => /\/generate$|\/upload$/.test(r.url)));
});

test("已编辑AI稿的撤回确认在离页后失效", async () => {
  const h = setup(); h.page.onWriteInput(event("原稿")); h.page.onAiPolish();
  reply(h.requests.at(-1), { text: "AI新稿" }); await flush();
  h.page.onWriteInput(event("手动又编辑了")); h.page.onUndoAiText();
  const modal = h.modals.at(-1); h.page.onHide(); modal.success({ confirm: true });
  assert.equal(h.page.data.writeText, "手动又编辑了");
});

test("AI写文迟到时保留用户新输入，主动采用后可恢复原稿", async () => {
  const h = setup(); h.page.onWriteInput(event("原稿")); h.page.onAiPolish();
  const request = h.requests.at(-1);
  h.page.onWriteInput(event("等待时补上的内容"));
  reply(request, { text: "AI润色结果" }); await flush();
  assert.equal(h.page.data.writeText, "等待时补上的内容");
  assert.equal(h.page.data.aiSuggestion, "AI润色结果");
  h.page.onApplyAiSuggestion();
  assert.equal(h.page.data.writeText, "AI润色结果");
  h.page.onUndoAiText();
  assert.equal(h.page.data.writeText, "等待时补上的内容");
  assert.equal(h.page.data.aiTextUsed, false);
});

test("AI直接替换时也保留可撤回的原文，空AI回包不清掉正文", async () => {
  const h = setup(); h.page.setData({ writeText: "原文" }); h.page.onAiGenerate();
  reply(h.requests.at(-1), { text: "新稿" }); await flush();
  assert.equal(h.page.data.canUndoAiText, true);
  h.page.onUndoAiText(); assert.equal(h.page.data.writeText, "原文");
  h.page.onAiGenerate(); reply(h.requests.at(-1), { text: "  " }); await flush();
  assert.equal(h.page.data.writeText, "原文");
});

test("示例只填写输入，不扣生成次数或发出请求", () => {
  const h = setup(); h.page.onExample({ currentTarget: { dataset: { text: "一枝兰花" } } });
  assert.equal(h.page.data.description, "一枝兰花"); assert.equal(h.requests.length, 0);
});

test("照片版本切换后预览、发送、收藏与分享保持同一版本", async () => {
  const h = setup();
  h.page.adoptJob({ job_id: "base", preview_url: "/base.png", refined: {
    status: "ready", job_id: "refined", preview_url: "/refined.png" } });
  assert.equal(h.page.currentPreviewUrl(), "/refined.png");
  assert.match(h.requests.at(-1).url, /\/refined\/share$/);
  const refinedShare = h.requests.at(-1);
  h.page.onSelectVersion({ currentTarget: { dataset: { version: "original" } } });
  assert.equal(h.page.currentPreviewUrl(), "/base.png");
  const originalShare = h.requests.at(-1);
  reply(refinedShare, { share_token: "wrong-version" }); await flush();
  assert.equal(h.page.data.shareToken, null);
  reply(originalShare, { share_token: "base-version" }); await flush();
  assert.match(h.page.onShareAppMessage().path, /base-version/);
  h.page.onSave(); assert.match(h.requests.at(-1).url, /\/base\/save$/);
  reply(h.requests.at(-1), { status: "saved" }); await flush();
  h.page.onSend(); assert.match(h.requests.at(-1).url, /\/base\/send$/);
  assert.equal(h.requests.at(-1).timeout, 150000);
});

test("多页放大可滑动全部预览，并打开当前页", () => {
  const h = setup(); let preview;
  h.wx.previewImage = (options) => { preview = options; };
  h.page.adoptJob({ job_id: "book", pages: [{ preview_url: "/1.png" }, { preview_url: "/2.png" }] });
  h.page.onNextPage(); h.page.onPreviewTap();
  assert.equal(preview.urls.length, 2); assert.match(preview.current, /\/2.png$/);
});

test("发送未自动开始时保留完整原因，用户主动进入控制页且旧弹窗不能跨页导航", async () => {
  const h = setup(); h.page.adoptJob({ job_id: "draw", preview_url: "/draw.png" });
  h.page.onSend(); reply(h.requests.at(-1), { status: "sent", detail: "自动开始画未完成：请在控制页点开始画" }); await flush();
  const modal = h.modals.at(-1);
  assert.match(modal.content, /自动开始画未完成/); assert.equal(modal.confirmText, "查看设备");
  modal.success({ confirm: true }); assert.equal(h.tabs.at(-1).url, "/pages/control/control");
  h.page.onHide(); modal.success({ confirm: true }); assert.equal(h.tabs.length, 1);
});
