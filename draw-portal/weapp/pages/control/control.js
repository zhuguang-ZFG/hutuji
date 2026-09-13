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

function activeDeviceMac(me) {
  // binding.device_mac 是展示用打码串；完整目标必须由后端明确给出。
  const mac = me && (me.active_device_mac || (me.binding && me.binding.device_mac));
  return typeof mac === "string" && /^(?:[\da-f]{2}:){5}[\da-f]{2}$/i.test(mac) ? mac.toLowerCase() : "";
}

// 设备控制：WeUI 官方模板布局 + 奎享同构能力
// 状态轮询 + 点动/步距/抬落笔/试笔 + 任务 暂停/继续/停止/重画 + 预览确认
Page({
  data: {
    footVersion: "v0.3.6",
    bound: false,
    bindingLoading: true,
    bindingChecked: false,
    status: null,
    statusError: "",
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
    manualLocked: true,
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
    speedError: "",
    speedHint: "正在读取设备状态…",
    canSetSpeed: false,
    canRepeat: false,
    bindingMac: "",
  },

  onShow() {
    this._visible = true;
    const generation = this._viewGeneration = (this._viewGeneration || 0) + 1;
    const session = getApp()._sessionGeneration;
    this.setData({ footVersion: displayVersion("v0.3.6"), speedSaving: false,
      speedLoaded: false, speedLoading: false, canSetSpeed: false, speedError: "", statusError: "",
      bound: false, bindingLoading: true, bindingChecked: false,
      bindingMac: "", status: null, busyAction: "", manualLocked: true,
      canConfirm: false, canAbort: false, canPause: false, canResume: false, canRepeat: false });
    phoneGate.ensurePhoneOrRedirect(api, () => this._isCurrentView(generation, session)).then((ok) => {
      if (!this._isCurrentView(generation, session)) return;
      if (!ok) {
        this.setData({ bindingLoading: false, statusError: "账号信息读取失败，请刷新重试" });
        return;
      }
      this.refresh();
    });
  },

  onHide() {
    this._visible = false;
    this._viewGeneration = (this._viewGeneration || 0) + 1;
    this._refreshPromise = null;
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
      clearTimeout(this._timer);
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
    this._timer = setTimeout(() => {
      this._timer = null;
      this.refresh(true);
    }, ms);
  },

  _deriveControls(status) {
    const state = (status && (status.state || status.job_state)) || "";
    const linkOk = !!(status && status.connected);
    const canConfirm = linkOk && (
      state === "previewing" ||
      state === "awaiting_confirmation" ||
      !!(status && status.awaiting_confirmation));
    const settled = ["idle", "done", "error", "aborted"].includes(state);
    const canAbort = !!(status && status.connected && state && !settled && state !== "ready");
    const canPause = linkOk && state === "streaming";
    const canResume = linkOk && state === "paused";
    const ready = !!(linkOk && status.ready && status.authorized && status.grbl_settings_ok);
    const axisControl = !!(status && status.speed && status.speed.axis_control === true);
    const speedPending = !!(status && status.speed && status.speed.state === "pending");
    const canSetSpeed = ready && status.plotter_sku === "nopaper" && axisControl &&
      !speedPending && settled && !!this.data.bindingMac;
    const speedHint = !this.data.bindingMac ? "设备信息未就绪，请刷新后重试" :
      !linkOk ? "丹青未连接，连接后即可调速" :
      !ready ? "丹青正在准备，稍后即可调速" :
      !axisControl ? "设备固件暂不支持分轴调速，请先升级" :
      !settled ? "任务结束后可调整速度" :
      speedPending ? "正在读取或设置速度，请稍候" : "拖动后松开即可设置，重启后仍保留";
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
      canSetSpeed,
      speedHint,
      canRepeat: ready && settled && !speedPending,
      canConfirm,
      canAbort,
      canPause,
      canResume,
      stateLabel: ({ idle: "空闲", ready: "就绪", done: "已完成", error: "需要处理", aborted: "已停止",
        previewing: "预览中", awaiting_confirmation: "等待确认", downloading: "准备中", verifying: "检查中",
        streaming: "绘制中", paused: "已暂停", paper_change: "整理纸张" })[state] || "查询中",
      linkOk,
      linkLabel,
      grblLabel: (status && status.grbl_state) || "",
      posLabel,
      pageLabel: prog.pageLabel || pageLabel,
      statusLine,
      lastLine,
      manualLocked: !ready || !settled || speedPending || !!this.data.busyAction,
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
    if (this._refreshPromise) return this._refreshPromise;
    if (!this.data.bindingChecked) this.setData({ bindingLoading: true });
    this._clearPoll();
    const generation = this._viewGeneration || 0;
    const session = getApp()._sessionGeneration;
    const refreshGeneration = this._refreshGeneration = (this._refreshGeneration || 0) + 1;
    const current = () => this._isCurrentView(generation, session) &&
      this._refreshGeneration === refreshGeneration;
    let nextPollMs = jobProgress.POLL_MS_IDLE;
    const pending = api
      .request("/api/auth/me")
      .then((me) => {
        if (!current()) return;
        const bound = !!(me.binding && me.binding.bound);
        const bindingMac = activeDeviceMac(me);
        if (bindingMac !== this.data.bindingMac) {
          this._speedGeneration = (this._speedGeneration || 0) + 1;
          this.setData({ speedRates: { x: 10000, y: 10000, z: 1200 },
            speedSaving: false, speedLoaded: false, speedLoading: false, canSetSpeed: false, speedError: "" });
        }
        this.setData({ bound, bindingMac, bindingLoading: false, bindingChecked: true });
        if (!bound || !bindingMac) {
          this._statusFailed(bound ? "无法读取当前设备信息，请刷新后重试" : "");
          this._clearPoll();
          return;
        }
        return this.control("hutuji.status", null, true).then((r) => {
          if (!current() || this.data.bindingMac !== bindingMac) return;
          if (r && r.status === "ok" && r.result) {
            const status = mcpResult.parseToolJson(r.result);
            if (status) {
              this.setData(Object.assign({ status, statusError: "" }, this._deriveControls(status)));
              nextPollMs = jobProgress.deriveProgressView(status).pollMs;
              this._loadMachineSpeed();
            } else {
              this._statusFailed("设备状态读取失败，请刷新重试");
            }
          } else {
            this._statusFailed((r && r.detail) || "设备状态读取失败，请刷新重试");
          }
        });
      })
      .catch((error) => {
        if (!current()) return;
        this._statusFailed((error && error.detail) || "设备状态读取失败，请刷新重试");
      })
      .finally(() => {
        if (this._refreshPromise === pending) this._refreshPromise = null;
        if (current() && this.data.bound) this._reschedulePoll(nextPollMs);
      });
    this._refreshPromise = pending;
    return pending;
  },

  _statusFailed(detail) {
    this.setData(Object.assign(this._deriveControls(null), {
      status: null, statusError: detail, linkLabel: "读取失败", speedLoaded: false, bindingLoading: false,
    }));
  },

  control(tool, action, quiet) {
    const generation = this._viewGeneration || 0;
    const session = getApp()._sessionGeneration;
    const bindingMac = this.data.bindingMac;
    const current = () => this._isCurrentView(generation, session) && this.data.bindingMac === bindingMac;
    return api
      .request("/api/device/control", {
        method: "POST",
        data: { tool, action, device_mac: this.data.bindingMac || undefined },
      })
      .then((r) => {
        if (r.status === "error" && !quiet && current()) {
          wx.showToast({ title: r.detail || "设备未响应", icon: "none" });
        }
        return r;
      })
      .catch((err) => {
        if (!quiet && current()) wx.showToast({ title: err.detail || "网络不可用", icon: "none" });
        return { status: "error", detail: err.detail || "网络不可用" };
      });
  },

  fire(tool, action) {
    if (this.data.busyAction || this.data.speedSaving || this.data.speedLoading || !this.data.bindingMac) return Promise.resolve(false);
    // 笔/任务 cells 在不可用态仍可能被点：前端再闸一道
    if (tool === "hutuji.pause" && !this.data.canPause) {
      wx.showToast({ title: "当前不可暂停", icon: "none" });
      return Promise.resolve(false);
    }
    if (tool === "hutuji.resume" && !this.data.canResume) {
      wx.showToast({ title: "当前不可继续", icon: "none" });
      return Promise.resolve(false);
    }
    if ((tool === "hutuji.confirm" && !this.data.canConfirm) ||
      (tool === "hutuji.abort" && !this.data.canAbort)) return Promise.resolve(false);
    if (((tool === "hutuji.manual" || tool === "hutuji.pen_test") && this.data.manualLocked) ||
      (tool === "hutuji.repeat" && !this.data.canRepeat)) {
      wx.showToast({ title: "设备空闲并就绪后可操作", icon: "none" });
      return Promise.resolve(false);
    }
    const generation = this._viewGeneration || 0;
    const session = getApp()._sessionGeneration;
    const bindingMac = this.data.bindingMac;
    const key = action || tool;
    this.setData({ busyAction: key, manualLocked: true });
    return this.control(tool, action).then((r) => {
      if (!this._isCurrentView(generation, session) || this.data.bindingMac !== bindingMac) return false;
      this.setData({ busyAction: "" });
      if (r && r.status !== "error") {
        this.refresh(true);
      } else {
        this.setData(this._deriveControls(this.data.status));
      }
      return !!(r && r.status !== "error");
    });
  },

  onJog(e) {
    this.fire("hutuji.manual", e.currentTarget.dataset.dir);
  },

  onStepToggle() {
    const next = this.data.step === 10 ? 1 : 10;
    this.fire("hutuji.manual", next === 1 ? "jog_step_1" : "jog_step_10").then((ok) => {
      if (ok) this.setData({ step: next });
    });
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
    const generation = this._viewGeneration || 0;
    const session = getApp()._sessionGeneration;
    const bindingMac = this.data.bindingMac;
    wx.showModal({
      title: "停止当前任务",
      content: "停止后会抬笔并回到原点，本次任务不可继续。",
      confirmText: "停止",
      confirmColor: "#FA5151",
      success: (r) => {
        if (r.confirm && this._isCurrentView(generation, session) && this.data.bindingMac === bindingMac) {
          this.fire("hutuji.abort");
        }
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
      this.setData({ speedSaving: false, speedRates: previous, speedLoaded: false,
        speedError: detail || "速度设置未确认，请重新读取后再试" });
      wx.showToast({ title: detail || "调整失败，请重试", icon: "none" });
    };
    this.setData({ speedSaving: true, speedError: "" });
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
          this.setData({ speedSaving: false, speedRates: rates, speedLoaded: true, speedError: "" });
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
    this.setData({ speedLoading: true, speedError: "" });
    api.request(`/api/settings/machine-speed?device_mac=${encodeURIComponent(bindingMac)}`)
      .then((r) => {
        if (!current()) return;
        const rates = readSpeedRates(r);
        this.setData(Object.assign({ speedLoading: false, speedLoaded: !!(r && r.status === "ok" && rates),
          speedError: r && r.status === "ok" && rates ? "" : "速度读取失败，请重试" },
          r && r.status === "ok" && rates ? { speedRates: rates } : {}));
      })
      .catch((error) => {
        if (current()) this.setData({ speedLoading: false, speedLoaded: false,
          speedError: (error && error.detail) || "速度读取失败，请检查连接后重试" });
      });
  },

  onRetrySpeed() {
    if (this.data.speedLoading || this.data.speedSaving) return;
    this.setData({ speedLoaded: false, speedError: "" });
    this.refresh();
  },

  onGoBind() {
    wx.navigateTo({ url: "/pages/bind/bind" });
  },
});
