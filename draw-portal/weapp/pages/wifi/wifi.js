// 一键配网：扫设备屏 WiFi 码（或手输热点名）→ 手机连设备热点 →
// TCP socket 直发 AP 配置接口（LAN IP 走不了 wx.request 白名单，socket 不受限）→
// 设备上家网。回退路径：浏览器连热点开 http://192.168.4.1
const api = require("../../utils/api");

Page({
  data: {
    step: "scan", // scan → manual → (连热点+扫列表) → aps → creds → working → done
    apSsid: "",
    homeSsid: "",
    homePassword: "",
    apList: [],
    wifiLocked: false, // 从设备扫描列表点选的 WiFi 不允许改名
    deviceMac: "", // 2026-09-08 起配网码带 &m=<MAC>：配网成功后自动认领绑定
    bindState: "", // "" | claiming | waiting | bound | manual（仅 done 页展示用）
  },

  // 微信「扫一扫」直接扫设备配网码（mp 后台「扫普通链接二维码打开小程序」规则：
  // https://hutuji.donglicao.com/draw-upload/wifi?s=<ssid> → 本页带 s 参数拉起）→ 免扫码直进
  onLoad(options) {
    const s = options && options.s ? String(options.s) : "";
    if (s) {
      let ssid = s;
      try { ssid = decodeURIComponent(s); } catch (e) {}
      this.setData({ apSsid: ssid });
    }
    const m = options && options.m ? String(options.m) : "";
    if (m) {
      let mac = m;
      try { mac = decodeURIComponent(m); } catch (e) {}
      this.setData({ deviceMac: mac.trim() });
    }
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
        const raw = String(res.result || "").trim();
        // 三种形态：① 设备屏 URL 码（5431a15 起：…/draw-upload/wifi?s=<ssid>，与微信扫一扫规则同源）
        // ② 标准 WIFI 码（旧固件：WIFI:T:nopass;S:<ssid>;;）③ 纯热点名/带空格变体
        let ssid = "";
        const mu = /^https:\/\/hutuji\.donglicao\.com\/draw-upload\/wifi\?(?:[^&\s]*&)*s=([^&\s]+)/i.exec(raw);
        if (mu) {
          try { ssid = decodeURIComponent(mu[1]); } catch (e) { ssid = mu[1]; }
          const mm = /^https:\/\/hutuji\.donglicao\.com\/draw-upload\/wifi\?(?:[^&\s]*&)*m=([^&\s]+)/i.exec(raw);
          if (mm) {
            let mac = mm[1];
            try { mac = decodeURIComponent(mm[1]); } catch (e) {}
            this.setData({ deviceMac: mac.trim() });
          }
        }
        if (!ssid) {
          const m = /WIFI:T:[^;]*;S:([^;]*);/i.exec(raw);
          ssid = m ? m[1] : (/^[\x20-\x7e]{1,32}$/.test(raw) && raw.indexOf("http") !== 0 ? raw : "");
        }
        if (!ssid) {
          wx.showModal({
            title: "没认出这个码",
            content: "扫到的是：" + (raw.slice(0, 40) || "（空）") + "。请扫写字机屏幕上「配网模式」的二维码，或改用手动输入热点名",
            showCancel: false,
          });
          return;
        }
        // 扫到设备热点名 → 直接开始（连热点→扫列表→点选）
        this.setData({ apSsid: ssid });
        this.onStart();
      },
      fail: () => {},
    });
  },

  onManualAp() {
    this.setData({ step: "manual" });
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

  // 儿童友好：连上设备热点后自动 /scan 拿设备搜到的 WiFi 列表，孩子点选+只输密码
  onStart() {
    if (!this.data.apSsid) {
      wx.showToast({ title: "先扫设备配网码或填热点名", icon: "none" });
      return;
    }
    this.setData({ step: "working", log: "" });
    this.appendLog("连接设备热点 " + this.data.apSsid + " …");
    // 官方要求先 startWifi（缺省报 errCode 12000 not init）；
    // password 是文档必填项，开放热点传空串，省略在 iOS 上直接 fail
    wx.startWifi({
      success: () => {
        wx.connectWifi({
          SSID: this.data.apSsid,
          password: "",
          success: () => {
            this.appendLog("已连上设备热点，读取附近 WiFi…");
            setTimeout(() => this.scanAps(), 1500);
          },
          fail: (err) => this.onConnectFail(err),
        });
      },
      fail: (err) => this.onConnectFail(err),
    });
  },

  onConnectFail(err) {
    this.appendLog("连热点失败：" + (err.errMsg || ""));
    this.setData({ step: "manual" });
    wx.showModal({
      title: "连不上设备热点",
      content:
        "可以改用浏览器配网：手机 WiFi 设置里手动连接「" +
        this.data.apSsid +
        "」，浏览器打开 192.168.4.1 填家里 WiFi",
      showCancel: false,
    });
  },

  // TCP GET /scan → {aps:[{ssid,rssi,authmode}]}；失败回落手输
  scanAps() {
    const http =
      "GET /scan HTTP/1.1\r\nHost: 192.168.4.1\r\nConnection: close\r\n\r\n";
    const tcp = wx.createTCPSocket();
    let got = "";
    let finished = false;
    const finish = (list) => {
      if (finished) return;
      finished = true;
      try {
        tcp.close();
      } catch (e) {}
      if (list && list.length) {
        this.appendLog("找到 " + list.length + " 个 WiFi，点选家里的");
        this.setData({ step: "aps", apList: list });
      } else {
        this.appendLog("读不到列表，改手输 WiFi 名");
        this.setData({ step: "creds" });
      }
    };
    const timer = setTimeout(() => finish(null), 10000);
    tcp.onConnect(() => {
      tcp.write(http);
    });
    tcp.onMessage((res) => {
      got += res.message ? String(res.message) : "";
      const i = got.indexOf("\r\n\r\n");
      if (i < 0) return;
      const body = got.slice(i + 4);
      if (body.indexOf("]}") < 0) return; // JSON 未齐
      clearTimeout(timer);
      try {
        const j = JSON.parse(body);
        const aps = (j.aps || [])
          .filter((a) => a && a.ssid)
          .sort((a, b) => (b.rssi || 0) - (a.rssi || 0));
        finish(aps);
      } catch (e) {
        finish(null);
      }
    });
    tcp.onClose(() => {
      clearTimeout(timer);
      if (!finished) {
        try {
          const i = got.indexOf("\r\n\r\n");
          const j = JSON.parse(got.slice(i + 4));
          finish((j.aps || []).filter((a) => a && a.ssid));
        } catch (e) {
          finish(null);
        }
      }
    });
    tcp.onError(() => {
      clearTimeout(timer);
      finish(null);
    });
    tcp.connect({ address: "192.168.4.1", port: 80, timeout: 8 });
  },

  onPickAp(e) {
    const ssid = e.currentTarget.dataset.ssid || "";
    this.setData({ homeSsid: ssid, wifiLocked: true, step: "creds" });
  },

  onManualSsid() {
    this.setData({ homeSsid: "", wifiLocked: false, step: "creds" });
  },

  onSubmitCreds() {
    if (!this.data.homeSsid) {
      wx.showToast({ title: "先选家里 WiFi", icon: "none" });
      return;
    }
    this.setData({ step: "working" });
    this.appendLog("下发家里 WiFi 凭据…");
    this.ensureOnDeviceAp(0, () => this.submitCreds(false));
  },

  // iOS 会把「无互联网」的热点几秒内切回有网 WiFi（2026-09-08 实机抓包：手机 join
  // 设备热点 9s 后 leave，用户输完密码点下发时已不在热点 → TCP 连不上 192.168.4.1）。
  // 下发前先确认还挂在设备热点上，不在就重新 connectWifi 拉回，最多重试 2 次。
  ensureOnDeviceAp(n, next) {
    wx.getConnectedWifi({
      success: (r) => {
        if (r.wifi && r.wifi.SSID === this.data.apSsid) {
          next();
          return;
        }
        this.rejoinDeviceAp(n, next);
      },
      fail: () => this.rejoinDeviceAp(n, next),
    });
  },

  rejoinDeviceAp(n, next) {
    if (n >= 2) {
      this.appendLog("手机离开了设备热点");
      this.setData({ step: "creds" });
      wx.showModal({
        title: "手机离开了设备热点",
        content:
          "iPhone 会把没网的热点自动切走：弹「此无线局域网似乎未接入互联网」时选「仍要使用」；或在系统 WiFi 设置里手动连上「" +
          this.data.apSsid +
          "」再回来点一键配网",
        showCancel: false,
      });
      return;
    }
    this.appendLog("重新连接设备热点…");
    wx.startWifi({
      success: () => {
        wx.connectWifi({
          SSID: this.data.apSsid,
          password: "",
          success: () => setTimeout(next, 1200),
          fail: () => this.rejoinDeviceAp(n + 1, next),
        });
      },
      fail: () => this.rejoinDeviceAp(n + 1, next),
    });
  },

  submitCreds(retried) {
    const payload = JSON.stringify({ ssid: this.data.homeSsid, password: this.data.homePassword });
    const http =
      "POST /submit HTTP/1.1\r\nHost: 192.168.4.1\r\nContent-Type: application/json\r\nContent-Length: " +
      payload.length +
      "\r\nConnection: close\r\n\r\n" +
      payload;
    const tcp = wx.createTCPSocket();
    let got = "";
    let finished = false;
    const finish = (ok, msg, reconnectable) => {
      if (finished) return;
      finished = true;
      try {
        tcp.close();
      } catch (e) {}
      if (ok) {
        this.appendLog("已下发，设备正在连家里 WiFi（约 10-30 秒）");
        this.setData({ step: "done" });
        this.autoBind();
      } else {
        if (!retried && reconnectable) {
          this.appendLog("手机不在设备热点上了，拉回重试…");
          this.ensureOnDeviceAp(1, () => this.submitCreds(true));
          return;
        }
        this.appendLog("下发失败：" + msg);
        this.setData({ step: "creds" });
        wx.showModal({ title: "配网失败", content: msg, showCancel: false });
      }
    };
    const timer = setTimeout(() => finish(false, "设备应答超时，请重试或用浏览器配网", true), 20000);
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
        finish(false, "设备拒绝：请核对 WiFi 名称和密码", false);
      }
    });
    tcp.onClose(() => {
      clearTimeout(timer);
      finish(got.indexOf('"success":true') >= 0, "连接已断开但未收到成功应答", true);
    });
    tcp.onError(() => {
      clearTimeout(timer);
      finish(false, "连不上设备配置口，确认手机还在设备热点上", true);
    });
    tcp.connect({ address: "192.168.4.1", port: 80, timeout: 8 });
  },

  // 一次扫码合一（2026-09-08 定案）：配网码带设备 MAC（在场证明），配网成功后
  // 直接认领；设备上线 announce 由 portal 撮合完成绑定。旧固件码/未登录 → 手动降级。
  autoBind() {
    const mac = this.data.deviceMac;
    if (!mac) {
      this.appendLog("这个码没带设备身份，可点下方按钮手动绑定");
      return;
    }
    if (!api.getSession()) {
      this.appendLog("登录后会自动绑定，也可稍后点下方按钮手动绑定");
      return;
    }
    this.setData({ bindState: "claiming" });
    this.appendLog("正在为你绑定这台设备…");
    this.claimMac(mac, false);
  },

  claimMac(mac, force) {
    api
      .request("/api/user/bind/claim", { method: "POST", data: { mac, force: !!force } })
      .then((res) => {
        if (res.status === "bound" || res.status === "already_bound") {
          this.bindDone();
          return;
        }
        // claimed：等设备重启上线 announce 自动撮合（重启+激活约 1-2 分钟）
        this.setData({ bindState: "waiting" });
        this.appendLog("配网完成，设备上线后会自动完成绑定…");
        this.pollBound(0);
      })
      .catch((err) => {
        if (err.status === 409) {
          // 与绑定页同口径：设备绑在别的账号时在场（扫到屏显码）即可确认换绑
          wx.showModal({
            title: "换绑到当前账号？",
            content: "这台写字机之前绑过别的账号，换绑后那边就不能控制它了",
            confirmText: "换绑",
            cancelText: "不换",
            success: (r) => {
              if (r.confirm) this.claimMac(mac, true);
            },
          });
          return;
        }
        // 认领没抵达服务器：不阻断配网成功，留手动降级
        this.appendLog("自动绑定没连上服务器，可点下方按钮手动绑定");
        this.setData({ bindState: "manual" });
      });
  },

  pollBound(n) {
    if (n >= 24 || this.data.bindState === "bound") return; // ~2 分钟等设备上线
    setTimeout(() => {
      api
        .request("/api/auth/me", { timeout: 15000 })
        .then((me) => {
          if (me.binding && me.binding.bound) {
            this.bindDone();
            return;
          }
          this.pollBound(n + 1);
        })
        .catch(() => this.pollBound(n + 1));
    }, 5000);
  },

  bindDone() {
    this.setData({ bindState: "bound" });
    this.appendLog("绑定成功！现在可以直接对写字机说话了");
    wx.showToast({ title: "绑定成功", icon: "success" });
  },

  onGoBind() {
    wx.redirectTo({ url: "/pages/bind/bind" });
  },

  onRestart() {
    this.setData({ step: "scan", log: "", apList: [], wifiLocked: false, homeSsid: "", homePassword: "", apSsid: "" });
  },
});
