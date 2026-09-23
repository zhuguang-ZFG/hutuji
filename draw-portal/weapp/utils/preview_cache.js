// 预览持久缓存（2026-09-23 方案3 裁决）：downloadFile 的临时文件会被微信随时回收，
// 页面实例级 Map 又随进页销毁，导致每次进收藏/图库页全量重下（生产复现根因）。
// 这里把预览落 USER_DATA_PATH，键 = 内容身份（saved id / market item+v 修订号），
// LRU 上界与内存层对齐 128 项（原 32 会在长列表会话内逐出在屏文件导致闪失）；
// 登出/换账号清空。命中即免网络请求。
const INDEX_KEY = "preview_cache_index_v1";
const MAX_ITEMS = 128;

// 文件名用键的 cyrb53 哈希（稳定 + 低碰撞；非安全用途）：旧语义键经字符替换后
// 不同键可映射同一文件名互相覆盖串图（如 "a.b" 与 "a/b" 都落成 a_b.png）。
function hashKey(key) {
  const s = String(key);
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < s.length; i++) {
    const ch = s.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (h2 >>> 0).toString(16).padStart(8, "0") + (h1 >>> 0).toString(16).padStart(8, "0");
}

function _fs() {
  return wx.getFileSystemManager();
}

function _dir() {
  return wx.env.USER_DATA_PATH + "/preview-cache";
}

function _path(key) {
  return _dir() + "/" + hashKey(key) + ".png";
}

// 旧语义命名文件（非 16 位十六进制哈希名）永不再被索引引用，每进程一次性扫掉。
let _legacySwept = false;
function _sweepLegacy() {
  if (_legacySwept) return;
  _legacySwept = true;
  let names;
  try {
    names = _fs().readdirSync(_dir());
  } catch (e) {
    return; // 目录不存在
  }
  for (const name of names || []) {
    if (!/^[0-9a-f]{16}\.png$/.test(name)) {
      try {
        _fs().removeSync(_dir() + "/" + name);
      } catch (e) { /* 忽略 */ }
    }
  }
}

// 进程内索引缓存：get 命中只动内存序不落盘（列表页逐项命中会放大成上百次
// 同步存储写）；持久化只在 put/remove/clear/缺文件剔除时发生，进程被杀最坏
// 丢次序精化，仅影响逐出优先级。
let _memIdx = null;
function _loadIndex() {
  if (_memIdx) return _memIdx;
  const idx = wx.getStorageSync(INDEX_KEY);
  _memIdx = Array.isArray(idx) ? idx : [];
  return _memIdx;
}

function _saveIndex(idx) {
  _memIdx = idx;
  try {
    wx.setStorageSync(INDEX_KEY, idx);
  } catch (e) { /* 存储满时宁可丢索引不炸页面 */ }
}

function get(key) {
  _sweepLegacy();
  const idx = _loadIndex();
  const pos = idx.findIndex((e) => e.key === key);
  if (pos < 0) return "";
  const hit = idx[pos];
  try {
    _fs().accessSync(hit.file);
  } catch (e) {
    _saveIndex(idx.filter((e) => e.key !== key)); // 文件被系统清掉：索引同步剔除
    return "";
  }
  idx.splice(pos, 1); // LRU 提头：仅内存序
  idx.unshift(hit);
  return hit.file;
}

function put(key, tempPath) {
  if (!tempPath) return "";
  _sweepLegacy();
  try {
    _fs().mkdirSync(_dir(), true);
  } catch (e) { /* 已存在 */ }
  const file = _path(key);
  try {
    _fs().removeSync(file); // 同键覆盖：先删再存
  } catch (e) { /* 不存在 */ }
  try {
    _fs().saveFileSync(tempPath, file);
  } catch (e) {
    return "";
  }
  const idx = _loadIndex().filter((e) => e.key !== key);
  idx.unshift({ key, file, t: Date.now() });
  while (idx.length > MAX_ITEMS) {
    const tail = idx.pop();
    try {
      _fs().removeSync(tail.file);
    } catch (e) { /* 已被系统清理 */ }
  }
  _saveIndex(idx);
  return file;
}

function remove(key) {
  const idx = _loadIndex();
  const hit = idx.find((e) => e.key === key);
  _saveIndex(idx.filter((e) => e.key !== key));
  if (hit) {
    try {
      _fs().removeSync(hit.file);
    } catch (e) { /* 不存在 */ }
  }
}

function clear() {
  _memIdx = [];
  try {
    _fs().rmdirSync(_dir(), true);
  } catch (e) { /* 目录不存在 */ }
  try {
    wx.removeStorageSync(INDEX_KEY);
  } catch (e) { /* 忽略 */ }
}

module.exports = { get, put, remove, clear, hashKey, MAX_ITEMS };
