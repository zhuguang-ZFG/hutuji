const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const mcpResult = require("../../utils/mcp_result");
const { displayVersion } = require("../../utils/version.js");
const jobProgress = require("../../utils/job_progress");

function readSpeedRates(result) {
  const rates = result && result.rates;
  if (!rates || !["x", "y", "z"].every((axis) =>
    typeof rates[axis] === "number" && Number.isFinite(rates[axis]) && rates[axis] > 0)) return null;
  return { x: rates.x, y: rates.y, z: rates.z };
}

// 设备控制：WeUI 官方模板布局 + 奎享同构能力
// 状态轮询 + 点动/步距/抬落笔/试笔 + 任务 暂停/继续/停止/重画 + 预览确认
Page({
  data: {
    footVersion: "v0.3.6",
    bound: false,
    status: null,
    step: 10,
    busyAction: "",
    canConfirm: false,
    canAbort: false,
    canPause: false,
    canResume: false,
    stateLabel: "",
    linkOk: false,
    linkLabel: "查询中…",
    grblLabel: "",
    posLabel: "",
    pageLabel: "",
    lastLine: "",
    statusLine: "",
    manualLocked: false,
    timelineIndex: 0,
    timelineLabels: jobProgress.TIMELINE_LABELS.slice(),
    timelineHint: "",
    pageShow: false,
    pagePercent: 0,
    elapsedLabel: "",
    etaLabel: "",
    otaLine: "",
    speedAxes: [
      { key: "x", label: "X 轴", min: 3000, max: 16000, step: 500 },
      { key: "y", label: "Y 轴", min: 3000, max: 16000, step: 500 },
      { key: "z", label: "Z 轴 · 抬落笔", min: 100, max: 3000, step: 100 },
    ],
    speedRates: { x: 10000, y: 10000, z: 1200 },
    speedLoaded: false,
    speedLoading: false,
    speedSaving: false,
    canSetSpeed: false,
    bindingMac: "",
  },

  onShow() {
    this._visible = true;
    const generation = this._viewGeneration = (this._viewGeneration || 0) + 1;
    const session = getApp()._sessionGeneration;
    this.setData({ footVersion: displayVersion("v0.3.6"), speedSaving: false,
      speedLoaded: false, speedLoading: false, canSetSpeed: false });
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (!ok || !this._isCurrentView(generation, session)) return;
      this._reschedulePoll(jobProgress.POLL_MS_IDLE);
      this.refresh();
    });
  },

  onHide() {
    this._visible = false;
    this._viewGeneration = (this._viewGeneration || 0) + 1;
    this._clearPoll();
  },

  onUnload() {
    this.onHide();
  },

  _isCurrentView(generation, session) {
    return this._visible !== false &&
      (this._viewGeneration || 0) === generation &&
      getApp()._sessionGeneration === session;
  },

  _clearPoll() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    this._pollMs = 0;
  },

  _reschedulePoll(pollMs) {
    if (this._visible === false) return;
    const ms = pollMs || jobProgress.POLL_MS_IDLE;
    if (this._timer && this._pollMs === ms) return;
    this._clearPoll();
    this._pollMs = ms;
    this._timer = setInterval(() => this.refresh(true), ms);
  },

  _deriveControls(status) {
    const state = (status && (status.state || status.job_state)) || "";
    const canConfirm =
      state === "previewing" ||
      state === "awaiting_confirmation" ||
      !!(status && status.awaiting_confirmation);
    const canAbort = !!(
      state &&
      state !== "idle" &&
      state !== "ready" &&
      state !== ""
    );
    const canPause = state === "streaming";
    const canResume = state === "paused";
    const linkOk = !!(status && status.connected);
    const linkLabel = status
      ? linkOk
        ? "已连接"
        : "未连接"
      : "查询中…";
    let posLabel = "";
    if (status && typeof status.mpos_x === "number") {
      const x = Number(status.mpos_x).toFixed(1);
      const y = Number(status.mpos_y).toFixed(1);
      const z = Number(status.mpos_z).toFixed(1);
      posLabel = `X${x} Y${y} Z${z}`;
    }
    let pageLabel = "";
    if (status && status.pages > 0) {
      pageLabel = `${status.page || 1} / ${status.pages}`;
    }
    // 人话状态行：机台 + 坐标 + 页码；禁止把 Grbl 原始 `<Idle|MPos:…>` 刷到屏上
    const bits = [];
    if (status && status.grbl_state) bits.push(String(status.grbl_state));
    if (posLabel) bits.push(posLabel);
    if (pageLabel) bits.push(pageLabel);
    const statusLine = bits.join("  ·  ");
    const rawLast = (status && status.last_line) || "";
    const lastLine =
      rawLast && !/^</.test(rawLast) && !/MPos:|Bf:|FS:/.test(rawLast)
        ? rawLast
        : "";
    const prog = jobProgress.deriveProgressView(status);
    return {
      canSetSpeed: !!(linkOk && status && status.ready && status.authorized && status.grbl_settings_ok &&
        status.plotter_sku === "nopaper" && status.speed && status.speed.axis_control === true &&
        status.speed.state !== "pending" &&
        ["idle", "done", "error", "aborted"].includes(state)),
      canConfirm,
      canAbort,
      canPause,
      canResume,
      stateLabel: state || "",
      linkOk,
      linkLabel,
      grblLabel: (status && status.grbl_state) || "",
      posLabel,
      pageLabel: prog.pageLabel || pageLabel,
      statusLine,
      lastLine,
      manualLocked: !!(canConfirm || canPause || this.data.busyAction),
      timelineIndex: prog.timelineIndex,
      timelineLabels: prog.timelineLabels,
      timelineHint: prog.timelineHint,
      pageShow: prog.pageShow,
      pagePercent: prog.pagePercent,
      elapsedLabel: prog.elapsedLabel,
      etaLabel: prog.etaLabel,
      otaLine: prog.otaLine,
    };
  },

  refresh(silent) {
    const generation = this._viewGeneration || 0;
    const session = getApp()._sessionGeneration;
    const refreshGeneration = this._refreshGeneration = (this._refreshGeneration || 0) + 1;
    const current = () => this._isCurrentView(generation, session) &&
      this._refreshGeneration === refreshGeneration;
    api
      .request("/api/auth/me")
      .then((me) => {
        if (!current()) return;
        const bound = !!(me.binding && me.binding.bound);
        const bindingMac = (me.binding && (me.binding.device_mac || me.binding.mac)) || "";
        if (bindingMac !== this.data.bindingMac) {
          this._speedGeneration = (this._speedGeneration || 0) + 1;
          this.setData({ speedRates: { x: 10000, y: 10000, z: 1200 },
            speedSaving: false, speedLoaded: false, speedLoading: false, canSetSpeed: false });
        }
        this.setData({ bound, bindingMac });
        if (!bound) {
          this._clearPoll();
          return;
        }
        return this.control("hutuji.status", null, true).then((r) => {
          if (!current() || this.data.bindingMac !== bindingMac) return;
          if (r && r.result) {
            const status = mcpResult.parseToolJson(r.result);
            if (status) {
              this.setData(Object.assign({ status }, this._deriveControls(status)));
              this._reschedulePoll(jobProgress.deriveProgressView(status).pollMs);
              this._loadMachineSpeed();
            } else {
              const emptyProg = jobProgress.deriveProgressView(null);
              this.setData({
                status: null,
                canConfirm: false,
                canAbort: false,
                canPause: false,
                canResume: false,
                canSetSpeed: false,
                stateLabel: "",
                linkOk: false,
                linkLabel: "查询中…",
                grblLabel: "",
                posLabel: "",
                pageLabel: "",
                lastLine: "",
                statusLine: "",
                manualLocked: !!this.data.busyAction,
                timelineIndex: emptyProg.timelineIndex,
                timelineLabels: emptyProg.timelineLabels,
                timelineHint: emptyProg.timelineHint,
                pageShow: emptyProg.pageShow,
                pagePercent: emptyProg.pagePercent,
                elapsedLabel: emptyProg.elapsedLabel,
                etaLabel: emptyProg.etaLabel,
                otaLine: emptyProg.otaLine,
              });
              this._reschedulePoll(jobProgress.POLL_MS_IDLE);
            }
          } else {
            this._reschedulePoll(jobProgress.POLL_MS_IDLE);
          }
        });
      })
      .catch(() => {
        if (!this._isCurrentView(generation, session)) return;
        this._reschedulePoll(jobProgress.POLL_MS_IDLE);
      });
  },

  control(tool, action, quiet) {
    return api
      .request("/api/device/control", {
        method: "POST",
        data: { tool, action },
      })
      .then((r) => {
        if (r.status === "error" && !quiet) {
          wx.showToast({ title: r.detail || "设备未响应", icon: "none" });
        }
        return r;
      })
      .catch((err) => {
        if (!quiet) wx.showToast({ title: err.detail || "网络不可用", icon: "none" });
        return null;
      });
  },

  fire(tool, action) {
    if (this.data.busyAction) return;
    // 笔/任务 cells 在不可用态仍可能被点：前端再闸一道
    if (tool === "hutuji.pause" && !this.data.canPause) {
      wx.showToast({ title: "当前不可暂停", icon: "none" });
      return;
    }
    if (tool === "hutuji.resume" && !this.data.canResume) {
      wx.showToast({ title: "当前不可继续", icon: "none" });
      return;
    }
    if (
      (tool === "hutuji.manual" || tool === "hutuji.pen_test" || tool === "hutuji.repeat") &&
      (this.data.canConfirm || this.data.canPause)
    ) {
      wx.showToast({ title: "请先确认预览或暂停任务", icon: "none" });
      return;
    }
    const key = action || tool;
    this.setData({ busyAction: key, manualLocked: true });
    this.control(tool, action).then((r) => {
      this.setData({ busyAction: "" });
      if (r && r.status !== "error") {
        setTimeout(() => this.refresh(true), 600);
      } else {
        this.setData({
          manualLocked: !!(this.data.canConfirm || this.data.canPause),
        });
      }
    });
  },

  onJog(e) {
    this.fire("hutuji.manual", e.currentTarget.dataset.dir);
  },

  onStepToggle() {
    const next = this.data.step === 10 ? 1 : 10;
    this.fire("hutuji.manual", next === 1 ? "jog_step_1" : "jog_step_10");
    this.setData({ step: next });
  },

  onPen(e) {
    this.fire("hutuji.manual", e.currentTarget.dataset.action);
  },

  onPenTest() {
    this.fire("hutuji.pen_test");
  },

  onHome() {
    this.fire("hutuji.manual", "home");
  },

  onConfirm() {
    this.fire("hutuji.confirm");
  },

  onJobAction(e) {
    this.fire(e.currentTarget.dataset.tool);
  },

  onStop() {
    wx.showModal({
      title: "停止当前任务",
      content: "彻底放弃当前绘图（不可续画）；换纸中会等换纸完成后才停",
      confirmText: "停止",
      confirmColor: "#FA5151",
      success: (r) => {
        if (r.confirm) this.fire("hutuji.abort");
      },
    });
  },

  onSpeedChange(e) {
    const axis = e.currentTarget && e.currentTarget.dataset.axis;
    const rate = e.detail.value;
    const limits = this.data.speedAxes.find((item) => item.key === axis);
    if (!limits || !Number.isInteger(rate) || rate < limits.min || rate > limits.max ||
      this.data.speedSaving || this.data.speedLoading || !this.data.speedLoaded ||
      !this.data.canSetSpeed || this.data.busyAction) {
      this.setData({ speedRates: Object.assign({}, this.data.speedRates) });
      return;
    }
    // 只提交拖动轴；回包属于同一页面、登录与设备才更新三轴读回值。
    const previous = Object.assign({}, this.data.speedRates);
    const generation = this._viewGeneration || 0;
    const session = getApp()._sessionGeneration;
    const bindingMac = this.data.bindingMac;
    const speedGeneration = this._speedGeneration = (this._speedGeneration || 0) + 1;
    const current = () => this._isCurrentView(generation, session) &&
      this._speedGeneration === speedGeneration && this.data.bindingMac === bindingMac;
    const fail = (detail) => {
      if (!current()) return;
      this.setData({ speedSaving: false, speedRates: previous, speedLoaded: false });
      wx.showToast({ title: detail || "调整失败，请重试", icon: "none" });
    };
    this.setData({ speedSaving: true });
    api
      .request("/api/settings/machine-speed", {
        method: "POST",
        data: { axis, rate, device_mac: bindingMac || undefined },
      })
      .then((r) => {
        if (!current()) return;
        const rates = readSpeedRates(r);
        if (r && r.status === "ok" && r.axis === axis && Number.isInteger(r.rate) &&
          r.rate === rate && rates && rates[axis] === rate) {
          this.setData({ speedSaving: false, speedRates: rates, speedLoaded: true });
          wx.showToast({ title: `${axis.toUpperCase()} 轴已调整`, icon: "success" });
        } else {
          fail(r && r.detail);
        }
      })
      .catch((error) => fail(error && error.detail));
  },

  _loadMachineSpeed() {
    if (!this.data.canSetSpeed || this.data.speedLoaded || this.data.speedLoading ||
      this.data.speedSaving || this.data.busyAction) return;
    const generation = this._viewGeneration || 0;
    const session = getApp()._sessionGeneration;
    const bindingMac = this.data.bindingMac;
    const speedGeneration = this._speedGeneration || 0;
    const current = () => this._isCurrentView(generation, session) &&
      (this._speedGeneration || 0) === speedGeneration && this.data.bindingMac === bindingMac;
    this.setData({ speedLoading: true });
    api.request(`/api/settings/machine-speed?device_mac=${encodeURIComponent(bindingMac)}`)
      .then((r) => {
        if (!current()) return;
        const rates = readSpeedRates(r);
        this.setData(Object.assign({ speedLoading: false, speedLoaded: !!(r && r.status === "ok" && rates) },
          r && r.status === "ok" && rates ? { speedRates: rates } : {}));
      })
      .catch(() => {
        if (current()) this.setData({ speedLoading: false, speedLoaded: false });
      });
  },

  onGoBind() {
    wx.navigateTo({ url: "/pages/bind/bind" });
  },
});
