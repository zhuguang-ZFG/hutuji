// 2026-10-08 照片续作批第 3 项（图库）：返回详情保留分类与已加载页、翻页失败从原游标
// 重试、下拉刷新仍重新读取；旧请求/旧账号/隐藏页不得回写或续派预览。
const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush, persistedPreviewPath } = require("./harness");

const ME = { phone_bound: true, active_device_mac: "aa:bb:cc:dd:ee:ff",
  binding: { bound: true, device_mac: "aa:bb:cc:**:**:**" } };
const CATEGORIES = { categories: [{ name: "动物", count: 35 }, { name: "植物", count: 20 }] };
const reply = (request, data, statusCode = 200) => request.success({ statusCode, data });
const event = (data) => ({ currentTarget: { dataset: data } });
const cards = (count, prefix = "animal") => Array.from({ length: count }, (_, i) => ({
  id: prefix + i, title: "作品 " + i, preview_url: "/preview/" + prefix + i,
}));

function setup() {
  const h = createWeapp();
  h.storage.set("session_token", "owner");
  h.load("pages/market/market.js");
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
// 从详情页返回：onShow 仍会软闸查一次 /me，但不得再读目录与列表。
async function returnToList(h) {
  const before = h.requests.length;
  h.page.onShow();
  await flush();
  assert(h.requests.at(-1).url.endsWith("/api/auth/me"));
  reply(h.requests.at(-1), ME);
  await flush();
  assert.equal(h.requests.length, before + 1, "返回图库不得重读目录或列表");
}

test("从详情返回保留分类与已加载页，只恢复预览队列", async () => {
  const h = setup();
  await showList(h, cards(3), 3);
  h.page.onHide();
  await returnToList(h);
  assert.equal(h.page.data.categories.length, 2);
  assert.equal(h.page.data.activeCategory, "动物");
  assert.equal(h.page.data.items.length, 3);
  assert.equal(h.page.data.nextCursor, 3);
  assert.equal(h.page.data.loading, false);
});

test("返回后已缓存预览直接复用，在途与视口内的预览不重复派发", async () => {
  const h = setup();
  await showList(h, cards(30), 30);
  assert.equal(h.downloads.length, 3);
  for (let i = 0; i < 3; i++) h.downloads[i].success({ statusCode: 200, tempFilePath: "t" + i });
  await flush();
  assert.equal(h.downloads.length, 6); // 首屏 6 张泵出，其中 3 张在途
  h.page.onHide();
  await returnToList(h);
  assert.equal(h.downloads.length, 6, "返回不得重下已缓存或仍在途的预览");
  assert.equal(h.page.data.items[0].previewTmp, persistedPreviewPath("animal0|/preview/animal0"));
  assert.equal(h.page.data.items[0].previewState, "ready");
  for (let i = 3; i < 6; i++) h.downloads[i].success({ statusCode: 200, tempFilePath: "t" + i });
  await flush();
  assert.equal(h.page.data.items[3].previewTmp, persistedPreviewPath("animal3|/preview/animal3"));
  assert.equal(h.page.data.items[6].previewTmp, ""); // 首屏外仍未下载
  const ob = h.observers.at(-1);
  assert.equal(ob.selector, ".gallery-card");
  ob.cb({ dataset: { id: "animal10" }, intersectionRatio: 0.5 });
  await flush();
  assert.equal(h.downloads.length, 7, "返回后可视区懒加载须继续派发");
  assert.ok(h.downloads.at(-1).url.endsWith("/preview/animal10"));
});

test("翻页失败后重试仍从原游标读取，不重读目录也不丢已加载页", async () => {
  const h = setup();
  await showList(h, cards(2), 2);
  h.page.onReachBottom();
  assert(h.requests.at(-1).url.endsWith("cursor=2"));
  reply(h.requests.at(-1), { detail: "作品加载失败，请重试" }, 503);
  await flush();
  assert.equal(h.page.data.loadError, "作品加载失败，请重试");
  assert.equal(h.page.data.loading, false);
  h.page.onRetry();
  await flush();
  assert(h.requests.at(-1).url.endsWith("/api/market/list?category=%E5%8A%A8%E7%89%A9&cursor=2"),
    "重试必须回到原游标，而不是重读目录");
  assert.equal(h.page.data.items.length, 2, "重试期间已加载页不能清空");
  reply(h.requests.at(-1), { items: cards(2, "more"), next_cursor: null });
  await flush();
  assert.equal(h.page.data.items.length, 4);
  assert.equal(h.page.data.nextCursor, null);
  assert.equal(h.page.data.loadError, "");
});

test("主动下拉刷新仍重新读取目录与首页", async () => {
  const h = setup();
  await showList(h, cards(3), 3);
  const pending = h.page.onPullDownRefresh();
  await flush();
  assert(h.requests.at(-1).url.endsWith("/api/auth/me"));
  reply(h.requests.at(-1), ME);
  await flush();
  assert(h.requests.at(-1).url.endsWith("/api/market/categories"), "下拉刷新必须重读目录");
  reply(h.requests.at(-1), CATEGORIES);
  await flush();
  assert(h.requests.at(-1).url.endsWith("cursor=0"));
  reply(h.requests.at(-1), { items: cards(1, "fresh"), next_cursor: null });
  await pending;
  await flush();
  assert.equal(h.page.data.items.length, 1);
  assert.equal(h.page.data.items[0].id, "fresh0");
});

test("隐藏页迟到的翻页回包不写入列表", async () => {
  const h = setup();
  await showList(h, cards(2), 2);
  h.page.onReachBottom();
  const stale = h.requests.at(-1);
  h.page.onHide();
  reply(stale, { items: cards(5, "late"), next_cursor: 7 });
  await flush();
  assert.equal(h.page.data.items.length, 2);
  assert.equal(h.page.data.nextCursor, 2);
});

test("换账号后返回不保留上一账号的分类与列表", async () => {
  const h = setup();
  await showList(h, cards(3), 3);
  h.app.logout();
  h.storage.set("session_token", "next-user");
  h.page.onShow();
  await flush();
  assert.equal(h.page.data.categories.length, 0);
  assert.equal(h.page.data.items.length, 0);
  assert.equal(h.page.data.nextCursor, null);
  reply(h.requests.at(-1), ME);
  await flush();
  reply(h.requests.at(-1), CATEGORIES);
  await flush();
  reply(h.requests.at(-1), { items: cards(1, "next"), next_cursor: null });
  await flush();
  assert.equal(h.page.data.items.length, 1);
  assert.equal(h.page.data.items[0].id, "next0");
});

test("返回后切换分类仍从该分类第一页读取", async () => {
  const h = setup();
  await showList(h, cards(3), 3);
  h.page.onHide();
  await returnToList(h);
  h.page.onCategoryTap(event({ name: "植物" }));
  await flush();
  assert(h.requests.at(-1).url.includes("category=%E6%A4%8D%E7%89%A9&cursor=0"));
  assert.equal(h.page.data.items.length, 0);
  reply(h.requests.at(-1), { items: cards(2, "plant"), next_cursor: null });
  await flush();
  assert.equal(h.page.data.activeCategory, "植物");
  assert.equal(h.page.data.items.length, 2);
});

test("目录刷新失败后重试目录，不能误作旧列表翻页", async () => {
  const h = setup();
  await showList(h, cards(2), 2);
  h.page.onPullDownRefresh();
  await flush();
  reply(h.requests.at(-1), ME);
  await flush();
  reply(h.requests.at(-1), { detail: "目录暂不可用" }, 503);
  await flush();
  h.page.onRetry();
  await flush();
  assert(h.requests.at(-1).url.endsWith("/api/market/categories"));
  assert.equal(h.page.data.items.length, 2);
});

test("空分类返回仍保留当前分类，首页失败只重试当前分类", async () => {
  const h = setup();
  await showList(h);
  h.page.onCategoryTap(event({ name: "植物" }));
  reply(h.requests.at(-1), { detail: "当前分类读取失败" }, 503);
  await flush();
  h.page.onRetry();
  await flush();
  assert(h.requests.at(-1).url.endsWith("category=%E6%A4%8D%E7%89%A9&cursor=0"));
  reply(h.requests.at(-1), { items: [], next_cursor: null });
  await flush();
  h.page.onHide();
  await returnToList(h);
  assert.equal(h.page.data.activeCategory, "植物");
  assert.equal(h.page.data.items.length, 0);
});

test("重试中的重复点击不并发读取同一游标", async () => {
  const h = setup();
  await showList(h, cards(2), 2);
  h.page.onReachBottom();
  reply(h.requests.at(-1), { detail: "读取失败" }, 503);
  await flush();
  h.page.onRetry();
  const count = h.requests.length;
  h.page.onRetry();
  assert.equal(h.requests.length, count);
});

test("回屏后旧视口回调不能为新展示代次派发预览", async () => {
  const h = setup();
  await showList(h, cards(30), 30);
  for (let i = 0; i < 6; i++) {
    h.downloads[i].success({ statusCode: 200, tempFilePath: "t" + i });
    await flush();
  }
  const stale = h.observers.at(-1);
  h.page.onHide();
  await returnToList(h);
  const before = h.downloads.length;
  stale.cb({ dataset: { id: "animal20" }, intersectionRatio: 1 });
  await flush();
  assert.equal(h.downloads.length, before);
  h.observers.at(-1).cb({ dataset: { id: "animal20" }, intersectionRatio: 1 });
  await flush();
  assert.equal(h.downloads.length, before + 1);
});
