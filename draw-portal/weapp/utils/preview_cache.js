// 预览持久缓存（2026-09-23 方案3 裁决）：downloadFile 的临时文件会被微信随时回收，
// 页面实例级 Map 又随进页销毁，导致每次进收藏/图库页全量重下（生产复现根因）。
// 这里把预览落 USER_DATA_PATH，键 = 内容身份（saved id / market item+v 修订号），
// LRU 上界 32 项；登出清空。命中即免网络请求。
const INDEX_KEY = "preview_cache_index_v1";
const MAX_ITEMS = 32;

function _fs() {
  return wx.getFileSystemManager();
}

function _dir() {
  return wx.env.USER_DATA_PATH + "/preview-cache";
}

function _path(key) {
  return _dir() + "/" + String(key).replace(/[^A-Za-z0-9_-]/g, "_") + ".png";
}

function _loadIndex() {
  const idx = wx.getStorageSync(INDEX_KEY);
  return Array.isArray(idx) ? idx : [];
}

function _saveIndex(idx) {
  try {
    wx.setStorageSync(INDEX_KEY, idx);
  } catch (e) { /* 存储满时宁可丢索引不炸页面 */ }
}

function get(key) {
  const idx = _loadIndex();
  const hit = idx.find((e) => e.key === key);
  if (!hit) return "";
  try {
    _fs().accessSync(hit.file);
  } catch (e) {
    _saveIndex(idx.filter((e) => e.key !== key)); // 文件被系统清掉：索引同步剔除
    return "";
  }
  _saveIndex([hit].concat(idx.filter((e) => e.key !== hit.key))); // LRU 提头
  return hit.file;
}

function put(key, tempPath) {
  if (!tempPath) return "";
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
  try {
    _fs().rmdirSync(_dir(), true);
  } catch (e) { /* 目录不存在 */ }
  try {
    wx.removeStorageSync(INDEX_KEY);
  } catch (e) { /* 忽略 */ }
}

module.exports = { get, put, remove, clear, MAX_ITEMS };
