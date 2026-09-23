const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp } = require("./harness");

test("预览持久缓存：写入可读回，文件被系统回收即失效并剔除索引", () => {
  const h = createWeapp();
  const cache = h.load("utils/preview_cache.js");
  assert.equal(cache.get("a|/x"), "");
  const file = cache.put("a|/x", "wxfile://temp1");
  assert.ok(file.startsWith("wxfile://userdata/preview-cache/"));
  assert.equal(cache.get("a|/x"), file);
  h.files.delete(file); // 模拟系统回收 USER_DATA_PATH 文件
  assert.equal(cache.get("a|/x"), "");
  assert.equal(h.storage.get("preview_cache_index_v1").length, 0);
});

test("LRU 上界 128（与内存层对齐）：逐出最旧条目并同步删除文件", () => {
  const h = createWeapp();
  const cache = h.load("utils/preview_cache.js");
  const first = cache.put("k0", "t0");
  for (let i = 1; i <= 128; i++) cache.put("k" + i, "t" + i);
  assert.equal(h.storage.get("preview_cache_index_v1").length, 128);
  assert.equal(cache.get("k0"), "");
  assert.equal(h.files.has(first), false);
  assert.ok(cache.get("k128"));
});

test("语义键不串图：字符替换后可碰撞的键落到不同文件", () => {
  const h = createWeapp();
  const cache = h.load("utils/preview_cache.js");
  const fa = cache.put("a.b", "t1");
  const fb = cache.put("a/b", "t2");
  assert.notEqual(fa, fb);
  assert.equal(cache.get("a.b"), fa);
  assert.equal(cache.get("a/b"), fb);
});

test("旧语义命名文件一次性清扫，哈希名文件保留", () => {
  const h = createWeapp();
  // 清扫是每进程一次性的（首次 get/put 触发）：旧文件必须在首次缓存操作前在场
  h.files.set("wxfile://userdata/preview-cache/write-b__preview-b.png", "legacy");
  const cache = h.load("utils/preview_cache.js");
  const keep = cache.put("keep", "t1");
  assert.equal(h.files.has("wxfile://userdata/preview-cache/write-b__preview-b.png"), false);
  assert.equal(cache.get("keep"), keep);
});

test("remove 与 clear 同时清理文件与索引", () => {
  const h = createWeapp();
  const cache = h.load("utils/preview_cache.js");
  const f1 = cache.put("a", "t1");
  cache.put("b", "t2");
  cache.remove("a");
  assert.equal(cache.get("a"), "");
  assert.equal(h.files.has(f1), false);
  cache.clear();
  assert.equal(h.storage.get("preview_cache_index_v1"), undefined);
  assert.equal([...h.files.keys()].filter((k) => k.includes("preview-cache")).length, 0);
});
