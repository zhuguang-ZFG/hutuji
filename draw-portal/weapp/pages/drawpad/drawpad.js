const api = require('../../utils/api');
const pageSession = require('../../utils/page_session');
const pad = require('../../utils/drawpad');
const paperConfig = require('../../utils/paper_config');

const toolHints = { pen: '顺着指尖画，笔迹自动顺滑', eraser: '划过笔画可整笔擦除，撤销可恢复',
  line: '拖出一条直线', rect: '从一个角拖到另一个角', ellipse: '拖出一个圆或椭圆',
  select: '点选线条后拖动，也可以放大或缩小' };
let nextUpload = 0;

Page({
  data: {
    tool: 'pen', toolHint: toolHints.pen, strokeCount: 0, submitting: false,
    canvasW: 300, canvasH: 437, canUndo: false, canRedo: false,
    grid: true, zoom: 100, draftHint: '画稿仅保存在本机', canvasError: '',
    paperLabel: 'A4 · 竖向', paperLoading: false, paperError: '', paperChanged: false,
    symmetry: 'none', creative: false, selectedIndex: -1, replaying: false, replayPercent: 0,
    symmetries: [{ key: 'none', label: '自由' }, { key: 'mirror', label: '镜像' }, { key: 'radial', label: '六向' }],
    shapes: [{ key: 'star', label: '星星', glyph: '☆' }, { key: 'heart', label: '爱心', glyph: '♡' },
      { key: 'flower', label: '花朵', glyph: '✿' }],
    tools: [{ key: 'pen', label: '画笔' }, { key: 'eraser', label: '橡皮' },
      { key: 'line', label: '直线' }, { key: 'rect', label: '方框' }, { key: 'ellipse', label: '圆形' },
      { key: 'select', label: '移动' }],
  },

  onLoad() {
    this._strokes = [];
    this._history = [];
    this._redo = [];
    this._view = { scale: 1, x: 0, y: 0 };
    this._viewport = Object.assign({}, pad.DEFAULT_VIEWPORT);
    this._paperMarker = paperConfig.marker(paperConfig.defaultFor('paper'));
    this._paperMac = '';
  },

  onShow() {
    pageSession.show(this);
    const app = getApp();
    const owner = (app.globalData.user && app.globalData.user.email) || '';
    if (this._draftSession !== app._sessionGeneration || this._draftOwner !== owner) {
      this._draftSession = app._sessionGeneration;
      this._draftOwner = owner;
      this._strokes = [];
      this._history = [];
      this._redo = [];
      this._cur = null;
      this._submission = null;
      this._pendingPreview = null;
      this._pendingError = '';
      this._nextPaper = null;
      this._viewport = Object.assign({}, pad.DEFAULT_VIEWPORT);
      this._paperMarker = paperConfig.marker(paperConfig.defaultFor('paper'));
      this._paperMac = '';
      this._view = { scale: 1, x: 0, y: 0 };
      let saved;
      try { saved = pad.readDraft(wx.getStorageSync(pad.DRAFT_KEY), owner); } catch (error) {}
      if (saved) {
        this._strokes = saved.strokes;
        this._viewport = saved.viewport;
        this._paperMarker = saved.paperMarker || this._paperMarker;
        this._paperMac = saved.deviceMac;
      }
      this.setData({ zoom: 100, selectedIndex: -1, paperChanged: false, paperError: '',
        draftHint: saved && saved.strokes.length ? '已恢复本机草稿' :
        owner ? '画稿自动保存于本机' : '当前画稿仅在本页保留' });
    }
    this.setData({ submitting: !!this._submission });
    this._syncHistory();
    this._redraw();
    if (this._pendingPreview) { this._openPreview(); return; }
    if (this._pendingError) {
      this._fail(this._pendingError);
      this._pendingError = '';
    }
    if (!this._submission) return this.refreshPaper();
  },

  onReady() { this._setupCanvas(); },
  onResize() { this._setupCanvas(); },

  onHide() {
    this._stopReplay();
    this.onTouchCancel();
    this._saveDraft();
    pageSession.hide(this);
    if (this._frame && this._canvas) this._canvas.cancelAnimationFrame(this._frame);
    this._frame = null;
  },

  refreshPaper() {
    const visible = pageSession.capture(this);
    const id = this._paperRequest = (this._paperRequest || 0) + 1;
    const current = () => visible() && id === this._paperRequest;
    const user = getApp().globalData.user;
    const mac = user && user.active_device_mac || '';
    this._nextPaper = null;
    this.setData({ paperError: '', paperLoading: !!mac });
    if (!mac) {
      const config = paperConfig.defaultFor('paper');
      this._receivePaper({ config, marker: paperConfig.marker(config), device_mac: '' });
      return Promise.resolve();
    }
    return api.request('/api/settings/paper?device_mac=' + encodeURIComponent(mac), { timeout: 25000 })
      .then((res) => {
        if (!current()) return;
        if (!res || res.device_mac !== mac || res.marker !== paperConfig.marker(res.config)) throw new Error('纸张回包不匹配，请重试');
        this._receivePaper(res);
      }).catch((err) => {
        if (current()) this.setData({ paperLoading: false, paperError: paperConfig.requestError(err, '纸张读取失败，画稿会保留') });
      });
  },

  _receivePaper(res) {
    this._nextPaper = res;
    const dimensions = paperConfig.dimensions(res.config);
    const changed = this._strokes.length > 0 && (this._paperMarker !== res.marker || this._paperMac !== res.device_mac);
    this.setData({ paperLoading: false, paperError: '', paperChanged: changed,
      paperLabel: (res.config.paper === 'custom' ? '自定义' : res.config.paper) +
        (res.config.landscape ? ' · 横向' : ' · 竖向'),
      paperSize: dimensions.pageWidth + ' × ' + dimensions.pageHeight + ' mm' });
    if (!changed) this._usePaper(res, false);
  },

  _usePaper(res, adapt) {
    const dimensions = paperConfig.dimensions(res.config);
    const viewport = pad.viewportFor(dimensions.pageWidth, dimensions.pageHeight);
    const resized = viewport.width !== this._viewport.width || viewport.height !== this._viewport.height;
    if (adapt) this._strokes = pad.fitStrokes(this._strokes, this._viewport, viewport);
    this._viewport = viewport;
    this._paperMarker = res.marker;
    this._paperMac = res.device_mac;
    if (adapt || resized) {
      this._history = []; this._redo = [];
      this._view = { scale: 1, x: 0, y: 0 };
      this.setData({ zoom: 100, selectedIndex: -1 });
      this._syncHistory();
    }
    this.setData({ paperChanged: false });
    if (adapt) this._saveDraft();
    if (this._canvas) this._setupCanvas();
    else this._redraw();
  },

  adaptPaper() {
    if (!this._editable() || !this.data.paperChanged || !this._nextPaper) return;
    const target = this._nextPaper, current = pageSession.capture(this);
    wx.showModal({ title: '适配到当前纸张',
      content: '画稿将等比缩放并居中，不拉伸线条。适配后从新的画纸继续，原撤销记录会清空。', confirmText: '等比适配',
      success: (res) => {
        if (res.confirm && current() && this._editable() && target === this._nextPaper) this._usePaper(target, true);
      } });
  },

  onPaperSettings() {
    if (this.data.submitting) return;
    wx.navigateTo({ url: '/pages/paper/paper' });
  },

  onUnload() {
    this.onHide();
    this._unloaded = true;
  },

  _setupCanvas() {
    const current = pageSession.capture(this);
    const query = wx.createSelectorQuery().in(this);
    query.select('.paper-stage').boundingClientRect();
    query.select('#pad').fields({ node: true, size: true });
    query.exec((result) => {
      if (!current()) return;
      const stage = result && result[0], info = result && result[1];
      if (!stage || !info || !info.node || stage.height <= 0) {
        this.setData({ canvasError: '画布未准备好，请重新打开手绘' });
        return;
      }
      const w = Math.max(1, Math.floor(Math.min(stage.width - 24, (stage.height - 12) * this._viewport.width / this._viewport.height)));
      const h = Math.round(w * this._viewport.height / this._viewport.width);
      let dpr = 2;
      try { dpr = (wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync()).pixelRatio || 2; } catch (error) {}
      this.setData({ canvasW: w, canvasH: h, canvasError: '' }, () => {
        if (!current()) return;
        const node = this._canvas = info.node;
        node.width = Math.round(w * dpr);
        node.height = Math.round(h * dpr);
        this._ctx = node.getContext('2d');
        this._ctx.scale(dpr, dpr);
        this._w = w;
        this._h = h;
        this._canvasRect = null;
        // setData 改变了画纸大小，之后读取实际位置，兼容只给页面坐标的触摸事件。
        wx.createSelectorQuery().in(this).select('#pad').boundingClientRect().exec((rects) => {
          if (current() && rects && rects[0]) this._canvasRect = rects[0];
        });
        this._redraw();
      });
    });
  },

  _editable() {
    return !this.data.submitting && !this.data.replaying && this._visible !== false && !this._unloaded &&
      this._draftSession === getApp()._sessionGeneration;
  },

  setTool(e) {
    const tool = e.currentTarget.dataset.tool;
    if (!this._editable() || this._cur || this._eraseBefore || !toolHints[tool]) return;
    this.setData({ tool, toolHint: toolHints[tool], selectedIndex: -1,
      symmetry: tool !== 'pen' && this.data.symmetry === 'radial' ? 'none' : this.data.symmetry });
  },

  _local(touch) {
    if (!touch || !this._w || !this._h) return null;
    let x = touch.x, y = touch.y;
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      const rect = this._canvasRect;
      if (!rect) return null;
      x = (Number.isFinite(touch.clientX) ? touch.clientX : touch.pageX) - rect.left;
      y = (Number.isFinite(touch.clientY) ? touch.clientY : touch.pageY) - rect.top;
    }
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return [x * this._viewport.width / this._w, y * this._viewport.height / this._h];
  },

  _point(touch) {
    const point = this._local(touch), view = this._view;
    if (!point) return null;
    const result = [pad.clamp((point[0] - view.x) / view.scale, 0, this._viewport.width),
      pad.clamp((point[1] - view.y) / view.scale, 0, this._viewport.height)];
    return this.data.tool === 'pen' && this.data.symmetry === 'radial' ? pad.radialPoint(result, this._viewport) : result;
  },

  _startGesture(touches) {
    this.onTouchCancel();
    const a = this._local(touches[0]), b = this._local(touches[1]);
    if (!a || !b) return;
    this._gesture = { distance: Math.max(1, Math.hypot(b[0] - a[0], b[1] - a[1])),
      center: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], view: Object.assign({}, this._view) };
    this._gestureActive = true;
  },

  onTouchStart(e) {
    if (!this._ctx || !this._editable() || !e.touches.length) return;
    if (e.touches.length >= 2) { this._startGesture(e.touches); return; }
    if (this._gestureActive) return;
    const point = this._point(e.touches[0]);
    if (!point) return;
    if (this.data.tool === 'select') {
      let index = this._strokes.length - 1;
      while (index >= 0 && !pad.hitsStroke(this._strokes[index], point, point, 14 / this._view.scale)) index--;
      this.setData({ selectedIndex: index });
      if (index >= 0) this._selectionDrag = { index, point, before: this._strokes.slice(), moved: false };
    } else if (this.data.tool === 'eraser') {
      this._eraseBefore = this._strokes.slice();
      this._erasePoint = point;
      this._eraseTo(point);
    } else this._cur = { tool: this.data.tool, pts: [point] };
    this._queueDraw();
  },

  onTouchMove(e) {
    if (!this._ctx || !this._editable() || !e.touches.length) return;
    if (e.touches.length >= 2) {
      if (!this._gesture) this._startGesture(e.touches);
      const a = this._local(e.touches[0]), b = this._local(e.touches[1]), start = this._gesture;
      if (!a || !b || !start) return;
      const scale = pad.clamp(start.view.scale * Math.hypot(b[0] - a[0], b[1] - a[1]) / start.distance, 1, 4);
      this._view = { scale,
        x: pad.clamp((a[0] + b[0]) / 2 - (start.center[0] - start.view.x) * scale / start.view.scale, this._viewport.width * (1 - scale), 0),
        y: pad.clamp((a[1] + b[1]) / 2 - (start.center[1] - start.view.y) * scale / start.view.scale, this._viewport.height * (1 - scale), 0) };
      this.setData({ zoom: Math.round(scale * 100) });
      this._queueDraw();
      return;
    }
    if (this._gestureActive) return;
    const point = this._point(e.touches[0]);
    if (!point) return;
    if (this._selectionDrag) {
      const selected = this._selectionDrag;
      const dx = point[0] - selected.point[0], dy = point[1] - selected.point[1];
      this._strokes[selected.index] = pad.moveStroke(selected.before[selected.index], this._viewport, dx, dy);
      selected.moved = selected.moved || Math.hypot(dx, dy) > 0.5;
      this._queueDraw(); return;
    }
    if (this._eraseBefore) { this._eraseTo(point); return; }
    if (!this._cur) return;
    const pts = this._cur.pts;
    if (this._cur.tool === 'pen') {
      const last = pts[pts.length - 1];
      if (Math.hypot(point[0] - last[0], point[1] - last[1]) < 0.8 / this._view.scale) return;
      if (pts.length >= pad.MAX_POINTS) {
        this.onTouchCancel();
        wx.showToast({ title: '这笔太长了，请抬手分段画', icon: 'none' });
        return;
      }
      pts.push(point);
    } else pts[1] = point;
    this._queueDraw();
  },

  onTouchEnd(e = {}) {
    if (!this._editable()) { this.onTouchCancel(); return; }
    if (this._gestureActive) {
      if (!e.touches || !e.touches.length) { this._gesture = null; this._gestureActive = false; }
      return;
    }
    // 快速拖动可能只收到起点和 touchend，结束位置也须进入笔迹和擦除路径。
    if (e.changedTouches && e.changedTouches.length) this.onTouchMove({ touches: [e.changedTouches[0]] });
    if (this._selectionDrag) {
      const selected = this._selectionDrag;
      this._selectionDrag = null;
      if (selected.moved) this._commit(selected.before);
      else this._strokes = selected.before;
      this._redraw(); return;
    }
    if (this._eraseBefore) {
      const before = this._eraseBefore;
      this._eraseBefore = null;
      if (before.length !== this._strokes.length) this._commit(before);
      return;
    }
    const stroke = this._cur;
    this._cur = null;
    if (!stroke) return;
    if (!this._editable()) { this._redraw(); return; }
    if (stroke.tool === 'pen' && stroke.pts.length === 1) {
      const point = stroke.pts[0];
      const dx = Math.min(1.2, this._viewport.width / 2);
      stroke.pts.push([point[0] < this._viewport.width - dx ? point[0] + dx : point[0] - dx, point[1]]);
    }
    if (stroke.tool !== 'pen' && (!stroke.pts[1] ||
      Math.hypot(stroke.pts[1][0] - stroke.pts[0][0], stroke.pts[1][1] - stroke.pts[0][1]) * this._view.scale < 6)) {
      this._redraw();
      return;
    }
    const candidate = this._strokes.concat(pad.symmetryStrokes(stroke, this.data.symmetry, this._viewport));
    try { pad.buildSvg(candidate, this._viewport); } catch (error) {
      wx.showToast({ title: error.message, icon: 'none' });
      this._redraw();
      return;
    }
    const before = this._strokes;
    this._strokes = candidate;
    this._commit(before);
  },

  onTouchCancel() {
    this._cur = null;
    this._gesture = null;
    this._gestureActive = false;
    if (this._eraseBefore) this._strokes = this._eraseBefore;
    if (this._selectionDrag) this._strokes = this._selectionDrag.before;
    this._selectionDrag = null;
    this._eraseBefore = null;
    this._redraw();
  },

  _eraseTo(point) {
    const from = this._erasePoint;
    this._strokes = this._strokes.filter((stroke) => !pad.hitsStroke(stroke, from, point, 12 / this._view.scale));
    this._erasePoint = point;
    this._queueDraw();
  },

  _commit(before) {
    this._history.push(before);
    if (this._history.length > 60) this._history.shift();
    this._redo = [];
    this._syncHistory();
    this._saveDraft();
    this._redraw();
  },

  _syncHistory() {
    this.setData({ strokeCount: this._strokes.length, canUndo: !!this._history.length, canRedo: !!this._redo.length });
  },

  undo() {
    if (!this._editable() || this._cur || this._eraseBefore || !this._history.length) return;
    this._redo.push(this._strokes);
    this._strokes = this._history.pop();
    this.setData({ selectedIndex: -1 });
    this._syncHistory(); this._saveDraft(); this._redraw();
  },

  redo() {
    if (!this._editable() || this._cur || this._eraseBefore || !this._redo.length) return;
    this._history.push(this._strokes);
    this._strokes = this._redo.pop();
    this.setData({ selectedIndex: -1 });
    this._syncHistory(); this._saveDraft(); this._redraw();
  },

  clearAll() {
    if (!this._editable() || !this._strokes.length || this._cur || this._eraseBefore) return;
    const current = pageSession.capture(this);
    wx.showModal({ title: '清空画纸', content: '清空后仍可点撤销恢复。', confirmText: '清空',
      success: (result) => {
        if (!result.confirm || !current() || !this._editable()) return;
        const before = this._strokes;
        this._strokes = [];
        this._commit(before);
      } });
  },

  toggleGrid() {
    if (!this._editable()) return;
    this.setData({ grid: !this.data.grid });
    this._redraw();
  },

  resetView() {
    if (!this._editable()) return;
    this.onTouchCancel();
    this._view = { scale: 1, x: 0, y: 0 };
    this.setData({ zoom: 100 });
    this._redraw();
  },

  toggleCreative() {
    if (!this._editable()) return;
    this.setData({ creative: !this.data.creative }, () => this._setupCanvas());
  },

  setSymmetry(e) {
    if (!this._editable() || this._cur || this._eraseBefore || this._selectionDrag) return;
    const symmetry = e.currentTarget.dataset.symmetry;
    if (!['none', 'mirror', 'radial'].includes(symmetry)) return;
    this.setData({ symmetry, tool: 'pen', toolHint: symmetry === 'radial' ? '在圆形范围内画，六个方向一起生长' :
      symmetry === 'mirror' ? '画纸两侧同步成形' : toolHints.pen, selectedIndex: -1 });
    this._redraw();
  },

  addShape(e) {
    if (!this._editable() || this._cur || this._eraseBefore || this._selectionDrag) return;
    const tool = e.currentTarget.dataset.shape;
    if (!['star', 'heart', 'flower'].includes(tool)) return;
    const v = this._viewport, radius = Math.min(v.width, v.height) * 0.23;
    const stroke = { tool, pts: [[v.width / 2 - radius, v.height / 2 - radius],
      [v.width / 2 + radius, v.height / 2 + radius]] };
    const before = this._strokes, candidate = before.concat(stroke);
    try { pad.buildSvg(candidate, v); } catch (err) { this._fail(err.message); return; }
    this._strokes = candidate;
    this._commit(before);
    this.setData({ tool: 'select', toolHint: toolHints.select, symmetry: 'none', selectedIndex: candidate.length - 1 });
    this._redraw();
  },

  resizeSelected(e) {
    const index = this.data.selectedIndex;
    if (!this._editable() || this._selectionDrag || index < 0 || !this._strokes[index]) return;
    const action = e.currentTarget.dataset.action;
    const before = this._strokes, candidate = before.slice();
    if (action === 'delete') candidate.splice(index, 1);
    else {
      if (!['grow', 'shrink'].includes(action)) return;
      try { candidate[index] = pad.moveStroke(before[index], this._viewport, 0, 0, action === 'grow' ? 1.15 : 0.85); }
      catch (err) { this._fail(err.message); return; }
    }
    this._strokes = candidate;
    if (action === 'delete') this.setData({ selectedIndex: -1 });
    this._commit(before);
  },

  replay() {
    if (this.data.replaying) { this._stopReplay(); return; }
    if (!this._editable() || !this._strokes.length || this._cur || this._eraseBefore || this._selectionDrag) return;
    const current = pageSession.capture(this);
    this._replayCount = 0;
    this.setData({ replaying: true, replayPercent: 0, selectedIndex: -1 });
    const step = () => {
      if (!current() || !this.data.replaying) { this._stopReplay(); return; }
      this._replayCount++;
      this.setData({ replayPercent: Math.round(this._replayCount / this._strokes.length * 100) });
      this._redraw();
      if (this._replayCount >= this._strokes.length) { this._stopReplay(); return; }
      this._playTimer = setTimeout(step, Math.max(30, Math.min(240, 12000 / this._strokes.length)));
    };
    this._redraw();
    this._playTimer = setTimeout(step, 120);
  },

  _stopReplay() {
    if (this._playTimer) clearTimeout(this._playTimer);
    this._playTimer = null;
    this._replayCount = null;
    this.setData({ replaying: false });
    this._redraw();
  },

  _saveDraft() {
    const app = getApp();
    if (!this._draftOwner || this._draftSession !== app._sessionGeneration ||
      !app.globalData.user || app.globalData.user.email !== this._draftOwner) return;
    try {
      if (this._strokes.length) wx.setStorageSync(pad.DRAFT_KEY,
        { version: 3, owner: this._draftOwner, strokes: this._strokes, viewport: this._viewport,
          paperMarker: this._paperMarker, deviceMac: this._paperMac });
      else wx.removeStorageSync(pad.DRAFT_KEY);
      this.setData({ draftHint: this._strokes.length ? '草稿已保存在本机' : '画稿自动保存于本机' });
    } catch (error) { this.setData({ draftHint: '本机保存失败，离开前请生成预览' }); }
  },

  submit() {
    if (!this._editable() || this._submission || !this._strokes.length || this._cur || this._eraseBefore ||
        this._selectionDrag || this.data.paperLoading || this.data.paperChanged || this.data.paperError) return;
    const current = pageSession.captureTask(this);
    const path = wx.env.USER_DATA_PATH + '/drawpad_' + Date.now() + '_' + (++nextUpload) + '.svg';
    const fsm = wx.getFileSystemManager();
    try { fsm.writeFileSync(path, this._buildSvg(), 'utf8'); } catch (error) { this._fail('本地暂存失败，请重试'); return; }
    const submission = this._submission = {};
    this.setData({ submitting: true });
    const cleanup = () => { try { fsm.unlinkSync(path); } catch (error) {} };
    api.uploadFile('/api/draw/upload', path, 'file', {
      paper_marker: this._paperMarker, device_mac: this._paperMac, preserve_canvas: 'true'
    }).then((data) => {
      cleanup();
      if (!current() || this._submission !== submission) return;
      this._submission = null;
      if (!data || !data.job_id) throw new Error('未能生成预览，请重试');
      this._pendingPreview = data;
      if (this._visible !== false) { this.setData({ submitting: false }); this._openPreview(); }
    }).catch((error) => {
      cleanup();
      if (!current() || (this._submission && this._submission !== submission)) return;
      this._submission = null;
      const detail = error.detail || error.message || '转换失败，画稿已保留';
      if (this._visible === false) this._pendingError = detail;
      else this._fail(detail);
    });
  },

  _openPreview() {
    const data = this._pendingPreview;
    if (!data || this._visible === false) return;
    this._pendingPreview = null;
    const current = pageSession.capture(this);
    getApp().globalData.pendingAdopt = data;
    const channel = typeof this.getOpenerEventChannel === 'function' ? this.getOpenerEventChannel() : null;
    if (channel && typeof channel.emit === 'function') channel.emit('adopt', data);
    wx.navigateBack({ fail: () => { if (current()) wx.switchTab({ url: '/pages/index/index' }); } });
  },

  _fail(message) {
    this.setData({ submitting: false });
    wx.showToast({ title: message, icon: 'none' });
  },

  _buildSvg() { return pad.buildSvg(this._strokes, this._viewport); },

  _queueDraw() {
    if (this._frame || this._visible === false) return;
    if (!this._canvas || !this._canvas.requestAnimationFrame) { this._redraw(); return; }
    this._frame = this._canvas.requestAnimationFrame(() => { this._frame = null; this._redraw(); });
  },

  _redraw() {
    if (!this._ctx || !this._view) return;
    const ctx = this._ctx;
    ctx.clearRect(0, 0, this._w, this._h);
    ctx.fillStyle = '#fffdf8';
    ctx.fillRect(0, 0, this._w, this._h);
    ctx.save();
    ctx.scale(this._w / this._viewport.width, this._h / this._viewport.height);
    ctx.translate(this._view.x, this._view.y);
    ctx.scale(this._view.scale, this._view.scale);
    if (this.data.grid) {
      ctx.fillStyle = '#d7d7d2';
      for (let x = 20; x < this._viewport.width; x += 20) {
        for (let y = 20; y < this._viewport.height; y += 20) ctx.fillRect(x - 0.6, y - 0.6, 1.2, 1.2);
      }
    }
    ctx.strokeStyle = '#242b33';
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (this.data.symmetry !== 'none') {
      ctx.strokeStyle = '#b6d2cb'; ctx.lineWidth = 1;
      ctx.beginPath();
      if (this.data.symmetry === 'radial') ctx.arc(this._viewport.width / 2, this._viewport.height / 2,
        Math.max(0, Math.min(this._viewport.width, this._viewport.height) / 2 - 2), 0, Math.PI * 2);
      else { ctx.moveTo(this._viewport.width / 2, 0); ctx.lineTo(this._viewport.width / 2, this._viewport.height); }
      ctx.stroke();
    }
    const strokes = this.data.replaying ? this._strokes.slice(0, this._replayCount) : this._strokes.concat(
      this._cur && this._cur.pts.length > 1 ? pad.symmetryStrokes(this._cur, this.data.symmetry, this._viewport) : []);
    for (let index = 0; index < strokes.length; index++) {
      const stroke = strokes[index];
      ctx.strokeStyle = index === this.data.selectedIndex ? '#218f8a' : '#242b33';
      ctx.lineWidth = index === this.data.selectedIndex ? 2.6 : 2;
      ctx.beginPath();
      for (const cmd of pad.commands(stroke)) {
        if (cmd[0] === 'M') ctx.moveTo(cmd[1], cmd[2]);
        else if (cmd[0] === 'Q') ctx.quadraticCurveTo(cmd[1], cmd[2], cmd[3], cmd[4]);
        else ctx.lineTo(cmd[1], cmd[2]);
      }
      ctx.stroke();
    }
    ctx.restore();
    // 空态直接画在 canvas 中，避免原生画布盖住普通 view，也不拦截第一笔触摸。
    if (!this._strokes.length && !this._cur) {
      const x = this._w / 2, y = this._h / 2;
      ctx.strokeStyle = '#97aca7';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(x - 22, y - 48);
      ctx.quadraticCurveTo(x - 5, y - 85, x - 5, y - 49);
      ctx.quadraticCurveTo(x + 7, y - 78, x + 10, y - 46);
      ctx.quadraticCurveTo(x + 20, y - 61, x + 24, y - 50);
      ctx.stroke();
      ctx.textAlign = 'center';
      ctx.fillStyle = '#92998e';
      ctx.font = '14px sans-serif';
      ctx.fillText('从这里，画第一笔', x, y - 6);
      ctx.fillStyle = '#a7aea3';
      ctx.font = '11px sans-serif';
      ctx.fillText('双指放大，细节慢慢画', x, y + 17);
    }
  },
});
