const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");

Page({
  data: {
    me: null,
    avatarSrc: "",
    nicknameDraft: "",
    savingNick: false,
    needPhone: false,
  },

  onShow() {
    this.refresh();
  },

  _applyMe(me) {
    const needPhone = !phoneGate.isPhoneBound(me);
    const app = getApp();
    if (app && app.globalData) {
      app.globalData.user = me;
      app.globalData.phoneBound = !needPhone;
    }
    // 后端给相对路径 /api/auth/avatar/{uid}；小程序 <image> 必须绝对 URL 才能出图
    const avatarSrc = me && me.avatar_url
      ? api.mediaUrl(me.avatar_url) + "?t=" + Date.now()
      : "";
    this.setData({ me, nicknameDraft: (me && me.nickname) || "", needPhone, avatarSrc });
  },

  refresh() {
    api
      .request("/api/auth/me")
      .then((me) => this._applyMe(me))
      .catch(() => {});
  },

  onChooseAvatar(e) {
    const path = e.detail && e.detail.avatarUrl;
    if (!path) {
      wx.showToast({ title: "未拿到头像", icon: "none" });
      return;
    }
    // 先本地预览，再上传；上传成功后仍 refresh 拿正式 URL
    this.setData({ avatarSrc: path });
    api
      .uploadFile("/api/auth/wx-avatar", path, "file")
      .then(() => {
        wx.showToast({ title: "头像已更新", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        wx.showToast({ title: err.detail || "头像上传失败", icon: "none" });
        this.refresh();
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
    if (this.data.needPhone) {
      wx.showToast({ title: "请先授权手机号", icon: "none" });
      return;
    }
    wx.navigateTo({ url: "/pages/bind/bind" });
  },

  onGoWifi() {
    if (this.data.needPhone) {
      wx.showToast({ title: "请先授权手机号", icon: "none" });
      return;
    }
    wx.navigateTo({ url: "/pages/wifi/wifi" });
  },

  onGoFirmware() {
    if (this.data.needPhone) {
      wx.showToast({ title: "请先授权手机号", icon: "none" });
      return;
    }
    const bound = !!(this.data.me && this.data.me.binding && this.data.me.binding.bound);
    if (!bound) {
      wx.showToast({ title: "请先绑定设备", icon: "none" });
      setTimeout(() => wx.navigateTo({ url: "/pages/bind/bind" }), 400);
      return;
    }
    wx.navigateTo({ url: "/pages/firmware/firmware" });
  },
});
