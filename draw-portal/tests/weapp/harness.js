// 在隔离的 wx 容器中执行真实小程序模块，允许测试控制网络回包次序。
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "../../weapp");

function createWeapp() {
  const storage = new Map();
  const logins = [];
  const requests = [];
  const uploads = [];
  const toasts = [];
  const tabs = [];
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
    showModal() {},
    switchTab: (options) => tabs.push(options),
  };
  const context = vm.createContext({
    wx,
    App: (definition) => { app = definition; },
    Page: (definition) => {
      page = definition;
      page.setData = (data) => Object.assign(page.data, data);
    },
    getApp: () => app,
    setTimeout,
    clearTimeout,
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
    app, load, storage, logins, requests, uploads, toasts, tabs,
    get page() { return page; },
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

module.exports = { createWeapp, flush };
