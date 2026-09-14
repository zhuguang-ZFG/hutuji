// 私有预览按登录会话缓存，最多三张并发；隐藏页不派发新请求。
const api = require("./api");

function key(card) { return card.id + "|" + card.preview_url; }

class PreviewQueue {
  constructor(page, changed) {
    this.page = page;
    this.changed = changed;
    this.generation = getApp()._sessionGeneration;
    this.session = api.getSession();
    this.cache = new Map();
    this.pending = new Set();
    this.rows = new Map();
    this.queue = [];
    this.active = 0;
    this.visible = () => false;
  }
  owns() {
    return !this.page._unloaded && this.generation === getApp()._sessionGeneration && this.session === api.getSession();
  }
  replace(cards, visible) {
    this.visible = visible;
    this.rows = new Map(cards.map((card) => [key(card), card]));
    this.queue = [];
    return cards.map((card) => {
      const local = this.cache.get(key(card)) || "";
      if (!local && card.preview_url) this.queue.push(card);
      return Object.assign({}, card, { previewTmp: local,
        previewState: local ? "ready" : card.preview_url ? "loading" : "error" });
    });
  }
  update(card, patch) {
    if (this.owns() && this.visible() && this.rows.has(key(card))) this.changed(card, patch);
  }
  pump() {
    if (!this.owns() || !this.visible()) return;
    while (this.active < 3 && this.queue.length) {
      const card = this.queue.shift();
      const id = key(card);
      if (this.pending.has(id) || this.cache.has(id)) continue;
      this.pending.add(id);
      this.active++;
      api.downloadFile(card.preview_url).then((local) => {
        if (!this.owns()) return;
        this.cache.set(id, local);
        while (this.cache.size > 128) this.cache.delete(this.cache.keys().next().value);
        this.update(card, { previewTmp: local, previewState: "ready" });
      }).catch((err) => {
        if (err && err.status === 401 && this.generation === getApp()._sessionGeneration && !api.getSession()) {
          this.cache.clear();
          this.queue = [];
          if (!this.page._unloaded && this.visible()) {
            this.page._ready = false;
            this.page.setData({ loadError: "登录已过期，请重新登录", loading: false });
          }
        }
        this.update(card, { previewTmp: "", previewState: "error" });
      }).finally(() => {
        this.pending.delete(id);
        this.active--;
        this.pump();
      });
    }
  }
  invalidate(card, retry) {
    this.cache.delete(key(card));
    this.update(card, { previewTmp: "", previewState: retry ? "loading" : "error" });
    if (retry && this.owns() && this.visible()) {
      this.queue.unshift(card);
      this.pump();
    }
  }
}

module.exports = { PreviewQueue, key };
