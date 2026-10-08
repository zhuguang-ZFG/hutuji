// 照片生图体验回归（2026-10-08）：精修等待绝对时限、畸形 ready 回包、网络失败只读核对、原图对照。
const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, createClock, flush } = require("./harness");

const PENDING = { refined: { status: "pending" } };
const READY = { refined: { status: "ready", job_id: "refined", preview_url: "/refined.png" } };
const reply = (r, data) => r.success({ statusCode: 200, data });
const lastJobQuery = (h) => [...h.requests].reverse().find((r) => /\/api\/draw\/jobs\//.test(r.url));
const noRegenerate = (h) => !h.requests.some((r) => /\/generate$|\/upload$/.test(r.url)) && h.uploads.length === 0;

function setup() {
  const clock = createClock();
  const h = createWeapp({ clock });
  h.load("pages/index/index.js"); h.page.onLoad(); h.page.onShow();
  h.requests.length = 0;
  return { h, clock };
}

// 回屏与真实 onShow 同流程：补 /me 回包后页面才走 resumeJobState。
async function showAgain(h) {
  h.page.onShow(); await flush();
  reply([...h.requests].reverse().find((r) => r.url.endsWith("/api/auth/me")), { phone_bound: true });
  await flush();
}

// 逐轮以 pending 回包推进轮询直到终态；只回 jobs 查询，不碰其它请求。
async function drive(h, clock, onTick) {
  const answered = new Set();
  const start = clock.now;
  for (let i = 0; i < 500 && !h.page.data.job.refineFailed; i++) {
    if (onTick) await onTick(clock.now - start);
    clock.advance(5000);
    for (const r of h.requests) {
      if (!answered.has(r) && /\/api\/draw\/jobs\//.test(r.url)) { answered.add(r); reply(r, PENDING); }
    }
    await flush();
  }
  return clock.now - start;
}

test("精修等待使用绝对时限，切页回屏不重置等待窗口", async () => {
  const { h, clock } = setup();
  h.page.adoptJob({ job_id: "photo", preview_url: "/base.png", refined: { status: "pending" } });
  const windowMs = h.page.refineWindowMs();
  assert(windowMs >= 120000 && windowMs <= 600000, `等待窗口 ${windowMs} 不在 2～10 分钟内`);
  let switched = false;
  const elapsed = await drive(h, clock, async (t) => {
    if (!switched && t >= windowMs / 2) { switched = true; h.page.onHide(); await showAgain(h); }
  });
  assert.equal(switched, true);
  assert.equal(h.page.data.job.refineFailed, "timeout");
  assert.equal(h.page.data.job.refining, false);
  // 回屏续轮只续剩余时间：总等待≈一个窗口；若回屏重新起算则 ≥1.5 个窗口
  assert(elapsed <= windowMs + 5000, `总等待 ${elapsed}ms 超过绝对时限 ${windowMs}ms`);
  assert(elapsed >= windowMs - 5000, `提前超时 ${elapsed}ms`);
  assert.equal(clock.pending.size, 0);
  assert(noRegenerate(h));
});

test("超时后的回屏补查仍只读，pending 不再重新起算等待", async () => {
  const { h, clock } = setup();
  h.page.adoptJob({ job_id: "photo", preview_url: "/base.png", refined: { status: "pending" } });
  await drive(h, clock);
  assert.equal(h.page.data.job.refineFailed, "timeout");
  h.page.onHide(); await showAgain(h);
  reply(lastJobQuery(h), PENDING); await flush();
  assert.equal(h.page.data.job.refining, false);
  assert.equal(h.page.data.job.refineFailed, "timeout");
  assert.equal(clock.pending.size, 0);
  assert(noRegenerate(h));
});

for (const path of ["adopt", "polling", "resume"]) {
  test(`畸形 ready 回包（缺 job_id）经${path}路径不能成为可发送结果`, async () => {
    const { h, clock } = setup();
    const malformed = { refined: { status: "ready", preview_url: "/x.png" } };
    if (path === "adopt") {
      h.page.adoptJob(Object.assign({ job_id: "photo", preview_url: "/base.png" }, malformed));
    } else {
      h.page.adoptJob({ job_id: "photo", preview_url: "/base.png", refined: { status: "pending" } });
      if (path === "polling") clock.advance(5000);
      else { h.page.onHide(); await showAgain(h); }
      const req = lastJobQuery(h);
      assert.match(req.url, /\/api\/draw\/jobs\/photo$/);
      reply(req, malformed); await flush();
    }
    assert.equal(h.page.data.job.refined, null);
    assert.equal(!!h.page.data.job.useRefined, false);
    assert.equal(h.page.selectedJobId(), "photo");
    assert(!h.toasts.some((t) => /精修版好了/.test(t.title)));
    // 畸形包按仍在等待处理：轮询继续但受绝对时限约束
    assert.equal(h.page.data.job.refining, true);
    assert.equal(clock.pending.size, 1);
    h.page.onSend();
    assert.match(h.requests.at(-1).url, /\/api\/draw\/photo\/send$/);
  });
}

test("精修轮询网络失败给出可见提示，可只读核对且不重新上传或生成", async () => {
  const { h, clock } = setup();
  h.page.adoptJob({ job_id: "photo", preview_url: "/base.png", refined: { status: "pending" } });
  clock.advance(5000);
  h.requests.at(-1).fail({ errMsg: "request:fail" }); await flush();
  assert.equal(h.page.data.job.refineOffline, true);
  assert.equal(h.page.data.job.refining, true);
  assert.equal(clock.pending.size, 1, "网络失败后轮询链仍在（受绝对时限约束）");
  const before = h.requests.length;
  h.page.onRecheckRefined(); h.page.onRecheckRefined();
  assert.equal(h.requests.length, before + 1, "核对中不重复发起");
  const check = h.requests.at(-1);
  assert.equal(check.method, "GET"); assert.match(check.url, /\/api\/draw\/jobs\/photo$/);
  assert.equal(h.page.data.job.refineChecking, true);
  reply(check, READY); await flush();
  assert.equal(h.page.data.job.refined.jobId, "refined");
  assert.equal(h.page.data.job.useRefined, true);
  assert.equal(h.page.data.job.refineOffline, false);
  assert.equal(h.page.data.job.refining, false);
  assert.equal(h.page.data.job.refineChecking, false);
  assert.equal(clock.pending.size, 0, "就绪后轮询链停止");
  assert(noRegenerate(h));
});

test("网络恢复后轮询拿到 pending 即清除提示，再次失败不改变终态时限", async () => {
  const { h, clock } = setup();
  h.page.adoptJob({ job_id: "photo", preview_url: "/base.png", refined: { status: "pending" } });
  clock.advance(5000);
  h.requests.at(-1).fail({ errMsg: "request:fail" }); await flush();
  assert.equal(h.page.data.job.refineOffline, true);
  clock.advance(5000);
  reply(h.requests.at(-1), PENDING); await flush();
  assert.equal(h.page.data.job.refineOffline, false);
  // 只读核对也失败：提示保留、不弹窗、链不断
  h.page.onRecheckRefined();
  h.requests.at(-1).fail({ errMsg: "request:fail" }); await flush();
  assert.equal(h.page.data.job.refineChecking, false);
  assert.equal(h.modals.length, 0);
  assert.equal(clock.pending.size, 1);
});

test("拍照上传保留本机原图，对照使用当前选中版本", async () => {
  const { h, clock } = setup();
  let picker; const previews = [];
  h.wx.chooseMedia = (o) => { picker = o; };
  h.wx.previewImage = (o) => previews.push(o);
  h.page.onTakePhoto({ currentTarget: { dataset: { source: "camera" } } });
  picker.success({ tempFiles: [{ size: 20, tempFilePath: "wxfile://tmp/photo.jpg" }] });
  assert.equal(h.uploads.length, 1);
  h.uploads[0].success({ statusCode: 200, data: JSON.stringify({
    job_id: "photo", preview_url: "/base.png", refined: { status: "pending" } }) });
  await flush();
  assert.equal(h.page.data.job.photoSource, "wxfile://tmp/photo.jpg");
  h.page.onComparePhoto();
  assert.equal(previews.length, 1);
  assert.equal(previews[0].current, "wxfile://tmp/photo.jpg");
  assert.equal(previews[0].urls[0], "wxfile://tmp/photo.jpg");
  assert.match(previews[0].urls[1], /\/base\.png$/);
  clock.advance(5000);
  reply(lastJobQuery(h), READY); await flush();
  h.page.onComparePhoto();
  assert.match(previews[1].urls[1], /\/refined\.png$/);
  h.page.onSelectVersion({ currentTarget: { dataset: { version: "original" } } });
  h.page.onComparePhoto();
  assert.match(previews[2].urls[1], /\/base\.png$/);
  assert.equal(previews[2].urls.length, 2);
});

test("文件上传与文字生成不带原图，对照入口不可用", async () => {
  const { h } = setup();
  let picker; const previews = [];
  h.wx.chooseMessageFile = (o) => { picker = o; };
  h.wx.previewImage = (o) => previews.push(o);
  h.page.onPickFile();
  picker.success({ tempFiles: [{ size: 20, path: "/file.svg", tempFilePath: "wxfile://tmp/file.svg" }] });
  h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ job_id: "svg", preview_url: "/svg.png" }) });
  await flush();
  assert.equal(h.page.data.job.photoSource, "");
  h.page.onComparePhoto();
  assert.equal(previews.length, 0);
  h.page.setData({ description: "猫" }); h.page.onGenerate();
  reply(h.requests.at(-1), { job_id: "text", preview_url: "/text.png" }); await flush();
  assert.equal(h.page.data.job.jobId, "text");
  assert.equal(h.page.data.job.photoSource, "");
});
