const assert = require("node:assert/strict");
const test = require("node:test");
const { createWeapp, flush } = require("./harness");

const rows = [
  { id: "draw-a", title: "AI: 猫", preview_url: "/preview-a" },
  { id: "write-b", title: "写字: 你好", preview_url: "/preview-b" },
];
// 新契约（2026-09-23 方案3）：下载成功即落持久缓存，页面展示持久路径而非临时路径。
const persisted = (k) => "wxfile://userdata/preview-cache/" + k.replace(/[^A-Za-z0-9_-]/g, "_") + ".png";
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
  assert.equal(h.page.data.filtered[0].previewUrl, persisted("write-b|/preview-b"));
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

async function show(h) {
  h.page.onShow(); await flush();
  h.requests.at(-1).success({ statusCode: 200, data: { phone_bound: true } }); await flush();
}

for (const completesHidden of [false, true]) {
  test(`重生成切页后${completesHidden ? '隐藏时' : '回屏后'}完成保留结果，回屏只交接一次`, async () => {
    const h = setup(); await show(h);
    h.page._redraw('draw', '猫');
    const generation = h.requests.at(-1);
    assert.equal(generation.timeout, 300000);
    h.page.onHide();
    if (completesHidden) {
      generation.success({ statusCode: 200, data: { job_id: 'new-draw', preview_url: '/preview' } }); await flush();
      assert.equal(h.tabs.length, 0);
    }
    await show(h);
    if (!completesHidden) {
      assert.equal(h.page.data.redrawing, true);
      generation.success({ statusCode: 200, data: { job_id: 'new-draw', preview_url: '/preview' } }); await flush();
    }
    assert.equal(h.app.globalData.pendingAdopt.job_id, 'new-draw');
    assert.equal(h.tabs.length, 1);
    assert.equal(h.requests.filter((r) => r.url.endsWith('/draw/generate')).length, 1);
    assert.equal(h.page.data.redrawing, false);
  });
}

for (const leave of ['logout', 'onUnload']) {
  test(`${leave} 后的重生成结果不能交给当前页`, async () => {
    const h = setup(); await show(h);
    h.page._redraw('draw', '旧作品');
    const generation = h.requests.at(-1);
    if (leave === 'logout') h.app.logout(); else h.page.onUnload();
    generation.success({ statusCode: 200, data: { job_id: 'old' } }); await flush();
    assert.equal(h.tabs.length, 0);
    assert.equal(!!h.app.globalData.pendingAdopt, false);
  });
}

test('重生成失败在隐藏时不弹窗，回屏后提供错误且不重复提交', async () => {
  const h = setup(); await show(h);
  h.page._redraw('draw', '猫');
  const generation = h.requests.at(-1);
  h.page.onHide(); generation.fail(); await flush();
  assert.equal(h.modals.length, 0);
  await show(h);
  assert.equal(h.page.data.redrawing, false);
  assert.match(h.modals.at(-1).content, /网络/);
});

test('账号读取失败不能显示为空收藏，重新读取也须完成账号校验', async () => {
  const h = setup(); h.page.onShow(); await flush();
  h.requests.at(-1).fail(); await flush();
  assert.match(h.page.data.loadError, /账号/);
  h.page.onRetry(); await flush();
  assert(h.requests.at(-1).url.endsWith('/auth/me'));
});

test('同账号刷新或返回历史复用已经下载的收藏预览', async () => {
  const h = setup();
  h.storage.set('session_token', 'owner-session');
  await load(h, [rows[0]]);
  h.downloads[0].success({ statusCode: 200, tempFilePath: 'wxfile://cached' }); await flush();
  h.page.onHide();
  await show(h);
  assert.equal(h.page.data.jobs[0].previewUrl, persisted('draw-a|/preview-a'));
  h.requests.at(-1).success({ statusCode: 200, data: { items: [rows[0]] } }); await flush();
  assert.equal(h.downloads.length, 1);
  assert.equal(h.page.data.filtered[0].previewUrl, persisted('draw-a|/preview-a'));
});

test('退出后即使返回同一收藏 ID 也不能复用前账号图片', async () => {
  const h = setup(); await load(h, [rows[0]]);
  h.downloads[0].success({ statusCode: 200, tempFilePath: 'wxfile://old' }); await flush();
  h.app.logout();
  h.page.onShow();
  assert.equal(h.page.data.jobs.length, 0);
  await load(h, [rows[0]]);
  assert.equal(h.downloads.length, 2);
  assert.equal(h.page.data.jobs[0].previewUrl, '');
});

test('收藏图片限量下载，切页后不继续启动剩余请求', async () => {
  const h = setup();
  const items = Array.from({ length: 8 }, (_, i) => ({ id: `saved-${i}`, preview_url: `/saved-${i}` }));
  await load(h, items);
  assert.equal(h.downloads.length, 3);
  h.downloads[0].success({ statusCode: 200, tempFilePath: 'wxfile://0' }); await flush();
  assert.equal(h.downloads.length, 4);
  h.page.onHide();
  h.downloads[1].success({ statusCode: 200, tempFilePath: 'wxfile://1' }); await flush();
  assert.equal(h.downloads.length, 4);
  assert.equal(h.page.data.jobs[1].previewUrl, '');
  await show(h);
  h.requests.at(-1).success({ statusCode: 200, data: { items } }); await flush();
  assert.equal(h.page.data.jobs[1].previewUrl, persisted('saved-1|/saved-1'));
});

test('收藏预览超时能单张重试，成功后恢复图片', async () => {
  const h = setup(); await load(h, [rows[0]]);
  assert.equal(h.downloads[0].timeout, 12000);
  h.downloads[0].fail({ errMsg: 'downloadFile:fail timeout' }); await flush();
  assert.equal(h.page.data.filtered[0].previewState, 'error');
  assert.match(h.page.data.filtered[0].previewError, /超时/);
  h.page.onRetryPreview({ currentTarget: { dataset: { id: rows[0].id } } });
  h.page.onRetryPreview({ currentTarget: { dataset: { id: rows[0].id } } });
  assert.equal(h.downloads.length, 2);
  h.downloads[1].success({ statusCode: 200, tempFilePath: 'wxfile://retried' }); await flush();
  assert.equal(h.page.data.filtered[0].previewState, 'ready');
  assert.equal(h.page.data.filtered[0].previewUrl, persisted('draw-a|/preview-a'));
});

test('临时图片失效显示重试，旧图片错误回调不清空新图片', async () => {
  const h = setup(); await load(h, [rows[0]]);
  h.downloads[0].success({ statusCode: 200, tempFilePath: 'wxfile://expired' }); await flush();
  const currentError = { currentTarget: { dataset: { id: rows[0].id, url: persisted('draw-a|/preview-a') } } };
  h.page.onPreviewError(currentError);
  assert.equal(h.page.data.jobs[0].previewState, 'error');
  h.page.onRetryPreview(currentError);
  h.downloads[1].success({ statusCode: 200, tempFilePath: 'wxfile://new' }); await flush();
  // 持久路径按内容键稳定：迟到的旧临时路径回调不匹配当前图，不得清空
  h.page.onPreviewError({ currentTarget: { dataset: { id: rows[0].id, url: 'wxfile://expired' } } });
  h.page.onPreviewError({ currentTarget: { dataset: { id: rows[0].id, url: 'wxfile://new-temp' } } });
  assert.equal(h.page.data.jobs[0].previewUrl, persisted('draw-a|/preview-a'));
});

test("第二次进页命中持久缓存零下载（内存缓存销毁后仍免网络）", async () => {
  const h = setup();
  await load(h, [rows[0]]);
  h.downloads[0].success({ statusCode: 200, tempFilePath: "wxfile://t" });
  await flush();
  h.page._previews.cache.clear(); // 模拟页面实例内存缓存销毁
  await load(h, [rows[0]]);
  assert.equal(h.downloads.length, 1); // 持久缓存命中，零新下载
  assert.equal(h.page.data.jobs[0].previewUrl, persisted("draw-a|/preview-a"));
});

test('收藏时间按后端秒时间戳显示日期，空下载路径必须报错', async () => {
  const h = setup();
  await load(h, [{ ...rows[0], saved_at: new Date(2026, 8, 13, 15, 7).getTime() / 1000 }]);
  assert.equal(h.page.data.jobs[0].createdAt, '2026-09-13 15:07');
  h.downloads[0].success({ statusCode: 200, tempFilePath: '' }); await flush();
  assert.equal(h.page.data.jobs[0].previewState, 'error');
});

test('图片会话失效清空收藏并显示登录提示，旧 401 不清除新账号', async () => {
  const h = setup(); h.storage.set('session_token', 'first'); await load(h, rows);
  h.downloads[0].success({ statusCode: 401 }); await flush();
  assert.equal(h.page.data.jobs.length, 0);
  assert.match(h.page.data.loadError, /登录/);
  h.app._sessionGeneration++;
  h.storage.set('session_token', 'second');
  await load(h, [rows[0]]);
  h.downloads[1].success({ statusCode: 401 }); await flush();
  assert.equal(h.storage.get('session_token'), 'second');
  assert.equal(h.page.data.jobs.length, 1);
});

test("切筛选后 deferred 卡片经视口回调补发下载（观察器重观察）", async () => {
  const h = setup();
  const items = Array.from({ length: 8 }, (_, i) => ({ id: `saved-${i}`, title: "AI: 图" + i, preview_url: `/saved-${i}` }));
  await load(h, items);
  assert.equal(h.downloads.length, 3); // 并发 3，首屏 eager 6、deferred 2
  h.downloads[0].success({ statusCode: 200, tempFilePath: "t0" });
  await flush();
  assert.equal(h.downloads.length, 4); // 名额腾出后泵 eager 余量
  const before = h.observers.length;
  h.page.onFilter({ currentTarget: { dataset: { kind: "draw" } } });
  assert.ok(h.observers.length > before, "切筛选必须重建观察器，否则 deferred 卡片永停加载中");
  const ob = h.observers.at(-1);
  assert.equal(ob.selector, ".work-cell");
  ob.cb({ dataset: { id: "saved-6" }, intersectionRatio: 0.6 });
  await flush();
  assert.equal(h.downloads.length, 4); // 名额仍满：deferred 只入队不抢跑
  h.downloads[1].success({ statusCode: 200, tempFilePath: "t1" });
  await flush();
  assert.equal(h.downloads.length, 5);
  assert.ok(h.downloads.at(-1).url.endsWith("/saved-6")); // 视口卡片提到队首优先派发
});
