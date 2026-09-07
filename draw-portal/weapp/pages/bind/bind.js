const api = require("../../utils/api");

Page({
  data: {
    step: "input", // input | waiting | bound
    code: "",
    submitting: false,
    macSuffix: "",
  },


  // 微信「扫一扫」直接扫设备绑定码（mp 后台「扫普通链接二维码打开小程序」规则：
  // https://hutuji.donglicao.com/draw-upload/bind?c=XXXXXX → 本页带 c 参数拉起）→ 免输码自动绑
  onLoad(options) {
    const c = (options && options.c ? String(options.c) : "").toUpperCase().replace(/[^0-9A-Z]/g, "");
    if (c.length === 6) {
      this.setData({ code: c });
      this.doBind(false);
    }
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

  doBind(force) {
    if (this.data.submitting || this.data.code.length !== 6) return;
    this.setData({ submitting: true });
    api
      .request("/api/user/bind/complete", {
        method: "POST",
        data: { bind_code: this.data.code, force: !!force },
      })
      .then((res) => {
        this.setData({ submitting: false, step: "bound", macSuffix: res.mac_suffix || "" });
        wx.showToast({ title: "绑定成功", icon: "success" });
      })
      .catch((err) => {
        this.setData({ submitting: false });
        if (err.status === 409) {
          // 设备绑在别的账号（如网页版邮箱账号）：在场持有屏显码即可强制换绑
          wx.showModal({
            title: "设备已被绑定",
            content: "这台写字机绑在其他账号下，换绑到当前账号？",
            confirmText: "换绑",
            success: (r) => {
              if (r.confirm) this.doBind(true);
            },
          });
          return;
        }
        const d = err.detail || "";
        if (/pending|等待|设备/.test(d) || err.status === 404) {
          // 会话存在但设备尚未 announce → 进入等待态轮询
          this.setData({ step: "waiting" });
          return;
        }
        wx.showModal({ title: "绑定失败", content: d || "请重试", showCancel: false });
      });
  },

  onSubmit() {
    this.doBind(false);
  },

  // 一键扫码绑定：设备屏「绑定」二维码内容是 https://…/draw-upload/bind?c=XXXXXX
  onScanBind() {
    wx.scanCode({
      success: (res) => {
        const m = /[?&]c=([0-9A-Za-z]{6})/.exec(res.result || "");
        if (!m) {
          wx.showModal({
            title: "不是绑定二维码",
            content: "请扫写字机屏幕上「绑定」状态的二维码",
            showCancel: false,
          });
          return;
        }
        this.setData({ code: m[1].toUpperCase() });
        this.doBind(false);
      },
      fail: () => {},
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
