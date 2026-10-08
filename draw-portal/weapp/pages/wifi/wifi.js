const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const identity = require("../../utils/bind_identity");
const transport = require("../../utils/wifi_transport");

Page({
  data: {
    step: "scan", apSsid: "", homeSsid: "", homePassword: "", apList: [],
    wifiLocked: false, deviceMac: "", bindState: "", log: "", authReady: false, showPassword: false, wifiError: "",
  },

  onLoad(options) {
    this._generation = 0;
    this._timers = new Set();
    this._unloaded = false;
    this._begin();
    try {
      const scan = identity.fromLaunch(options, "wifi");
      const saved = scan ? null : identity.restore("wifi");
      this._scan = scan || (saved && saved.scan);
      this._phase = saved ? saved.phase : "scan";
      if (this._scan) this.applyScan(this._scan);
    } catch (e) {
      identity.clearPending();
      wx.showToast({ title: e.message, icon: "none" });
    }
    this.ensureAuthAndInit();
  },

  onShow() {
    if (this._sessionGeneration !== getApp()._sessionGeneration) {
      this._begin();
      this.setData({ authReady: false, homePassword: "", showPassword: false, bindState: "" });
    }
    if (!this.data.authReady) this.ensureAuthAndInit();
  },

  onUnload() {
    this._unloaded = true;
    this._begin();
    this.data.homePassword = "";
    this.data.showPassword = false;
  },
  onHide() { this.setData({ showPassword: false }); },
  onTogglePassword() { if (this.data.step === "creds") this.setData({ showPassword: !this.data.showPassword }); },
  onGoCreate() { if (this.data.bindState === "bound") wx.switchTab({ url: "/pages/index/index" }); },
  onGoControl() { if (this.data.bindState === "bound") wx.switchTab({ url: "/pages/control/control" }); },

  _begin() {
    this._generation = (this._generation || 0) + 1;
    if (this._timers) this._timers.forEach((timer) => clearTimeout(timer));
    this._timers = new Set();
    if (this._cancelTcp) this._cancelTcp();
    this._cancelTcp = null;
    this._sessionGeneration = getApp()._sessionGeneration;
    return this._generation;
  },

  _current(generation) {
    return !this._unloaded && generation === this._generation &&
      this._sessionGeneration === getApp()._sessionGeneration;
  },

  _later(generation, action, delay) {
    const timer = setTimeout(() => {
      this._timers.delete(timer);
      if (this._current(generation)) action();
    }, delay);
    this._timers.add(timer);
  },

  ensureAuthAndInit() {
    if (this._authPending === this._generation || this._unloaded) return;
    const generation = this._generation;
    this._authPending = generation;
    if (this._scan) identity.remember(this._scan, "wifi", this._phase, true);
    // switchTab 会销毁本页；扫码信息先落短期恢复记录，授权后由「我的」自动返回。
    const login = getApp().ensureSession();
    // ensureSession 会同步开启新登录代次；记录它，后续退出/重登再换代即作废。
    this._sessionGeneration = getApp()._sessionGeneration;
    login.then((me) => {
      if (!this._current(generation)) return false;
      if (!me) { phoneGate.redirectToPhoneGate("请先登录"); return false; }
      return phoneGate.ensurePhoneOrRedirect(api, () => this._current(generation));
    }).then((ok) => {
      if (!this._current(generation) || !ok) return;
      this._sessionGeneration = getApp()._sessionGeneration;
      this.setData({ authReady: true });
      if (this._scan) identity.remember(this._scan, "wifi", this._phase, false);
      if (this._phase === "waiting") { this.autoBind(); return; }
      wx.startWifi({
        success: () => {
          if (!this._current(generation)) return;
          wx.getConnectedWifi({
            success: (res) => {
              if (this._current(generation) && !this.data.homeSsid && res.wifi &&
                  res.wifi.SSID !== this.data.apSsid) this.setData({ homeSsid: res.wifi.SSID });
            },
            fail: () => {},
          });
          if (this._scan) this.onStart();
        },
        fail: () => {
          if (this._current(generation)) this.onConnectFail();
        },
      });
    }).catch(() => {
      if (this._current(generation)) wx.showToast({ title: "请先登录，再继续配网", icon: "none" });
    }).finally(() => { if (this._authPending === generation) this._authPending = false; });
  },

  applyScan(scan) {
    this._scan = scan;
    this.setData({ apSsid: scan.ssid, deviceMac: scan.mac, bindState: "" });
  },

  appendLog(text) {
    this.setData({ log: (this.data.log ? this.data.log + "\n" : "") + text });
  },

  onScanAp() {
    const generation = this._begin();
    wx.scanCode({
      success: (res) => {
        if (!this._current(generation)) return;
        try {
          const scan = identity.parseQr(res.result, "wifi");
          this._phase = "scan";
          this.applyScan(scan);
          identity.remember(scan, "wifi", "scan", !this.data.authReady);
          if (this.data.authReady) this.onStart();
          else this.ensureAuthAndInit();
        } catch (e) {
          this._scan = null;
          identity.clearPending();
          this.setData({ apSsid: "", deviceMac: "", bindState: "" });
          wx.showModal({ title: "请扫设备配网码", content: e.message, showCancel: false });
        }
      },
      fail: () => {},
    });
  },

  onManualAp() {
    this._begin();
    this._scan = null;
    identity.clearPending();
    this.setData({ step: "manual", apSsid: "", deviceMac: "", bindState: "" });
  },

  onApInput(e) {
    this._scan = null;
    identity.clearPending();
    this.setData({ apSsid: e.detail.value, deviceMac: "" });
  },
  onSsidInput(e) { this.setData({ homeSsid: e.detail.value }); },
  onPwdInput(e) { this.setData({ homePassword: e.detail.value }); },

  onStart() {
    if (!this.data.authReady) { this.ensureAuthAndInit(); return; }
    if (!this.data.apSsid) { wx.showToast({ title: "请先扫描设备配网码", icon: "none" }); return; }
    const generation = this._begin();
    this.setData({ step: "working", log: "正在连接设备热点…", apList: [], wifiError: "" });
    this.connectDevice(generation, 0, () => this.scanAps(generation));
  },

  connectDevice(generation, attempt, next) {
    if (!this._current(generation)) return;
    wx.startWifi({
      success: () => {
        if (!this._current(generation)) return;
        wx.connectWifi({
          SSID: this.data.apSsid, password: "",
          success: () => this._later(generation, next, 1200),
          fail: () => {
            if (!this._current(generation)) return;
            if (attempt < 1) this._later(generation, () => this.connectDevice(generation, attempt + 1, next), 1500);
            else this.onConnectFail();
          },
        });
      },
      fail: () => { if (this._current(generation)) this.onConnectFail(); },
    });
  },

  onConnectFail() {
    if (!this.data.apSsid) {
      this.setData({ step: "scan", wifiError: "暂时无法使用手机 Wi-Fi，请开启 Wi-Fi 后扫描设备配网码" });
      return;
    }
    this.setData({ step: this.data.homeSsid ? "creds" : "manual" });
    this.appendLog("暂时连不上设备热点，请保持设备在配网页面后重试");
    const generation = this._generation;
    wx.showModal({
      title: "连接设备热点", content: "请在手机 Wi-Fi 设置中连接「" + this.data.apSsid + "」，再回到这里继续。",
      confirmText: "打开设置", cancelText: "稍后重试",
      success: (res) => { if (res.confirm && this._current(generation)) this.openWifiSettings(); },
    });
  },

  openWifiSettings() {
    if (typeof wx.openSystemWifiSetting === "function") wx.openSystemWifiSetting({ fail: () => {} });
    else wx.showToast({ title: "请打开手机的 Wi-Fi 设置", icon: "none" });
  },

  scanAps(generation) {
    if (!this._current(generation)) return;
    this.appendLog("正在读取附近 Wi-Fi…");
    this._cancelTcp = transport.request({
      method: "GET", path: "/scan", timeout: 12000,
      done: (res) => {
        if (!this._current(generation)) return;
        const aps = res.status === 200 && res.data && Array.isArray(res.data.aps) ? res.data.aps : [];
        const signal = (ap) => typeof ap.rssi === "number" && Number.isFinite(ap.rssi) ? ap.rssi : -Infinity;
        const seen = new Set();
        const list = aps.filter((ap) => ap && typeof ap.ssid === "string" && ap.ssid)
          .sort((a, b) => signal(b) - signal(a))
          .filter((ap) => { if (seen.has(ap.ssid)) return false; seen.add(ap.ssid); return true; });
        this.setData({ step: list.length ? "aps" : "creds", apList: list });
        if (!list.length) this.appendLog("暂时读不到列表，可以直接输入家里的 Wi-Fi 名称");
      },
    });
  },

  onPickAp(e) {
    const ssid = e.currentTarget.dataset.ssid || "";
    this.setData({ homeSsid: ssid, homePassword: ssid === this.data.homeSsid ? this.data.homePassword : "",
      wifiLocked: true, step: "creds", showPassword: false });
  },
  onManualSsid() { this.setData({ homeSsid: "", wifiLocked: false, step: "creds" }); },

  onSubmitCreds() {
    if (!this.data.homeSsid) { wx.showToast({ title: "先选家里 Wi-Fi", icon: "none" }); return; }
    const generation = this._begin();
    this.setData({ step: "working", bindState: "", showPassword: false });
    this.appendLog("正在把网络信息发送给设备…");
    this.ensureOnDeviceAp(generation, () => this.submitCreds(generation, 0));
  },

  ensureOnDeviceAp(generation, next) {
    wx.getConnectedWifi({
      success: (res) => {
        if (!this._current(generation)) return;
        if (res.wifi && res.wifi.SSID === this.data.apSsid) next();
        else this.connectDevice(generation, 0, next);
      },
      fail: () => { if (this._current(generation)) this.connectDevice(generation, 0, next); },
    });
  },

  submitCreds(generation, attempt) {
    if (!this._current(generation)) return;
    this._cancelTcp = transport.request({
      method: "POST", path: "/submit", timeout: 20000,
      body: { ssid: this.data.homeSsid, password: this.data.homePassword },
      done: (res) => {
        if (!this._current(generation)) return;
        if ((res.data && res.data.success === false) || res.status >= 400) {
          this.setData({ step: "creds" });
          this.appendLog("设备未接受网络信息，请核对 Wi-Fi 名称和密码后重试");
          return;
        }
        if (!res.sent && attempt < 1) {
          this.connectDevice(generation, 0, () => this.submitCreds(generation, attempt + 1));
          return;
        }
        // AP 关闭/连接中断只说明发送结果不确定，必须等本次身份绑定完成才显示成功。
        this._phase = "waiting";
        identity.remember(this._scan, "wifi", "waiting", false);
        this.setData({ step: "done", bindState: "waiting" });
        this.appendLog("正在等待设备联网，并让手机回到家里的 Wi-Fi…");
        this._later(generation, () => this.restoreHome(generation, 0), 2500);
      },
    });
  },

  restoreHome(generation, attempt) {
    if (!this._current(generation)) return;
    if (!this.data.homeSsid) { this.autoBind(generation); return; }
    wx.connectWifi({
      SSID: this.data.homeSsid, password: this.data.homePassword,
      success: () => {
        if (!this._current(generation)) return;
        this.appendLog("手机已连接家里的 Wi-Fi，正在确认设备状态…");
        this.autoBind(generation);
      },
      fail: () => {
        if (!this._current(generation)) return;
        if (attempt < 1) this._later(generation, () => this.restoreHome(generation, attempt + 1), 3000);
        else {
          this.appendLog("正在尝试通过当前网络确认绑定，请保持手机联网");
          this.autoBind(generation);
        }
      },
    });
  },

  onClaimOnly() { this.autoBind(); },
  onRetryBind() { this.autoBind(); },

  autoBind(generation) {
    if (generation === undefined) generation = this._begin();
    if (!this._current(generation)) return;
    this.setData({ step: "done", bindState: "claiming" });
    if (!this._scan || !this._scan.mac) {
      this.setData({ bindState: "manual" });
      this.appendLog("设备联网后，请打开下方的绑定页面继续");
      return;
    }
    this._phase = "waiting";
    identity.remember(this._scan, "wifi", "waiting", false);
    this._bindDeadline = Date.now() + 300000;
    this.claimMac(this._scan, false, 0, generation);
  },

  claimMac(scan, force, attempt, generation) {
    if (!this._current(generation)) return;
    api.request("/api/user/bind/claim", {
      method: "POST", data: identity.claimPayload(scan, force), timeout: 12000,
    }).then((res) => {
      if (!this._current(generation)) return;
      if (res.status === "bound" || res.status === "already_bound") { this.bindDone(); return; }
      this.setData({ bindState: "waiting" });
      if (res.expires_at) this._bindDeadline = Math.min(this._bindDeadline, res.expires_at * 1000);
      this.pollBound(scan, generation);
    }).catch((err) => {
      if (!this._current(generation)) return;
      if (err.code === "binding_conflict") {
        wx.showModal({
          title: "换绑到当前账号？", content: "这台设备已绑定其他账号，换绑后原账号将不能控制它。",
          confirmText: "换绑", cancelText: "取消",
          success: (res) => {
            if (!this._current(generation)) return;
            if (res.confirm) this.claimMac(scan, true, 0, generation);
            else this.setData({ bindState: "manual" });
          },
        });
        return;
      }
      if ((!err.status || err.status >= 500 || err.status === 429) && attempt < 5) {
        this._later(generation, () => this.claimMac(scan, force, attempt + 1, generation), Math.min(1500 * Math.pow(2, attempt), 10000));
        return;
      }
      this.bindError(err);
    });
  },

  pollBound(scan, generation) {
    if (!this._current(generation)) return;
    if (Date.now() >= this._bindDeadline) {
      this.bindError({ detail: "还未确认设备上线。请检查设备屏幕，密码有误时可返回修改。" });
      return;
    }
    this._later(generation, () => {
      const query = identity.query(Object.assign({ mac: scan.mac }, identity.fieldsFor(scan)));
      api.request("/api/user/bind/claim/status?" + query, { timeout: 12000 }).then((res) => {
        if (!this._current(generation)) return;
        if (res.status === "bound") this.bindDone();
        else if (res.status === "expired") this.bindError({ code: "binding_expired" });
        else this.pollBound(scan, generation);
      }).catch((err) => {
        if (!this._current(generation)) return;
        if (!err.status || err.status >= 500 || err.status === 429) this.pollBound(scan, generation);
        else this.bindError(err);
      });
    }, 5000);
  },

  bindError(err) {
    this.setData({ bindState: "manual" });
    if (err.status === 401 || err.code === "phone_required") {
      identity.remember(this._scan, "wifi", "waiting", true);
      this.setData({ authReady: false });
      this.ensureAuthAndInit();
      return;
    }
    this.appendLog(err.code === "binding_expired" ? "本次二维码已过期，请在设备上重新打开配网并扫码。" :
      err.detail || "暂时无法确认绑定。请让手机联网后点“重试绑定”，无需重输密码。");
  },

  bindDone() {
    this._begin();
    identity.clearPending();
    this.setData({ step: "done", bindState: "bound", homePassword: "", showPassword: false });
    this.appendLog("绑定成功，现在可以使用这台设备了");
    wx.showToast({ title: "绑定成功", icon: "success" });
  },

  onEditWifi() {
    this._begin();
    this._phase = "scan";
    identity.remember(this._scan, "wifi", "scan", false);
    this.setData({ step: "creds", bindState: "" });
  },

  onGoBind() { wx.redirectTo({ url: "/pages/bind/bind" }); },

  onRestart() {
    this._begin();
    this._scan = null;
    this._phase = "scan";
    identity.clearPending();
    this.setData({ step: "scan", log: "", apList: [], wifiLocked: false, homeSsid: "", homePassword: "", apSsid: "", deviceMac: "", bindState: "", showPassword: false });
  },
});
