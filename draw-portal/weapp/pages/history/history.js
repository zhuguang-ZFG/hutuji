const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const subscribe = require("../../utils/subscribe");
const pageSession = require("../../utils/page_session");
const previewCache = require("../../utils/preview_cache");
const feedback = require("../../utils/operation_feedback");
const KINDS = [
  { key: "all", label: "全部" },
  { key: "draw", label: "AI 画" },
  { key: "write", label: "写字" },
  { key: "teach", label: "笔顺" },
  { key: "upload", label: "照片/手绘" },
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

function formatSavedTime(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const date = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function previewKey(job) { return job.savedId + "|" + job.previewPath; }

Page({
  data: {
    kinds: KINDS,
    jobs: [],
    filtered: [],
    filterKind: "all",
    searchText: "",
    sortOrder: "newest",
    redrawing: false,
    loading: true,
    loadError: "",
    sendingId: "",
    deletingId: "",
  },

  onShow() {
    pageSession.show(this);
    this._ready = false;
    this._ensurePreviewState();
    const session = getApp()._sessionGeneration;
    if (this._redrawSession !== session) {
      this._redrawSession = session;
      this._redrawPending = null;
    }
    const current = pageSession.capture(this);
    this.setData({ loading: true, loadError: "", redrawing: !!this._redrawPending,
      sendingId: "", deletingId: "" });
    return phoneGate.ensurePhone(api, current).then((ok) => {
      if (!current()) return;
      if (ok === false) { this.setData({ jobs: [], filtered: [], loading: false, loadError: "账号信息读取失败，请重试" }); return; }
      if (!ok) { this.setData({ jobs: [], filtered: [], loading: false, loadError: "未登录或账号未读取：可先浏览，授权登录请到「我的」页" }); return; }
      this._ready = true;
      if (!this._deliverRedraw()) this.refresh();
    });
  },

  onHide() {
    pageSession.hide(this);
    this._ready = false;
    if (this._previewObserver) { this._previewObserver.disconnect(); this._previewObserver = null; }
  },
  onUnload() {
    this._unloaded = true;
    if (this._previewObserver) { this._previewObserver.disconnect(); this._previewObserver = null; }
    this.onHide();
  },

  onRetry() { return this.onShow(); },

  onPullDownRefresh() {
    if (!this._ready) { this.onShow().finally(() => wx.stopPullDownRefresh()); return; }
    this.refresh(() => wx.stopPullDownRefresh());
  },

  refresh(done) {
    const previews = this._ensurePreviewState();
    const visible = pageSession.capture(this);
    const requestId = this._listRequest = (this._listRequest || 0) + 1;
    const current = () => visible() && this._listRequest === requestId;
    this.setData({ loading: true, loadError: "" });
    return api
      .request("/api/draw/saved", { timeout: 15000 })
      .then((data) => {
        if (!current()) return;
        // 收藏路径属于当前账号；预览经持久缓存跨进页复用（键=内容身份，登出清空），列表仍从服务端核对。
        const jobs = (data.items || []).map((it) => {
          const parsed = parseKind(it.title);
          const k = it.id + "|" + it.preview_url;
          let local = previews.cache.get(k) || "";
          if (!local) {
            local = previewCache.get(k); // 持久缓存命中：跨进页免重下（2026-09-23 方案3）
            if (local) previews.cache.set(k, local);
          }
          return {
            savedId: it.id,
            previewUrl: local,
            previewPath: it.preview_url,
            previewState: local ? "ready" : (it.preview_url ? "loading" : "error"),
            previewError: it.preview_url ? "" : "图片已不可用",
            sourceName: it.title || "作品",
            credit: it.credit || null,
            createdAt: formatSavedTime(it.saved_at),
            savedAt: Number(it.saved_at) || 0,
            kind: parsed.kind,
            content: parsed.content,
          };
        });
        this.setData({ jobs, loading: false });
        this._applyFilter();
        const keys = new Set(jobs.map(previewKey));
        for (const key of previews.cache.keys()) if (!keys.has(key)) previews.cache.delete(key);
        previews.current = current;
        // 可视区懒加载：首屏 6 张立即泵，其余进视口时经 _requestPreview 派发
        const pendingJobs = jobs.filter((job) => job.previewPath && !job.previewUrl);
        previews.queue = pendingJobs.slice(0, 6);
        previews.deferred = pendingJobs.slice(6);
        this._pumpPreviews(previews);
        this._observePreviews();
      })
      .catch((err) => {
        if (!current()) return;
        if (err.status === 401) {
          previews.cache.clear();
          this.setData({ jobs: [], filtered: [] });
        }
        this.setData({ loading: false, loadError: err.detail || "作品读取失败，请重试" });
      })
      .finally(() => {
        if (current() && typeof done === "function") done();
      });
  },

  _ensurePreviewState() {
    const generation = getApp()._sessionGeneration;
    const session = api.getSession();
    const state = this._previews;
    if (!state || state.generation !== generation || state.session !== session) {
      this._previews = { generation, session, cache: new Map(), pending: new Set(), queue: [], deferred: [],
        active: 0, current: () => false };
      this.setData({ jobs: [], filtered: [], searchText: "", filterKind: "all", sortOrder: "newest" });
    }
    return this._previews;
  },

  _setPreview(job, patch) {
    this.setData({ jobs: this.data.jobs.map((item) => previewKey(item) === previewKey(job)
      ? Object.assign({}, item, patch) : item) });
    this._applyFilter();
  },

  _pumpPreviews(state) {
    if (this._previews !== state || state.session !== api.getSession() || !state.current()) return;
    // 留出网络名额给登录、控制等请求；切页停止派发，已发出的同账号图片可留在内存。
    while (state.active < 3 && state.queue.length) {
      const job = state.queue.shift();
      const key = previewKey(job);
      if (state.pending.has(key) || state.cache.has(key)) continue;
      state.pending.add(key);
      state.active++;
      const owner = pageSession.captureTask(this);
      const belongs = () => owner() && this._previews === state && state.session === api.getSession() &&
        this.data.jobs.some((item) => previewKey(item) === key);
      api.downloadFile(job.previewPath).then((local) => {
        if (!belongs()) return;
        const persisted = previewCache.put(key, local) || local; // 落持久缓存：下次进页免重下
        state.cache.set(key, persisted);
        if (state.current()) this._setPreview(job, { previewUrl: persisted, previewState: "ready", previewError: "" });
      }).catch((err) => {
        if (err && err.status === 401 && owner() && this._previews === state && state.current() && !api.getSession()) {
          state.cache.clear();
          state.queue = [];
          this.setData({ jobs: [], filtered: [], loading: false, loadError: "登录已过期，请重新登录" });
          return;
        }
        if (belongs() && state.current()) this._setPreview(job, {
          previewUrl: "", previewState: "error", previewError: (err && err.detail) || "图片加载失败，请重试",
        });
      }).finally(() => {
        state.pending.delete(key);
        state.active--;
        this._pumpPreviews(state);
      });
    }
  },

  onRetryPreview(e) {
    const job = this.data.jobs.find((item) => item.savedId === e.currentTarget.dataset.id);
    const state = this._previews;
    if (!job || !job.previewPath || job.previewState !== "error" || !state || !state.current()) return;
    state.cache.delete(previewKey(job));
    previewCache.remove(previewKey(job)); // 持久层同步剔除，防坏文件被下次进页命中
    this._setPreview(job, { previewUrl: "", previewState: "loading", previewError: "" });
    state.queue.unshift(job);
    this._pumpPreviews(state);
  },

  onPreviewError(e) {
    const data = e.currentTarget.dataset;
    const job = this.data.jobs.find((item) => item.savedId === data.id);
    const state = this._previews;
    if (!job || !state || !state.current() || job.previewUrl !== data.url) return;
    state.cache.delete(previewKey(job));
    previewCache.remove(previewKey(job));
    this._setPreview(job, { previewUrl: "", previewState: "error", previewError: "图片加载失败，请重试" });
  },

  _observePreviews() {
    if (this._previewObserver) this._previewObserver.disconnect();
    if (typeof wx.createIntersectionObserver !== "function") return;
    const observer = wx.createIntersectionObserver(this, { observeAll: true });
    this._previewObserver = observer;
    // 可视区懒加载：卡片进视口才把预览从候命队列提到下载队列
    observer.relativeToViewport().observe(".work-cell", (res) => {
      const id = res && res.dataset && res.dataset.id;
      if (id && res.intersectionRatio > 0) this._requestPreview(id);
    });
  },

  _requestPreview(savedId) {
    const state = this._previews;
    if (!state || !state.current()) return;
    const i = (state.deferred || []).findIndex((job) => job.savedId === savedId);
    if (i < 0) return;
    const job = state.deferred.splice(i, 1)[0];
    state.queue.unshift(job);
    this._pumpPreviews(state);
  },

  _applyFilter() {
    const kind = this.data.filterKind;
    const words = (this.data.searchText || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
    const filtered = this.data.jobs.filter((job) => {
      if (kind !== "all" && (kind === "upload" ? !!job.kind : job.kind !== kind)) return false;
      const text = (String(job.sourceName || "") + " " + String(job.content || "")).toLowerCase();
      return words.every((word) => text.includes(word));
    });
    filtered.sort((a, b) => ((b.savedAt || 0) - (a.savedAt || 0)) * (this.data.sortOrder === "oldest" ? -1 : 1));
    this.setData({ filtered });
  },

  _refreshFilter() {
    this._applyFilter();
    this._observePreviews();
    // 搜索命中的首屏作品可能原本在候命区，不必等再次滚动才加载。
    this.data.filtered.slice(0, 6).forEach((job) => this._requestPreview(job.savedId));
  },
  onSearch(e) { this.setData({ searchText: String(e.detail.value || "").slice(0, 80) }); this._refreshFilter(); },
  onClearSearch() { this.setData({ searchText: "" }); this._refreshFilter(); },
  onClearFilters() { this.setData({ searchText: "", filterKind: "all" }); this._refreshFilter(); },
  onSortToggle() { this.setData({ sortOrder: this.data.sortOrder === "newest" ? "oldest" : "newest" }); this._refreshFilter(); },
  onGoCreate() { wx.switchTab({ url: "/pages/index/index" }); },

  onFilter(e) {
    const key = e.currentTarget.dataset.kind;
    if (!KINDS.some((kind) => kind.key === key) || key === this.data.filterKind) return;
    this.setData({ filterKind: key });
    this._refreshFilter();
  },

  // 一键重画（2026-09-11）：标题谱系还原请求，成功后跨页交接给创作页上屏。
  onRedraw(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const id = e.currentTarget.dataset.id;
    const item = id ? this.data.jobs.find((job) => job.savedId === id) : this.data.filtered[idx];
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
    if (this._redrawPending || this._visible === false || !this._ready) return;
    const pending = this._redrawPending = { current: pageSession.captureTask(this), done: false };
    this.setData({ redrawing: true });
    const current = () => pending.current() && this._redrawPending === pending;
    const ok = (data) => {
      if (!current()) return;
      if (!data || !data.job_id) throw { detail: "未能生成预览，请重试" };
      pending.done = true;
      pending.data = data;
      this._deliverRedraw();
    };
    const path = kind === 'draw' ? '/api/draw/generate' : kind === 'write' ? '/api/draw/write' : '/api/draw/teach';
    api.request(path, { method: 'POST', timeout: 300000,
      data: kind === 'draw' ? { description: content } : { text: content } })
      .then(ok).catch((error) => {
        if (!current()) return;
        pending.done = true;
        pending.error = (error && error.detail) || '请稍后再试';
        this._deliverRedraw();
      });
  },

  _deliverRedraw() {
    const pending = this._redrawPending;
    if (!pending || !pending.done || !pending.current() || this._visible === false || !this._ready) return false;
    this._redrawPending = null;
    this.setData({ redrawing: false, loading: false });
    if (pending.error) {
      wx.showModal({ title: '重画失败', content: pending.error, showCancel: false });
      return false;
    } else {
      getApp().globalData.pendingAdopt = pending.data;
      wx.switchTab({ url: '/pages/index/index' });
      return true;
    }
  },

  onPreview(e) {
    const url = e.currentTarget.dataset.url;
    if (!url) return; // 预览尚未下载完成
    wx.previewImage({ urls: [url], fail: () => this.onPreviewError(e) });
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
          .request(`/api/draw/saved/${savedId}/send`, { method: "POST", timeout: feedback.SEND_TIMEOUT_MS })
          .then((r) => {
            if (!current()) return;
            feedback.showSendResult(r, current);
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
            this._observePreviews(); // 删卡后节点重建，恢复 deferred 卡片的视口派发
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
