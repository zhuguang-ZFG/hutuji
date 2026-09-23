// 私有预览按登录会话缓存，最多三张并发；隐藏页不派发新请求。
// 2026-09-23 方案3：preview_cache 二级持久缓存（跨进页免重下）+ 可视区懒加载
//（replace 只泵首屏 IMMEDIATE 张，其余进视口时经 request 派发；生产复现裁决见 README）。
const api = require("./api");
const previewCache = require("./preview_cache");

const IMMEDIATE_DEFAULT = 6;

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
    this.deferred = new Map(); // 懒加载候命：card.id → card
    this.active = 0;
    this.visible = () => false;
  }
  owns() {
    return !this.page._unloaded && this.generation === getApp()._sessionGeneration && this.session === api.getSession();
  }
  replace(cards, visible, immediate = IMMEDIATE_DEFAULT) {
    this.visible = visible;
    this.rows = new Map(cards.map((card) => [key(card), card]));
    this.queue = [];
    this.deferred = new Map();
    let eager = 0;
    return cards.map((card) => {
      const k = key(card);
      let local = this.cache.get(k) || "";
      if (!local) {
        local = previewCache.get(k); // 持久缓存命中：免网络、跨进页复用
        if (local) this.cache.set(k, local);
      }
      if (!local && card.preview_url) {
        if (eager < immediate) {
          this.queue.push(card);
          eager++;
        } else {
          this.deferred.set(card.id, card);
        }
      }
      return Object.assign({}, card, { previewTmp: local,
        previewState: local ? "ready" : card.preview_url ? "loading" : "error" });
    });
  }
  request(id) { // 可视区懒加载：卡片进入视口时由 IntersectionObserver 触发
    const card = this.deferred.get(id);
    if (!card) return;
    this.deferred.delete(id);
    this.queue.unshift(card);
    this.pump();
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
        const persisted = previewCache.put(id, local) || local;
        this.cache.set(id, persisted);
        while (this.cache.size > 128) this.cache.delete(this.cache.keys().next().value);
        this.update(card, { previewTmp: persisted, previewState: "ready" });
      }).catch((err) => {
        if (err && err.status === 401 && this.generation === getApp()._sessionGeneration && !api.getSession()) {
          this.cache.clear();
          this.queue = [];
          this.deferred.clear();
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
    const k = key(card);
    this.cache.delete(k);
    previewCache.remove(k); // 文件可能损坏：持久层同步剔除，否则下次进页命中坏文件
    this.deferred.delete(card.id);
    this.update(card, { previewTmp: "", previewState: retry ? "loading" : "error" });
    if (retry && this.owns() && this.visible()) {
      this.queue.unshift(card);
      this.pump();
    }
  }
}

module.exports = { PreviewQueue, key, IMMEDIATE_DEFAULT };
