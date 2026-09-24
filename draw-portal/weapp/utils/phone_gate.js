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

/** 浏览时软闸（2026-09-24 合规批）：查 /me 与刷新缓存同硬闸，但未登录/未绑定
 *  **不强跳「我的」**——页面渲染可浏览的未登录态，授权只在用户主动操作时提。
 *  依据：0.3.18 审核驳回「未完整浏览页面详情即要求授权登录，登录规范不合规」，
 *  微信要求先体验后自主选择授权。Tab/内容页 onShow 一律用本函数；
 *  硬闸 ensurePhoneOrRedirect 仅保留给绑定/配网等目的页与后端 phone_required 动作回包。
 *  返回值三态：已绑定 → /me 回包（truthy）；确定未登录/未绑定 → null（页面给可浏览未登录态）；
 *  网络失败或会话代次过期 → false（页面给「读取失败可重试」态，两者话术不得混淆）。 */
function ensurePhone(api, isCurrent = () => true) {
  const app = getApp();
  const generation = app._sessionGeneration;
  const current = () => generation === getApp()._sessionGeneration && isCurrent();
  return Promise.resolve(app._sessionPromise)
    .then(() => current() ? api.request("/api/auth/me") : null)
    .then((me) => {
      if (!current()) return false;
      if (!me) return null;
      const app2 = getApp();
      if (app2 && app2.globalData) {
        app2.globalData.user = me;
        app2.globalData.phoneBound = isPhoneBound(me);
      }
      return isPhoneBound(me) ? me : null;
    })
    // 401 = 服务端确认未登录/会话无效 → null（可浏览未登录态）；其余失败 → false。
    .catch((err) => (err && err.status === 401 ? null : false));
}

/** 页面 onShow：无手机号则切到「我的」。已绑定 resolve 本次 /me 回包（truthy），
 *  调用方可在同一 onShow 链路内复用、避免紧跟着再打一次 /me；未绑定/失败 resolve false。 */
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
      return me;
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
module.exports = { isPhoneBound, redirectToPhoneGate, ensurePhoneOrRedirect, ensurePhone };
