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
function ensurePhoneOrRedirect(api) {
  return api
    .request("/api/auth/me")
    .then((me) => {
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
      if (err && err.code === "phone_required") {
        redirectToPhoneGate(err.message);
      }
      return false;
    });
}

module.exports = { isPhoneBound, redirectToPhoneGate, ensurePhoneOrRedirect };
