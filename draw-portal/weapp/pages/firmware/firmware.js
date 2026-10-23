const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const pageSession = require("../../utils/page_session");
const { displayVersion } = require("../../utils/version.js");

// 设计稿 §2.4 / 失败表 F1–F8 的 reason token → 中文
const REASON_ZH = {
  busy: "出图结束后再试",
  board_mismatch: "固件与机型不符",
  check_failed: "检查更新失败，请稍后",
  status_timeout: "暂未确认升级结果，请检查设备后重新查询",
  download_failed: "下载失败。请确认设备联网；若仍失败，重启小派后重新检查更新",
  verify_failed: "固件校验失败",
  write_failed: "写入失败，请重试",
  up_to_date: "已是最新版本",
  rolled_back: "已回滚到旧版",
  ok: "升级成功",
};

const POLL_MS = 2000;
const POLL_MAX = 150; // ~5 分钟；超时提示看设备屏
const POLL_WINDOW_MS = 10 * 60 * 1000; // 包含慢请求耗时；离页不重置等待窗口。

function reasonText(token) {
  if (!token) return "";
  const key = String(token).trim();
  if (REASON_ZH[key]) return REASON_ZH[key];
  // 网关瞬态（502/504/读超时）：设备可能已在升级，不要当失败报
  if (/^HTTP\s+50[234]/i.test(key) || /^网络\s+timeout/i.test(key)) {
    return "连接不稳，设备可能仍在升级，请稍后";
  }
  if (/^HTTP\s+\d+/i.test(key) || /^网络/.test(key)) return key.slice(0, 40);
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
    resultUncertain: false,
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
      this._pollDeadline = 0;
      this._afterFlight = null;
      this._busyProbe = false;
      this._upgradePost = false;
      // 换代后旧请求的回包全部作废，操作令牌同步前进。
      this._otaOp = (this._otaOp || 0) + 1;
      this.setData({ upgrading: false, resultUncertain: false, updateAvailable: false, current: "", latest: "", targetVersion: "", progress: 0 });
    }
    const current = pageSession.capture(this);
    if (!this.data.upgrading && !this.data.resultUncertain) {
      this._deviceMac = "";
      // 没有在跟踪的升级时，冷启动若发现别的入口已开始升级，窗口从本次恢复起算；
      // 仍在升级中的回屏不清，绝对上限不因离页延长。
      this._pollDeadline = 0;
    }
    this.setData({ footVersion: displayVersion(), checking: true, updateAvailable: false, loadError: "" });
    phoneGate.ensurePhone(api, current).then((me) => {
      if (!current()) return;
      if (me === false) {
        this.setData({ checking: false, loadError: "账号信息读取失败，请重试" });
        return;
      }
      if (!me) {
        this.setData({ checking: false, loadError: "未登录或账号未读取：可先浏览，授权登录请到「我的」页" });
        return;
      }
      // 切换活动设备不等于取消原设备升级；服务端继续验证原设备的当前归属。
      if (this.data.upgrading && this._upgradeMac) {
        this.setData({ checking: false });
        // 旧视图的请求还没结算：先挂起，等它清掉单飞对象再续查，避免并发第二条。
        if (this._pollFlight) {
          this._afterFlight = () => this._pollOnce();
          return;
        }
        this._pollOnce();
        return;
      }
      if (this.data.resultUncertain && this._upgradeMac) {
        this.setData({ checking: false });
        this._setTip("上次升级结果尚未确认，可重新核对原设备", "warn");
        if (this._pollFlight) {
          this._afterFlight = () => this._queryOtaStatus("recheck", this._upgradeMac);
          return;
        }
        this._queryOtaStatus("recheck", this._upgradeMac);
        return;
      }
      this._ensureBound(me).then((bound) => {
        if (!current()) return;
        this.setData({ checking: false });
        if (!bound) return;
        // onHide 停了轮询；升级中若只 silent check 会因 upgrading 早退卡死 UI
        if (this.data.upgrading) {
          if (this._pollFlight) {
            this._afterFlight = () => this._pollOnce();
            return;
          }
          this._pollOnce();
          return;
        }
        const mac = this._deviceMac;
        if (!mac) return;
        this.setData({ checking: true });
        // 冷启动先核对服务端是否已有升级（可能在本页创建前就开始了），
        // 只有明确 idle 才进入常规 check，避免把升级中的设备显示成可升级。
        if (this._pollFlight) {
          this._afterFlight = () => this._queryOtaStatus("cold", mac);
          return;
        }
        this._queryOtaStatus("cold", mac);
      });
    });
  },

  onHide() {
    pageSession.hide(this);
    this._stopPoll();
    // 在途请求不在这里清：清掉会让回屏后再发一条与旧请求并发。
    this._afterFlight = null;
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
    if (this._visible === false) return;
    if (this.data.resultUncertain) return this.onRecheckResult();
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
    if (!this.data.updateAvailable || this.data.upgrading || this.data.checking || this.data.resultUncertain) {
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
    if (this._visible === false || this._upgradePost) return;
    if (!this.data.updateAvailable || this.data.upgrading || this.data.checking || this.data.resultUncertain) return;
    const mac = this._deviceMac;
    if (!mac) {
      this._setTip("设备信息未就绪，请刷新后重试", "warn");
      return;
    }
    const current = pageSession.capture(this);
    this._upgradeMac = mac;
    // POST 未结算前任何状态查询都可能读到上一次的终态账本，单飞状态查询先让位。
    // 令牌归属本次 POST：登录代次切换后旧 POST 的收尾不得清掉新 POST 的门。
    const post = (this._upgradePostToken = (this._upgradePostToken || 0) + 1);
    this._upgradePost = true;
    this._busyProbe = false;
    this._stopPoll();
    this._pollDeadline = Date.now() + POLL_WINDOW_MS;
    this.setData({
      upgrading: true,
      updateAvailable: false,
      resultUncertain: false,
      progress: 0,
      progressLabel: "正在下发升级…",
      targetVersion: this.data.latest || "",
    });
    this._setTip("升级中，请保持设备联网", "");
    api
      // 下发应快速回 started/unknown；超时走 catch 的瞬态分支继续轮询对账，30s 足够。
      .request("/api/device/firmware/upgrade", { method: "POST", timeout: 30000, data: { device_mac: mac } })
      .then((data) => {
        if (!current() || post !== this._upgradePostToken) return;
        // 先解 POST 门：紧随其后的 status 查询是本次下发的一部分，不能让位给 finally。
        this._upgradePost = false;
        if (!data || (data.status !== "started" && data.status !== "unknown")) {
          const token = (data && data.detail) || "check_failed";
          // 忙 = 设备/队列忙或已有升级账本。不能当普通失败，也不能再 POST：
          // 只读核对原设备状态，升级中的话原地恢复轮询。
          if (data && data.status === "error" && token === "busy") {
            this.setData({
              upgrading: true,
              updateAvailable: false,
              resultUncertain: false,
              progress: 0,
              progressLabel: "正在核对升级…",
            });
            if (data.target_version) this.setData({ targetVersion: data.target_version });
            this._setTip("设备忙或已有升级，正在核对设备状态", "warn");
            // busy = 本次没有开始升级；只有查到真实升级账本才转为普通升级跟踪。
            this._busyProbe = true;
            this._queryOtaStatus("busy", mac);
            return;
          }
          const msg = reasonText(token) || "升级未能开始";
          // 设备已是最新时后端会拒升，按钮状态同步复位，避免再次误点
          if (token === "up_to_date") this.setData({ updateAvailable: false });
          this._upgradeMac = "";
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
        if (!current() || post !== this._upgradePostToken) return;
        this._upgradePost = false;
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
        this._upgradeMac = "";
        this.setData({ upgrading: false, progress: 0 });
        this._setTip(msg, "warn");
        wx.showToast({ title: String(msg).slice(0, 20), icon: "none" });
      })
      .finally(() => {
        if (post === this._upgradePostToken) this._upgradePost = false;
      });
  },

  // 状态查询唯一入口：冷启动、忙恢复、手动查询、重新核对与轮询共用。
  // 单飞对象存活到请求真正结束；onHide 不清它，回屏后才不会叠出第二条 GET。
  _queryOtaStatus(mode, mac) {
    if (this._pollFlight) return this._pollFlight;
    if (this._visible === false) return Promise.resolve(null);
    const target = mac || this._upgradeMac || this._deviceMac || "";
    if (!target) {
      this._applyOtaError({ status: 400, detail: "设备信息未就绪，请刷新后重试" }, mode);
      return Promise.resolve(null);
    }
    const current = pageSession.capture(this);
    const op = this._otaOp = (this._otaOp || 0) + 1;
    this._flightMac = target;
    let flight = null;
    flight = api
      // 2s 间隔轮询：单请求 10s 封顶，挂起请求不能把「超时未确认」拖成数小时。
      .request("/api/device/firmware/ota_status?device_mac=" + encodeURIComponent(target), { timeout: 10000 })
      .then((st) => {
        if (!this._otaResultCurrent(current, target, op)) return;
        this._applyOtaStatus(st || {}, mode);
      })
      .catch((err) => {
        if (!this._otaResultCurrent(current, target, op)) return;
        this._applyOtaError(err || {}, mode);
      })
      .finally(() => {
        if (this._pollFlight !== flight) return;
        this._pollFlight = null;
        const resume = this._afterFlight;
        this._afterFlight = null;
        // 回屏时挂起的续查只跑一次，且必须在页面可见时；新定时器由续查自己安排。
        if (resume && this._visible !== false) resume();
      });
    this._pollFlight = flight;
    return flight;
  },

  // 展示代次、登录代次、目标 MAC 与操作令牌任一变化，回包都不得写界面或续发。
  _otaResultCurrent(current, mac, op) {
    return current() && mac === (this._upgradeMac || this._deviceMac) && op === (this._otaOp || 0);
  },

  _applyOtaStatus(st, mode) {
    const state = String((st && st.state) || "").toLowerCase();
    const mac = this._flightMac || this._upgradeMac || this._deviceMac || "";
    const progress = Number.isFinite(st && st.progress)
      ? Math.max(0, Math.min(100, st.progress))
      : this.data.progress;
    const reason = (st && st.reason) || "";
    if (st && st.target_version) {
      this.setData({ targetVersion: st.target_version });
    }
    if (st && st.firmware_version) {
      this.setData({ current: st.firmware_version });
    }

    if (state === "ok") {
      this._stopPoll();
      this._busyProbe = false;
      this._upgradeMac = "";
      this.setData({
        checking: false,
        upgrading: false,
        resultUncertain: false,
        progress: 100,
        progressLabel: "升级完成",
        updateAvailable: false,
      });
      this._setTip(reasonText("ok"), "ok");
      if (mode === "poll") {
        wx.showToast({ title: "升级成功", icon: "success" });
        // 再 check 一次对齐 latest；期间可能已切换活动设备，先重读归属再查。
        const current = pageSession.capture(this);
        this._pollTimer = setTimeout(() => {
          if (!current()) return;
          this._ensureBound().then((bound) => {
            if (bound && current()) this.onCheck(true);
          });
        }, 800);
      }
      return;
    }

    if (state === "failed" || state === "rolled_back") {
      this._stopPoll();
      this._busyProbe = false;
      const msg =
        reasonText(reason) ||
        (state === "rolled_back" ? reasonText("rolled_back") : "升级失败");
      const uncertain = reason === "status_timeout";
      // 结果待确认时留住原设备，只读重查才有明确目标；有结论就释放。
      if (uncertain) {
        if (mac) this._upgradeMac = mac;
      } else this._upgradeMac = "";
      this.setData({
        checking: false,
        upgrading: false,
        resultUncertain: uncertain,
        updateAvailable: false,
        progress,
        progressLabel: uncertain ? "结果待确认" : (state === "rolled_back" ? "已回滚" : "升级失败"),
      });
      this._setTip(msg, "warn");
      if (mode === "poll") wx.showToast({ title: msg.slice(0, 20), icon: "none" });
      return;
    }

    if (state === "upgrading") {
      // 已看到真实升级账本，busy 核对上下文结束，转入普通升级跟踪。
      this._busyProbe = false;
      // starting / uncertain 由服务端折成 upgrading：冷启动恢复也要认领原设备。
      if (mac) this._upgradeMac = mac;
      let label = "升级中…";
      let tip = "升级中，请保持设备联网";
      const ota = (st && st.ota) || {};
      if (ota.state === "failed" || ota.state === "rolled_back") {
        // 该失败可能属于上次升级；保留占位，只解释设备报告并查询本轮结果。
        label = "正在核对升级结果…";
        const failure = { download_failed: "下载失败", verify_failed: "校验失败", write_failed: "写入失败" };
        tip = "设备报告" + (ota.state === "rolled_back" ? "已回滚" : failure[ota.reason] || "升级失败") + "，本次结果仍在核对，请勿重复升级";
      } else if (progress >= 100) label = "等待设备重启并核对版本…";
      else if (ota.state === "upgrading") label = "下载 / 写入中…";
      else label = "等待设备确认升级…";
      // 冷启动恢复或用户重新核对到仍在升级时，从本次观察重新计时；
      // 轮询回包不得续期，否则每 2s 一次的观察会把绝对上限无限推后。
      const observed = mode === "cold" || mode === "busy" || mode === "recheck";
      if (observed && (!this._pollDeadline || Date.now() >= this._pollDeadline)) {
        this._pollDeadline = Date.now() + POLL_WINDOW_MS;
        this._pollCount = 0;
      }
      this.setData({
        checking: false,
        upgrading: true,
        resultUncertain: false,
        updateAvailable: false,
        progress,
        progressLabel: label,
        statusTip: tip,
      });
      if (this._visible !== false) {
        this._pollTimer = setTimeout(() => this._pollOnce(), POLL_MS);
      }
      return;
    }

    // idle / 缺 state / 未知 state：
    this._stopPoll();
    if (state !== "idle") {
      // 只有明确 idle 才代表没有升级账本；缺字段/未知状态不能当 idle 去 check。
      if (mode === "cold") {
        this.setData({ checking: false, upgrading: false });
        this._setTip("设备状态无法识别，请稍后重试", "warn");
        return;
      }
      if (mode === "busy" || this._busyProbe) {
        this._leaveBusy("设备忙，暂时无法核对升级状态，请稍后重新检查更新");
        return;
      }
      if (mac) this._upgradeMac = mac;
      this.setData({
        checking: false,
        upgrading: false,
        resultUncertain: true,
        updateAvailable: false,
        progressLabel: "结果待确认",
      });
      this._setTip("设备状态无法识别，请稍后重新核对", "warn");
      return;
    }
    if (mode === "cold") {
      // 没有升级账本才是常规「检查更新」，且只走一次。
      this.setData({
        checking: false,
        upgrading: false,
        resultUncertain: false,
        updateAvailable: false,
        progress: 0,
        progressLabel: "升级中…",
      });
      this.onCheck(true);
      return;
    }
    if (mode === "busy" || this._busyProbe) {
      // 服务端明确说没有升级在进行：这是一次「设备忙」，不是待确认结果，
      // 必须让用户能重新 check + 确认后再下发。
      this._leaveBusy("设备忙，请稍后重试");
      return;
    }
    // poll / recover / recheck：已知升级却读到 idle，不能当成功也不能无限等；
    // 留住原设备 MAC，给只读重查入口。
    if (mac) this._upgradeMac = mac;
    this.setData({
      checking: false,
      upgrading: false,
      resultUncertain: true,
      updateAvailable: false,
      progressLabel: "结果待确认",
    });
    this._setTip("暂未确认升级结果，原设备可重新核对", "warn");
  },

  // busy 只表示「本次没有开始升级」；核对不到真实账本时只能回到设备忙，
  // 不能伪装成升级结果待确认，否则重查 idle 会永远卡住且无法重新检查更新。
  _leaveBusy(msg) {
    this._stopPoll();
    this._busyProbe = false;
    this._upgradeMac = "";
    this.setData({
      checking: false,
      upgrading: false,
      resultUncertain: false,
      updateAvailable: false,
      progress: 0,
      progressLabel: "升级中…",
    });
    this._setTip(msg || "设备忙，请稍后重试", "warn");
  },

  _applyOtaError(err, mode) {
    if (err && [400, 401, 403, 409].includes(err.status)) {
      this._stopPoll();
      this._busyProbe = false;
      if (mode !== "recheck") this._upgradeMac = "";
      this.setData({ checking: false, upgrading: false, updateAvailable: false });
      this._setTip((err && err.detail) || "设备绑定已变化，请刷新后重试", "warn");
      return;
    }
    if (mode === "poll") {
      // busy 只核对一次：读不到真实账本就回到设备忙，别把轮询拖成待确认。
      if (this._busyProbe) {
        this._leaveBusy("设备忙，暂时无法核对升级状态，请稍后重新检查更新");
        return;
      }
      // 短暂网络抖动：继续轮询，不立刻失败
      this.setData({ checking: false });
      this._pollTimer = setTimeout(() => this._pollOnce(), POLL_MS);
      return;
    }
    this._stopPoll();
    if (mode === "cold") {
      // 状态读不到时不盲发 check（避免把升级中的设备显示成可升级），让用户手动重试。
      this.setData({ checking: false, upgrading: false });
      this._setTip("设备状态暂不可读，请稍后重试", "warn");
      return;
    }
    if (mode === "busy" || this._busyProbe) {
      this._leaveBusy("设备忙，暂时无法核对升级状态，请稍后重新检查更新");
      return;
    }
    // recover / recheck：留住原设备与待确认，只读重查入口仍在。
    this.setData({ checking: false, upgrading: false, resultUncertain: true, updateAvailable: false });
    this._setTip((err && err.detail) || "暂时无法确认设备状态，请稍后重试", "warn");
  },

  _pollOnce() {
    if (this._visible === false || this._pollFlight || this._upgradePost) return;
    this._pollCount = (this._pollCount || 0) + 1;
    if (!this._pollDeadline) this._pollDeadline = Date.now() + POLL_WINDOW_MS;
    if (this._pollCount > POLL_MAX || Date.now() >= this._pollDeadline) {
      if (this._busyProbe) {
        this._leaveBusy("设备忙，暂未核对到升级状态，请稍后重新检查更新");
        return;
      }
      this._markUncertain();
      wx.showToast({ title: "请查看设备屏幕", icon: "none" });
      return;
    }
    this._queryOtaStatus("poll", this._upgradeMac || this._deviceMac || "");
  },

  _markUncertain() {
    this._stopPoll();
    this.setData({ upgrading: false, resultUncertain: true, updateAvailable: false, progressLabel: "结果待确认" });
    this._setTip("暂未确认升级结果，请查看设备屏幕后重新核对", "warn");
  },

  onQueryUpgrade() {
    if (this._visible === false || !this.data.upgrading || this._pollFlight || this._upgradePost) return;
    if (this._pollTimer) clearTimeout(this._pollTimer);
    this._pollTimer = null;
    this._pollOnce();
  },

  onRecheckResult() {
    if (this._visible === false) return;
    if (!this.data.resultUncertain || !this._upgradeMac || this.data.checking || this.data.upgrading) return;
    const mac = this._upgradeMac;
    this.setData({ checking: true, updateAvailable: false });
    // 只查原升级设备（服务端允许仍在用户名下的设备）；check 只认当前活动设备，不能用。
    return this._queryOtaStatus("recheck", mac);
  },
});
