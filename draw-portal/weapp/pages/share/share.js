// 分享落地页：接收者点开分享卡 → 免会话只读预览 + 「我也要画」。
// 令牌自证（/api/draw/share/{token}，无 x-session）；预览图经 downloadFile
// 代理端点落地临时文件再显示（与收藏预览同口径）。
const api = require("../../utils/api");

Page({
  data: {
    loading: true,
    failed: false,
    title: "",
    previewUrl: "",
    createdAt: "",
  },

  onLoad(options) {
    const token = (options && options.share) || "";
    if (!token) {
      this.setData({ loading: false, failed: true });
      return;
    }
    this._token = token;
    api
      .request("/api/draw/share/" + encodeURIComponent(token))
      .then((info) => {
        this.setData({
          loading: false,
          failed: false,
          title: (info && info.title) || "作品",
          createdAt: this._fmt(info && info.created_at),
        });
        return api
          .downloadFile(info.preview_url)
          .then((local) => this.setData({ previewUrl: local }))
          .catch(() => {});
      })
      .catch(() => this.setData({ loading: false, failed: true }));
  },

  _fmt(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return "";
    const d = new Date(n * 1000);
    const p = (x) => (x < 10 ? "0" + x : "" + x);
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  },

  onGoDraw() {
    wx.switchTab({ url: "/pages/index/index" });
  },

  onShareAppMessage() {
    if (this._token) {
      return {
        title: this.data.title || "看看我的作品",
        path: "/pages/share/share?share=" + this._token,
      };
    }
    return { title: "动力巢", path: "/pages/index/index" };
  },
});
