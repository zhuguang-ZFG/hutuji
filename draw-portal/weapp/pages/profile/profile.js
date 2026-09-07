const api = require("../../utils/api");

Page({
  data: {
    me: null,
    nicknameDraft: "",
    savingNick: false,
  },

  onShow() {
    this.refresh();
  },

  refresh() {
    api
      .request("/api/auth/me")
      .then((me) => {
        this.setData({ me, nicknameDraft: me.nickname || "" });
      })
      .catch(() => {});
  },

  // 头像昵称填写能力：chooseAvatar 按钮 + nickname 输入框
  onChooseAvatar(e) {
    const path = e.detail.avatarUrl;
    api
      .uploadFile("/api/auth/wx-avatar", path, "file")
      .then(() => {
        wx.showToast({ title: "头像已更新", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        wx.showToast({ title: err.detail || "头像上传失败", icon: "none" });
      });
  },

  onNickInput(e) {
    this.setData({ nicknameDraft: e.detail.value });
  },

  onSaveNick() {
    const nickname = (this.data.nicknameDraft || "").trim();
    if (!nickname || this.data.savingNick) return;
    this.setData({ savingNick: true });
    api
      .request("/api/auth/wx-profile", { method: "POST", data: { nickname } })
      .then(() => {
        this.setData({ savingNick: false });
        wx.showToast({ title: "已保存", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        this.setData({ savingNick: false });
        wx.showToast({ title: err.detail || "保存失败", icon: "none" });
      });
  },

  onGetPhone(e) {
    if (!e.detail.code) {
      wx.showToast({ title: "未授权手机号", icon: "none" });
      return;
    }
    api
      .request("/api/auth/wx-phone", { method: "POST", data: { code: e.detail.code } })
      .then(() => {
        wx.showToast({ title: "手机号已绑定", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        wx.showToast({ title: err.detail || "绑定失败", icon: "none" });
      });
  },

  onGoBind() {
    wx.navigateTo({ url: "/pages/bind/bind" });
  },

  onGoWifi() {
    wx.navigateTo({ url: "/pages/wifi/wifi" });
  },
});
