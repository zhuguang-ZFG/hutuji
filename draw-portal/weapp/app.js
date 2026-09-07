// 静默登录：wx.login 拿 code → /api/auth/wx-login 换会话。
// 真机 wx.request 无 cookie 罐（iOS/Android 常丢 Set-Cookie）——后端在返回体里
// 下发 session HMAC blob，落 storage 后经 x-session 头回带（见 utils/api.js）。
App({
  globalData: {
    user: null,
  },
  onLaunch() {
    this.ensureSession();
  },
  ensureSession() {
    const api = require("./utils/api");
    return new Promise((resolve) => {
      wx.login({
        success: (res) => {
          if (!res.code) {
            resolve(null);
            return;
          }
          api
            .request("/api/auth/wx-login", {
              method: "POST",
              data: { code: res.code },
            })
            .then((data) => {
              api.setSession(data.session || "");
              wx.setStorageSync("session_ok", true);
              this.globalData.user = data.user || null;
              resolve(this.globalData.user);
            })
            .catch(() => {
              wx.removeStorageSync("session_ok");
              wx.removeStorageSync("session_token");
              this.globalData.user = null;
              // 登录失败必须可见：503=后端未配密钥、域名未白名单等都会走到这，
              // 静默匿名页会让真机联调误判成「功能坏了」
              wx.showToast({
                title: "登录失败，请检查网络后重试",
                icon: "none",
                duration: 3000,
              });
              resolve(null);
            });
        },
        fail: () => {
          wx.showToast({
            title: "微信登录不可用",
            icon: "none",
            duration: 3000,
          });
          resolve(null);
        },
      });
    });
  },
});
