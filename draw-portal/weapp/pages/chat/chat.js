// 小派聊天页：按住说话（语音 ASR→turn）+ 打字（文字 /api/chat）双通道。
// 语音链：voice/asr → voice/turn，预览确认走 voice/confirm|cancel。
// 文字链（2026-09-09）：/api/chat（内容安全+配额）→ 回复气泡；DRAW:: 意图落
// 画图卡，生成/发送/收藏复用既有 /api/draw/* 接口，不发明第二条出图链。
const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const subscribe = require("../../utils/subscribe");
const { displayVersion } = require("../../utils/version.js");

const SESSION_KEY = "voice_session_id";
const SESSION_LEN = 22;

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
      const name = t && t.name ? t.name : "?";
      const sum = t && t.summary ? t.summary : "";
      return sum ? name + " · " + sum : name;
    })
    .join("\n");
}

Page({
  data: {
    footVersion: "v0.3.6",
    messages: [],
    recording: false,
    busy: false,
    scrollAnchor: "",
    textInput: "",
  },

  onLoad() {
    this.setData({ footVersion: displayVersion("v0.3.6") });
    subscribe.prepare();
    this._sessionId = ensureSessionId();
    this._msgSeq = 0;
    this._recorder = wx.getRecorderManager();
    this._recorder.onStart(() => {
      this.setData({ recording: true });
    });
    this._recorder.onStop((res) => {
      this.setData({ recording: false });
      this._onRecordStop(res);
    });
    this._recorder.onError(() => {
      this.setData({ recording: false, busy: false });
      wx.showToast({ title: "录音失败", icon: "none" });
    });
    // 首次进页给一句小派自我介绍（含能力版图，2026-09-09 用户反馈「以为功能丢了」）
    if (!this.data.messages.length) {
      this._pushMessage(
        "assistant",
        "我是小派！按住下面按钮说话，或直接打字。我会画画、写字、教笔顺、唱歌、检查更新，也能查写字机状态、记住你告诉我的事~"
      );
    }
  },

  onShow() {
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (!ok) return;
      this._sessionId = ensureSessionId();
    });
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

  onHoldStart() {
    if (this.data.busy || this.data.recording) return;
    // 语音可能触发长链画/写；用户松手离开 app 时靠订阅消息召回
    subscribe.requestAtTap();
    try {
      this._recorder.start({
        format: "mp3",
        duration: 60000,
      });
    } catch (e) {
      wx.showToast({ title: "无法开始录音", icon: "none" });
    }
  },

  onHoldEnd() {
    if (this.data.recording) {
      this._recorder.stop();
      return;
    }
  },

  onConfirmPreview(e) {
    const id = e.currentTarget.dataset.id;
    if (!id || this.data.busy) return;
    this.setData({ busy: true });
    api
      .request("/api/voice/confirm", { method: "POST", data: {} })
      .then(() => {
        this._patchMessage(id, { previewPending: false, previewDone: "已开始画" });
        wx.showToast({ title: "已开始画", icon: "success" });
      })
      .catch((err) => {
        wx.showToast({
          title: ((err && err.detail) || "确认失败").slice(0, 20),
          icon: "none",
        });
      })
      .finally(() => this.setData({ busy: false }));
  },

  onCancelPreview(e) {
    const id = e.currentTarget.dataset.id;
    if (!id || this.data.busy) return;
    this.setData({ busy: true });
    api
      .request("/api/voice/cancel", { method: "POST", data: {} })
      .then(() => {
        this._patchMessage(id, { previewPending: false, previewDone: "已取消" });
        wx.showToast({ title: "已取消", icon: "none" });
      })
      .catch((err) => {
        wx.showToast({
          title: ((err && err.detail) || "取消失败").slice(0, 20),
          icon: "none",
        });
      })
      .finally(() => this.setData({ busy: false }));
  },

  // ---- 文字聊天（打字通道）----

  onTextInput(e) {
    this.setData({ textInput: e.detail.value });
  },

  onSendText() {
    const text = (this.data.textInput || "").trim();
    if (!text || this.data.busy) return;
    // 上下文：最近 10 轮用户/小派对话（与 MCP HISTORY_MAX_TURNS 同口径）
    const history = this.data.messages
      .filter((m) => (m.role === "user" || m.role === "assistant") && m.content)
      .slice(-10)
      .map((m) => ({ role: m.role, content: m.content.slice(0, 500) }));
    this._pushMessage("user", text);
    this.setData({ textInput: "", busy: true });
    api
      .request("/api/chat", {
        method: "POST",
        data: { message: text, history },
        timeout: 45000, // LLM + 两道内容安全审核；比默认 150s 收紧
      })
      .then((res) => {
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
        // 聊天里的失败落成小派的一条气泡（429/400 白话透传），不弹窗打断
        this._pushMessage(
          "assistant",
          (err && err.detail) || "网络不太好，等下再找我聊吧"
        );
      })
      .finally(() => this.setData({ busy: false }));
  },

  // ---- 聊内画图卡 ----

  onDrawGenerate(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this._findMsg(id);
    if (!msg || !msg.draw || msg.draw.state !== "idle") return;
    this._patchDraw(id, { state: "generating" });
    api
      .request("/api/draw/generate", {
        method: "POST",
        data: { description: msg.draw.description },
      })
      .then((res) => {
        this._patchDraw(id, {
          state: "ready",
          jobId: res.job_id,
          previewUrl: res.preview_url,
          lines: res.lines,
        });
      })
      .catch((err) => {
        this._patchDraw(id, {
          state: "failed",
          error: (err && err.detail) || "生成失败，换个说法试试",
        });
      });
  },

  onDrawSend(e) {
    const id = e.currentTarget.dataset.id;
    const msg = this._findMsg(id);
    if (!msg || !msg.draw || !msg.draw.jobId || msg.draw.sending) return;
    this._patchDraw(id, { sending: true });
    api
      .request(`/api/draw/${msg.draw.jobId}/send`, { method: "POST" })
      .then((res) => {
        this._patchDraw(id, { sending: false });
        if (res.status === "sent") {
          const started = /已自动开始画/.test(res.detail || "");
          wx.showModal({
            title: started ? "已开始画" : "已发送",
            content: started
              ? "写字机正在出图，无需再点确认"
              : res.detail || "已推送到写字机",
            showCancel: false,
          });
        } else {
          wx.showModal({
            title: "发送失败",
            content: res.detail || "设备未响应，请确认写字机在线",
            showCancel: false,
          });
        }
      })
      .catch((err) => {
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
    if (!msg || !msg.draw || !msg.draw.jobId || msg.draw.saved) return;
    api
      .request(`/api/draw/${msg.draw.jobId}/save`, { method: "POST", data: {} })
      .then(() => {
        this._patchDraw(id, { saved: true });
        wx.showToast({ title: "已收藏，历史页能再画", icon: "success" });
      })
      .catch((err) => {
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
    wx.previewImage({
      urls: [msg.draw.previewUrl],
      fail: () => {
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

  _onRecordStop(res) {
    const path = res && res.tempFilePath;
    if (!path) {
      this.setData({ busy: false, recording: false });
      wx.showToast({ title: "录音为空", icon: "none" });
      return;
    }
    this.setData({ busy: true });
    api
      .uploadFile("/api/voice/asr", path, "file")
      .then((asr) => {
        const text = (asr && asr.text ? asr.text : "").trim();
        if (!text) {
          wx.showToast({ title: "未识别到内容", icon: "none" });
          return;
        }
        this._pushMessage("user", text);
        return api
          .request("/api/voice/turn", {
            method: "POST",
            data: { session_id: this._sessionId, text },
          })
          .then((turn) => {
            const reply =
              (turn && turn.reply ? turn.reply : "").trim() || "（无回复）";
            const traces = turn && turn.tool_traces ? turn.tool_traces : [];
            const preview = turn && turn.preview ? turn.preview : null;
            this._pushMessage("assistant", reply, traces, preview);
          });
      })
      .catch((err) => {
        const status = err && err.status ? err.status : 0;
        let title = (err && err.detail) || "请求失败";
        if (status === 404 || status === 405) {
          title = "语音服务未上线";
        } else if (status === 503) {
          title = title.length > 20 ? "语音未配置" : title;
        }
        wx.showToast({
          title: title.slice(0, 20),
          icon: "none",
        });
      })
      .finally(() => {
        this.setData({ busy: false });
      });
  },
});
