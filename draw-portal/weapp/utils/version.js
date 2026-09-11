// 页脚版本号单一来源（2026-09-11）：运行时向微信要真实上传版本，
// 避免每次发版手改四个页脚又漏改（0.3.4 批页脚仍 v0.3.3 事故的根治）。
// 正式版返回 v{version}；体验版返回「体验版」；开发者工具/取不到时回落 fallback。
function displayVersion(fallback) {
  try {
    const mp = (wx.getAccountInfoSync() || {}).miniProgram || {};
    if (mp.version) return "v" + mp.version;
    if (mp.envVersion === "trial") return "体验版";
  } catch (e) {
    // 低版本基础库无该 API，回落
  }
  return fallback;
}

module.exports = { displayVersion };
