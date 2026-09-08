const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");

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
  // HTTP / 网络类 detail 原样截断展示
  if (/^HTTP\s+\d+/i.test(key) || /网络/.test(key)) return key.slice(0, 40);
  return key.slice(0, 40);
}

Page({
  data: {
    current: "",
    latest: "",
    notes: "",
    updateAvailable: false,
    checking: false,
    upgrading: false,
    progress: 0,
    progressLabel: "升级中…",
    targetVersion: "",
    statusTip: "",
    statusTipClass: "",
  },

  onShow() {
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (!ok) return;
      this._ensureBound().then((bound) => {
        if (!bound) return;
        this.onCheck(true);
      });
    });
  },

  onHide() {
    this._stopPoll();
  },

  onUnload() {
    this._stopPoll();
  },

  _ensureBound() {
    return api
      .request("/api/auth/me")
      .then((me) => {
        const bound = !!(me && me.binding && me.binding.bound);
        if (!bound) {
          wx.showToast({ title: "请先绑定设备", icon: "none" });
          setTimeout(() => wx.navigateTo({ url: "/pages/bind/bind" }), 400);
        }
        return bound;
      })
      .catch(() => false);
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
    const quiet = silent === true;
    this.setData({ checking: true });
    if (!quiet) this._setTip("");
    api
      .request("/api/device/firmware/check")
      .then((data) => {
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
    wx.showModal({
      title: "确认升级固件？",
      content:
        "升级过程中请勿断电、勿出图。目标版本：" +
        (this.data.latest || "最新") +
        "。确认后开始下载。",
      confirmText: "立即升级",
      success: (r) => {
        if (r.confirm) this._startUpgrade();
      },
    });
  },

  _startUpgrade() {
    this._stopPoll();
    this.setData({
      upgrading: true,
      progress: 0,
      progressLabel: "正在下发升级…",
      targetVersion: this.data.latest || "",
    });
    this._setTip("升级中，请保持设备联网", "");
    api
      .request("/api/device/firmware/upgrade", { method: "POST" })
      .then((data) => {
        if (!data || data.status !== "started") {
          const token = (data && data.detail) || "check_failed";
          const msg = reasonText(token) || "升级未能开始";
          this.setData({ upgrading: false, progress: 0 });
          this._setTip(msg, "warn");
          wx.showToast({ title: msg.slice(0, 20), icon: "none" });
          return;
        }
        if (data.target_version) {
          this.setData({ targetVersion: data.target_version });
        }
        this.setData({ progressLabel: "下载 / 写入中…" });
        this._pollCount = 0;
        this._pollOnce();
      })
      .catch((err) => {
        const msg = reasonText((err && err.detail) || "") || (err && err.detail) || "升级失败";
        this.setData({ upgrading: false, progress: 0 });
        this._setTip(msg, "warn");
        wx.showToast({ title: String(msg).slice(0, 20), icon: "none" });
      });
  },

  _pollOnce() {
    this._pollCount = (this._pollCount || 0) + 1;
    if (this._pollCount > POLL_MAX) {
      this._stopPoll();
      this.setData({ upgrading: false });
      this._setTip("超时未确认结果，请查看设备屏幕", "warn");
      wx.showToast({ title: "请查看设备屏幕", icon: "none" });
      return;
    }
    api
      .request("/api/device/firmware/ota_status")
      .then((st) => {
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
          setTimeout(() => this.onCheck(true), 800);
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
      .catch(() => {
        // 短暂网络抖动：继续轮询，不立刻失败
        this._pollTimer = setTimeout(() => this._pollOnce(), POLL_MS);
      });
  },
});
