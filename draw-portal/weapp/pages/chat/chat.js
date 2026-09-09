const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");

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
    messages: [],
    recording: false,
    busy: false,
    scrollAnchor: "",
  },

  onLoad() {
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

  _pushMessage(role, content, toolTraces) {
    const id = this._nextMsgId();
    const msg = { id, role, content };
    const tracesText = formatToolTraces(toolTraces);
    if (tracesText) msg.toolTracesText = tracesText;
    const messages = this.data.messages.concat(msg);
    this.setData({
      messages,
      scrollAnchor: "msg-" + id,
    });
  },

  onHoldStart() {
    // 录音中不可再开；上传中 busy 才锁。勿在 start 前 busy=true，
    // 否则 .disabled{pointer-events:none} 会吞掉 touchend，松手停不了。
    if (this.data.busy || this.data.recording) return;
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
            this._pushMessage("assistant", reply, traces);
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

