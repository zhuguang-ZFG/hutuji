const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

const fonts = [
  { id: "blood8-2_kvenjoy_blood8", name: "动力巢科技·速写" },
  { id: "blood8真迹_xiongzai_blood8", name: "动力巢科技·真迹手写" },
];

async function setup(statusCode = 200) {
  const h = createWeapp();
  h.load("pages/index/index.js");
  h.page.onLoad();
  h.page.adoptJob = () => {};
  h.page.loadFonts();
  // 页面初始化还会预取订阅配置；按端点匹配，避免把字库响应喂给别的请求。
  h.requests.find((request) => request.url.endsWith("/api/fonts")).success({ statusCode, data: { fonts } });
  await flush();
  h.page.setData({ writeText: "测试文字" });
  return h;
}

async function submit(h) {
  h.page.onLayoutPreview();
  const request = h.requests.at(-1);
  assert.ok(request.url.endsWith("/api/draw/write"));
  const payload = JSON.parse(JSON.stringify(request.data));
  request.success({ statusCode: 200, data: {} });
  await flush();
  return payload;
}

test("字体列表加载后默认提交不指定字体", async () => {
  const h = await setup();
  assert.equal(h.page.data.fontNames[h.page.data.fontIndex], "默认字体");
  assert.equal(Object.hasOwn(await submit(h), "font"), false);
});

test("明确选字库才提交 ID，切回默认后再次省略", async () => {
  const h = await setup();
  const selected = h.page.data.fonts.findIndex((font) => font.id === fonts[0].id);
  h.page.onFontChange({ detail: { value: String(selected) } });
  assert.equal((await submit(h)).font, fonts[0].id);
  h.page.onFontChange({ detail: { value: "0" } });
  assert.equal(Object.hasOwn(await submit(h), "font"), false);
});

test("字库列表加载失败仍可提交默认排版", async () => {
  const h = await setup(503);
  assert.equal(Object.hasOwn(await submit(h), "font"), false);
});
