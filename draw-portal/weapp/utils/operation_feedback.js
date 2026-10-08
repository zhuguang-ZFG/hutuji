// 长操作单独留足后端预算；普通读取仍使用api默认20秒，不自动重发控制命令。
const CREATION_TIMEOUT_MS = 300000;
const SEND_TIMEOUT_MS = 150000; // 后端最多90秒确认，另留推送与弱网往返时间。

function showSendResult(result, current) {
  if (!current()) return;
  const sent = result && result.status === "sent";
  const detail = (result && result.detail) || (sent ? "已推送到丹青，可到控制页查看状态" : "设备未响应，请查看设备状态后再试");
  const started = sent && /已自动开始画/.test(detail) && !/未完成|失败/.test(detail);
  wx.showModal({
    title: sent ? (started ? "已开始画" : "已发送") : "发送未完成",
    content: detail,
    confirmText: "查看设备", cancelText: "留在这里",
    success: (answer) => { if (answer.confirm && current()) wx.switchTab({ url: "/pages/control/control" }); },
  });
}

module.exports = { CREATION_TIMEOUT_MS, SEND_TIMEOUT_MS, showSendResult };
