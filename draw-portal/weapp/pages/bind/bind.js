const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");

Page({
  data: {
    step: "input", // input | waiting | bound
    code: "",
    submitting: false,
    macSuffix: "",
    waitSeconds: 0, // waiting 态已等待秒数（进度可见，不闷等）
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
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (ok) this.pollStatus();
    });
  },

  onCodeInput(e) {
    this.setData({
      code: (e.detail.value || "")
        .toUpperCase()
        .replace(/[^0-9A-Z]/g, ""),
    });
  },

  // 儿童友好核心：「无效或已过期」多半是设备 announce 还在路上（固件 3s 重试环），
  // 静默重试而非报错——孩子只需要「等一下」，永远不该看到技术性失败。
  doBind(force, retried) {
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
        // 孩子不该还要点「返回」——自动回首页
        setTimeout(() => {
          wx.switchTab({ url: "/pages/index/index", fail: () => {} });
        }, 1500);
      })
      .catch((err) => {
        if (err.status === 409) {
          this.setData({ submitting: false });
          // 设备绑在别的账号（如网页版邮箱账号）：在场持有屏显码即可强制换绑
          wx.showModal({
            title: "换绑到当前账号？",
            content: "这台写字机之前绑过别的账号，换绑后那边就不能控制它了",
            confirmText: "换绑",
            cancelText: "不换",
            success: (r) => {
              if (r.confirm) this.doBind(true, 0);
            },
          });
          return;
        }
        const d = err.detail || "";
        if (/无效|已过期|pending|等待|设备/.test(d) || err.status === 404 || err.status === 400) {
          // 设备 announce 在途：静默重试 30 次×2s（60s 覆盖常见在途窗口），
          // 等待秒数上屏让用户看见进展；超过仍没等到就给明确指引，不闷死。
          const n = (retried || 0) + 1;
          if (n <= 30) {
            this.setData({ step: "waiting", waitSeconds: n * 2 });
            setTimeout(() => {
              this.setData({ submitting: false });
              this.doBind(force, n);
            }, 2000);
            return;
          }
          this.setData({ submitting: false, step: "input", waitSeconds: 0 });
          wx.showModal({
            title: "没连上写字机",
            content: "请确认写字机已联网，再扫一次屏幕上的二维码",
            showCancel: false,
            confirmText: "知道了",
          });
          return;
        }
        this.setData({ submitting: false });
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
