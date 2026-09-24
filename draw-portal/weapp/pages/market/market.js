const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const pageSession = require("../../utils/page_session");
const { PreviewQueue, key } = require("../../utils/preview_queue");

Page({
  data: { categories: [], activeCategory: "", items: [], loading: true,
    loadError: "", nextCursor: null, empty: false },

  onShow() {
    pageSession.show(this);
    this._ready = false;
    if (!this._previews || !this._previews.owns()) {
      this._previews = new PreviewQueue(this, (card, patch) => {
        this.setData({ items: this.data.items.map((row) => key(row) === key(card) ? Object.assign({}, row, patch) : row) });
      });
      this.setData({ categories: [], activeCategory: "", items: [], nextCursor: null, loadError: "" });
    }
    const current = pageSession.capture(this);
    this.setData({ loading: true, loadError: "" });
    return phoneGate.ensurePhone(api, current).then((ok) => {
      if (!current()) return;
      if (ok === false) {
        this.setData({ items: [], loading: false, loadError: "账号信息读取失败，请重试" });
        return;
      }
      if (!ok) {
        this.setData({ items: [], loading: false, loadError: "未登录或账号未读取：可先浏览，授权登录请到「我的」页" });
        return;
      }
      this._ready = true;
      return this._loadCategories();
    });
  },
  onHide() { pageSession.hide(this); this._ready = false; },
  onUnload() {
    this._unloaded = true;
    if (this._previewObserver) { this._previewObserver.disconnect(); this._previewObserver = null; }
    this.onHide();
  },
  onRetry() { return this.onShow(); },
  onPullDownRefresh() { return this.onShow().finally(() => wx.stopPullDownRefresh()); },
  onReachBottom() {
    if (this._ready && this.data.nextCursor !== null && !this.data.loading) {
      return this._loadItems(this.data.activeCategory, this.data.nextCursor);
    }
  },

  _loadCategories() {
    const visible = pageSession.capture(this);
    const generation = this._requestId = (this._requestId || 0) + 1;
    const current = () => visible() && this._requestId === generation;
    return api.request("/api/market/categories", { timeout: 12000 }).then((res) => {
      if (!current()) return;
      const categories = (res.categories || []).filter((cat) => cat.name && cat.count > 0);
      this.setData({ categories, empty: !categories.length });
      if (!categories.length) {
        this.setData({ loading: false, items: [], activeCategory: "", nextCursor: null });
        return;
      }
      const name = categories.some((cat) => cat.name === this.data.activeCategory)
        ? this.data.activeCategory : categories[0].name;
      return this._switchCategory(name);
    }).catch((err) => {
      if (current()) this.setData({ loading: false, loadError: err.detail || "图库加载失败，请重试" });
    });
  },

  onCategoryTap(e) {
    const name = e.currentTarget.dataset.name;
    if (!this._ready || !this.data.categories.some((cat) => cat.name === name) || name === this.data.activeCategory) return;
    return this._switchCategory(name);
  },
  _switchCategory(name) {
    this.setData({ activeCategory: name, items: [], nextCursor: null, loadError: "", empty: false });
    return this._loadItems(name, 0);
  },
  _loadItems(category, cursor) {
    const visible = pageSession.capture(this);
    const generation = this._requestId = (this._requestId || 0) + 1;
    const current = () => visible() && this._requestId === generation && this.data.activeCategory === category;
    this.setData({ loading: true, loadError: "" });
    return api.request("/api/market/list?category=" + encodeURIComponent(category) + "&cursor=" + cursor,
      { timeout: 12000 }).then((res) => {
      if (!current()) return;
      const existing = cursor ? this.data.items : [];
      const ids = new Set(existing.map((card) => card.id));
      const incoming = (res.items || []).filter((card) => {
        if (!card.id || ids.has(card.id)) return false;
        ids.add(card.id);
        return true;
      });
      const items = this._previews.replace(existing.concat(incoming), () => visible() && this.data.activeCategory === category);
      const next = res.next_cursor;
      this.setData({ items, loading: false,
        nextCursor: Number.isInteger(next) && next > cursor ? next : null });
      this._previews.pump();
      this._observePreviews();
    }).catch((err) => {
      if (current()) this.setData({ loading: false, loadError: err.detail || "作品加载失败，请重试" });
    });
  },
  _observePreviews() {
    if (this._previewObserver) this._previewObserver.disconnect();
    if (typeof wx.createIntersectionObserver !== "function") return;
    const observer = wx.createIntersectionObserver(this);
    this._previewObserver = observer;
    // 可视区懒加载：卡片进视口才派发预览下载（首屏 6 张已由 replace 泵出）
    observer.relativeToViewport().observe(".gallery-card", (res) => {
      const id = res && res.dataset && res.dataset.id;
      if (id && res.intersectionRatio > 0 && this._previews) this._previews.request(id);
    });
  },
  onRetryPreview(e) {
    const card = this.data.items.find((row) => row.id === e.currentTarget.dataset.id);
    if (card && card.previewState === "error") this._previews.invalidate(card, true);
  },
  onPreviewError(e) {
    const card = this.data.items.find((row) => row.id === e.currentTarget.dataset.id);
    if (card && card.previewTmp === e.currentTarget.dataset.url) this._previews.invalidate(card, false);
  },
  onItemTap(e) {
    if (!this._ready) return;
    const id = e.currentTarget.dataset.id;
    if (this.data.items.some((card) => card.id === id)) {
      wx.navigateTo({ url: "/pages/market/detail?id=" + encodeURIComponent(id) });
    }
  },
});
