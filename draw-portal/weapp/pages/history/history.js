const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const subscribe = require("../../utils/subscribe");
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
    jobs: [],
    filtered: [],
    filterKind: "all",
    redrawing: false,
    loading: true,
  },

  onShow() {
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (ok) this.refresh();
      else this.setData({ loading: false });
    });
  },

  onPullDownRefresh() {
    this.refresh(() => wx.stopPullDownRefresh());
  },

  refresh(done) {
    api
      .request("/api/draw/saved")
      .then((data) => {
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
        jobs.forEach((job, idx) => {
          if (!job.previewPath) return;
          api
            .downloadFile(job.previewPath)
            .then((local) => this.setData({ [`jobs[${idx}].previewUrl`]: local }))
            .catch(() => {}); // 单条失败留占位，不打扰整页
        });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loading: false });
        if (done) done();
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
    subscribe.requestAtTap();
    const path = item.content || item.sourceName;
    wx.showModal({
      title: "重新生成？",
      content: path,
      success: (res) => {
        if (!res.confirm) return;
        this._redraw(item.kind, path);
      },
    });
  },

  _redraw(kind, content) {
    this.setData({ redrawing: true });
    wx.showLoading({ title: "重画中…", mask: true });
    const fail = (detail) => {
      wx.hideLoading();
      this.setData({ redrawing: false });
      wx.showModal({ title: "重画失败", content: detail || "请稍后再试", showCancel: false });
    };
    const ok = (data) => {
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
    const savedId = e.currentTarget.dataset.id;
    const idx = Number(e.currentTarget.dataset.idx);
    wx.showActionSheet({
      itemList: ["删除"],
      itemColor: "#FA5151",
      success: (res) => {
        if (res.tapIndex !== 0) return;
        wx.showModal({
          title: "删除这件作品",
          content: "删除后不能再发送到写字机，确定？",
          confirmText: "删除",
          confirmColor: "#FA5151",
          success: (r) => {
            if (!r.confirm) return;
            api
              .request(`/api/draw/saved/${savedId}`, { method: "DELETE" })
              .then(() => {
                const jobs = this.data.jobs.slice();
                jobs.splice(idx, 1);
                this.setData({ jobs });
                wx.showToast({ title: "已删除", icon: "success" });
              })
              .catch((err) => {
                wx.showModal({
                  title: "删除失败",
                  content: err.detail || "请稍后再试",
                  showCancel: false,
                });
              });
          },
        });
      },
    });
  },

  onSend(e) {
    const savedId = e.currentTarget.dataset.id;
    wx.showModal({
      title: "发送到写字机",
      content: "将直接开始画，无需再在小派上确认",
      success: (res) => {
        if (!res.confirm) return;
        api
          .request(`/api/draw/saved/${savedId}/send`, { method: "POST" })
          .then((r) => {
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
            if (err.status === 400 && /绑定/.test(err.detail || "")) {
              wx.navigateTo({ url: "/pages/bind/bind" });
              return;
            }
            wx.showModal({ title: "发送失败", content: err.detail || "请稍后再试", showCancel: false });
          });
      },
    });
  },

  onDelete(e) {
    const savedId = e.currentTarget.dataset.id;
    wx.showModal({
      title: "删除收藏",
      content: "删除后不能从历史再发送，确认删除？",
      confirmColor: "#e54d42",
      success: (res) => {
        if (!res.confirm) return;
        api
          .request(`/api/draw/saved/${savedId}`, { method: "DELETE" })
          .then(() => {
            wx.showToast({ title: "已删除", icon: "success" });
            this.refresh();
          })
          .catch((err) => {
            wx.showModal({
              title: "删除失败",
              content: err.detail || "请稍后再试",
              showCancel: false,
            });
          });
      },
    });
  },
});
