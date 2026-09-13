const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, createClock, flush } = require("./harness");

test("待处理的照片实际启动精修轮询并落到最终预览", async () => {
  const clock = createClock();
  const h = createWeapp({ clock });
  h.load("pages/index/index.js");
  h.page.adoptJob({ job_id: "photo", refined: { status: "pending" } });
  assert.equal(h.page.data.job.refining, true);
  clock.advance(5000);
  assert(h.requests.at(-1).url.endsWith("/api/draw/jobs/photo"));
  h.requests.at(-1).success({ statusCode: 200, data: {
    refined: { status: "ready", job_id: "refined", preview_url: "/refined.png" },
  } });
  await flush();
  assert.equal(h.page.data.job.refined.jobId, "refined");
  assert.equal(h.page.data.job.refining, false);
  assert.equal(clock.pending.size, 0);
});

test("旧分享令牌不能替换当前作品的分享图", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/index/index.js");
  h.page.adoptJob({ job_id: "old", preview_url: "/old.png" });
  const stale = h.requests.at(-1);
  h.page.adoptJob({ job_id: "new", preview_url: "/new.png" });
  h.requests.at(-1).success({ statusCode: 200, data: { share_token: "new-token" } });
  stale.success({ statusCode: 200, data: { share_token: "old-token" } });
  await flush();
  assert.equal(h.page.data.shareToken, "new-token");
  assert.match(h.page.onShareAppMessage().path, /new-token/);
});

test("退出登录后的生成完成不能上屏或申请分享链接", async () => {
  const h = createWeapp({ clock: createClock() });
  h.load("pages/index/index.js");
  h.page.setData({ description: "猫" });
  h.page.onGenerate();
  h.app.logout();
  h.requests.at(-1).success({ statusCode: 200, data: { job_id: "old-user-job" } });
  await flush();
  assert.equal(h.page.data.job, null);
  assert.equal(h.requests.length, 1);
  assert.equal(h.toasts.length, 0);
});

test("编辑 AI 文本后仍保留来源标识，清空全文才开始新稿", () => {
  const h = createWeapp();
  h.load("pages/index/index.js");
  h.page.setData({ aiTextUsed: true });
  h.page.onWriteInput({ detail: { value: "AI 草稿加一个字" } });
  assert.equal(h.page.data.aiTextUsed, true);
  h.page.onWriteInput({ detail: { value: "" } });
  assert.equal(h.page.data.aiTextUsed, false);
});

async function showIndex(h) {
  h.page.onShow();
  await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } });
  await flush();
}

for (const photo of [false, true]) {
  for (const beforeShow of [false, true]) {
    test(`${photo ? "相册" : "文件"}选择在回屏${beforeShow ? "前" : "后"}完成仍能转换预览`, async () => {
      const h = createWeapp({ clock: createClock() });
      let picker;
      h.wx.chooseMessageFile = h.wx.chooseMedia = (options) => { picker = options; };
      h.load("pages/index/index.js");
      await showIndex(h);
      if (photo) h.page.onTakePhoto({ currentTarget: { dataset: { source: "album" } } });
      else h.page.onPickFile();
      h.page.onHide();
      const selection = { tempFiles: [{ size: 20, path: "/file.svg", tempFilePath: "/photo.jpg" }] };
      if (beforeShow) {
        picker.success(selection);
        assert.equal(h.uploads.length, 0);
      }
      await showIndex(h);
      if (!beforeShow) picker.success(selection);
      assert.equal(h.uploads.length, 1);
      assert.equal(h.uploads[0].filePath, photo ? "/photo.jpg" : "/file.svg");
      h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ job_id: "picked" }) });
      await flush();
      assert.equal(h.page.data.job.jobId, "picked");
      assert.equal(h.page.data.uploading, false);
    });
  }
}

for (const boundary of ["logout", "mode", "unload"]) {
  test(`文件选择遇到 ${boundary} 后不能自动上传`, async () => {
    const h = createWeapp({ clock: createClock() });
    let picker;
    h.wx.chooseMessageFile = (options) => { picker = options; };
    h.load("pages/index/index.js");
    await showIndex(h);
    h.page.onPickFile();
    if (boundary === "logout") h.app.logout();
    else if (boundary === "mode") h.page.onModeTap({ currentTarget: { dataset: { mode: "teach" } } });
    else h.page.onUnload();
    picker.success({ tempFiles: [{ path: "/old.svg", size: 20 }] });
    assert.equal(h.uploads.length, 0);
  });
}

for (const beforeShow of [false, true]) {
  test(`切页期间生成在回屏${beforeShow ? "前" : "后"}完成，结果保留且不会重复生成`, async () => {
    const h = createWeapp({ clock: createClock() });
    h.load("pages/index/index.js");
    await showIndex(h);
    h.page.setData({ description: "猫" });
    h.page.onGenerate();
    const generation = h.requests.at(-1);
    h.page.onHide();
    if (beforeShow) {
      generation.success({ statusCode: 200, data: { job_id: "background" } });
      await flush();
      assert.equal(h.toasts.length, 0);
      assert.equal(h.requests.some((r) => r.url.endsWith("/share")), false);
    }
    await showIndex(h);
    if (!beforeShow) {
      assert.equal(h.page.data.generating, true);
      h.page.onGenerate();
      assert.equal(h.requests.filter((r) => r.url.endsWith("/generate")).length, 1);
      generation.success({ statusCode: 200, data: { job_id: "background" } });
      await flush();
    }
    assert.equal(h.page.data.job.jobId, "background");
    assert.equal(h.page.data.generating, false);
    assert.equal(h.requests.filter((r) => r.url.endsWith("/share")).length, 1);
  });
}
