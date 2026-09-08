// 配置唯一入口：改域名只动这里。appid 由开发者工具导入时用测试号或
// 正式号覆盖（project.config.json 不入敏感信息，appid 本身非机密）。
const BASE_URL = "https://hutuji.donglicao.com/draw-upload";

// 真机无 cookie 罐：wx-login 返回体里的 session 值落 storage，之后所有
// 请求经 x-session 头回带（后端 auth.SESSION_HEADER 同名接受）。
function getSession() {
  return wx.getStorageSync("session_token") || "";
}
function setSession(v) {
  if (v) wx.setStorageSync("session_token", v);
}

function _detailPayload(data) {
  if (!data) return { message: "请求失败，请稍后再试", code: null };
  const d = data.detail;
  if (d && typeof d === "object") {
    return {
      message: d.message || d.code || "请求失败，请稍后再试",
      code: d.code || null,
    };
  }
  if (typeof d === "string") return { message: d, code: null };
  return { message: "请求失败，请稍后再试", code: null };
}

function _handlePhoneRequired(payload) {
  if (payload.code !== "phone_required") return;
  try {
    const gate = require("./phone_gate");
    gate.redirectToPhoneGate(payload.message);
  } catch (e) {
    wx.showToast({ title: payload.message || "请先授权手机号", icon: "none" });
  }
}

function request(path, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + path,
      method: options.method || "GET",
      data: options.data || {},
      header: Object.assign(
        { "content-type": "application/json", "x-session": getSession() },
        options.header || {}
      ),
      timeout: options.timeout || 150000,
      success(res) {
        if (res.statusCode === 401) {
          // 会话过期：清态回首页重新静默登录
          wx.removeStorageSync("session_ok");
          wx.removeStorageSync("session_token");
          getApp().globalData.user = null;
          reject({ status: 401, detail: "登录已过期" });
          return;
        }
        if (res.statusCode >= 400) {
          const payload = _detailPayload(res.data);
          if (res.statusCode === 403 && payload.code === "phone_required") {
            _handlePhoneRequired(payload);
          }
          reject({
            status: res.statusCode,
            detail: payload.message,
            code: payload.code,
            message: payload.message,
          });
          return;
        }
        resolve(res.data);
      },
      fail(err) {
        reject({ status: 0, detail: "网络不可用，请检查网络" });
      },
    });
  });
}

function uploadFile(path, filePath, name) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: BASE_URL + path,
      filePath: filePath,
      name: name || "file",
      header: { "x-session": getSession() },
      timeout: 150000,
      success(res) {
        if (res.statusCode >= 400) {
          let data = null;
          try {
            data = JSON.parse(res.data);
          } catch (e) {}
          const payload = _detailPayload(data || { detail: "上传失败" });
          if (res.statusCode === 403 && payload.code === "phone_required") {
            _handlePhoneRequired(payload);
          }
          reject({
            status: res.statusCode,
            detail: payload.message,
            code: payload.code,
          });
          return;
        }
        let data = res.data;
        try {
          data = JSON.parse(res.data);
        } catch (e) {}
        resolve(data);
      },
      fail() {
        reject({ status: 0, detail: "网络不可用，请检查网络" });
      },
    });
  });
}

// 收藏预览等 secured 图片：<image>/wx.previewImage 发请求带不上 x-session 头，
// 真机会 401 白图；必须先经 wx.downloadFile（可带头）落地临时文件再显示。
// downloadFile 合法域名需在 mp 后台与 request/uploadFile 同配（2026-09-07 已加）。
function downloadFile(path) {
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: BASE_URL + path,
      header: { "x-session": getSession() },
      success(res) {
        if (res.statusCode === 200) {
          resolve(res.tempFilePath);
          return;
        }
        reject({ status: res.statusCode, detail: "图片加载失败" });
      },
      fail() {
        reject({ status: 0, detail: "网络不可用，请检查网络" });
      },
    });
  });
}


/** 相对路径 → 可给 <image> 用的绝对 URL；已是 http(s)/本地临时路径则原样返回。 */
function mediaUrl(path) {
  if (!path) return "";
  const s = String(path);
  if (/^(https?:|wxfile:|data:|blob:)/i.test(s)) return s;
  if (s.startsWith("//")) return "https:" + s;
  return BASE_URL + (s.startsWith("/") ? s : "/" + s);
}

module.exports = {
  BASE_URL,
  request,
  uploadFile,
  downloadFile,
  getSession,
  setSession,
  mediaUrl,
};
