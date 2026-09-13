const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

const rows = [
  { id: "draw-a", title: "AI: 猫", preview_url: "/preview-a" },
  { id: "write-b", title: "写字: 你好", preview_url: "/preview-b" },
];
function setup() {
  const h = createWeapp();
  h.load("pages/history/history.js");
  return h;
}
async function load(h, items = rows) {
  h.page.refresh();
  h.requests.at(-1).success({ statusCode: 200, data: { items } });
  await flush();
}

test("收藏筛选已提供选项，私有图片下载后同步到实际显示列表", async () => {
  const h = setup();
  h.storage.set("session_token", "owner-session");
  await load(h);
  assert.equal(h.page.data.kinds.length, 4);
  h.page.onFilter({ currentTarget: { dataset: { kind: "write" } } });
  assert.equal(h.downloads[1].header["x-session"], "owner-session");
  h.downloads[1].success({ statusCode: 200, tempFilePath: "wxfile://b" });
  await flush();
  assert.equal(h.page.data.filtered[0].previewUrl, "wxfile://b");
});

test("筛选后长按删除使用收藏 ID，不把筛选索引当原列表索引", async () => {
  const h = setup();
  await load(h);
  h.page.onFilter({ currentTarget: { dataset: { kind: "write" } } });
  h.page.onLongPress({ currentTarget: { dataset: { id: "write-b", idx: 0 } } });
  h.actionSheets.at(-1).success({ tapIndex: 0 });
  h.modals.at(-1).success({ confirm: true });
  assert(h.requests.at(-1).url.endsWith("/write-b"));
  h.requests.at(-1).success({ statusCode: 200, data: { status: "ok" } });
  await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(h.page.data.jobs.map((j) => j.savedId))), ["draw-a"]);
  assert.equal(h.page.data.filtered.length, 0);
});

test("新列表和退出登录都隔离旧图片及列表回包", async () => {
  const h = setup();
  await load(h);
  const oldImage = h.downloads[0];
  await load(h, [{ id: "new", title: "AI: 树" }]);
  oldImage.success({ statusCode: 200, tempFilePath: "wxfile://old" });
  await flush();
  assert.equal(h.page.data.jobs[0].previewUrl, "");
  h.page.refresh();
  const stale = h.requests.at(-1);
  h.app.logout();
  stale.success({ statusCode: 200, data: { items: rows } });
  await flush();
  assert.equal(h.page.data.jobs[0].savedId, "new");
});

test("离页后的发送确认不再下发，连续点击只开一次确认", () => {
  const h = setup();
  const event = { currentTarget: { dataset: { id: "draw-a" } } };
  h.page.onSend(event);
  h.page.onSend(event);
  assert.equal(h.modals.length, 1);
  h.page.onHide();
  h.modals[0].success({ confirm: true });
  assert.equal(h.requests.length, 0);
});

test("收藏网络失败不会冒充没有收藏", async () => {
  const h = setup();
  h.page.refresh();
  h.requests.at(-1).fail();
  await flush();
  assert.match(h.page.data.loadError, /网络/);
  assert.equal(h.page.data.loading, false);
});
