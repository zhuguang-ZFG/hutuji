const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const mcpResult = require("../../utils/mcp_result");

// 设备控制：WeUI 官方模板布局 + 奎享同构能力
// 状态轮询 + 点动/步距/抬落笔/试笔 + 任务 暂停/继续/停止/重画 + 预览确认
Page({
  data: {
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
  },

  onShow() {
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (!ok) return;
      this.refresh();
      this._timer = setInterval(() => this.refresh(true), 5000);
    });
  },

  onHide() {
    clearInterval(this._timer);
  },

  onUnload() {
    clearInterval(this._timer);
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
    return {
      canConfirm,
      canAbort,
      canPause,
      canResume,
      stateLabel: state || "",
      linkOk,
      linkLabel,
      grblLabel: (status && status.grbl_state) || "",
      posLabel,
      pageLabel,
      statusLine,
      lastLine,
      manualLocked: !!(canConfirm || canPause || this.data.busyAction),
    };
  },

  refresh(silent) {
    api
      .request("/api/auth/me")
      .then((me) => {
        const bound = !!(me.binding && me.binding.bound);
        this.setData({ bound });
        if (!bound) return;
        return this.control("hutuji.status", null, true).then((r) => {
          if (r && r.result) {
            const status = mcpResult.parseToolJson(r.result);
            if (status) {
              this.setData(Object.assign({ status }, this._deriveControls(status)));
            } else {
              this.setData({
                status: null,
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
                manualLocked: !!this.data.busyAction,
              });
            }
          }
        });
      })
      .catch(() => {});
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

  onGoBind() {
    wx.navigateTo({ url: "/pages/bind/bind" });
  },
});
