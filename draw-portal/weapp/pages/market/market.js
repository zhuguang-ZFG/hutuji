const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const pageSession = require("../../utils/page_session");
const { PreviewQueue, key, IMMEDIATE_DEFAULT } = require("../../utils/preview_queue");

Page({
  data: { categories: [], activeCategory: "", items: [], loading: true,
    loadError: "", accountError: "", nextCursor: null, empty: false },

  onShow() {
    pageSession.show(this);
    this._ready = false;
    // 2026-10-08：同账号从详情返回保留分类与已加载页，只恢复预览队列；
    // 换账号/卸载后（队列不再归属本会话）或用户主动下拉/整页重试才重新读取。
    const fresh = !this._previews || !this._previews.owns();
    const reload = fresh || this._reload === true;
    this._reload = false;
    if (fresh) {
      this._retryLoad = null;
      this._listLoaded = false;
      this._previews = new PreviewQueue(this, (card, patch) => {
        this.setData({ items: this.data.items.map((row) => key(row) === key(card) ? Object.assign({}, row, patch) : row) });
      });
      this.setData({ categories: [], activeCategory: "", items: [], nextCursor: null, loadError: "", accountError: "" });
    }
    const current = pageSession.capture(this);
    this.setData({ loading: true });
    return phoneGate.ensurePhone(api, current).then((ok) => {
      if (!current()) return;
      if (ok === false) {
        // 账号临时不可读不等于列表失效；保留同账号内容，但 _ready 仍为 false。
        this._reload = reload;
        this.setData({ loading: false, accountError: "账号信息读取失败，请重试后继续浏览" });
        return;
      }
      if (!ok) {
        this._listLoaded = false;
        this._retryLoad = null;
        this.setData({ items: [], categories: [], activeCategory: "", nextCursor: null, loading: false,
          accountError: "", loadError: "未登录或账号未读取：可先浏览，授权登录请到「我的」页" });
        return;
      }
      this._ready = true;
      this.setData({ accountError: "" });
      if (!reload && this.data.categories.length && this.data.activeCategory &&
        (this._listLoaded || this.data.items.length || this.data.loadError)) {
        return this._resumeItems();
      }
      return this._loadCategories();
    });
  },
  onHide() {
    pageSession.hide(this); this._ready = false;
    if (this._previewObserver) { this._previewObserver.disconnect(); this._previewObserver = null; }
  },
  onUnload() {
    this._unloaded = true;
    if (this._previewObserver) { this._previewObserver.disconnect(); this._previewObserver = null; }
    this.onHide();
  },
  onRetry() {
    if (this.data.loading) return Promise.resolve();
    if (this.data.accountError) return this.onShow();
    // 保留失败请求的类别与游标；目录刷新失败不能误判成旧列表翻页。
    if (this._ready && this._retryLoad) {
      const retry = this._retryLoad;
      return retry.kind === "categories" ? this._loadCategories()
        : this._loadItems(retry.category, retry.cursor);
    }
    this._reload = true;
    return this.onShow();
  },
  onPullDownRefresh() {
    this._reload = true;
    return this.onShow().finally(() => wx.stopPullDownRefresh());
  },
  onReachBottom() {
    if (this._ready && this.data.nextCursor !== null && !this.data.loading) {
      return this._loadItems(this.data.activeCategory, this.data.nextCursor);
    }
  },

  // 返回图库：已加载卡片原样保留，只按当前可视代次重建预览队列（命中持久缓存免下载，
  // 在途下载由队列 pending 去重），再挂回视口观察者续派首屏外预览。
  _resumeItems() {
    const visible = pageSession.capture(this);
    const category = this.data.activeCategory;
    // 返回时滚动位置可能在后续页，交给当前视口选择下载对象；不要重新挑六张未缓存图片。
    const immediate = typeof wx.createIntersectionObserver === "function" ? 0 : IMMEDIATE_DEFAULT;
    const items = this._previews.replace(this.data.items,
      () => visible() && this.data.activeCategory === category, immediate);
    this.setData({ items, loading: false });
    this._previews.pump();
    this._observePreviews();
  },

  _loadCategories() {
    const visible = pageSession.capture(this);
    const generation = this._requestId = (this._requestId || 0) + 1;
    const current = () => visible() && this._requestId === generation;
    this._retryLoad = { kind: "categories" };
    this.setData({ loading: true, loadError: "" });
    return api.request("/api/market/categories", { timeout: 12000 }).then((res) => {
      if (!current()) return;
      const categories = (res.categories || []).filter((cat) => cat.name && cat.count > 0);
      this.setData({ categories, empty: !categories.length });
      if (!categories.length) {
        this._retryLoad = null;
        this._listLoaded = false;
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
    this._listLoaded = false;
    this.setData({ activeCategory: name, items: [], nextCursor: null, loadError: "", empty: false });
    return this._loadItems(name, 0);
  },
  _loadItems(category, cursor) {
    const visible = pageSession.capture(this);
    const generation = this._requestId = (this._requestId || 0) + 1;
    const current = () => visible() && this._requestId === generation && this.data.activeCategory === category;
    this._retryLoad = { kind: "items", category, cursor };
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
      this._retryLoad = null;
      this._listLoaded = true;
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
    const observer = wx.createIntersectionObserver(this, { observeAll: true });
    this._previewObserver = observer;
    const current = pageSession.capture(this);
    // 可视区懒加载：卡片进视口才派发预览下载（首屏 6 张已由 replace 泵出）
    observer.relativeToViewport().observe(".gallery-card", (res) => {
      if (!current() || this._previewObserver !== observer || !this._ready) return;
      const id = res && res.dataset && res.dataset.id;
      if (id && res.intersectionRatio > 0 && this._previews) this._previews.request(id);
    });
  },
  onRetryPreview(e) {
    if (!this._ready) return;
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
