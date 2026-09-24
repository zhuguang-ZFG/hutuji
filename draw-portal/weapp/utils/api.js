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

function _networkFailure(err, writing) {
  const timedOut = /timeout/i.test((err && err.errMsg) || "");
  const detail = writing
    ? "网络连接中断或超时，操作结果尚未确认，请先核对状态，勿重复提交"
    : (timedOut ? "请求超时，请检查网络后重试" : "网络不可用，请检查网络");
  return { status: 0, detail, message: detail };
}

function _validResponse(res, data) {
  return Number.isInteger(res.statusCode) && res.statusCode >= 200 && res.statusCode < 300 &&
    data !== null && typeof data === "object";
}

function _invalidResponse(res) {
  const detail = "服务响应异常，结果尚未确认，请稍后查看状态";
  return { status: res.statusCode || 0, detail, message: detail };
}

// 401 统一处置：清会话态。2026-09-24 合规批起**不再**因 logged_out 强跳「我的」——
// 0.3.18 审核驳回「浏览页面即要求授权登录」，退出后的浏览页请求 401 只拒请求，
// 页面软闸（phone_gate.ensurePhone）自己渲染未登录可浏览态，重登入口在「我的」页。
function _reject401(reject, session, generation) {
  const app = getApp();
  // 网络回包可能跨越退出/重新登录；旧凭据的 401 只能拒掉该请求，
  // 无权清除新会话（HMAC token 也可能同秒重签相同，故同时比较代次）。
  if (session !== getSession() || generation !== app._sessionGeneration) {
    reject({ status: 401, detail: "登录已过期" });
    return;
  }
  wx.removeStorageSync("session_token");
  app.globalData.user = null;
  app.globalData.phoneBound = false;
  reject({ status: 401, detail: wx.getStorageSync("logged_out") ? "请先登录" : "登录已过期" });
}

function request(path, options = {}) {
  const generation = getApp()._sessionGeneration;
  const header = Object.assign(
    { "content-type": "application/json", "x-session": getSession() },
    options.header || {}
  );
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + path,
      method: options.method || "GET",
      data: options.data || {},
      header,
      // 默认 20s：状态/账号类查询走这个默认，弱网挂起不再把页面卡 150s。
      // 长任务由各调用点显式给出（生成/写字/笔顺 300s、语音 45s）；上传默认 150s 见 uploadFile。
      timeout: options.timeout || 20000,
      success(res) {
        if (res.statusCode === 401) {
          _reject401(reject, header["x-session"], generation);
          return;
        }
        if (res.statusCode >= 400) {
          const payload = _detailPayload(res.data);
          if (res.statusCode === 403 && payload.code === "phone_required" &&
            generation === getApp()._sessionGeneration && header["x-session"] === getSession()) {
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
        if (!_validResponse(res, res.data)) {
          reject(_invalidResponse(res));
          return;
        }
        resolve(res.data);
      },
      fail(err) {
        reject(_networkFailure(err, !!options.method && !/^(GET|HEAD)$/i.test(options.method)));
      },
    });
  });
}

function uploadFile(path, filePath, name, formData, options = {}) {
  const session = getSession();
  const generation = getApp()._sessionGeneration;
  return new Promise((resolve, reject) => {
    const opts = {
      url: BASE_URL + path,
      filePath: filePath,
      name: name || "file",
      header: { "x-session": session },
      timeout: options.timeout || 150000,
      success(res) {
        if (res.statusCode === 401) {
          _reject401(reject, session, generation);
          return;
        }
        if (res.statusCode >= 400) {
          let data = null;
          try {
            data = JSON.parse(res.data);
          } catch (e) {}
          const payload = _detailPayload(data || { detail: "上传失败" });
          if (res.statusCode === 403 && payload.code === "phone_required" &&
            generation === getApp()._sessionGeneration && session === getSession()) {
            _handlePhoneRequired(payload);
          }
          reject({
            status: res.statusCode,
            detail: payload.message,
            code: payload.code,
          });
          return;
        }
        let data = null;
        try {
          data = JSON.parse(res.data);
        } catch (e) {}
        if (!_validResponse(res, data)) {
          reject(_invalidResponse(res));
          return;
        }
        resolve(data);
      },
      fail(err) {
        reject(_networkFailure(err, true));
      },
    };
    if (formData && typeof formData === "object") {
      opts.formData = formData;
    }
    wx.uploadFile(opts);
  });
}

// 收藏预览等 secured 图片：<image>/wx.previewImage 发请求带不上 x-session 头，
// 真机会 401 白图；必须先经 wx.downloadFile（可带头）落地临时文件再显示。
// downloadFile 合法域名需在 mp 后台与 request/uploadFile 同配（2026-09-07 已加）。
function downloadFile(path) {
  const session = getSession();
  const generation = getApp()._sessionGeneration;
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: BASE_URL + path,
      header: { "x-session": session },
      // 收藏预览通常只有几 KB；弱网超过 12 秒便给出可重试状态，避免空占位久等。
      timeout: 12000,
      success(res) {
        if (res.statusCode === 401) {
          _reject401(reject, session, generation);
          return;
        }
        if (res.statusCode === 200 && res.tempFilePath) {
          resolve(res.tempFilePath);
          return;
        }
        reject({ status: res.statusCode, detail: res.statusCode === 404 ? "图片已不可用" : "图片加载失败，请重试" });
      },
      fail(err) {
        reject({ status: 0, detail: /timeout/i.test((err && err.errMsg) || "")
          ? "图片加载超时，请重试" : "网络不可用，请检查网络" });
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
