// 静默登录：wx.login 拿 code → /api/auth/wx-login 换会话。
// 真机 wx.request 无 cookie 罐（iOS/Android 常丢 Set-Cookie）——后端在返回体里
// 下发 session HMAC blob，落 storage 后经 x-session 头回带（见 utils/api.js）。
App({
  _sessionGeneration: 0,
  _sessionPromise: null,
  globalData: {
    user: null,
    phoneBound: false,
  },
  onLaunch() {
    this.ensureSession();
  },
  ensureSession() {
    // 「退出登录」闸门：用户主动退出后不再静默重登（否则退出立即被 wx.login
    // 无感恢复、形同虚设）。等用户在「我的」页显式点「重新登录」才清此标记。
    if (wx.getStorageSync("logged_out")) {
      this.globalData.user = null;
      this.globalData.phoneBound = false;
      return Promise.resolve(null);
    }
    if (this._sessionPromise) return this._sessionPromise;
    // 每轮登录和退出都换代：旧 wx.login、换会话及 /me 回包不得恢复已退出的
    // 身份，也不能清掉后来显式登录的新会话。并发调用共用一轮登录。
    const generation = ++this._sessionGeneration;
    const isCurrent = () =>
      generation === this._sessionGeneration && !wx.getStorageSync("logged_out");
    const api = require("./utils/api");
    const phoneGate = require("./utils/phone_gate");
    const pending = new Promise((resolve) => {
      wx.login({
        success: (res) => {
          if (!isCurrent() || !res.code) {
            resolve(null);
            return;
          }
          api
            .request("/api/auth/wx-login", {
              method: "POST",
              data: { code: res.code },
            })
            .then((data) => {
              if (!isCurrent()) {
                resolve(null);
                return;
              }
              api.setSession(data.session || "");
              wx.setStorageSync("session_ok", true);
              return api.request("/api/auth/me").then((me) => {
                if (!isCurrent()) {
                  resolve(null);
                  return;
                }
                this.globalData.user = me;
                this.globalData.phoneBound = phoneGate.isPhoneBound(me);
                resolve(me);
              });
            })
            .catch(() => {
              if (!isCurrent()) {
                resolve(null);
                return;
              }
              wx.removeStorageSync("session_ok");
              wx.removeStorageSync("session_token");
              this.globalData.user = null;
              this.globalData.phoneBound = false;
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
          if (!isCurrent()) {
            resolve(null);
            return;
          }
          wx.showToast({
            title: "微信登录不可用",
            icon: "none",
            duration: 3000,
          });
          resolve(null);
        },
      });
    });
    this._sessionPromise = pending;
    pending.then(() => {
      if (this._sessionPromise === pending) this._sessionPromise = null;
    });
    return pending;
  },
  // 退出登录：纯客户端清除（会话是无状态 HMAC blob，服务端无可吊销对象）。
  // 置 logged_out 闸门阻止下次冷启动被静默重登，详见 ensureSession。
  logout() {
    ++this._sessionGeneration;
    this._sessionPromise = null;
    wx.removeStorageSync("session_token");
    wx.removeStorageSync("session_ok");
    wx.removeStorageSync("voice_session_id");
    wx.setStorageSync("logged_out", true);
    this.globalData.user = null;
    this.globalData.phoneBound = false;
    this.globalData.pendingAdopt = null;
  },
  // 用户在「我的」页显式点重新登录：清闸门后走正常静默链路。
  loginAgain() {
    wx.removeStorageSync("logged_out");
    return this.ensureSession();
  },
});
