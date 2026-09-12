// 手绘画板：canvas 涂鸦 → SVG → /api/draw/upload（同传图链，后端零改动）。
// 画布按 A4 竖版 190:277 比例；导出 SVG 坐标 = 画布 CSS 像素（WYSIWYG），
// 上游 normalize=True 等比缩放进 190×277 页，纵横比一致不浪费纸。
// SVG 直出必须过 llm_svg._validate_svg 白名单：只有 svg/g/path + fill=none +
// 颜色描边，ellipse/rect 一律转 32 段/4 点 <path> 折线。
const api = require("../../utils/api");

const MAX_STROKES = 400;

Page({
  data: {
    tool: "pen",
    strokeCount: 0,
    submitting: false,
    canvasW: 300,
    canvasH: 437,
  },

  onLoad() {
    try {
      const win = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      // 顶栏 + 工具条 + 底栏的保守可用高度预算，画布按纸比例适配
      const avail = win.windowHeight - 210;
      let w = Math.floor(win.windowWidth - 24);
      let h = Math.round((w * 277) / 190);
      if (h > avail) {
        h = Math.floor(avail);
        w = Math.round((h * 190) / 277);
      }
      this.setData({ canvasW: w, canvasH: h });
    } catch (e) {
      // 取不到窗口尺寸保持默认 300×437（190:277 同比）
    }
  },

  onReady() {
    wx.createSelectorQuery()
      .in(this)
      .select("#pad")
      .fields({ node: true, size: true })
      .exec((res) => {
        const info = res && res[0];
        if (!info || !info.node) return;
        let dpr = 2;
        try {
          dpr =
            (wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync())
              .pixelRatio || 2;
        } catch (e) {}
        const node = info.node;
        node.width = info.width * dpr;
        node.height = info.height * dpr;
        this._ctx = node.getContext("2d");
        this._ctx.scale(dpr, dpr);
        this._w = info.width;
        this._h = info.height;
        this._strokes = [];
        this._cur = null;
        this._redraw();
      });
  },

  setTool(e) {
    this.setData({ tool: e.currentTarget.dataset.tool || "pen" });
  },

  onTouchStart(e) {
    if (!this._ctx || this.data.submitting) return;
    const t = e.touches[0];
    this._cur = { tool: this.data.tool, pts: [[t.x, t.y]] };
  },

  onTouchMove(e) {
    if (!this._cur) return;
    const t = e.touches[0];
    const p = [t.x, t.y];
    if (this.data.tool === "pen") {
      this._cur.pts.push(p);
    } else {
      this._cur.pts[1] = p;
    }
    this._redraw(this._cur);
  },

  onTouchEnd() {
    if (!this._cur) return;
    const s = this._cur;
    this._cur = null;
    if (s.tool === "pen") {
      if (s.pts.length === 1) {
        // 单击=点：零长线在 vpype 会被丢，展开成短段保住点
        s.pts.push([s.pts[0][0] + 1.2, s.pts[0][1]]);
      }
    } else if (
      !s.pts[1] ||
      Math.abs(s.pts[1][0] - s.pts[0][0]) +
        Math.abs(s.pts[1][1] - s.pts[0][1]) <
        6
    ) {
      this._redraw(); // 太小的形状当误触丢弃
      return;
    }
    if (this._strokes.length >= MAX_STROKES) {
      wx.showToast({ title: "笔画太多啦", icon: "none" });
      return;
    }
    this._strokes.push(s);
    this.setData({ strokeCount: this._strokes.length });
    this._redraw();
  },

  onTouchCancel() {
    this._cur = null;
    this._redraw();
  },

  undo() {
    if (this._cur || !this._strokes || !this._strokes.length) return;
    this._strokes.pop();
    this.setData({ strokeCount: this._strokes.length });
    this._redraw();
  },

  clearAll() {
    if (!this._strokes || !this._strokes.length || this._cur) return;
    wx.showModal({
      title: "清空画纸",
      content: "清掉当前所有笔画？",
      success: (res) => {
        if (!res.confirm) return;
        this._strokes = [];
        this.setData({ strokeCount: 0 });
        this._redraw();
      },
    });
  },

  submit() {
    if (this.data.submitting || !this._strokes || !this._strokes.length) return;
    this.setData({ submitting: true });
    const fsm = wx.getFileSystemManager();
    const path = `${wx.env.USER_DATA_PATH}/drawpad_${Date.now()}.svg`;
    try {
      fsm.writeFileSync(path, this._buildSvg(), "utf8");
    } catch (e) {
      this._fail("本地暂存失败");
      return;
    }
    const cleanup = () => {
      try {
        fsm.unlinkSync(path);
      } catch (e) {}
    };
    api
      .uploadFile("/api/draw/upload", path, "file")
      .then((data) => {
        cleanup();
        // 与拍照/相册/文档同汇合点：回创作页走 adoptJob 上预览
        const ch =
          typeof this.getOpenerEventChannel === "function"
            ? this.getOpenerEventChannel()
            : null;
        if (ch && typeof ch.emit === "function") ch.emit("adopt", data);
        wx.navigateBack();
      })
      .catch((err) => {
        cleanup();
        this._fail((err && err.detail) || "上传失败，稍后再试");
      });
  },

  _fail(msg) {
    this.setData({ submitting: false });
    wx.showToast({ title: msg, icon: "none" });
  },

  _shapePts(s) {
    if (s.tool === "line") return s.pts;
    const a = s.pts[0];
    const b = s.pts[1] || s.pts[0];
    const x0 = Math.min(a[0], b[0]);
    const x1 = Math.max(a[0], b[0]);
    const y0 = Math.min(a[1], b[1]);
    const y1 = Math.max(a[1], b[1]);
    if (s.tool === "rect") {
      return [
        [x0, y0],
        [x1, y0],
        [x1, y1],
        [x0, y1],
        [x0, y0],
      ];
    }
    // 白名单没有 <ellipse>：转 32 段 <path>（描摹端就是折线，等价）
    const pts = [];
    const cx = (x0 + x1) / 2;
    const cy = (y0 + y1) / 2;
    const rx = (x1 - x0) / 2;
    const ry = (y1 - y0) / 2;
    for (let i = 0; i <= 32; i++) {
      const ang = (i / 32) * Math.PI * 2;
      pts.push([cx + rx * Math.cos(ang), cy + ry * Math.sin(ang)]);
    }
    return pts;
  },

  _dOf(s) {
    const pts = s.tool === "pen" ? s.pts : this._shapePts(s);
    if (!pts || pts.length < 2) return "";
    return (
      "M" +
      pts
        .map(
          (p) =>
            Math.round(p[0] * 10) / 10 + " " + Math.round(p[1] * 10) / 10
        )
        .join("L")
    );
  },

  _buildSvg() {
    const w = Math.round(this._w);
    const h = Math.round(this._h);
    const paths = [];
    for (const s of this._strokes) {
      const d = this._dOf(s);
      if (d) paths.push(`<path d="${d}"/>`);
    }
    return (
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}">` +
      `<g fill="none" stroke="#000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
      paths.join("") +
      `</g></svg>`
    );
  },

  _redraw(live) {
    if (!this._ctx) return;
    const ctx = this._ctx;
    ctx.clearRect(0, 0, this._w, this._h);
    ctx.fillStyle = "#F7F5EF";
    ctx.fillRect(0, 0, this._w, this._h);
    ctx.strokeStyle = "#1A1A1A";
    ctx.lineWidth = 3;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const all = this._strokes.concat(live ? [live] : []);
    for (const s of all) {
      const pts = s.tool === "pen" ? s.pts : this._shapePts(s);
      if (!pts || !pts.length) continue;
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      if (pts.length === 1) ctx.lineTo(pts[0][0] + 1.2, pts[0][1]);
      ctx.stroke();
    }
  },
});
