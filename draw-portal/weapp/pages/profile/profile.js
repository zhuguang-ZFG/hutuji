const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const bindIdentity = require("../../utils/bind_identity");
const pageSession = require("../../utils/page_session");
const { displayVersion } = require("../../utils/version.js");

Page({
  data: {
    footVersion: "v0.3.6",
    me: null,
    avatarSrc: "",
    nicknameDraft: "",
    savingNick: false,
    needPhone: false,
    loggedOut: false,
    loadError: "",
    loggingIn: false,
    selectingDevice: false,
    supportEmail: "17150303974@163.com",
  },

  onShow() {
    pageSession.show(this);
    this.setData({ footVersion: displayVersion("v0.3.6") });
    const loggedOut = !!wx.getStorageSync("logged_out") || (!api.getSession() && !getApp()._sessionPromise);
    this.setData({ loggedOut, me: null, avatarSrc: "", nicknameDraft: "", needPhone: false,
      loadError: "", savingNick: false, loggingIn: false, selectingDevice: false });
    if (!loggedOut) this.refresh();
  },

  onHide() { pageSession.hide(this); },
  onUnload() { this.onHide(); },

  // 退出登录：确认后清本地会话并置 logged_out 闸门（app.logout），
  // 本页塌成登录墙；其他 tab 的接口会 401，等用户回本页显式重登。
  onLogout() {
    const current = pageSession.capture(this);
    wx.showModal({
      title: "退出登录",
      content: "退出后本机保存的会话将被清除，作品与绑定关系不受影响，重新登录即可恢复。",
      confirmText: "退出",
      confirmColor: "#d9534f",
      success: (res) => {
        if (!res.confirm || !current()) return;
        getApp().logout();
        this.setData({ loggedOut: true, me: null, avatarSrc: "", needPhone: false,
          nicknameDraft: "", savingNick: false, loadError: "" });
      },
    });
  },

  onLoginAgain() {
    if (this.data.loggingIn) return;
    this.setData({ loggingIn: true });
    const pending = getApp().loginAgain();
    const current = pageSession.capture(this);
    pending
      .then((me) => {
        if (!current()) return;
        this.setData({ loggingIn: false });
        if (me) {
          this.setData({ loggedOut: false });
          this._applyMe(me);
        } else {
          wx.showToast({ title: "登录失败，请稍后再试", icon: "none" });
        }
      });
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
    if (!needPhone) bindIdentity.resumeAfterAuth();
  },

  refresh() {
    const visible = pageSession.capture(this);
    const requestId = this._meRequest = (this._meRequest || 0) + 1;
    const current = () => visible() && requestId === this._meRequest;
    return Promise.resolve(getApp()._sessionPromise)
      .then(() => current() ? api.request("/api/auth/me") : null)
      .then((me) => {
        if (!me || !current()) return;
        this.setData({ loggedOut: false, loadError: "" });
        this._applyMe(me);
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ loadError: err.detail || "资料读取失败，请重试",
          loggedOut: err.status === 401, me: null, avatarSrc: "" });
      });
  },

  onChooseAvatar(e) {
    const current = pageSession.capture(this);
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
        if (!current()) return;
        wx.showToast({ title: "头像已更新", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        if (!current()) return;
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
    const current = pageSession.capture(this);
    api
      .request("/api/auth/wx-profile", { method: "POST", data: { nickname } })
      .then(() => {
        if (!current()) return;
        this.setData({ savingNick: false });
        wx.showToast({ title: "已保存", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ savingNick: false });
        wx.showToast({ title: err.detail || "保存失败", icon: "none" });
      });
  },

  onGetPhone(e) {
    const current = pageSession.capture(this);
    if (!e.detail.code) {
      wx.showToast({ title: "未授权手机号", icon: "none" });
      return;
    }
    api
      .request("/api/auth/wx-phone", { method: "POST", data: { code: e.detail.code } })
      .then(() => {
        if (!current()) return;
        wx.showToast({ title: "手机号已绑定", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        if (!current()) return;
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

  onSelectDevice(e) {
    const mac = e.currentTarget.dataset.mac;
    if (!mac || this.data.selectingDevice) return;
    const current = pageSession.capture(this);
    this.setData({ selectingDevice: true });
    api.request("/api/user/devices/select", { method: "POST", data: { device_mac: mac } })
      .then(() => {
        if (!current()) return;
        wx.showToast({ title: "已切换设备", icon: "success" });
        this.refresh();
      })
      .catch((err) => {
        if (current()) wx.showToast({ title: err.detail || "切换失败", icon: "none" });
      })
      .finally(() => { if (current()) this.setData({ selectingDevice: false }); });
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
      wx.navigateTo({ url: "/pages/bind/bind" });
      return;
    }
    wx.navigateTo({ url: "/pages/firmware/firmware" });
  },

  onGoChat() {
    if (this.data.needPhone) {
      wx.showToast({ title: "请先授权手机号", icon: "none" });
      return;
    }
    // chat 页 0.3.0 起进 tabBar：navigateTo 不能去 tab 页，必须 switchTab
    wx.switchTab({ url: "/pages/chat/chat" });
  },

  onPrivacy() {
    const current = pageSession.capture(this);
    if (typeof wx.openPrivacyContract !== "function") {
      this._showSupportFallback("当前微信版本暂不支持打开隐私指引，请更新微信后重试。");
      return;
    }
    wx.openPrivacyContract({
      fail: () => {
        if (current()) this._showSupportFallback("隐私指引暂时无法打开，请稍后重试。");
      },
    });
  },

  onContactError() {
    this._showSupportFallback("微信客服暂时无法打开。");
  },

  _showSupportFallback(message) {
    const current = pageSession.capture(this);
    wx.showModal({
      title: "联系帮助",
      content: message + "也可发邮件至 " + this.data.supportEmail + " 联系我们。",
      confirmText: "复制邮箱",
      cancelText: "关闭",
      success: (res) => {
        if (res.confirm && current()) this.onCopySupportEmail();
      },
    });
  },

  onCopySupportEmail() {
    const current = pageSession.capture(this);
    wx.setClipboardData({
      data: this.data.supportEmail,
      fail: () => {
        if (current()) wx.showToast({ title: "复制失败，请长按邮箱复制", icon: "none" });
      },
    });
  },
});
