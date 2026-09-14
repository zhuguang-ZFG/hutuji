// 分享落地页：接收者点开分享卡 → 免会话只读预览 + 「我也要画」。
// 令牌自证（/api/draw/share/{token}，无 x-session）；预览图经 downloadFile
// 代理端点落地临时文件再显示（与收藏预览同口径）。
const api = require("../../utils/api");
const pageSession = require("../../utils/page_session");

Page({
  data: {
    loading: true,
    failed: false,
    failureTitle: '',
    failureDetail: '',
    retryable: false,
    title: "",
    previewUrl: "",
    previewLoading: false,
    previewError: '',
    createdAt: "",
  },

  onLoad(options) {
    this._token = (options && options.share) || "";
  },

  onShow() {
    pageSession.show(this);
    if (!this._info) this._loadShare();
    else if (!this.data.previewUrl) this._loadPreview();
  },

  onHide() { pageSession.hide(this); },
  onUnload() { this._unloaded = true; this.onHide(); },

  onRetry() {
    if (!this.data.loading && this.data.retryable) this._loadShare();
  },

  _loadShare() {
    if (this._visible === false || this._unloaded) return;
    if (!this._token) {
      this.setData({ loading: false, failed: true, retryable: false,
        failureTitle: '分享链接不完整', failureDetail: '请重新打开分享卡片' });
      return;
    }
    const visible = pageSession.capture(this);
    const requestId = this._metadataRequest = (this._metadataRequest || 0) + 1;
    const current = () => visible() && this._metadataRequest === requestId;
    this.setData({ loading: true, failed: false, previewUrl: '', previewError: '' });
    api
      .request("/api/draw/share/" + encodeURIComponent(this._token))
      .then((info) => {
        if (!current()) return;
        this._info = info;
        this.setData({
          loading: false,
          failed: false,
          title: (info && info.title) || "作品",
          createdAt: this._fmt(info && info.created_at),
        });
        this._loadPreview();
      })
      .catch((error) => {
        if (!current()) return;
        const expired = error && (error.status === 404 || error.status === 410);
        this.setData({ loading: false, failed: true, retryable: !expired,
          failureTitle: expired ? '分享已失效' : '暂时无法读取作品',
          failureDetail: expired ? '链接过期或作品已删除' : '请检查网络后重试' });
      });
  },

  onRetryPreview() {
    if (!this.data.previewLoading) this._loadPreview();
  },

  _loadPreview() {
    if (this._visible === false || this._unloaded) return;
    const path = this._info && this._info.preview_url;
    if (!path) { this.onPreviewError(); return; }
    const visible = pageSession.capture(this);
    const requestId = this._imageRequest = (this._imageRequest || 0) + 1;
    const current = () => visible() && this._imageRequest === requestId;
    this.setData({ previewLoading: true, previewError: '', previewUrl: '' });
    api.downloadFile(path).then((local) => {
      if (current()) this.setData({ previewUrl: local, previewLoading: false });
    }).catch(() => { if (current()) this.onPreviewError(); });
  },

  onPreviewError() {
    if (this._visible === false || this._unloaded) return;
    this.setData({ previewUrl: '', previewLoading: false, previewError: '图片加载失败，请重试' });
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
        path: "/pages/share/share?share=" + encodeURIComponent(this._token),
      };
    }
    return { title: "动力巢科技", path: "/pages/index/index" };
  },
});
