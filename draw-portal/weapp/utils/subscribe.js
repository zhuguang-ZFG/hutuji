// 一次性订阅消息：生成完成/失败时把离开小程序的用户拉回来。
// 模板 ID 由 portal /api/wx/subscribe-config 下发（未启用 = null → 全链静默跳过）。
// requestSubscribeMessage 必须在「用户点按回调」内同步调用，故 prepare() 在页面
// onLoad 预取并缓存，requestAtTap() 在各提交 tap 处理器顶部同步调用。
const api = require("./api");

let cachedTemplateId = null;

function prepare() {
  api
    .request("/api/wx/subscribe-config")
    .then((d) => {
      cachedTemplateId = (d && d.template_id) || null;
      if (cachedTemplateId) {
        try {
          wx.setStorageSync("subscribe_tid", cachedTemplateId);
        } catch (e) {}
      }
    })
    .catch(() => {});
}

function requestAtTap() {
  let tid = cachedTemplateId;
  if (!tid) {
    try {
      tid = wx.getStorageSync("subscribe_tid") || null;
    } catch (e) {}
  }
  if (!tid || !wx.requestSubscribeMessage) return;
  wx.requestSubscribeMessage({ tmplIds: [tid], complete: () => {} });
}

module.exports = { prepare, requestAtTap };
