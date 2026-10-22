const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const identity = require("../../utils/bind_identity");

Page({
  data: { step: "input", code: "", submitting: false, macSuffix: "", waitSeconds: 0 },

  onLoad(options) {
    this._generation = 0;
    this._timers = new Set();
    this._unloaded = false;
    this._visible = true;
    this._begin();
    try {
      const scan = identity.fromLaunch(options, "bind");
      const saved = scan ? null : identity.restore("bind");
      this._scan = scan || (saved && saved.scan);
      if (this._scan) this.setData({ code: this._scan.code });
    } catch (e) {
      identity.clearPending();
      wx.showToast({ title: e.message, icon: "none" });
    }
    this.ensureAuth();
  },

  onShow() {
    const returning = this._visible === false;
    this._visible = true;
    if (this._sessionGeneration !== getApp()._sessionGeneration) {
      const completed = this.data.step === "bound";
      this._begin();
      this._authReady = false;
      this._boundMac = null;
      this._resumeCheck = !!this.data.code;
      if (completed) this._completedNeedsCheck = true;
      this.setData({ step: "input", submitting: false, macSuffix: "", waitSeconds: 0 });
    }
    if (this._scanning && this._scanning.session === getApp()._sessionGeneration) return;
    this._scanning = null;
    if (returning) {
      this.setData({ submitting: false });
      this._resumeCheck = this._resumeCheck || (!!this.data.code && this.data.step !== "bound");
      if (this._authReady && this._resumeCheck) { this.checkBinding(); return; }
    }
    if (!this._authReady) this.ensureAuth();
  },
  onHide() {
    // 原生扫码相机会暂时遮住页面；这一轮扫码本身不能被离页判据丢弃。
    if (this._scanning && this._scanning.session === getApp()._sessionGeneration) return;
    this._visible = false;
    this._begin();
    this.setData({ submitting: false });
  },
  onUnload() { this._unloaded = true; this._begin(); },

  _begin() {
    this._generation = (this._generation || 0) + 1;
    if (this._timers) this._timers.forEach((timer) => clearTimeout(timer));
    this._timers = new Set();
    this._sessionGeneration = getApp()._sessionGeneration;
    return this._generation;
  },
  _current(generation) {
    return !this._unloaded && this._visible !== false && generation === this._generation && this._sessionGeneration === getApp()._sessionGeneration;
  },
  _later(generation, action, delay) {
    const timer = setTimeout(() => {
      this._timers.delete(timer);
      if (this._current(generation)) action();
    }, delay);
    this._timers.add(timer);
  },

  ensureAuth() {
    if (this._authPending === this._generation || this._unloaded) return;
    const generation = this._generation;
    this._authPending = generation;
    identity.remember(this._scan, "bind", "scan", true);
    const login = getApp().ensureSession();
    this._sessionGeneration = getApp()._sessionGeneration;
    login.then((me) => {
      if (!this._current(generation)) return false;
      if (!me) { phoneGate.redirectToPhoneGate("请先登录"); return false; }
      return phoneGate.ensurePhoneOrRedirect(api, () => this._current(generation));
    }).then((ok) => {
      if (!ok || !this._current(generation)) return;
      this._authReady = true;
      this._sessionGeneration = getApp()._sessionGeneration;
      identity.remember(this._scan, "bind", "scan", false);
      if (this._completedNeedsCheck) {
        this._completedNeedsCheck = false;
        if (this._scan && this._scan.mac && this._scan.identity) this.checkBinding();
        else if (!this.data.code) this.pollStatus();
        else {
          this._resumeCheck = false;
          this.setData({ step: "input", submitting: false });
          wx.showToast({ title: "请核对设备绑定码后继续", icon: "none" });
        }
      }
      else if (this._resumeCheck && this.data.code) this.checkBinding();
      else if (this.data.code) this.doBind(false);
      else this.pollStatus();
    }).catch(() => {}).finally(() => { if (this._authPending === generation) this._authPending = false; });
  },

  onCodeInput(e) {
    this._begin();
    this._scan = null;
    this._resumeCheck = false;
    this._completedNeedsCheck = false;
    this._boundMac = null;
    identity.clearPending();
    this.setData({ code: String(e.detail.value || "").toUpperCase().replace(/[^0-9A-Z]/g, ""), submitting: false });
  },

  doBind(force, retried, generation) {
    if (!this._authReady) { this.ensureAuth(); return; }
    if (generation === undefined) {
      if (this.data.submitting || !/^[0-9A-Z]{6,8}$/.test(this.data.code)) return;
      generation = this._begin();
    }
    if (!this._current(generation)) return;
    this.setData({ submitting: true });
    const code = this.data.code;
    api.request("/api/user/bind/complete", {
      method: "POST", timeout: 12000,
      data: Object.assign({ bind_code: code, force: !!force }, identity.fieldsFor(this._scan)),
    }).then((res) => {
      if (!this._current(generation)) return;
      identity.clearPending();
      this._boundMac = res.device_mac || (this._scan && this._scan.mac);
      this.setData({ submitting: false, step: "bound", macSuffix: this._boundMac ? this._boundMac.slice(-5) : "" });
      wx.showToast({ title: "绑定成功", icon: "success" });
      this._later(generation, () => wx.switchTab({ url: "/pages/index/index", fail: () => {} }), 1500);
    }).catch((err) => {
      if (!this._current(generation)) return;
      if (err.code === "binding_conflict") {
        this.setData({ submitting: false });
        wx.showModal({
          title: "换绑到当前账号？", content: "这台设备已绑定其他账号，换绑后原账号将不能控制它。",
          confirmText: "换绑", cancelText: "取消",
          success: (res) => { if (res.confirm && this._current(generation)) this.doBind(true); },
        });
        return;
      }
      const attempt = retried || 0;
      if ((err.code === "binding_pending" || !err.status || err.status >= 500) && attempt < 30) {
        this.setData({ step: "waiting", waitSeconds: (attempt + 1) * 2 });
        this._later(generation, () => this.doBind(force, attempt + 1, generation), 2000);
        return;
      }
      this.setData({ submitting: false, step: "input", waitSeconds: 0 });
      if (err.status === 401 || err.code === "phone_required") {
        this._authReady = false;
        this.ensureAuth();
        return;
      }
      wx.showModal({ title: "暂未完成绑定", content: err.detail || "请确认设备已联网，再重新扫描屏幕二维码", showCancel: false });
    });
  },

  onSubmit() { this.doBind(false); },

  onScanBind() {
    const generation = this._begin();
    const operation = { generation, session: this._sessionGeneration };
    this._scanning = operation;
    this.setData({ submitting: false });
    wx.scanCode({
      success: (res) => {
        if (this._scanning !== operation) return;
        this._scanning = null;
        if (!this._current(generation)) return;
        try {
          const parsed = identity.parseScan(res.result, "bind");
          if (parsed.kind === "wifi") {
            this._scan = null;
            identity.clearPending();
            this.setData({ code: "", step: "input", waitSeconds: 0 });
            wx.showModal({
              title: "这是设备配网码", content: "请到一键配网页面连接 Wi-Fi，联网后会继续绑定。", confirmText: "去配网", cancelText: "取消",
              success: (answer) => {
                if (!answer.confirm || !this._current(generation)) return;
                identity.remember(parsed.scan, "wifi", "scan", !this._authReady);
                wx.redirectTo({ url: "/pages/wifi/wifi" });
              },
            });
            return;
          }
          this._scan = parsed.scan;
          this.setData({ code: this._scan.code, step: "input" });
          identity.remember(this._scan, "bind", "scan", !this._authReady);
          this.doBind(false);
        } catch (e) {
          this._scan = null;
          identity.clearPending();
          this.setData({ code: "", step: "input" });
          wx.showModal({ title: "请扫描设备绑定码", content: e.message, showCancel: false });
        }
      }, fail: () => { if (this._scanning === operation) this._scanning = null; },
    });
  },

  checkBinding() {
    this._resumeCheck = false;
    const generation = this._generation;
    // 回到页面先核原二维码的已完成记录，不重发后台结果不明的绑定请求。
    if (!this._scan || !this._scan.mac || !this._scan.identity) {
      this.setData({ step: "input", waitSeconds: 0 });
      wx.showToast({ title: "请核对设备绑定码后继续", icon: "none" });
      return;
    }
    const query = identity.query(Object.assign({ mac: this._scan.mac }, identity.fieldsFor(this._scan)));
    api.request("/api/user/bind/claim/status?" + query, { timeout: 12000 }).then((res) => {
      if (!this._current(generation)) return;
      if (res.status === "bound") {
        this._boundMac = this._scan.mac;
        identity.clearPending();
        this.setData({ step: "bound", macSuffix: this._boundMac.slice(-5), waitSeconds: 0 });
      } else {
        this.setData({ step: "input", waitSeconds: 0 });
        wx.showToast({ title: "绑定尚未确认，请核对设备后继续", icon: "none" });
      }
    }).catch((err) => {
      if (!this._current(generation)) return;
      this.setData({ step: "input", waitSeconds: 0 });
      wx.showToast({ title: err.detail || "暂时无法核对绑定，请稍后重试", icon: "none" });
    });
  },

  pollStatus() {
    // 有目标二维码时只能等该次绑定，不能把账号的另一台设备当作扫码成功。
    if (this.data.code || this._scan) return;
    const generation = this._generation;
    api.request("/api/user/devices").then((res) => {
      if (!this._current(generation) || this.data.code) return;
      const active = (res.devices || []).find((device) => device.active);
      if (active) {
        this._boundMac = active.device_mac;
        this.setData({ step: "bound", macSuffix: active.device_mac.slice(-5) });
      } else {
        this._boundMac = null;
        this.setData({ step: "input", macSuffix: "", submitting: false, waitSeconds: 0 });
      }
    }).catch(() => {});
  },

  onRefresh() { if (this.data.code) this.doBind(false); else this.pollStatus(); },
  onReset() {
    this._begin();
    this._scan = null;
    this._resumeCheck = false;
    this._completedNeedsCheck = false;
    this._boundMac = null;
    identity.clearPending();
    this.setData({ step: "input", code: "", submitting: false, waitSeconds: 0 });
  },

  onUnbind() {
    const generation = this._generation;
    wx.showModal({
      title: "解绑设备", content: "解绑后将无法通过小程序发送任务，确定？",
      success: (res) => {
        if (!res.confirm || !this._current(generation)) return;
        api.request("/api/user/bind/unbind", {
          method: "POST", data: this._boundMac ? { device_mac: this._boundMac } : {},
        }).then(() => {
          if (!this._current(generation)) return;
          this.onReset();
          wx.showToast({ title: "已解绑", icon: "success" });
        }).catch((err) => {
          if (this._current(generation)) wx.showModal({ title: "解绑失败", content: err.detail, showCancel: false });
        });
      },
    });
  },

  onBack() { wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/index/index" }) }); },
});
