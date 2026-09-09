// 小派文字聊天（2026-09-09）：portal /api/chat → MCP /internal/chat。
// 画图意图以气泡内卡片承接：生成走既有 /api/draw/generate，发送/收藏复用
// 创作页同一组接口——聊天页不发明第二条出图链。
const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");

const STORAGE_KEY = "chat_msgs";
const MAX_KEEP = 50; // 本地只留最近 50 条，卡片预览受 600s TTL 约束，过期图按加载失败处理

Page({
  data: {
    messages: [],
    input: "",
    sending: false,
    anchor: "",
  },

  onLoad() {
    this._msgId = 0;
    const saved = wx.getStorageSync(STORAGE_KEY) || [];
    const messages = saved.map((m) => {
      this._msgId = Math.max(this._msgId, m.id || 0);
      if (m.draw) {
        // 跨会话恢复：生成中的卡片退回待生成；在途发送/收藏锁全部释放
        if (m.draw.state === "generating") m.draw.state = "idle";
        m.draw.sending = false;
      }
      return m;
    });
    if (!messages.length) {
      messages.push({
        id: ++this._msgId,
        role: "pai",
        text: "我是小派，你的 AI 绘图小助手！想画什么就告诉我，比如「画一只小猫」「来一朵向日葵」~",
      });
    }
    this.setData({ messages }, () => this._scrollToEnd());
  },

  onShow() {
    phoneGate.ensurePhoneOrRedirect(api);
  },

  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  onSend() {
    const text = (this.data.input || "").trim();
    if (!text || this.data.sending) return;
    const history = this.data.messages
      .filter((m) => (m.role === "user" || m.role === "pai") && m.text)
      .slice(-10)
      .map((m) => ({
        role: m.role === "user" ? "user" : "assistant",
        content: m.text.slice(0, 500),
      }));
    const userMsg = { id: ++this._msgId, role: "user", text };
    this.setData({
      messages: [...this.data.messages, userMsg],
      input: "",
      sending: true,
    });
    this._scrollToEnd();
    api
      .request("/api/chat", {
        method: "POST",
        data: { message: text, history },
        timeout: 45000, // LLM + 两道内容安全审核；比默认 150s 收得紧，免得气泡干等
      })
      .then((res) => {
        const paiMsg = {
          id: ++this._msgId,
          role: "pai",
          text: res.reply || "小派这会儿没想好怎么回，换个说法试试？",
        };
        if (res.draw_intent && res.draw_intent.description) {
          paiMsg.draw = {
            description: res.draw_intent.description,
            state: "idle", // idle → generating → ready / failed
            jobId: "",
            previewUrl: "",
            lines: 0,
            sending: false,
            saved: false,
          };
        }
        this.setData({ messages: [...this.data.messages, paiMsg], sending: false });
        this._persist();
        this._scrollToEnd();
      })
      .catch((err) => {
        // 聊天里的失败不该弹窗打断：落成小派的一条气泡（429/400 白话透传）
        const paiMsg = {
          id: ++this._msgId,
          role: "pai",
          text: (err && err.detail) || "网络不太好，等下再找我聊吧",
        };
        this.setData({ messages: [...this.data.messages, paiMsg], sending: false });
        this._persist();
        this._scrollToEnd();
      });
  },

  onDrawGenerate(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const msg = this.data.messages[idx];
    if (!msg || !msg.draw || msg.draw.state !== "idle") return;
    this.setData({ [`messages[${idx}].draw.state`]: "generating" });
    api
      .request("/api/draw/generate", {
        method: "POST",
        data: { description: msg.draw.description },
      })
      .then((res) => {
        this.setData({
          [`messages[${idx}].draw.state`]: "ready",
          [`messages[${idx}].draw.jobId`]: res.job_id,
          [`messages[${idx}].draw.previewUrl`]: res.preview_url,
          [`messages[${idx}].draw.lines`]: res.lines,
        });
        this._persist();
        this._scrollToEnd();
      })
      .catch((err) => {
        this.setData({
          [`messages[${idx}].draw.state`]: "failed",
          [`messages[${idx}].draw.error`]: (err && err.detail) || "生成失败，换个说法试试",
        });
        this._persist();
      });
  },

  onDrawSend(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const msg = this.data.messages[idx];
    if (!msg || !msg.draw || !msg.draw.jobId || msg.draw.sending) return;
    this.setData({ [`messages[${idx}].draw.sending`]: true });
    api
      .request(`/api/draw/${msg.draw.jobId}/send`, { method: "POST" })
      .then((res) => {
        this.setData({ [`messages[${idx}].draw.sending`]: false });
        if (res.status === "sent") {
          const started = /已自动开始画/.test(res.detail || "");
          wx.showModal({
            title: started ? "已开始画" : "已发送",
            content: started
              ? "写字机正在出图，无需再在小派上点确认"
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
        this.setData({ [`messages[${idx}].draw.sending`]: false });
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
    const idx = Number(e.currentTarget.dataset.idx);
    const msg = this.data.messages[idx];
    if (!msg || !msg.draw || !msg.draw.jobId || msg.draw.saved) return;
    api
      .request(`/api/draw/${msg.draw.jobId}/save`, { method: "POST", data: {} })
      .then(() => {
        this.setData({ [`messages[${idx}].draw.saved`]: true });
        this._persist();
        wx.showToast({ title: "已收藏，去历史页能再画", icon: "success" });
      })
      .catch((err) => {
        wx.showToast({ title: (err && err.detail) || "收藏失败", icon: "none" });
      });
  },

  onDrawPreview(e) {
    const idx = Number(e.currentTarget.dataset.idx);
    const msg = this.data.messages[idx];
    if (!msg || !msg.draw || !msg.draw.previewUrl) return;
    wx.previewImage({ urls: [msg.draw.previewUrl] });
  },

  onDrawImgError(e) {
    // 预览图已过 600s TTL：卡片标失败，不留裂图
    const idx = Number(e.currentTarget.dataset.idx);
    const msg = this.data.messages[idx];
    if (!msg || !msg.draw || msg.draw.state !== "ready") return;
    this.setData({
      [`messages[${idx}].draw.state`]: "failed",
      [`messages[${idx}].draw.error`]: "预览已过期，重新生成一幅吧",
    });
  },

  _persist() {
    wx.setStorageSync(STORAGE_KEY, this.data.messages.slice(-MAX_KEEP));
  },

  _scrollToEnd() {
    this.setData({ anchor: "" }, () => {
      this.setData({ anchor: "chat-end" });
    });
  },
});
