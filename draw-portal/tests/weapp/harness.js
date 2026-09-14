// 在隔离的 wx 容器中执行真实小程序模块，允许测试控制网络回包次序。
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../../weapp");

function createClock(now = 1800000000000) {
  let sequence = 0;
  const pending = new Map();
  return {
    get now() { return now; },
    pending,
    setTimeout(fn, delay = 0) {
      const id = ++sequence;
      pending.set(id, { fn, at: now + delay });
      return id;
    },
    clearTimeout(id) { pending.delete(id); },
    advance(duration) {
      const until = now + duration;
      let count = 0;
      while (pending.size) {
        const [id, task] = [...pending].sort((a, b) => a[1].at - b[1].at)[0];
        if (task.at > until) break;
        if (++count > 1000) throw new Error("测试定时器未收敛");
        pending.delete(id);
        now = task.at;
        task.fn();
      }
      now = until;
    },
  };
}

function createWeapp(options = {}) {
  const storage = options.storage || new Map();
  const logins = [];
  const requests = [];
  const uploads = [];
  const toasts = [];
  const tabs = [];
  const modals = [];
  const navigations = [];
  const scans = [];
  const downloads = [];
  const actionSheets = [];
  // 微信运行时每种录音事件仅保留一个回调，没有 offStart/offStop/offError。
  const recorderEvents = {};
  const recorder = {
    starts: [], stops: 0,
    start(options) { this.starts.push(options); },
    stop() { this.stops++; },
    onStart(fn) { recorderEvents.start = fn; },
    onStop(fn) { recorderEvents.stop = fn; },
    onError(fn) { recorderEvents.error = fn; },
    onInterruptionBegin(fn) { recorderEvents.interruption = fn; },
    emit(event, result) { if (recorderEvents[event]) recorderEvents[event](result); },
  };
  let app;
  let page;
  const wx = {
    getStorageSync: (key) => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: (key) => storage.delete(key),
    login: (options) => logins.push(options),
    request: (options) => requests.push(options),
    uploadFile: (options) => uploads.push(options),
    showToast: (options) => toasts.push(options),
    showModal: (options) => modals.push(options),
    switchTab: (options) => tabs.push(options),
    navigateTo: (options) => navigations.push(options),
    redirectTo: (options) => navigations.push(options),
    scanCode: (options) => scans.push(options),
    downloadFile: (options) => downloads.push(options),
    showActionSheet: (options) => actionSheets.push(options),
    getRecorderManager: () => recorder,
    showLoading: () => {},
    hideLoading: () => {},
    stopPullDownRefresh: () => {},
    setNavigationBarTitle: () => {},
  };
  const clock = options.clock;
  const context = vm.createContext({
    wx,
    App: (definition) => { app = definition; },
    Page: (definition) => {
      page = definition;
      // 逻辑层到视图层会序列化；不能靠对象别名让两份列表误以为已同步。
      page.setData = (data) => {
        for (const [key, value] of Object.entries(data)) {
          if (value === undefined) continue;
          const parts = key.replace(/\[(\d+)\]/g, ".$1").split(".");
          let target = definition.data;
          for (const part of parts.slice(0, -1)) target = target[part];
          target[parts.at(-1)] = JSON.parse(JSON.stringify(value));
        }
      };
    },
    getApp: () => app,
    getCurrentPages: () => page ? [page] : [],
    setTimeout: clock ? clock.setTimeout : setTimeout,
    clearTimeout: clock ? clock.clearTimeout : clearTimeout,
    Date: clock ? class extends Date {
      constructor(...args) { super(...(args.length ? args : [clock.now])); }
      static now() { return clock.now; }
    } : Date,
  });
  const modules = new Map();
  function load(relative, parent = ROOT) {
    const filename = path.resolve(parent, relative).replace(/(?:\.js)?$/, ".js");
    if (modules.has(filename)) return modules.get(filename).exports;
    const module = { exports: {} };
    modules.set(filename, module);
    const run = vm.runInContext(
      `(function(require, module, exports) {\n${fs.readFileSync(filename, "utf8")}\n})`,
      context, { filename }
    );
    run((name) => load(name, path.dirname(filename)), module, module.exports);
    return module.exports;
  }
  load("app.js");
  return {
    app, load, storage, logins, requests, uploads, toasts, tabs, wx, modals, navigations, scans,
    downloads, actionSheets, recorder,
    get page() { return page; },
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

module.exports = { createWeapp, createClock, flush };
