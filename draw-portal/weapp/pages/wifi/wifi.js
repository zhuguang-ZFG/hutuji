// 一键配网：扫设备屏 WiFi 码（或手输热点名）→ 手机连设备热点 →
// TCP socket 直发 AP 配置接口（LAN IP 走不了 wx.request 白名单，socket 不受限）→
// 设备上家网。回退路径：浏览器连热点开 http://192.168.4.1
Page({
  data: {
    step: "scan", // scan → creds → working → done
    apSsid: "",
    homeSsid: "",
    homePassword: "",
    log: "",
  },

  onLoad() {
    wx.startWifi({ success: () => {}, fail: () => {} });
    // Android 可预填当前 WiFi 名；iOS 多半拿不到，手输即可
    wx.getConnectedWifi({
      success: (r) => {
        if (r.wifi && r.wifi.SSID) this.setData({ homeSsid: r.wifi.SSID });
      },
      fail: () => {},
    });
  },

  appendLog(t) {
    this.setData({ log: (this.data.log ? this.data.log + "\n" : "") + t });
  },

  onScanAp() {
    wx.scanCode({
      success: (res) => {
        const m = /WIFI:T:[^;]*;S:([^;]*);/.exec(res.result || "");
        if (!m) {
          wx.showModal({
            title: "不是配网二维码",
            content: "请扫写字机屏幕上「配网模式」的二维码，或改用手动输入热点名",
            showCancel: false,
          });
          return;
        }
        this.setData({ apSsid: m[1], step: "creds" });
      },
      fail: () => {},
    });
  },

  onManualAp() {
    this.setData({ step: "creds" });
  },

  onApInput(e) {
    this.setData({ apSsid: e.detail.value.trim() });
  },

  onSsidInput(e) {
    this.setData({ homeSsid: e.detail.value.trim() });
  },

  onPwdInput(e) {
    this.setData({ homePassword: e.detail.value });
  },

  onStart() {
    if (!this.data.apSsid || !this.data.homeSsid) {
      wx.showToast({ title: "热点名和家里 WiFi 名都要填", icon: "none" });
      return;
    }
    this.setData({ step: "working", log: "" });
    this.appendLog("连接设备热点 " + this.data.apSsid + " …");
    wx.connectWifi({
      SSID: this.data.apSsid,
      success: () => {
        this.appendLog("已连上设备热点，下发家里 WiFi 凭据…");
        setTimeout(() => this.submitCreds(), 1500);
      },
      fail: (err) => {
        this.appendLog("连热点失败：" + (err.errMsg || ""));
        this.setData({ step: "creds" });
        wx.showModal({
          title: "连不上设备热点",
          content:
            "可以改用浏览器配网：手机 WiFi 设置里手动连接「" +
            this.data.apSsid +
            "」，浏览器打开 192.168.4.1 填家里 WiFi",
          showCancel: false,
        });
      },
    });
  },

  submitCreds() {
    const payload = JSON.stringify({ ssid: this.data.homeSsid, password: this.data.homePassword });
    const http =
      "POST /submit HTTP/1.1\r\nHost: 192.168.4.1\r\nContent-Type: application/json\r\nContent-Length: " +
      payload.length +
      "\r\nConnection: close\r\n\r\n" +
      payload;
    const tcp = wx.createTCPSocket();
    let got = "";
    let finished = false;
    const finish = (ok, msg) => {
      if (finished) return;
      finished = true;
      try {
        tcp.close();
      } catch (e) {}
      if (ok) {
        this.appendLog("已下发，设备正在连家里 WiFi（约 10-30 秒）");
        this.setData({ step: "done" });
      } else {
        this.appendLog("下发失败：" + msg);
        this.setData({ step: "creds" });
        wx.showModal({ title: "配网失败", content: msg, showCancel: false });
      }
    };
    const timer = setTimeout(() => finish(false, "设备应答超时，请重试或用浏览器配网"), 20000);
    tcp.onConnect(() => {
      tcp.write(http);
    });
    tcp.onMessage((res) => {
      got += res.message ? String(res.message) : "";
      if (got.indexOf('"success":true') >= 0) {
        clearTimeout(timer);
        finish(true);
      } else if (got.indexOf('"success":false') >= 0) {
        clearTimeout(timer);
        finish(false, "设备拒绝：请核对 WiFi 名称和密码");
      }
    });
    tcp.onClose(() => {
      clearTimeout(timer);
      finish(got.indexOf('"success":true') >= 0, "连接已断开但未收到成功应答");
    });
    tcp.onError(() => {
      clearTimeout(timer);
      finish(false, "连不上设备配置口，确认手机还在设备热点上");
    });
    tcp.connect({ address: "192.168.4.1", port: 80, timeout: 8 });
  },

  onGoBind() {
    wx.redirectTo({ url: "/pages/bind/bind" });
  },

  onRestart() {
    this.setData({ step: "scan", log: "" });
  },
});
