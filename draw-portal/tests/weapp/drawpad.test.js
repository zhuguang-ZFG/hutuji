const assert = require('node:assert/strict');
const test = require('node:test');
const { createWeapp, createClock, flush } = require('./harness');

const plain = (value) => JSON.parse(JSON.stringify(value));
function setup(options = {}) {
  const h = createWeapp(options);
  h.app.globalData.user = { email: options.owner || 'artist@example.test', active_device_mac: options.mac || '' };
  const calls = [], files = new Map(), removed = [], backs = [];
  const ctx = new Proxy({}, { get(target, key) {
    if (!(key in target)) target[key] = (...args) => calls.push([key, ...args]);
    return target[key];
  } });
  const canvas = { getContext: () => ctx };
  h.wx.env = { USER_DATA_PATH: '/local' };
  h.wx.getFileSystemManager = () => ({ writeFileSync: (path, text) => files.set(path, text),
    unlinkSync: (path) => { removed.push(path); files.delete(path); } });
  h.wx.navigateBack = (args) => backs.push(args);
  h.wx.getWindowInfo = () => ({ pixelRatio: 2 });
  h.wx.createSelectorQuery = () => {
    const selectors = [];
    const query = { in: () => query, select: (selector) => { selectors.push(selector); return query; }, fields: () => query,
      boundingClientRect: () => query, exec: (done) => done(selectors.length === 1 ?
        [{ left: 30, top: 100, width: h.page._w, height: h.page._h }] : [{ width: 380, height: 570 }, { node: canvas }]) };
    return query;
  };
  h.load('pages/drawpad/drawpad.js');
  const setData = h.page.setData;
  h.page.setData = (value, done) => { setData(value); if (done) done(); };
  h.page.onLoad(); h.page.onShow(); h.page.onReady();
  const pad = h.load('utils/drawpad.js');
  const touch = (x, y) => ({ x: x * h.page._w / h.page._viewport.width, y: y * h.page._h / h.page._viewport.height });
  const draw = (tool, points) => {
    h.page.setTool({ currentTarget: { dataset: { tool } } });
    h.page.onTouchStart({ touches: [touch(...points[0])] });
    for (const point of points.slice(1)) h.page.onTouchMove({ touches: [touch(...point)] });
    h.page.onTouchEnd({ touches: [] });
  };
  return { h, calls, files, removed, backs, pad, touch, draw };
}

test('纸面大小适配屏幕，平滑笔迹和形状的渲染、SVG 共用几何', () => {
  const { h, draw, calls } = setup();
  draw('pen', [[20, 30], [60, 45], [100, 100], [160, 40]]);
  draw('rect', [[20, 160], [130, 220]]);
  draw('ellipse', [[160, 160], [260, 220]]);
  const svg = h.page._buildSvg();
  assert.match(svg, /viewBox="0 0 380 554"/);
  assert.match(svg, /width="380" height="554"/);
  assert.match(svg, /Q60 45 80 72.5/);
  assert(calls.some((call) => JSON.stringify(call) === JSON.stringify(['quadraticCurveTo', 60, 45, 80, 72.5])));
  assert.equal((svg.match(/<path /g) || []).length, 3);
  assert.doesNotMatch(svg, /<(?:ellipse|rect|image|script)\b/);
  assert(h.page.data.canvasW <= 356);
  assert(Math.abs(h.page.data.canvasH / h.page.data.canvasW - 277 / 190) < 0.002);
});

test('网格、缩放与屏幕大小不混进导出，边缘点不会越出画纸', () => {
  const { h, draw, pad } = setup();
  draw('pen', [[pad.WIDTH + 20, pad.HEIGHT + 20]]);
  const svg = h.page._buildSvg();
  for (const point of h.page._strokes[0].pts) {
    assert(point[0] >= 0 && point[0] <= pad.WIDTH && point[1] <= pad.HEIGHT);
  }
  h.page.toggleGrid();
  h.page._view = { scale: 3, x: -150, y: -220 };
  h.page._w = 200; h.page._h = 292;
  assert.equal(h.page._buildSvg(), svg);
});

test('快擦覆盖触摸间的路径，多笔擦除作为一次撤销，重做可重放', () => {
  const { h, draw } = setup();
  draw('line', [[100, 100], [220, 100]]);
  draw('line', [[100, 150], [220, 150]]);
  draw('line', [[300, 300], [350, 300]]);
  const before = h.page._buildSvg();
  draw('eraser', [[160, 40], [160, 230]]);
  assert.equal(h.page.data.strokeCount, 1);
  const erased = h.page._buildSvg();
  h.page.undo();
  assert.equal(h.page._buildSvg(), before);
  h.page.redo();
  assert.equal(h.page._buildSvg(), erased);
});

test('取消擦除恢复整组笔画，新笔画会清掉旧重做分支', () => {
  const { h, draw, touch } = setup();
  draw('line', [[20, 20], [100, 20]]);
  h.page.setTool({ currentTarget: { dataset: { tool: 'eraser' } } });
  h.page.onTouchStart({ touches: [touch(40, 20)] });
  assert.equal(h.page._strokes.length, 0);
  h.page.onTouchCancel();
  assert.equal(h.page._strokes.length, 1);
  h.page.undo();
  assert.equal(h.page.data.canRedo, true);
  draw('line', [[20, 80], [100, 80]]);
  assert.equal(h.page.data.canRedo, false);
});

test('快速拖动的最终落点来自 touchend，直线和橡皮不漏掉收尾', () => {
  const { h, touch } = setup();
  h.page.setTool({ currentTarget: { dataset: { tool: 'line' } } });
  h.page.onTouchStart({ touches: [touch(50, 100)] });
  h.page.onTouchEnd({ touches: [], changedTouches: [touch(250, 100)] });
  assert.equal(h.page.data.strokeCount, 1);
  assert.deepEqual(plain(h.page._strokes[0].pts), [[50, 100], [250, 100]]);
  h.page.setTool({ currentTarget: { dataset: { tool: 'eraser' } } });
  h.page.onTouchStart({ touches: [touch(150, 50)] });
  h.page.onTouchEnd({ touches: [], changedTouches: [touch(150, 150)] });
  assert.equal(h.page.data.strokeCount, 0);
  h.page.undo();
  assert.equal(h.page.data.strokeCount, 1);
});

for (const prefix of ['client', 'page']) {
  test(`触摸仅有 ${prefix} 坐标也能按画布位置绘制，不能导出 NaN`, () => {
    const { h, touch } = setup();
    const globalTouch = (x, y) => {
      const local = touch(x, y);
      return { [prefix + 'X']: 30 + local.x, [prefix + 'Y']: 100 + local.y };
    };
    h.page.onTouchStart({ touches: [globalTouch(50, 100)] });
    h.page.onTouchMove({ touches: [globalTouch(250, 120)] });
    h.page.onTouchEnd({ touches: [] });
    assert.equal(h.page.data.strokeCount, 1);
    assert.doesNotMatch(h.page._buildSvg(), /NaN|Infinity/);
    const pts = h.page._strokes[0].pts;
    assert(Math.abs(pts[0][0] - 50) < 0.001);
    assert(Math.abs(pts[1][1] - 120) < 0.001);
  });
}

test('不完整触摸不能生成空笔画，无效坐标在本机导出前就拒绝', () => {
  const { h, pad } = setup();
  h.page.onTouchStart({ touches: [{ identifier: 1 }] });
  h.page.onTouchEnd({ touches: [] });
  assert.equal(h.page.data.strokeCount, 0);
  assert.throws(() => pad.buildSvg([{ tool: 'pen', pts: [[0, 0], [NaN, 10]] }]), /坐标/);
});

test('擦除手势中退出账号，迟到的结束不提交历史或保存草稿', () => {
  const { h, touch, draw, pad } = setup();
  draw('line', [[50, 100], [250, 100]]);
  h.page.setTool({ currentTarget: { dataset: { tool: 'eraser' } } });
  h.page.onTouchStart({ touches: [touch(150, 100)] });
  const history = h.page._history.length;
  h.app.logout();
  h.page.onTouchEnd({ touches: [] });
  assert.equal(h.page._history.length, history);
  assert.equal(h.storage.has(pad.DRAFT_KEY), false);
});

test('清空可撤销，迟到的清空确认不能改隐藏页', () => {
  const { h, draw } = setup();
  draw('rect', [[20, 20], [100, 100]]);
  h.page.clearAll();
  h.modals.at(-1).success({ confirm: true });
  assert.equal(h.page.data.strokeCount, 0);
  h.page.undo();
  assert.equal(h.page.data.strokeCount, 1);
  h.page.clearAll();
  h.page.onHide();
  h.modals.at(-1).success({ confirm: true });
  assert.equal(h.page.data.strokeCount, 1);
});

test('第二根手指取消未完成笔画，双指缩放移动不画线，复位保留作品', () => {
  const { h, draw, touch } = setup();
  draw('line', [[20, 20], [100, 100]]);
  const svg = h.page._buildSvg();
  h.page.onTouchStart({ touches: [touch(100, 120)] });
  h.page.onTouchMove({ touches: [touch(120, 140)] });
  h.page.onTouchStart({ touches: [touch(100, 120), touch(200, 120)] });
  h.page.onTouchMove({ touches: [touch(50, 120), touch(250, 120)] });
  assert.equal(h.page.data.zoom, 200);
  h.page.onTouchEnd({ touches: [touch(50, 120)] });
  h.page.onTouchMove({ touches: [touch(70, 130)] });
  h.page.onTouchEnd({ touches: [] });
  assert.equal(h.page.data.strokeCount, 1);
  assert.equal(h.page._buildSvg(), svg);
  const point = h.page._point(touch(150, 120));
  assert.deepEqual(plain(point), [150, 120]);
  h.page.resetView();
  assert.equal(h.page.data.zoom, 100);
  assert.equal(h.page._buildSvg(), svg);
});

test('完成笔画保存为本机草稿，同账号可恢复，别的账号不读它', () => {
  const { h, draw, pad } = setup();
  draw('pen', [[20, 20], [40, 40], [80, 20]]);
  const svg = h.page._buildSvg();
  assert.equal(h.storage.get(pad.DRAFT_KEY).owner, 'artist@example.test');
  const same = setup({ storage: h.storage });
  assert.equal(same.h.page._buildSvg(), svg);
  assert.match(same.h.page.data.draftHint, /恢复/);
  const other = setup({ storage: h.storage, owner: 'other@example.test' });
  assert.equal(other.h.page.data.strokeCount, 0);
});

test('退出登录清草稿，旧页面隐藏不能写回或把上传结果交给新账号', async () => {
  const { h, draw, pad, removed, backs } = setup();
  draw('line', [[20, 20], [100, 100]]);
  h.page.submit();
  h.app.logout(); h.page.onHide();
  assert.equal(h.storage.has(pad.DRAFT_KEY), false);
  h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ job_id: 'old' }) });
  await flush();
  assert.equal(h.app.globalData.pendingAdopt, null);
  assert.equal(backs.length, 0);
  assert.equal(removed.length, 1);
});

test('损坏或越界的本机草稿不进入画布，复杂笔画保留既有画稿', () => {
  const { h, pad, draw } = setup();
  for (const pts of [[[0, 0], [Infinity, 1]], [[0, 0], [-1, 2]], [[0, 0], ['<script>', 0]]]) {
    assert.equal(pad.restoreDraft({ version: 2, owner: 'x', strokes: [{ tool: 'pen', pts }] }, 'x'), null);
  }
  h.page._strokes = Array.from({ length: 400 }, () => ({ tool: 'line', pts: [[1, 1], [20, 20]] }));
  draw('line', [[20, 30], [120, 30]]);
  assert.equal(h.page._strokes.length, 400);
  assert.equal(h.page._cur, null);
  assert.match(h.toasts.at(-1).title, /复杂/);
});

test('上传期间切回页面不重复提交，隐藏时完成的预览到回屏才交接', async () => {
  const { h, draw, files, removed, backs } = setup();
  draw('line', [[20, 20], [100, 100]]);
  h.page.submit();
  assert.equal(files.size, 1);
  h.page.onHide(); h.page.onShow(); h.page.submit();
  assert.equal(h.uploads.length, 1);
  assert.equal(h.page.data.submitting, true);
  h.page.onHide();
  h.uploads[0].success({ statusCode: 200, data: JSON.stringify({ job_id: 'drawing', preview_url: '/preview.png' }) });
  await flush();
  assert.equal(removed.length, 1);
  assert.equal(backs.length, 0);
  assert.equal(h.requests.length, 0);
  h.page.onShow();
  assert.equal(backs.length, 1);
  assert.equal(h.app.globalData.pendingAdopt.job_id, 'drawing');
  assert.equal(h.page.data.strokeCount, 1);
});

test('上传失败保留草稿并清理文件，卸载后返回不会自动跳转', async () => {
  const { h, draw, removed, backs } = setup();
  draw('line', [[20, 20], [100, 100]]);
  h.page.submit();
  h.uploads[0].fail({ errMsg: 'timeout' });
  await flush();
  assert.equal(h.page.data.submitting, false);
  assert.equal(h.page.data.strokeCount, 1);
  assert.equal(removed.length, 1);
  h.page.submit(); h.page.onUnload();
  h.uploads[1].success({ statusCode: 200, data: JSON.stringify({ job_id: 'gone' }) });
  await flush();
  assert.equal(backs.length, 0);
  assert.equal(removed.length, 2);
});

test('镜像与六向笔迹所见即所得，越过圆边时不出界，整组只需一次撤销', () => {
  const { h, draw, pad } = setup();
  h.page.setSymmetry({ currentTarget: { dataset: { symmetry: 'mirror' } } });
  draw('pen', [[30, 40], [80, 70], [100, 50]]);
  assert.equal(h.page.data.strokeCount, 2);
  assert.equal((h.page._buildSvg().match(/<path /g) || []).length, 2);
  for (let i = 0; i < 3; i++) assert.equal(h.page._strokes[0].pts[i][0] + h.page._strokes[1].pts[i][0], pad.WIDTH);
  h.page.undo();
  assert.equal(h.page.data.strokeCount, 0);
  h.page.setSymmetry({ currentTarget: { dataset: { symmetry: 'radial' } } });
  draw('pen', [[0, 0], [380, 0], [380, 554]]);
  assert.equal(h.page.data.strokeCount, 6);
  for (const stroke of h.page._strokes) for (const [x, y] of stroke.pts) {
    assert(x >= 0 && x <= pad.WIDTH && y >= 0 && y <= pad.HEIGHT);
  }
  assert.equal((h.page._buildSvg().match(/<path /g) || []).length, 6);
  h.page.undo();
  assert.equal(h.page.data.strokeCount, 0);
});

test('几何素材可移动缩放，取消拖动与撤销都恢复原始线条', () => {
  const { h, touch, pad } = setup();
  h.page.addShape({ currentTarget: { dataset: { shape: 'star' } } });
  const original = h.page._buildSvg();
  const first = pad.shapePoints(h.page._strokes[0])[0];
  h.page.onTouchStart({ touches: [touch(...first)] });
  h.page.onTouchMove({ touches: [touch(first[0] + 20, first[1] + 30)] });
  assert.notEqual(h.page._buildSvg(), original);
  h.page.onTouchCancel();
  assert.equal(h.page._buildSvg(), original);
  h.page.resizeSelected({ currentTarget: { dataset: { action: 'grow' } } });
  assert.notEqual(h.page._buildSvg(), original);
  h.page.undo();
  assert.equal(h.page._buildSvg(), original);
  for (const shape of ['heart', 'flower']) h.page.addShape({ currentTarget: { dataset: { shape } } });
  assert.equal(h.page.data.strokeCount, 3);
  assert.doesNotMatch(h.page._buildSvg(), /<image|NaN|Infinity/);
});

test('回放仅改变展示，不能上传半幅作品，离页立即停止且保留完整草稿', () => {
  const clock = createClock();
  const { h, draw, pad } = setup({ clock });
  draw('line', [[20, 30], [80, 60]]);
  draw('line', [[20, 100], [80, 160]]);
  const original = h.page._buildSvg();
  h.page.replay();
  clock.advance(120);
  assert.equal(h.page.data.replaying, true);
  assert.equal(h.page.data.replayPercent, 50);
  h.page.submit();
  assert.equal(h.uploads.length, 0);
  assert.equal(h.page._buildSvg(), original);
  h.page.onHide();
  assert.equal(h.page.data.replaying, false);
  assert.equal(clock.pending.size, 0);
  assert.equal(h.storage.get(pad.DRAFT_KEY).strokes.length, 2);
  h.page.onShow(); h.page.replay(); clock.advance(1000);
  assert.equal(h.page.data.replaying, false);
  assert.equal(h.page._buildSvg(), original);
});

test('纸张回包使用明确目标，草稿需确认后等比适配，上传携带相同配置', async () => {
  const mac = 'aa:bb:cc:dd:ee:ff';
  const { h, draw, pad } = setup({ mac });
  const paper = h.load('utils/paper_config.js');
  draw('line', [[20, 20], [100, 100]]);
  const old = h.page._buildSvg();
  assert.match(h.requests[0].url, /device_mac=aa%3Abb%3Acc%3Add%3Aee%3Aff/);
  const config = { paper: 'A3', width_mm: 297, height_mm: 420, landscape: true, margin_mm: 10,
    swap_xy: false, max_x_mm: 400, max_y_mm: 277 };
  const marker = paper.marker(config);
  h.requests[0].success({ statusCode: 200, data: { config, marker, device_mac: mac, online: true, capable: true } });
  await flush();
  assert.equal(h.page.data.paperChanged, true);
  assert.equal(h.page._buildSvg(), old);
  h.page.submit(); assert.equal(h.uploads.length, 0);
  h.page.adaptPaper(); h.modals.at(-1).success({ confirm: true });
  const points = h.page._strokes[0].pts;
  assert(Math.abs(points[1][0] - points[0][0] - (points[1][1] - points[0][1])) < 0.11);
  assert.match(h.page._buildSvg(), /viewBox="0 0 380 263.2"/);
  assert.equal(h.page.data.paperChanged, false);
  assert.equal(h.storage.get(pad.DRAFT_KEY).paperMarker, marker);
  h.page.submit();
  assert.equal(h.uploads[0].formData.paper_marker, marker);
  assert.equal(h.uploads[0].formData.device_mac, mac);
  assert.equal(h.uploads[0].formData.preserve_canvas, 'true');
});

test('旧纸张回包和离页后的适配确认不能重写草稿', async () => {
  const { h, draw } = setup({ mac: 'aa:bb:cc:dd:ee:ff' });
  draw('line', [[20, 20], [100, 100]]);
  const old = h.page._buildSvg();
  const paper = h.load('utils/paper_config.js');
  const config = paper.defaultFor('nopaper');
  const reply = { config, marker: paper.marker(config), device_mac: 'aa:bb:cc:dd:ee:ff' };
  h.page.onHide();
  h.requests[0].success({ statusCode: 200, data: reply }); await flush();
  assert.equal(h.page._buildSvg(), old);
  h.page.onShow();
  h.requests[1].success({ statusCode: 200, data: reply }); await flush();
  h.page.adaptPaper(); h.page.onHide(); h.modals.at(-1).success({ confirm: true });
  assert.equal(h.page._buildSvg(), old);
  assert.equal(h.page.data.paperChanged, true);
});
