const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");
const event = (id) => ({ currentTarget: { dataset: { id } } });
async function setup() {
  const h = createWeapp(); h.load("pages/history/history.js");
  h.page.refresh(); h.requests.at(-1).success({ statusCode: 200, data: { items: [
    { id: "a", title: "AI: 小猫", saved_at: 10 }, { id: "b", title: "写字: 春天", saved_at: 20 },
    { id: "c", title: "猫的照片.jpg", saved_at: 30 }, { id: "d", title: "AI: 小猫与月亮", saved_at: 40 },
  ] } }); await flush(); return h;
}
test("作品可按关键词、类别和时间组合查找", async () => {
  const h = await setup(); h.page.onSearch({ detail: { value: "小猫" } });
  assert.deepEqual(Array.from(h.page.data.filtered, (j) => j.savedId), ["d", "a"]);
  h.page.onSortToggle(); assert.equal(h.page.data.filtered[0].savedId, "a");
  h.page.onClearSearch(); h.page.onFilter({ currentTarget: { dataset: { kind: "upload" } } });
  assert.deepEqual(Array.from(h.page.data.filtered, (j) => j.savedId), ["c"]);
  h.page.onClearFilters(); assert.equal(h.page.data.filtered.length, 4);
});
test("搜索重排后的重画按作品ID选择，不误用原列表下标", async () => {
  const h = await setup(); h.page.setData({ filtered: [h.page.data.jobs.find((j) => j.savedId === "d"), h.page.data.jobs.find((j) => j.savedId === "a")] });
  h.page.onRedraw({ currentTarget: { dataset: { id: "a", idx: 0 } } });
  assert.equal(h.modals.at(-1).content, "小猫");
});
test("历史发送展示完整未开始原因并使用完整等待预算", async () => {
  const h = await setup(); h.page.onSend(event("a")); h.modals.at(-1).success({ confirm: true });
  assert.equal(h.requests.at(-1).timeout, 150000);
  h.requests.at(-1).success({ statusCode: 200, data: { status: "sent", detail: "自动开始画未完成：请手动确认" } });
  await flush(); assert.match(h.modals.at(-1).content, /请手动确认/);
});
test("历史和图库的观察器显式监听多张卡片", () => {
  for (const name of ["history/history", "market/market"]) {
    const h = createWeapp(); h.load("pages/" + name + ".js"); let options;
    h.wx.createIntersectionObserver = (_owner, opts) => {
      options = opts; const ob = { relativeToViewport: () => ob, observe() {}, disconnect() {} }; return ob;
    };
    h.page._observePreviews(); assert.equal(options.observeAll, true);
  }
});
