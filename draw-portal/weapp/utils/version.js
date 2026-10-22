// 页脚版本号单一来源（2026-09-11）：运行时向微信要真实上传版本，
// 避免每次发版手改四个页脚又漏改（0.3.4 批页脚仍 v0.3.3 事故的根治）。
// 正式版优先返回上传版本；体验版和开发者工具使用本次编译版本，便于定位截图。
const BUILD_VERSION = "0.3.31";

function displayVersion() {
  try {
    const mp = (wx.getAccountInfoSync() || {}).miniProgram || {};
    if (mp.version) return "v" + mp.version;
    if (mp.envVersion === "trial") return "v" + BUILD_VERSION + " · 体验版";
  } catch (e) {
    // 低版本基础库无该 API，回落
  }
  return "v" + BUILD_VERSION;
}

module.exports = { displayVersion };
