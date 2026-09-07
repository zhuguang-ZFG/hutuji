const api = require("../../utils/api");

Page({
  data: {
    step: "input", // input | waiting | bound
    code: "",
    submitting: false,
    macSuffix: "",
  },

  onShow() {
    this.pollStatus();
  },

  onCodeInput(e) {
    this.setData({
      code: (e.detail.value || "")
        .toUpperCase()
        .replace(/[^0-9A-Z]/g, ""),
    });
  },

  onSubmit() {
    if (this.data.submitting || this.data.code.length !== 6) return;
    this.setData({ submitting: true });
    api
      .request("/api/user/bind/complete", {
        method: "POST",
        data: { bind_code: this.data.code },
      })
      .then((res) => {
        this.setData({ submitting: false, step: "bound", macSuffix: res.mac_suffix || "" });
        wx.showToast({ title: "绑定成功", icon: "success" });
      })
      .catch((err) => {
        this.setData({ submitting: false });
        const d = err.detail || "";
        if (/pending|等待|设备/.test(d) || err.status === 404) {
          // 会话存在但设备尚未 announce → 进入等待态轮询
          this.setData({ step: "waiting" });
          return;
        }
        wx.showModal({ title: "绑定失败", content: d, showCancel: false });
      });
  },

  pollStatus() {
    api
      .request("/api/auth/me")
      .then((me) => {
        if (me.binding && me.binding.deviceMac) {
          this.setData({
            step: "bound",
            macSuffix: (me.binding.macSuffix || me.binding.deviceMac).slice(-5),
          });
        }
      })
      .catch(() => {});
  },

  onRefresh() {
    this.pollStatus();
  },

  onReset() {
    this.setData({ step: "input", code: "" });
  },

  onUnbind() {
    wx.showModal({
      title: "解绑设备",
      content: "解绑后将无法通过小程序发送任务，确定？",
      success: (res) => {
        if (!res.confirm) return;
        api
          .request("/api/user/bind/unbind", { method: "POST" })
          .then(() => {
            this.setData({ step: "input", code: "", macSuffix: "" });
            wx.showToast({ title: "已解绑", icon: "success" });
          })
          .catch((err) => {
            wx.showModal({ title: "解绑失败", content: err.detail, showCancel: false });
          });
      },
    });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/index/index" }) });
  },
});
