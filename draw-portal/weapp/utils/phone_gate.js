// 手机号硬前置：未绑定则拦截 Tab/产品页，引导到「我的」授权。
const PROFILE_TAB = "/pages/profile/profile";

function isPhoneBound(me) {
  if (!me) return false;
  if (typeof me.phone_bound === "boolean") return me.phone_bound;
  return !!(me.phone && String(me.phone).trim());
}

function redirectToPhoneGate(message) {
  const title = message || "请先授权手机号";
  wx.showToast({ title, icon: "none", duration: 2000 });
  const pages = getCurrentPages();
  const cur = pages.length ? pages[pages.length - 1] : null;
  const route = cur ? "/" + cur.route : "";
  if (route === PROFILE_TAB) return;
  wx.switchTab({ url: PROFILE_TAB });
}

/** 页面 onShow：无手机号则切到「我的」。返回 Promise<boolean> 是否已绑定。 */
function ensurePhoneOrRedirect(api, isCurrent = () => true) {
  const app = getApp();
  const generation = app._sessionGeneration;
  // 授权回包也可能跨越退出登录或页面重扫；旧请求不能复活资料或发起跳转。
  const current = () => generation === getApp()._sessionGeneration && isCurrent();
  // 冷启动先等正在进行的登录，避免拿空会话查 /me 后让页面一直停在未读取。
  return Promise.resolve(app._sessionPromise)
    .then(() => current() ? api.request("/api/auth/me") : null)
    .then((me) => {
      if (!me || !current()) return false;
      const app = getApp();
      if (app && app.globalData) {
        app.globalData.user = me;
        app.globalData.phoneBound = isPhoneBound(me);
      }
      if (!isPhoneBound(me)) {
        redirectToPhoneGate();
        return false;
      }
      return true;
    })
    .catch((err) => {
      if (!current()) return false;
      if (err && err.code === "phone_required") {
        redirectToPhoneGate(err.message);
      } else if (err && err.status === 401) {
        redirectToPhoneGate("登录已过期，请重新登录");
      }
      return false;
    });
}

module.exports = { isPhoneBound, redirectToPhoneGate, ensurePhoneOrRedirect };
