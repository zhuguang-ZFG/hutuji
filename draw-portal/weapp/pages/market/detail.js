const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const pageSession = require("../../utils/page_session");
const { PreviewQueue } = require("../../utils/preview_queue");
const feedback = require("../../utils/operation_feedback");

Page({
  data: { id: "", title: "", attribution: "", license: "", changes: "", revision: "",
    previewTmp: "", previewState: "loading", loading: true, loadError: "",
    sending: false, favoriting: false, actionHint: "", deviceMac: "", deviceLabel: "" },

  onLoad(options) { this._id = (options && options.id) || ""; },
  onShow() {
    pageSession.show(this);
    this._ready = false;
    if (!this._previews || !this._previews.owns()) {
      this._previews = new PreviewQueue(this, (_card, patch) => this.setData(patch));
      this._loaded = null;
      this._action = null;
      this.setData({ id: "", title: "", attribution: "", license: "", changes: "", revision: "",
        previewTmp: "", previewState: "loading", loadError: "", actionHint: "", sending: false, favoriting: false });
    }
    const current = pageSession.capture(this);
    return phoneGate.ensurePhone(api, current).then((ok) => {
      if (!current()) return;
      if (ok === false) { this.setData({ loading: false, loadError: "账号信息读取失败，请重试", previewTmp: "" }); return; }
      if (!ok) { this.setData({ loading: false, loadError: "未登录或账号未读取：可先浏览，授权登录请到「我的」页", previewTmp: "" }); return; }
      this._ready = true;
      const me = getApp().globalData.user || {};
      const mac = me.active_device_mac || "";
      this.setData({ deviceMac: mac, deviceLabel: mac ? "当前写字机 · " + mac.slice(-5).toUpperCase() : "连接后即可绘制" });
      return this._loadDetail();
    });
  },
  onHide() { pageSession.hide(this); this._ready = false; },
  onUnload() { this._unloaded = true; this.onHide(); },
  _loadDetail() {
    if (!this._id) { this.setData({ loading: false, loadError: "作品不存在" }); return; }
    const visible = pageSession.capture(this);
    const generation = this._requestId = (this._requestId || 0) + 1;
    const current = () => visible() && this._requestId === generation;
    this.setData({ loading: true, loadError: "" });
    return api.request("/api/market/item/" + encodeURIComponent(this._id), { timeout: 12000 }).then((res) => {
      if (!current()) return;
      if (!res || res.id !== this._id || !res.revision || !res.preview_url ||
          (res.license === "CC-BY-4.0" && !res.attribution)) {
        throw { detail: "作品信息不完整，请稍后重试" };
      }
      this._loaded = res;
      const preview = this._previews.replace([res], current)[0];
      this.setData({ id: res.id, title: res.title, attribution: res.attribution || "",
        license: res.license, changes: res.changes || "", revision: res.revision, loading: false,
        previewTmp: preview.previewTmp, previewState: preview.previewState });
      wx.setNavigationBarTitle({ title: res.title || "精品图库" });
      this._previews.pump();
    }).catch((err) => {
      if (current()) this.setData({ loading: false, loadError: err.detail || "作品加载失败，请重试" });
    });
  },
  onRetry() { return this.onShow(); },
  onRetryPreview() { if (this._loaded && this._ready) this._previews.invalidate(this._loaded, true); },
  onPreviewError(e) {
    if (this._loaded && e.currentTarget.dataset.url === this.data.previewTmp) this._previews.invalidate(this._loaded, false);
  },
  onPreview() { if (this.data.previewState === "ready") wx.previewImage({ current: this.data.previewTmp, urls: [this.data.previewTmp] }); },
  onSend() {
    if (!this._ready || this._action || this.data.previewState !== "ready" || this.data.loading || this.data.loadError) return;
    if (!this.data.deviceMac) { wx.navigateTo({ url: "/pages/bind/bind" }); return; }
    return this._perform("send");
  },
  onFavorite() {
    if (!this._ready || this._action || this.data.previewState !== "ready" || this.data.loading || this.data.loadError) return;
    return this._perform("favorite");
  },
  _perform(kind) {
    const field = kind === "send" ? "sending" : "favoriting";
    const action = this._action = { current: pageSession.captureTask(this) };
    const current = () => action.current() && this._action === action;
    const data = { revision: this.data.revision, device_mac: this.data.deviceMac || null };
    this.setData({ [field]: true, actionHint: kind === "send" ? "正在准备并发送作品…" : "正在收藏作品…" });
    // 已发出的操作保留同账号结果，离页不续发控制，卸载或退出立即作废。
    return api.request("/api/market/item/" + encodeURIComponent(this.data.id) + "/" + kind,
      { method: "POST", data, timeout: kind === "send" ? feedback.SEND_TIMEOUT_MS : 90000 }).then((res) => {
      if (!current()) return;
      const ok = res && res.status === (kind === "send" ? "sent" : "saved");
      const detail = (res && res.detail) || "";
      // 2026-09-25：发送成功≠已开画——自动确认未完成时 detail 带可行动指引，
      // 必须透出，否则用户看到「已发送」而机器不动（当日生产事故）。
      const notStarted = ok && kind === "send" && detail.indexOf("自动开始画未完成") !== -1;
      this.setData({ actionHint: notStarted ? detail
        : ok ? (kind === "send" ? "作品已发送，请留意设备状态" : "已收藏，可在「历史」查看")
        : (detail || "操作未完成，请稍后重试") });
    }).catch((err) => {
      if (current()) this.setData({ actionHint: err.detail || "网络不可用，请稍后重试" });
    }).finally(() => {
      if (!current()) return;
      this._action = null;
      this.setData({ [field]: false });
    });
  },
});
