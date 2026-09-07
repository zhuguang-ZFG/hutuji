// 静默登录：wx.login 拿 code → /api/auth/wx-login 换会话 cookie。
// wx.request 自动携带/保存 Set-Cookie（同域），后端下发的是 draw-portal
// 同款签名会话——小程序与网页共用账号体系。
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
              resolve(null);
            });
        },
        fail: () => resolve(null),
      });
    });
  },
});
