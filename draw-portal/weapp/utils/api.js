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
          const detail =
            (res.data && res.data.detail) || "请求失败，请稍后再试";
          reject({ status: res.statusCode, detail });
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
          let detail = "上传失败";
          try {
            const data = JSON.parse(res.data);
            if (data && data.detail) detail = data.detail;
          } catch (e) {}
          reject({ status: res.statusCode, detail });
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

module.exports = { BASE_URL, request, uploadFile, getSession, setSession };
