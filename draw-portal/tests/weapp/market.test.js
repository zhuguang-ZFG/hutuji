const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { createWeapp, flush, persistedPreviewPath } = require("./harness");

const ME = { phone_bound: true, active_device_mac: "aa:bb:cc:dd:ee:ff",
  binding: { bound: true, device_mac: "aa:bb:cc:**:**:**" } };
const CATEGORIES = { categories: [{ name: "动物", count: 35 }, { name: "植物", count: 20 }] };
const DETAIL = { id: "sample", title: "小猫", license: "CC-BY-4.0",
  attribution: "示例作者，CC BY 4.0", changes: "转为单线绘图", revision: "a".repeat(64),
  preview_url: "/api/market/item/sample/preview.png?v=1" };
const reply = (request, data) => request.success({ statusCode: 200, data });
// 同 history：页面展示持久缓存路径而非下载临时路径。

const event = (data) => ({ currentTarget: { dataset: data } });
const cards = (count, prefix = "animal") => Array.from({ length: count }, (_, i) => ({
  id: prefix + i, title: "作品 " + i, preview_url: "/preview/" + prefix + i,
}));

function setup(detail = false) {
  const h = createWeapp();
  h.storage.set("session_token", "owner");
  h.load(detail ? "pages/market/detail.js" : "pages/market/market.js");
  if (detail) h.page.onLoad({ id: "sample" });
  return h;
}
async function authorize(h, me = ME) {
  h.page.onShow();
  await flush();
  assert(h.requests.at(-1).url.endsWith("/api/auth/me"));
  reply(h.requests.at(-1), me);
  await flush();
}
async function showList(h, items = cards(2), next = null) {
  await authorize(h);
  reply(h.requests.at(-1), CATEGORIES);
  await flush();
  reply(h.requests.at(-1), { items, next_cursor: next });
  await flush();
}
async function showDetail(h, download = true) {
  await authorize(h);
  reply(h.requests.at(-1), DETAIL);
  await flush();
  if (download) {
    h.downloads.at(-1).success({ statusCode: 200, tempFilePath: "wxfile://sample" });
    await flush();
  }
}

test("手机号闸未绑定不强跳、不读取目录，控制目标来自 active_device_mac", async () => {
  const h = setup();
  await authorize(h, { phone_bound: false });
  assert.equal(h.requests.length, 1);
  // 2026-09-24 合规批：浏览时不强跳授权页（0.3.18 审核驳回），页面渲染可浏览未登录态。
  assert(!h.tabs.some((tab) => tab.url.includes("profile")));
  assert.match(h.page.data.loadError, /我的/);
  const detail = setup(true);
  await showDetail(detail);
  detail.page.onSend();
  assert.equal(detail.requests.at(-1).data.device_mac, ME.active_device_mac);
  assert.equal(detail.requests.at(-1).data.revision, DETAIL.revision);
});

test("分页使用 next_cursor，图片最多三张并发", async () => {
  const h = setup();
  await showList(h, cards(30), 30);
  assert.equal(h.page.data.nextCursor, 30);
  assert.equal(h.downloads.length, 3);
  h.downloads[0].success({ statusCode: 200, tempFilePath: "wxfile://zero" });
  await flush();
  assert.equal(h.downloads.length, 4);
  assert.equal(h.page.data.items[0].previewTmp, persistedPreviewPath("animal0|/preview/animal0"));
  h.page.onReachBottom();
  assert(h.requests.at(-1).url.endsWith("cursor=30"));
  reply(h.requests.at(-1), { items: cards(5, "last"), next_cursor: null });
  await flush();
  assert.equal(h.page.data.items.length, 35);
  assert.equal(h.page.data.nextCursor, null);
});

test("首屏外预览不主动下载，进视口经观察者派发（可视区懒加载）", async () => {
  const h = setup();
  await showList(h, cards(30), 30);
  assert.equal(h.downloads.length, 3); // 并发 3，首屏队列 6
  for (let i = 0; i < 3; i++) h.downloads[i].success({ statusCode: 200, tempFilePath: "t" + i });
  await flush();
  assert.equal(h.downloads.length, 6); // 首屏 6 张泵完即停
  for (let i = 3; i < 6; i++) h.downloads[i].success({ statusCode: 200, tempFilePath: "t" + i });
  await flush();
  assert.equal(h.downloads.length, 6); // 首屏外不自动派发
  const ob = h.observers.at(-1);
  assert.equal(ob.selector, ".gallery-card");
  ob.cb({ dataset: { id: "animal10" }, intersectionRatio: 0.5 });
  await flush();
  assert.equal(h.downloads.length, 7);
  assert.ok(h.downloads.at(-1).url.endsWith("/preview/animal10"));
  h.downloads.at(-1).success({ statusCode: 200, tempFilePath: "t10" });
  await flush();
  assert.equal(h.page.data.items.find((c) => c.id === "animal10").previewTmp, persistedPreviewPath("animal10|/preview/animal10"));
});

test("连续切分类后旧列表与图片不会覆盖新卡片", async () => {
  const h = setup();
  await showList(h);
  const oldImage = h.downloads[0];
  h.page.onCategoryTap(event({ name: "植物" }));
  const plants = h.requests.at(-1);
  h.page.onCategoryTap(event({ name: "动物" }));
  const animals = h.requests.at(-1);
  reply(animals, { items: cards(1, "latest"), next_cursor: null });
  reply(plants, { items: cards(2, "stale"), next_cursor: null });
  oldImage.success({ statusCode: 200, tempFilePath: "wxfile://old" });
  await flush();
  assert.equal(h.page.data.items.length, 1);
  assert.equal(h.page.data.items[0].id, "latest0");
  assert.equal(h.page.data.items[0].previewTmp, "");
});

test("隐藏停止派发，返回复用同账号临时图片并解除 loading", async () => {
  const h = setup();
  await showList(h, cards(5));
  h.page.onHide();
  h.downloads[0].success({ statusCode: 200, tempFilePath: "wxfile://cached" });
  await flush();
  assert.equal(h.downloads.length, 3);
  await showList(h, cards(5));
  assert.equal(h.page.data.items[0].previewTmp, persistedPreviewPath("animal0|/preview/animal0"));
  assert.equal(h.page.data.loading, false);
});

test("新账号不会看到旧图库状态，401 图片有可重试提示", async () => {
  const h = setup();
  await showList(h);
  const stale = h.downloads[0];
  h.app.logout();
  h.storage.set("session_token", "new-owner");
  h.page.onShow();
  assert.equal(h.page.data.items.length, 0);
  stale.success({ statusCode: 200, tempFilePath: "wxfile://old-owner" });
  await flush();
  assert.equal(h.page.data.items.length, 0);
  reply(h.requests.at(-1), ME);
  await flush();
  reply(h.requests.at(-1), CATEGORIES);
  await flush();
  reply(h.requests.at(-1), { items: cards(1, "new"), next_cursor: null });
  await flush();
  h.downloads.at(-1).success({ statusCode: 401 });
  await flush();
  assert.match(h.page.data.loadError, /登录已过期/);
});

test("预览超时或临时路径损坏只重试该图", async () => {
  const h = setup();
  await showList(h);
  h.downloads[0].fail({ errMsg: "timeout" });
  await flush();
  assert.equal(h.page.data.items[0].previewState, "error");
  h.page.onRetryPreview(event({ id: "animal0" }));
  assert.equal(h.downloads.length, 3);
  h.downloads[2].success({ statusCode: 200, tempFilePath: "wxfile://fresh" });
  await flush();
  h.page.onPreviewError(event({ id: "animal0", url: "wxfile://stale" }));
  assert.equal(h.page.data.items[0].previewTmp, persistedPreviewPath("animal0|/preview/animal0"));
  h.page.onPreviewError(event({ id: "animal0", url: persistedPreviewPath("animal0|/preview/animal0") }));
  assert.equal(h.page.data.items[0].previewState, "error");
});

test("详情未看见预览不能发送，发送不重复且隐藏回包解除忙态", async () => {
  const h = setup(true);
  await showDetail(h, false);
  const before = h.requests.length;
  h.page.onSend();
  assert.equal(h.requests.length, before);
  h.downloads[0].success({ statusCode: 200, tempFilePath: "wxfile://detail" });
  await flush();
  h.page.onSend();
  h.page.onSend();
  assert.equal(h.requests.length, before + 1);
  h.page.onHide();
  reply(h.requests.at(-1), { status: "sent", detail: "ok" });
  await flush();
  assert.equal(h.page.data.sending, false);
  assert.match(h.page.data.actionHint, /已发送/);
  assert(!h.page.data.actionHint.includes("开始绘制"));
});

test("发送成功但自动开画未完成时透出 detail 指引，而非「已发送」", async () => {
  // 2026-09-25 事故回归：confirm 重试窗口耗尽仍 sent 时 push.py detail 带
  // 「自动开始画未完成：…可在小派屏幕或控制页点『开始画』」，客户端必须透出，
  // 否则用户看到「已发送」而机器不动。字符串标记与服务端 push.py 字面量耦合。
  const h = setup(true);
  await showDetail(h);
  h.page.onSend();
  const detail = "已发送；自动开始画未完成：设备未确认。可在小派屏幕或控制页点「开始画」，或稍后重新发送";
  reply(h.requests.at(-1), { status: "sent", detail });
  await flush();
  assert.equal(h.page.data.actionHint, detail);
  assert(!h.page.data.actionHint.includes("留意设备状态"));
  assert.equal(h.page.data.sending, false);
});

test("收藏路径 detail 含未完成标记不触发透出，仍显示已收藏", async () => {
  // notStarted 闸只对 kind==="send" 生效；favorite 回包 detail 被忽略。
  const h = setup(true);
  await showDetail(h);
  h.page.onFavorite();
  reply(h.requests.at(-1), { status: "saved", detail: "自动开始画未完成：不应透出" });
  await flush();
  assert.match(h.page.data.actionHint, /已收藏/);
  assert(!h.page.data.actionHint.includes("自动开始画未完成"));
});

test("收藏保留同账号结果，卸载后的回包无权更新", async () => {
  const h = setup(true);
  await showDetail(h);
  h.page.onFavorite();
  assert.equal(h.requests.at(-1).data.device_mac, ME.active_device_mac);
  h.page.onHide();
  reply(h.requests.at(-1), { status: "saved", id: "saved-1" });
  await flush();
  assert.equal(h.page.data.favoriting, false);
  assert.match(h.page.data.actionHint, /已收藏/);
  await showDetail(h);
  h.page.onSend();
  h.page.onUnload();
  const snapshot = JSON.stringify(h.page.data);
  reply(h.requests.at(-1), { status: "sent" });
  await flush();
  assert.equal(JSON.stringify(h.page.data), snapshot);
});

test("失败显示可理解的提示，CC-BY 署名出现在详情与发送卡", async () => {
  const h = setup(true);
  await showDetail(h);
  h.page.onSend();
  reply(h.requests.at(-1), { status: "push_failed", detail: "设备离线，请重新连接" });
  await flush();
  assert.equal(h.page.data.actionHint, "设备离线，请重新连接");
  const markup = fs.readFileSync(path.join(__dirname, "../../weapp/pages/market/detail.wxml"), "utf8");
  assert(markup.includes('class="credit-text">{{attribution}}'));
  assert(markup.includes('本次作品署名：{{attribution}}'));
});

test("同一条目换账号必须重读，坏署名拒绝发送", async () => {
  const h = setup(true);
  await showDetail(h);
  h.app.logout();
  h.storage.set("session_token", "next-user");
  await authorize(h);
  assert.equal(h.page.data.previewTmp, "");
  reply(h.requests.at(-1), Object.assign({}, DETAIL, { attribution: "" }));
  await flush();
  assert.match(h.page.data.loadError, /不完整/);
  const before = h.requests.length;
  h.page.onSend();
  assert.equal(h.requests.length, before);
});
