const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const pageSession = require("../../utils/page_session");
const { displayVersion } = require("../../utils/version.js");

// 设计稿 §2.4 / 失败表 F1–F8 的 reason token → 中文
const REASON_ZH = {
  busy: "出图结束后再试",
  board_mismatch: "固件与机型不符",
  check_failed: "检查更新失败，请稍后",
  download_failed: "下载失败，可重试",
  verify_failed: "固件校验失败",
  write_failed: "写入失败，请重试",
  up_to_date: "已是最新版本",
  rolled_back: "已回滚到旧版",
  ok: "升级成功",
};

const POLL_MS = 2000;
const POLL_MAX = 150; // ~5 分钟；超时提示看设备屏

function reasonText(token) {
  if (!token) return "";
  const key = String(token).trim();
  if (REASON_ZH[key]) return REASON_ZH[key];
  // 网关瞬态（502/504/读超时）：设备可能已在升级，不要当失败报
  if (/^HTTP\s+50[234]/i.test(key) || /^网络\s+timeout/i.test(key)) {
    return "连接不稳，设备可能仍在升级，请稍后";
  }
  if (/^HTTP\s+\d+/i.test(key) || /网络/.test(key)) return key.slice(0, 40);
  return key.slice(0, 40);
}

Page({
  data: {
    footVersion: "v0.3.6",
    current: "",
    latest: "",
    notes: "",
    updateAvailable: false,
    checking: false,
    loadError: "",
    upgrading: false,
    progress: 0,
    progressLabel: "升级中…",
    targetVersion: "",
    statusTip: "",
    statusTipClass: "",
  },

  onShow() {
    pageSession.show(this);
    if (this._firmwareSession !== getApp()._sessionGeneration) {
      this._firmwareSession = getApp()._sessionGeneration;
      this._upgradeMac = "";
      this.setData({ upgrading: false, updateAvailable: false, current: "", latest: "" });
    }
    const current = pageSession.capture(this);
    if (!this.data.upgrading) this._deviceMac = "";
    this.setData({ footVersion: displayVersion(), checking: true, updateAvailable: false, loadError: "" });
    phoneGate.ensurePhoneOrRedirect(api, current).then((me) => {
      if (!current()) return;
      if (!me) {
        this.setData({ checking: false, loadError: "账号信息读取失败，请重试" });
        return;
      }
      // 切换活动设备不等于取消原设备升级；服务端继续验证原设备的当前归属。
      if (this.data.upgrading && this._upgradeMac) {
        this.setData({ checking: false });
        this._pollOnce();
        return;
      }
      this._ensureBound(me).then((bound) => {
        if (!current()) return;
        this.setData({ checking: false });
        if (!bound) return;
        // onHide 停了轮询；升级中若只 silent check 会因 upgrading 早退卡死 UI
        if (this.data.upgrading) {
          this._pollOnce();
          return;
        }
        this.onCheck(true);
      });
    });
  },

  onHide() {
    pageSession.hide(this);
    this._pollFlight = null;
    this._stopPoll();
  },

  onUnload() {
    this.onHide();
  },

  // onShow 的手机号闸刚查过 /me，直接复用；其他入口（暂无）仍自取。
  _ensureBound(me) {
    const current = pageSession.capture(this);
    return (me ? Promise.resolve(me) : api.request("/api/auth/me"))
      .then((me) => {
        if (!current()) return false;
        const bound = !!(me && me.binding && me.binding.bound);
        if (!bound) {
          this._deviceMac = "";
          wx.showToast({ title: "请先绑定设备", icon: "none" });
          wx.navigateTo({ url: "/pages/bind/bind" });
          return false;
        }
        const mac = me.active_device_mac || "";
        if (!mac) {
          this.setData({ loadError: "设备信息未就绪，请重试" });
          return false;
        }
        if (this._deviceMac && this._deviceMac !== mac) {
          this.setData({ upgrading: false, updateAvailable: false });
        }
        this._deviceMac = mac;
        return bound;
      })
      .catch(() => {
        if (current()) this.setData({ loadError: "设备信息读取失败，请重试" });
        return false;
      });
  },

  _stopPoll() {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
    this._pollCount = 0;
  },

  _setTip(text, cls) {
    this.setData({ statusTip: text || "", statusTipClass: cls || "" });
  },

  _applyCheck(data) {
    const updateAvailable = !!(data && data.update_available);
    const reason = data && data.reason;
    let tip = "";
    let tipClass = "";
    if (data && reason === "check_failed") {
      tip = reasonText(reason) || "检查更新失败，请稍后";
      tipClass = "warn";
    } else if (data && reason === "board_mismatch") {
      tip = reasonText(reason);
      tipClass = "warn";
    } else if (data && !updateAvailable && reason === "up_to_date") {
      tip = reasonText("up_to_date");
      tipClass = "ok";
    } else if (updateAvailable) {
      tip = "发现新版本，确认后可升级";
      tipClass = "ok";
    }
    this.setData({
      current: (data && data.current) || "",
      latest: (data && data.latest) || "",
      notes: (data && data.notes) || "",
      updateAvailable,
      statusTip: tip,
      statusTipClass: tipClass,
    });
  },

  onCheck(silent) {
    if (this.data.checking || this.data.upgrading) return;
    const current = pageSession.capture(this);
    const mac = this._deviceMac;
    if (!mac) {
      this.setData({ updateAvailable: false });
      this._setTip("设备信息未就绪，请刷新后重试", "warn");
      return;
    }
    const quiet = silent === true;
    this.setData({ checking: true, updateAvailable: false });
    if (!quiet) this._setTip("");
    api
      // 设备链查询通常 <10s；30s 是弱网上限，别落到 150s 旧默认。
      .request("/api/device/firmware/check?device_mac=" + encodeURIComponent(mac || ""), { timeout: 30000 })
      .then((data) => {
        if (!current() || mac !== this._deviceMac) return;
        this.setData({ checking: false });
        this._applyCheck(data);
        if (!quiet && data && data.reason === "check_failed") {
          wx.showToast({
            title: reasonText("check_failed"),
            icon: "none",
          });
        }
      })
      .catch((err) => {
        if (!current() || mac !== this._deviceMac) return;
        this.setData({ checking: false });
        const msg = (err && err.detail) || reasonText("check_failed");
        this._setTip(msg, "warn");
        if (!quiet) wx.showToast({ title: msg.slice(0, 20), icon: "none" });
      });
  },

  onUpgrade() {
    if (!this.data.updateAvailable || this.data.upgrading || this.data.checking) {
      return;
    }
    const current = pageSession.capture(this);
    const mac = this._deviceMac;
    wx.showModal({
      title: "确认升级固件？",
      content:
        "升级过程中请勿断电、勿出图。目标版本：" +
        (this.data.latest || "最新") +
        "。确认后开始下载。",
      confirmText: "立即升级",
      success: (r) => {
        if (r.confirm && current() && mac === this._deviceMac) this._startUpgrade();
      },
    });
  },

  _startUpgrade() {
    if (this.data.upgrading || this.data.checking) return;
    const mac = this._deviceMac;
    if (!mac) {
      this._setTip("设备信息未就绪，请刷新后重试", "warn");
      return;
    }
    const current = pageSession.capture(this);
    this._upgradeMac = mac;
    this._stopPoll();
    this.setData({
      upgrading: true,
      progress: 0,
      progressLabel: "正在下发升级…",
      targetVersion: this.data.latest || "",
    });
    this._setTip("升级中，请保持设备联网", "");
    api
      // 下发应快速回 started/unknown；超时走 catch 的瞬态分支继续轮询对账，30s 足够。
      .request("/api/device/firmware/upgrade", { method: "POST", timeout: 30000, data: { device_mac: mac } })
      .then((data) => {
        if (!current()) return;
        if (!data || (data.status !== "started" && data.status !== "unknown")) {
          const token = (data && data.detail) || "check_failed";
          const msg = reasonText(token) || "升级未能开始";
          // 设备已是最新时后端会拒升，按钮状态同步复位，避免再次误点
          if (token === "up_to_date") this.setData({ updateAvailable: false });
          this.setData({ upgrading: false, progress: 0 });
          this._setTip(msg, "warn");
          wx.showToast({ title: msg.slice(0, 20), icon: "none" });
          return;
        }
        // unknown = 网关超时但命令可能已送达（实证：504 当轮设备已开下载）
        // → 不报错，继续轮询 ota_status 等真实终态
        if (data.status === "unknown") {
          this._setTip("下发结果确认中，继续等待设备…", "");
        }
        if (data.target_version) {
          this.setData({ targetVersion: data.target_version });
        }
        this.setData({ progressLabel: "下载 / 写入中…" });
        this._pollCount = 0;
        this._pollOnce();
      })
      .catch((err) => {
        if (!current()) return;
        const detail = (err && err.detail) || "";
        const msg = reasonText(detail) || detail || "升级失败";
        // 网关瞬态（502/504/读超时）设备可能已在升级：不清 UI，继续轮询对账
        const transient =
          (err && (err.status === 0 || [502, 503, 504].includes(err.status))) ||
          /^HTTP\s+50[234]/i.test(String(detail)) || /^网络\s+timeout/i.test(String(detail));
        if (transient) {
          this._setTip("下发结果确认中，继续等待设备…", "");
          this._pollCount = 0;
          this._pollOnce();
          return;
        }
        this.setData({ upgrading: false, progress: 0 });
        this._setTip(msg, "warn");
        wx.showToast({ title: String(msg).slice(0, 20), icon: "none" });
      });
  },

  _pollOnce() {
    if (this._visible === false || this._pollFlight) return;
    const current = pageSession.capture(this);
    this._pollCount = (this._pollCount || 0) + 1;
    if (this._pollCount > POLL_MAX) {
      this._stopPoll();
      this.setData({ upgrading: false, progress: 0 });
      this._setTip("超时未确认结果，请查看设备屏幕", "warn");
      wx.showToast({ title: "请查看设备屏幕", icon: "none" });
      return;
    }
    const flight = this._pollFlight = {};
    api
      // 2s 间隔轮询：单请求 10s 封顶，挂起请求不能把「超时未确认」拖成数小时。
      .request("/api/device/firmware/ota_status?device_mac=" + encodeURIComponent(this._upgradeMac || this._deviceMac || ""), { timeout: 10000 })
      .then((st) => {
        if (!current()) return;
        const state = String((st && st.state) || "").toLowerCase();
        const progress =
          typeof (st && st.progress) === "number" ? Math.max(0, Math.min(100, st.progress)) : this.data.progress;
        const reason = (st && st.reason) || "";
        if (st && st.target_version) {
          this.setData({ targetVersion: st.target_version });
        }
        if (st && st.firmware_version) {
          this.setData({ current: st.firmware_version });
        }

        if (state === "ok") {
          this._stopPoll();
          this.setData({
            upgrading: false,
            progress: 100,
            progressLabel: "升级完成",
            updateAvailable: false,
          });
          this._setTip(reasonText("ok"), "ok");
          wx.showToast({ title: "升级成功", icon: "success" });
          // 再 check 一次对齐 latest
          this._pollTimer = setTimeout(() => { if (current()) this.onCheck(true); }, 800);
          return;
        }
        if (state === "failed" || state === "rolled_back") {
          this._stopPoll();
          const msg =
            reasonText(reason) ||
            (state === "rolled_back" ? reasonText("rolled_back") : "升级失败");
          this.setData({
            upgrading: false,
            progress,
            progressLabel: state === "rolled_back" ? "已回滚" : "升级失败",
          });
          this._setTip(msg, "warn");
          wx.showToast({ title: msg.slice(0, 20), icon: "none" });
          return;
        }

        // upgrading / idle / 其它进行中
        let label = "升级中…";
        if (state === "upgrading") label = "下载 / 写入中…";
        this.setData({
          upgrading: true,
          progress,
          progressLabel: label,
        });
        this._pollTimer = setTimeout(() => this._pollOnce(), POLL_MS);
      })
      .catch((err) => {
        if (!current()) return;
        if (err && [400, 401, 403, 409].includes(err.status)) {
          this._stopPoll();
          this.setData({ upgrading: false, updateAvailable: false });
          this._setTip(err.detail || "设备绑定已变化，请刷新后重试", "warn");
          return;
        }
        // 短暂网络抖动：继续轮询，不立刻失败
        this._pollTimer = setTimeout(() => this._pollOnce(), POLL_MS);
      })
      .finally(() => { if (this._pollFlight === flight) this._pollFlight = null; });
  },
});
