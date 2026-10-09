// 页面恢复回归：只用真实模块和受控网络次序，不操作设备。
const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, createClock, flush } = require("./harness");
const ME = { phone_bound: true };
const reply = (r, data) => r.success({ statusCode: 200, data });
const event = (dataset) => ({ currentTarget: { dataset } });
const shareRequests = (h) => h.requests.filter((r) => /\/share$/.test(r.url));
async function show(h) {
  h.page.onShow(); await flush();
  reply(h.requests.at(-1), ME); await flush();
}
async function index() {
  const clock = createClock();
  const h = createWeapp({ clock });
  h.load("pages/index/index.js"); h.page.onLoad();
  await show(h);
  return { h, clock };
}

test("分享准备被切页打断后补发，迟到旧响应不能覆盖新分享", async () => {
  const { h } = await index();
  h.page.adoptJob({ job_id: "work", preview_url: "/work.png" });
  const old = shareRequests(h)[0];
  h.page.onHide(); await show(h);
  assert.equal(shareRequests(h).length, 2);
  reply(shareRequests(h)[1], { share_token: "new-share" }); await flush();
  reply(old, { share_token: "old-share" }); await flush();
  assert.equal(h.page.data.shareLoading, false);
  assert.match(h.page.onShareAppMessage().path, /share=new-share$/);
});

test("已有精修的分享失败可重试且去重，回屏复用成功结果", async () => {
  const { h } = await index();
  h.page.adoptJob({ job_id: "base", preview_url: "/base.png",
    refined: { status: "ready", job_id: "refined", preview_url: "/refined.png" } });
  shareRequests(h)[0].fail({ errMsg: "offline" }); await flush();
  assert(h.page.data.shareError);
  h.page.onRetryShare(); h.page.onRetryShare();
  assert.equal(shareRequests(h).length, 2);
  assert.match(shareRequests(h)[1].url, /\/refined\/share$/);
  reply(shareRequests(h)[1], { share_token: "refined-share" }); await flush();
  h.page.onHide(); await show(h);
  assert.equal(shareRequests(h).length, 2);
  assert.equal(h.page.data.shareError, "");
  assert.match(h.page.onShareAppMessage().path, /share=refined-share$/);
  h.page.adoptJob({ job_id: "refined", preview_url: "/refined.png" });
  assert.equal(h.page.data.shareToken, "refined-share", "同作品重新交接仍可展示已准备的分享入口");
});

test("隐藏时完成的生成不准备分享，回屏才准备，登出丢弃回包", async () => {
  const { h } = await index();
  h.page.onHide();
  h.page.adoptJob({ job_id: "hidden", preview_url: "/hidden.png" });
  assert.equal(shareRequests(h).length, 0);
  await show(h);
  assert.equal(shareRequests(h).length, 1);
  h.app.logout();
  reply(shareRequests(h)[0], { share_token: "expired-owner" }); await flush();
  assert.equal(h.page.data.shareToken, null);
});

test("精修请求悬挂也在绝对截止时退出，迟到 ready 不改写超时", async () => {
  const { h, clock } = await index();
  h.page.adoptJob({ job_id: "photo", preview_url: "/p.png", refined: { status: "pending" } });
  clock.advance(5000);
  const pending = h.requests.at(-1);
  clock.advance(295000);
  assert.equal(h.page.data.job.refineFailed, "timeout");
  assert.equal(h.page.data.job.refining, false);
  assert.equal(clock.pending.size, 0);
  reply(pending, { refined: { status: "ready", job_id: "late", preview_url: "/late.png" } }); await flush();
  assert.equal(h.page.data.job.refined, null);
  h.page.onRetryRefined();
  reply(h.requests.at(-1), { refined: { status: "ready", job_id: "late", preview_url: "/late.png" } }); await flush();
  assert.equal(h.page.data.job.refined.jobId, "late");
});

test("精修在截止边界不再启动新 GET", async () => {
  const { h, clock } = await index();
  h.page.adoptJob({ job_id: "photo", preview_url: "/p.png", refined: { status: "pending" } });
  const answered = new Set();
  for (let elapsed = 5000; elapsed <= 295000; elapsed += 5000) {
    clock.advance(5000);
    const r = h.requests.at(-1);
    if (/\/jobs\//.test(r.url) && !answered.has(r)) { answered.add(r); reply(r, { refined: { status: "pending" } }); }
    await flush();
  }
  const count = h.requests.length;
  clock.advance(5000);
  assert.equal(h.requests.length, count);
  assert.equal(h.page.data.job.refineFailed, "timeout");
});

test("首次回包已明确精修失败时显示失败状态", async () => {
  const { h } = await index();
  h.page.adoptJob({ job_id: "photo", preview_url: "/p.png", refined: { status: "failed" } });
  assert.equal(h.page.data.job.refineFailed, "failed");
});

async function market() {
  const h = createWeapp(); h.storage.set("session_token", "owner");
  h.load("pages/market/market.js"); await show(h);
  reply(h.requests.at(-1), { categories: [{ name: "动物", count: 4 }] }); await flush();
  reply(h.requests.at(-1), { items: [{ id: "cat", title: "猫", preview_url: "/cat.png" }], next_cursor: 1 }); await flush();
  return h;
}
async function failAccount(h) {
  h.page.onHide(); h.page.onShow(); await flush();
  h.requests.at(-1).fail({ errMsg: "offline" }); await flush();
}

test("图库临时账号失败保留列表与游标，重试只恢复账号且禁用旧操作", async () => {
  const h = await market(); await failAccount(h);
  assert.equal(h.page.data.items.length, 1);
  assert(h.page.data.accountError);
  h.page.onItemTap(event({ id: "cat" }));
  assert.equal(h.navigations.length, 0);
  const before = h.requests.length;
  h.page.onRetry(); h.page.onRetry(); await flush();
  reply(h.requests.at(-1), ME); await flush();
  assert.equal(h.requests.length, before + 1);
  assert.equal(h.page.data.nextCursor, 1);
  assert.equal(h.page.data.accountError, "");
  h.page.onItemTap(event({ id: "cat" }));
  assert.equal(h.navigations.length, 1);
});

test("图库账号重试成功后仍保留原翻页错误及重试游标", async () => {
  const h = await market();
  h.page.onReachBottom(); h.requests.at(-1).fail({ errMsg: "offline" }); await flush();
  const listError = h.page.data.loadError;
  await failAccount(h);
  h.page.onRetry(); await flush(); reply(h.requests.at(-1), ME); await flush();
  assert.equal(h.page.data.loadError, listError);
  h.page.onRetry();
  assert.match(h.requests.at(-1).url, /cursor=1$/);
});

const historyRows = Array.from({ length: 30 }, (_, i) => ({ id: "id" + i,
  title: i === 11 ? "写字: 春天" : "AI: 猫" + i, preview_url: "/preview" + i }));
async function history() {
  const h = createWeapp(); h.storage.set("session_token", "owner");
  h.load("pages/history/history.js"); await show(h);
  reply(h.requests.at(-1), { items: historyRows }); await flush();
  for (let i = 0; i < 6; i++) { h.downloads[i].success({ statusCode: 200, tempFilePath: "t" + i }); await flush(); }
  return h;
}
test("收藏回屏后旧视口不能触发新展示代次下载", async () => {
  const h = await history(); const old = h.observers.at(-1);
  h.page.onHide(); await show(h);
  reply(h.requests.at(-1), { items: historyRows }); await flush();
  for (let i = 6; i < 12; i++) { h.downloads[i].success({ statusCode: 200, tempFilePath: "t" + i }); await flush(); }
  const before = h.downloads.length;
  old.cb({ dataset: { id: "id29" }, intersectionRatio: 1 }); await flush();
  assert.equal(h.downloads.length, before);
  h.observers.at(-1).cb({ dataset: { id: "id29" }, intersectionRatio: 1 }); await flush();
  assert.equal(h.downloads.length, before + 1);
});

test("收藏刷新优先当前筛选，不能把隐藏作品排到可见作品前面", async () => {
  const h = await history();
  h.page.setData({ filterKind: "write" });
  const before = h.downloads.length;
  h.page.refresh(); reply(h.requests.at(-1), { items: historyRows }); await flush();
  assert.equal(h.page.data.filtered[0].savedId, "id11");
  assert.equal(h.downloads.length, before + 1);
  assert(h.downloads.at(-1).url.endsWith("/preview11"));
});
