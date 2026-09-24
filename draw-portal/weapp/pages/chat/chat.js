// 小派聊天页：按住说话（语音 ASR→turn）+ 打字（文字 /api/chat）双通道。
// 语音链：voice/asr → voice/turn，预览确认走 voice/confirm|cancel。
// 文字链（2026-09-09）：/api/chat（内容安全+配额）→ 回复气泡；DRAW:: 意图落
// 画图卡，生成/发送/收藏复用既有 /api/draw/* 接口，不发明第二条出图链。
const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const subscribe = require("../../utils/subscribe");
const pageSession = require("../../utils/page_session");
const paperSummary = require("../../utils/paper_summary");
const { displayVersion } = require("../../utils/version.js");

const SESSION_KEY = "voice_session_id";
const SESSION_LEN = 22;
let recordingOwner = null;
let sharedRecorder = null;

function getRecorder() {
  if (sharedRecorder) return sharedRecorder;
  const recorder = sharedRecorder = wx.getRecorderManager();
  // RecorderManager 没有 offStart/offStop/offError；单次注册后转交本次录音的页面。
  // 新页面不得覆盖旧页面等待中的回调，否则迟到的 onStart 无人停止。
  const dispatch = (method) => (result) => {
    const owner = recordingOwner;
    if (owner) owner[method](result);
  };
  recorder.onStart(dispatch('_onRecorderStart'));
  recorder.onStop(dispatch('_onRecorderStop'));
  recorder.onError(dispatch('_onRecorderError'));
  if (recorder.onInterruptionBegin) recorder.onInterruptionBegin(dispatch('_onRecorderInterrupted'));
  return recorder;
}

function ensureSessionId() {
  let sid = wx.getStorageSync(SESSION_KEY);
  if (typeof sid === "string" && sid.length >= 16) return sid;
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  let out = "";
  for (let i = 0; i < SESSION_LEN; i++) {
    out += chars[Math.floor(Math.random() * chars.length)];
  }
  wx.setStorageSync(SESSION_KEY, out);
  return out;
}

function formatToolTraces(traces) {
  if (!traces || !traces.length) return "";
  return traces
    .map((t) => {
      const sum = t && t.summary ? t.summary : "";
      return sum;
    })
    .filter(Boolean)
    .join("\n");
}

Page({
  data: {
    footVersion: "v0.3.6",
    messages: [],
    recording: false,
    recordingStarting: false,
    recordingStopping: false,
    recordPermission: "checking",
    micAuthorizing: false,
    recordCancel: false,
    recordSeconds: 0,
    voiceHint: "按住说话，松开发送，上滑取消",
    voiceStage: "",
    enabled: false,
    loadError: "",
    busy: false,
    scrollAnchor: "",
    textInput: "",
    inputMode: "voice",
    inputFocus: false,
    replyPending: false,
    paperSummary: { title: "纸张与方向", detail: "正在读取当前设备…", state: "loading" },
  },

  onLoad() {
    this.setData({ footVersion: displayVersion() });
    subscribe.prepare();
    this._sessionId = ensureSessionId();
    this._msgSeq = 0;
    this._recorder = getRecorder();
    this._onRecorderStart = () => {
      if (recordingOwner !== this) return;
      const attempt = this._recordAttempt;
      if (!attempt) return;
      attempt.started = true;
      if (!attempt.held || attempt.discarded || !attempt.current()) {
        attempt.discarded = true;
        this._stopRecorder(attempt);
        return;
      }
      this.setData({ recording: true, recordingStarting: false });
      this._tickRecording(attempt);
    };
    this._onRecorderStop = (res) => {
      if (recordingOwner !== this) return;
      const attempt = this._finishRecording();
      if (!attempt || !attempt.current()) return;
      if (!attempt.discarded) this._onRecordStop(res, attempt.current, attempt.sessionId);
    };
    this._onRecorderError = (error) => {
      if (recordingOwner !== this) return;
      const attempt = this._finishRecording();
      if (!attempt || !attempt.current()) return;
      if (attempt.discarded) return;
      const permission = /auth|permission|privacy/i.test((error && error.errMsg) || "");
      this.setData({ voiceHint: permission ? "麦克风未开启，请先授权后再按住说话" : "录音未能开始，请重新按住说话",
        recordPermission: permission ? "denied" : this.data.recordPermission });
      wx.showToast({ title: permission ? "请先开启麦克风" : "录音失败，请重试", icon: "none" });
    };
    this._onRecorderInterrupted = () => {
      if (recordingOwner !== this) return;
      this._cancelRecording();
      if (this._visible !== false) this.setData({ voiceHint: "录音被中断，请重新按住说话" });
    };
  },

  onShow() {
    pageSession.show(this);
    paperSummary.reset(this);
    const session = getApp()._sessionGeneration;
    if (this._chatSession !== session) {
      this._chatSession = session;
      this._textPending = null;
      this._voicePending = null;
      this._msgSeq = 0;
      this.setData({ messages: [], textInput: "", scrollAnchor: "", inputMode: "voice", inputFocus: false });
    }
    this._permissionPending = null;
    this.setData({ enabled: false, loadError: "", busy: !!(this._textPending || this._voicePending),
      replyPending: !!this._textPending,
      voiceStage: this._voicePending ? this._voicePending.label : "",
      recording: false, recordingStarting: false, recordingStopping: !!this._recordAttempt,
      micAuthorizing: false, recordCancel: false,
      messages: this.data.messages.map((msg) => msg.draw ? Object.assign({}, msg, {
        draw: Object.assign({}, msg.draw, { sending: false, saving: false }),
      }) : msg) });
    const current = pageSession.capture(this);
    this._checkMicrophone(current);
    phoneGate.ensurePhone(api, current).then((ok) => {
      if (!current()) return;
      if (ok === false) { this.setData({ loadError: "账号信息读取失败，请重试",
        paperSummary: { title: "纸张与方向", detail: "账号信息未读取，点此查看", state: "unavailable" } }); return; }
      if (!ok) { this.setData({ loadError: "未登录或账号未读取：可先浏览，授权登录请到「我的」页",
        paperSummary: { title: "纸张与方向", detail: "账号信息未读取，点此查看", state: "unavailable" } }); return; }
      this._sessionId = ensureSessionId();
      this.setData({ enabled: true });
      paperSummary.refresh(this);
      this._continueVoiceTurn(this._voicePending);
    });
  },

  onHide() {
    pageSession.hide(this);
    this._cancelRecording();
    this.setData({ enabled: false, recording: false, recordingStarting: false, inputFocus: false });
  },

  onUnload() {
    this._unloaded = true;
    this.onHide();
  },

  _finishRecording() {
    const attempt = this._recordAttempt;
    this._recordAttempt = null;
    if (recordingOwner === this) recordingOwner = null;
    if (this._recordTimer) clearTimeout(this._recordTimer);
    this._recordTimer = null;
    if (this._visible !== false && !this._unloaded) this.setData({ recording: false,
      recordingStarting: false, recordingStopping: false, recordCancel: false });
    return attempt;
  },

  _cancelRecording() {
    const attempt = this._recordAttempt;
    if (!attempt) return;
    attempt.held = false;
    attempt.discarded = true;
    if (attempt.started) this._stopRecorder(attempt);
  },

  _stopRecorder(attempt) {
    if (attempt.stopping) return;
    attempt.stopping = true;
    if (this._visible !== false) this.setData({ recordingStarting: false, recordingStopping: true });
    try { this._recorder.stop(); } catch (error) {
      this._finishRecording();
      if (attempt.current()) this.setData({ voiceHint: "录音未能结束，请重新打开小派页" });
    }
  },

  _tickRecording(attempt) {
    if (!attempt.current() || this._recordAttempt !== attempt || attempt.stopping) return;
    this._recordTimer = setTimeout(() => {
      if (!attempt.current() || this._recordAttempt !== attempt || attempt.stopping) return;
      this.setData({ recordSeconds: Math.min(60, this.data.recordSeconds + 1) });
      this._tickRecording(attempt);
    }, 1000);
  },

  _checkMicrophone(current) {
    if (!wx.getSetting) {
      this.setData({ recordPermission: "unknown", voiceHint: "请先开启麦克风" });
      return;
    }
    wx.getSetting({
      success: (result) => {
        if (!current()) return;
        const granted = (result.authSetting || {})["scope.record"];
        this.setData({ recordPermission: granted === true ? "granted" : granted === false ? "denied" : "unknown" });
      },
      fail: () => { if (current()) this.setData({ recordPermission: "unknown", voiceHint: "请点击开启麦克风" }); },
    });
  },

  onEnableMicrophone() {
    if (!this.data.enabled || this.data.busy || this._recordAttempt || this._permissionPending) return;
    const current = pageSession.capture(this);
    const pending = this._permissionPending = {};
    this.setData({ micAuthorizing: true });
    const finish = (granted) => {
      if (this._permissionPending !== pending) return;
      this._permissionPending = null;
      if (!current()) return;
      this.setData({ micAuthorizing: false, recordPermission: granted ? "granted" : "denied",
        voiceHint: granted ? "麦克风已开启，请重新按住说话" : "麦克风未开启，点按钮到设置中打开" });
    };
    const authorize = () => {
      if (!current()) return;
      if (this.data.recordPermission === "denied" && wx.openSetting) {
        wx.openSetting({ success: (result) => finish(!!(result.authSetting || {})["scope.record"]), fail: () => finish(false) });
      } else if (wx.authorize) {
        wx.authorize({ scope: "scope.record", success: () => finish(true), fail: () => finish(false) });
      } else finish(false);
    };
    // 授权弹窗会打断长按，所以只在独立点击中完成，绝不在回包后自动录音。
    if (wx.requirePrivacyAuthorize) wx.requirePrivacyAuthorize({ success: authorize, fail: () => finish(false) });
    else authorize();
  },

  _nextMsgId() {
    this._msgSeq += 1;
    return "m" + this._msgSeq;
  },

  _pushMessage(role, content, toolTraces, preview) {
    const id = this._nextMsgId();
    const msg = { id, role, content };
    const tracesText = formatToolTraces(toolTraces);
    if (tracesText) msg.toolTracesText = tracesText;
    if (preview && preview.preview_url) {
      msg.previewUrl = preview.preview_url;
      msg.jobId = preview.job_id || "";
      msg.previewKind = preview.kind || "";
      msg.previewDeviceMac = preview.device_mac || "";
      msg.previewPending = true;
    }
    const messages = this.data.messages.concat(msg);
    this.setData({
      messages,
      scrollAnchor: "msg-" + id,
    });
    return id;
  },

  _patchMessage(id, patch) {
    const messages = this.data.messages.map((m) =>
      m.id === id ? Object.assign({}, m, patch) : m
    );
    this.setData({ messages });
  },

  _findMsg(id) {
    return this.data.messages.find((m) => m.id === id);
  },

  _patchDraw(id, patch) {
    const msg = this._findMsg(id);
    if (!msg || !msg.draw) return;
    this._patchMessage(id, { draw: Object.assign({}, msg.draw, patch) });
  },

  onHoldStart(e = {}) {
    if (!this.data.enabled || this.data.busy || recordingOwner) return;
    if (this.data.recordPermission !== "granted") {
      this.setData({ voiceHint: "请先开启麦克风，再按住说话" });
      return;
    }
    const touch = e.touches && e.touches[0];
    this._recordAttempt = { current: pageSession.capture(this), sessionId: this._sessionId,
      touchY: touch ? (typeof touch.clientY === "number" ? touch.clientY : touch.pageY) : null,
      held: true, started: false, discarded: false };
    recordingOwner = this;
    this.setData({ recordingStarting: true, recordingStopping: false, recordCancel: false,
      recordSeconds: 0, voiceHint: "松开发送，上滑取消" });
    try {
      this._recorder.start({
        format: "mp3",
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 32000,
        duration: 60000,
      });
    } catch (e) {
      this._finishRecording();
      this.setData({ recordingStarting: false });
      wx.showToast({ title: "无法开始录音", icon: "none" });
    }
  },

  onHoldEnd() {
    const attempt = this._recordAttempt;
    if (!attempt) return;
    attempt.held = false;
    if (this.data.recordCancel) attempt.discarded = true;
    if (attempt.started) this._stopRecorder(attempt);
    else attempt.discarded = true;
    this.setData({ recordingStarting: false, recordingStopping: !!this._recordAttempt });
    if (attempt.discarded) this.setData({ voiceHint: "已取消，重新按住即可录音" });
  },

  onHoldMove(e) {
    const attempt = this._recordAttempt, touch = e.touches && e.touches[0];
    if (!attempt || !attempt.held || !touch || typeof attempt.touchY !== "number") return;
    const y = typeof touch.clientY === "number" ? touch.clientY : touch.pageY;
    this.setData({ recordCancel: attempt.touchY - y > 56 });
  },

  onHoldCancel() {
    this._cancelRecording();
    if (this._visible !== false) this.setData({ voiceHint: "已取消，重新按住即可录音" });
  },

  onConfirmPreview(e) {
    const id = e.currentTarget.dataset.id;
    if (!id || this.data.busy || !this.data.enabled) return;
    const msg = this._findMsg(id);
    if (!msg || !msg.previewPending || !msg.previewDeviceMac) {
      wx.showToast({ title: "预览已失效，请重新生成", icon: "none" });
      return;
    }
    const current = pageSession.capture(this);
    this.setData({ busy: true });
    api
      .request("/api/voice/confirm", { method: "POST", data: { device_mac: msg.previewDeviceMac } })
      .then((result) => {
        if (!current()) return;
        if (!result || result.status !== "ok") throw { detail: (result && result.detail) || "设备未接受确认" };
        this._patchMessage(id, { previewPending: false, previewDone: "已开始画" });
        wx.showToast({ title: "已开始画", icon: "success" });
      })
      .catch((err) => {
        if (!current()) return;
        wx.showToast({
          title: ((err && err.detail) || "确认失败").slice(0, 20),
          icon: "none",
        });
      })
      .finally(() => { if (current()) this.setData({ busy: false }); });
  },

  onCancelPreview(e) {
    const id = e.currentTarget.dataset.id;
    if (!id || this.data.busy || !this.data.enabled) return;
    const msg = this._findMsg(id);
    if (!msg || !msg.previewPending || !msg.previewDeviceMac) {
      wx.showToast({ title: "预览已失效，请重新生成", icon: "none" });
      return;
    }
    const current = pageSession.capture(this);
    this.setData({ busy: true });
    api
      .request("/api/voice/cancel", { method: "POST", data: { device_mac: msg.previewDeviceMac } })
      .then((result) => {
        if (!current()) return;
        if (!result || result.status !== "ok") throw { detail: (result && result.detail) || "设备未接受取消" };
        this._patchMessage(id, { previewPending: false, previewDone: "已取消" });
        wx.showToast({ title: "已取消", icon: "none" });
      })
      .catch((err) => {
        if (!current()) return;
        wx.showToast({
          title: ((err && err.detail) || "取消失败").slice(0, 20),
          icon: "none",
        });
      })
      .finally(() => { if (current()) this.setData({ busy: false }); });
  },

  // ---- 文字聊天（打字通道）----

  onTextInput(e) {
    this.setData({ textInput: e.detail.value });
  },

  onToggleInput() {
    // 录音过程中不能卸载触摸目标，否则 touchend 丢失会留下未结束的录音。
    if (this._recordAttempt || this.data.micAuthorizing) return;
    const mode = this.data.inputMode === "voice" ? "text" : "voice";
    this.setData({ inputMode: mode, inputFocus: mode === "text" });
  },

  onSuggestion(e) {
    if (!this.data.enabled || this.data.busy || this._recordAttempt || this.data.micAuthorizing) return;
    const text = e.currentTarget.dataset.text;
    if (typeof text !== "string" || !text.trim()) return;
    // 只填写草稿，让用户确认措辞后发送。
    this.setData({ inputMode: "text", inputFocus: true, textInput: text.slice(0, 200) });
  },

  onSendText() {
    const text = (this.data.textInput || "").trim();
    if (!text || this.data.busy || !this.data.enabled || this._recordAttempt) return;
    const current = pageSession.captureTask(this);
    const pending = this._textPending = {};
    // 上下文：最近 10 轮用户/小派对话（与 MCP HISTORY_MAX_TURNS 同口径）
    const history = this.data.messages
      .filter((m) => (m.role === "user" || m.role === "assistant") && m.content)
      .slice(-10)
      .map((m) => {
        let content = m.content;
        // 画图卡消息在历史中回填 DRAW:: 标记行：服务端解析后剥离标记，不带回的话
        // 模型从历史学到「要画也只回纯文本」，多轮后不再出绘画按钮（2026-09-23 生产复现裁决）。
        if (m.role === "assistant" && m.draw && m.draw.description) {
          content = "DRAW::" + JSON.stringify({ description: m.draw.description }) + "\n" + content;
        }
        return { role: m.role, content: content.slice(0, 500) };
      });
    this._pushMessage("user", text);
    this.setData({ textInput: "", busy: true, replyPending: true });
    api
      .request("/api/chat", {
        method: "POST",
        data: { message: text, history },
        timeout: 45000, // LLM + 两道内容安全审核；显式放宽（默认 20s 不够）
      })
      .then((res) => {
        if (!current()) return;
        const reply =
          (res && res.reply ? res.reply : "").trim() ||
          "小派这会儿没想好怎么回，换个说法试试？";
        const id = this._pushMessage("assistant", reply);
        if (res.draw_intent && res.draw_intent.description) {
          this._patchMessage(id, {
            draw: {
              description: res.draw_intent.description,
              state: "idle", // idle → generating → ready / failed
              jobId: "",
              previewUrl: "",
              lines: 0,
              sending: false,
              saved: false,
            },
          });
        }
      })
      .catch((err) => {
        if (!current()) return;
        // 聊天里的失败落成小派的一条气泡（429/400 白话透传），不弹窗打断
        this._pushMessage(
          "assistant",
          (err && err.detail) || "网络不太好，等下再找我聊吧"
        );
      })
      .finally(() => {
        if (this._textPending === pending) this._textPending = null;
        if (current()) this.setData({ busy: false, replyPending: false });
      });
  },

  // ---- 聊内画图卡 ----

  onDrawGenerate(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this._findMsg(id);
    if (!this.data.enabled || !msg || !msg.draw || !["idle", "failed"].includes(msg.draw.state)) return;
    const current = pageSession.captureTask(this);
    this._patchDraw(id, { state: "generating" });
    api
      .request("/api/draw/generate", {
        method: "POST",
        // 与 index 生成同口径：portal 侧等待上限 300s，这里同步放宽避免
        // 客户端先超时把还在生成的任务标成失败。
        timeout: 300000,
        data: { description: msg.draw.description },
      })
      .then((res) => {
        if (!current()) return;
        this._patchDraw(id, {
          state: "ready",
          jobId: res.job_id,
          previewUrl: res.preview_url,
          lines: res.lines,
        });
      })
      .catch((err) => {
        if (!current()) return;
        this._patchDraw(id, {
          state: "failed",
          error: (err && err.detail) || "生成失败，换个说法试试",
        });
      });
  },

  onDrawSend(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this._findMsg(id);
    if (!this.data.enabled || !msg || !msg.draw || !msg.draw.jobId || msg.draw.sending) return;
    const current = pageSession.capture(this);
    this._patchDraw(id, { sending: true });
    api
      .request(`/api/draw/${msg.draw.jobId}/send`, { method: "POST" })
      .then((res) => {
        if (!current()) return;
        this._patchDraw(id, { sending: false });
        if (res.status === "sent") {
          const started = /已自动开始画/.test(res.detail || "");
          wx.showModal({
            title: started ? "已开始画" : "已发送",
            content: started
              ? "丹青正在出图，无需再点确认"
              : res.detail || "已推送到丹青",
            showCancel: false,
          });
        } else {
          wx.showModal({
            title: "发送失败",
            content: res.detail || "设备未响应，请确认丹青在线",
            showCancel: false,
          });
        }
      })
      .catch((err) => {
        if (!current()) return;
        // job 可能已过 TTL：卡片退回待生成态，不留永远点不动的死按钮
        const expired = err && err.status === 400;
        this._patchDraw(id, {
          sending: false,
          state: expired ? "idle" : msg.draw.state,
          error: expired ? "预览已过期，点「重新生成」" : undefined,
        });
        if (err.status === 400 && /绑定/.test(err.detail || "")) {
          wx.navigateTo({ url: "/pages/bind/bind" });
          return;
        }
        wx.showModal({
          title: "发送失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  onDrawSave(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this._findMsg(id);
    if (!this.data.enabled || !msg || !msg.draw || !msg.draw.jobId || msg.draw.saved || msg.draw.saving) return;
    const current = pageSession.capture(this);
    this._patchDraw(id, { saving: true });
    api
      .request(`/api/draw/${msg.draw.jobId}/save`, { method: "POST", data: {} })
      .then(() => {
        if (!current()) return;
        this._patchDraw(id, { saved: true, saving: false });
        wx.showToast({ title: "已收藏，历史页能再画", icon: "success" });
      })
      .catch((err) => {
        if (!current()) return;
        this._patchDraw(id, { saving: false });
        if (err && err.status === 400) {
          // job 过期：退回待生成，别留死按钮
          this._patchDraw(id, { state: "idle", error: "预览已过期，点「重新生成」" });
        }
        wx.showToast({ title: (err && err.detail) || "收藏失败", icon: "none" });
      });
  },

  onDrawPreview(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this._findMsg(id);
    if (!msg || !msg.draw || !msg.draw.previewUrl) return;
    const current = pageSession.capture(this);
    wx.previewImage({
      urls: [msg.draw.previewUrl],
      fail: () => {
        if (!current()) return;
        // TTL 过期的死链：不靠 binderror（视口外不触发），在点开时兜底
        this._patchDraw(id, { state: "idle", error: "预览已过期，点「重新生成」" });
      },
    });
  },

  onDrawImgError(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this._findMsg(id);
    if (!msg || !msg.draw || msg.draw.state !== "ready") return;
    this._patchDraw(id, { state: "idle", error: "预览已过期，点「重新生成」" });
  },

  // ---- 语音通道 ----

  _onRecordStop(res, current, sessionId) {
    if (!current()) return;
    if (res && typeof res.duration === "number" && res.duration < 600) {
      this.setData({ voiceHint: "录音太短，请按住说完再松开" });
      return;
    }
    const path = res && res.tempFilePath;
    if (!path) {
      this.setData({ busy: false, recording: false, voiceHint: "没有录到声音，请重新按住说话" });
      wx.showToast({ title: "录音为空", icon: "none" });
      return;
    }
    const pending = this._voicePending = { current: pageSession.captureTask(this), sessionId,
      stage: "asr", label: "正在识别语音…" };
    this.setData({ busy: true, voiceStage: pending.label });
    api
      .uploadFile("/api/voice/asr", path, "file", {}, { timeout: 45000 })
      .then((asr) => {
        if (!pending.current() || this._voicePending !== pending) return;
        const text = (asr && asr.text ? asr.text : "").trim();
        if (!text) {
          this._voiceFailed(pending, { detail: "没听清，请按住再说一次" });
          return;
        }
        pending.text = text;
        pending.stage = "ready";
        pending.label = "回到小派后继续回复";
        this._continueVoiceTurn(pending);
      })
      .catch((error) => this._voiceFailed(pending, error));
  },

  _continueVoiceTurn(pending) {
    // ASR 可在后台收尾；会调设备的 turn 只在当前账号重新回屏并就绪后发出。
    if (!pending || pending.stage !== "ready" || !pending.current() ||
      this._voicePending !== pending || this._visible === false || !this.data.enabled) return;
    pending.stage = "turn";
    pending.label = "小派正在回复…";
    this.setData({ busy: true, voiceStage: pending.label });
    this._pushMessage("user", pending.text);
    api.request("/api/voice/turn", {
      method: "POST", data: { session_id: pending.sessionId, text: pending.text },
      timeout: 300000, // 语音可能走画图/写字生成，与创作页长任务保持同一等待预算。
    }).then((turn) => {
      if (!pending.current() || this._voicePending !== pending) return;
      const reply = (turn && turn.reply ? turn.reply : "").trim() || "这次没能完成回复，请换个说法试试。";
      this._pushMessage("assistant", reply, turn && turn.tool_traces, turn && turn.preview);
      this._finishVoice(pending);
    }).catch((error) => this._voiceFailed(pending, error));
  },

  _finishVoice(pending) {
    if (!pending.current() || this._voicePending !== pending) return;
    this._voicePending = null;
    this.setData({ busy: !!this._textPending, voiceStage: "" });
  },

  _voiceFailed(pending, error) {
    if (!pending.current() || this._voicePending !== pending) return;
    const status = error && error.status;
    const detail = status === 404 || status === 405 ? "语音服务暂时不可用，请稍后再试" :
      (error && error.detail) || "语音处理失败，请重新按住说话";
    this._finishVoice(pending);
    this.setData({ voiceHint: detail });
    if (this._visible !== false) wx.showToast({ title: detail.slice(0, 20), icon: "none" });
  },
});
