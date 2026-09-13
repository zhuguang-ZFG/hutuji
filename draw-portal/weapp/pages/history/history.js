const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const subscribe = require("../../utils/subscribe");
const pageSession = require("../../utils/page_session");
const KINDS = [
  { key: "all", label: "全部" },
  { key: "draw", label: "AI 画" },
  { key: "write", label: "写字" },
  { key: "teach", label: "笔顺" },
];

// 标题前缀即任务谱系（source_name 在 create_draw_job 时带语种前缀）；
// 传图/精修没有可重画的自然语言内容 → kind=null，不显示「重画」钮。
function parseKind(title) {
  const t = String(title || "");
  if (/^AI: /.test(t) || /^语音画: /.test(t)) {
    return { kind: "draw", content: t.replace(/^(AI|语音画): /, "") };
  }
  if (/^写字: /.test(t) || /^语音写: /.test(t)) {
    return { kind: "write", content: t.replace(/^(写字|语音写): /, "") };
  }
  if (/^笔顺: /.test(t)) return { kind: "teach", content: t.replace(/^笔顺: /, "") };
  return { kind: null, content: "" };
}

Page({
  data: {
    kinds: KINDS,
    jobs: [],
    filtered: [],
    filterKind: "all",
    redrawing: false,
    loading: true,
    loadError: "",
    sendingId: "",
    deletingId: "",
  },

  onShow() {
    pageSession.show(this);
    const current = pageSession.capture(this);
    this.setData({ jobs: [], filtered: [], loading: true, loadError: "", redrawing: false,
      sendingId: "", deletingId: "" });
    phoneGate.ensurePhoneOrRedirect(api, current).then((ok) => {
      if (!current()) return;
      if (ok) this.refresh();
      else this.setData({ loading: false });
    });
  },

  onHide() {
    pageSession.hide(this);
    if (this._loadingOverlay) wx.hideLoading();
    this._loadingOverlay = false;
  },
  onUnload() { this.onHide(); },

  onPullDownRefresh() {
    this.refresh(() => wx.stopPullDownRefresh());
  },

  refresh(done) {
    const visible = pageSession.capture(this);
    const requestId = this._listRequest = (this._listRequest || 0) + 1;
    const current = () => visible() && this._listRequest === requestId;
    this.setData({ loading: true, loadError: "" });
    return api
      .request("/api/draw/saved")
      .then((data) => {
        if (!current()) return;
        // 收藏列表契约：{items:[{id,title,saved_at,preview_url}]}，发送走 saved/{id}/send。
        // preview_url 是 secured 相对路径，<image> 直连必 401（带不上 x-session），
        // 先置空、逐条 downloadFile 落地临时文件后回填本地路径。
        const jobs = (data.items || []).map((it) => {
          const parsed = parseKind(it.title);
          return {
            savedId: it.id,
            previewUrl: "",
            previewPath: it.preview_url,
            sourceName: it.title || "作品",
            createdAt: String(it.saved_at || "").replace("T", " ").slice(0, 16),
            kind: parsed.kind,
            content: parsed.content,
          };
        });
        this.setData({ jobs, loading: false });
        this._applyFilter();
        jobs.forEach((job) => {
          if (!job.previewPath) return;
          api
            .downloadFile(job.previewPath)
            .then((local) => {
              if (!current()) return;
              this.setData({ jobs: this.data.jobs.map((item) => item.savedId === job.savedId
                ? Object.assign({}, item, { previewUrl: local }) : item) });
              this._applyFilter();
            })
            .catch(() => {}); // 单条失败留占位，不打扰整页
        });
      })
      .catch((err) => {
        if (current()) this.setData({ loading: false, loadError: err.detail || "作品读取失败，请重试" });
      })
      .finally(() => {
        if (current() && typeof done === "function") done();
      });
  },

  _applyFilter() {
    const kind = this.data.filterKind;
    const filtered =
      kind === "all" ? this.data.jobs.slice() : this.data.jobs.filter((j) => j.kind === kind);
    this.setData({ filtered });
  },

  onFilter(e) {
    const key = e.currentTarget.dataset.kind;
    if (!key || key === this.data.filterKind) return;
    this.setData({ filterKind: key });
    this._applyFilter();
  },

  // 一键重画（2026-09-11）：标题谱系还原请求，成功后跨页交接给创作页上屏。
  onRedraw(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const item = this.data.filtered[idx];
    if (!item || !item.kind || this.data.redrawing) return;
    const current = pageSession.capture(this);
    this.setData({ redrawing: true });
    subscribe.requestAtTap();
    const path = item.content || item.sourceName;
    wx.showModal({
      title: "重新生成？",
      content: path,
      success: (res) => {
        if (!current()) return;
        if (!res.confirm) { this.setData({ redrawing: false }); return; }
        this._redraw(item.kind, path);
      },
      fail: () => { if (current()) this.setData({ redrawing: false }); },
    });
  },

  _redraw(kind, content) {
    const current = pageSession.capture(this);
    this.setData({ redrawing: true });
    this._loadingOverlay = true;
    wx.showLoading({ title: "重画中…", mask: true });
    const fail = (detail) => {
      if (!current()) return;
      this._loadingOverlay = false;
      wx.hideLoading();
      this.setData({ redrawing: false });
      wx.showModal({ title: "重画失败", content: detail || "请稍后再试", showCancel: false });
    };
    const ok = (data) => {
      if (!current()) return;
      this._loadingOverlay = false;
      wx.hideLoading();
      this.setData({ redrawing: false });
      getApp().globalData.pendingAdopt = data;
      wx.switchTab({ url: "/pages/index/index" });
    };
    if (kind === "draw") {
      api
        .request("/api/draw/generate", { method: "POST", data: { description: content } })
        .then(ok)
        .catch((err) => fail(err.detail));
    } else if (kind === "write") {
      api
        .request("/api/draw/write", { method: "POST", data: { text: content } })
        .then(ok)
        .catch((err) => fail(err.detail));
    } else {
      api
        .request("/api/draw/teach", { method: "POST", data: { text: content } })
        .then(ok)
        .catch((err) => fail(err.detail));
    }
  },

  onPreview(e) {
    const url = e.currentTarget.dataset.url;
    if (!url) return; // 预览尚未下载完成
    wx.previewImage({ urls: [url] });
  },

  // 长按删除（2026-09-09 补）：后端 DELETE /api/draw/saved/{id} 早已上线但前端无入口
  onLongPress(e) {
    const current = pageSession.capture(this);
    wx.showActionSheet({
      itemList: ["删除"],
      itemColor: "#FA5151",
      success: (res) => {
        if (res.tapIndex === 0 && current()) this.onDelete(e);
      },
    });
  },

  onSend(e) {
    const savedId = e.currentTarget.dataset.id;
    if (!savedId || this.data.sendingId) return;
    const current = pageSession.capture(this);
    this.setData({ sendingId: savedId });
    wx.showModal({
      title: "发送到丹青",
      content: "将直接开始画，无需再在小派上确认",
      success: (res) => {
        if (!current()) return;
        if (!res.confirm) { this.setData({ sendingId: "" }); return; }
        api
          .request(`/api/draw/saved/${savedId}/send`, { method: "POST" })
          .then((r) => {
            if (!current()) return;
            if (r.status === "sent") {
              const started = /已自动开始画/.test(r.detail || "");
              wx.showToast({
                title: started ? "已开始画" : "已发送",
                icon: "success",
              });
            } else {
              wx.showModal({ title: "发送失败", content: r.detail || "设备未响应", showCancel: false });
            }
          })
          .catch((err) => {
            if (!current()) return;
            if (err.status === 400 && /绑定/.test(err.detail || "")) {
              wx.navigateTo({ url: "/pages/bind/bind" });
              return;
            }
            wx.showModal({ title: "发送失败", content: err.detail || "请稍后再试", showCancel: false });
          })
          .finally(() => { if (current()) this.setData({ sendingId: "" }); });
      },
      fail: () => { if (current()) this.setData({ sendingId: "" }); },
    });
  },

  onDelete(e) {
    const savedId = e.currentTarget.dataset.id;
    if (!savedId || this.data.deletingId) return;
    const current = pageSession.capture(this);
    this.setData({ deletingId: savedId });
    wx.showModal({
      title: "删除收藏",
      content: "删除后不能从历史再发送，确认删除？",
      confirmColor: "#e54d42",
      success: (res) => {
        if (!current()) return;
        if (!res.confirm) { this.setData({ deletingId: "" }); return; }
        api
          .request(`/api/draw/saved/${savedId}`, { method: "DELETE" })
          .then(() => {
            if (!current()) return;
            // 筛选页的 index 不属于原列表；按稳定 ID 同时更新数据和显示。
            this.setData({ jobs: this.data.jobs.filter((item) => item.savedId !== savedId) });
            this._applyFilter();
            wx.showToast({ title: "已删除", icon: "success" });
          })
          .catch((err) => {
            if (!current()) return;
            wx.showModal({
              title: "删除失败",
              content: err.detail || "请稍后再试",
              showCancel: false,
            });
          })
          .finally(() => { if (current()) this.setData({ deletingId: "" }); });
      },
      fail: () => { if (current()) this.setData({ deletingId: "" }); },
    });
  },
});
