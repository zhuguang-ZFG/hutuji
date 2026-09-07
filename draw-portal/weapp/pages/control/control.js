const api = require("../../utils/api");

// 设备控制（奎享同构）：状态轮询 + 点动/步距/抬落笔/试笔 + 任务 暂停/继续/停止/重画
Page({
  data: {
    bound: false,
    status: null, // 解析后的 hutuji.status JSON
    step: 10, // 当前步距 mm
    busyAction: "",
  },

  onShow() {
    this.refresh();
    this._timer = setInterval(() => this.refresh(true), 5000);
  },

  onHide() {
    clearInterval(this._timer);
  },

  onUnload() {
    clearInterval(this._timer);
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
            try {
              const s = JSON.parse(r.result);
              // 设备回包可能包一层 content/isError
              const inner = typeof s === "object" && s.content ? s.content : s;
              this.setData({ status: typeof inner === "object" ? inner : null });
            } catch (e) {
              this.setData({ status: null });
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
    const key = action || tool;
    this.setData({ busyAction: key });
    this.control(tool, action).then((r) => {
      this.setData({ busyAction: "" });
      if (r && r.status !== "error") {
        setTimeout(() => this.refresh(true), 600);
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

  onJobAction(e) {
    this.fire(e.currentTarget.dataset.tool);
  },

  onStop() {
    wx.showModal({
      title: "停止当前任务",
      content: "彻底放弃当前绘图（不可续画）；换纸中会等换纸完成后才停",
      confirmText: "停止",
      confirmColor: "#B3403A",
      success: (r) => {
        if (r.confirm) this.fire("hutuji.abort");
      },
    });
  },

  onGoBind() {
    wx.navigateTo({ url: "/pages/bind/bind" });
  },
});
