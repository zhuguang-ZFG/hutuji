// 小派设置页（2026-09-12 §10.4.19）：音色 / 模型切换 + 设备验证码绑定。
// 数据面：GET/POST /api/settings/xiaopai（+ /bind），portal 转发本机 MCP
// /internal/xiaozhi/*；控制台凭据只在服务端。归属语义：切换只写本人克隆
// agent，设备经验证码绑定后生效；未绑定设备不改任何人的现网配置。
const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const pageSession = require("../../utils/page_session");

const MODEL_LABELS = {
  "xz-lite": "轻量（官方默认）",
  deepseek: "深度思考",
  qwen: "通义千问",
  doubao: "豆包",
};

Page({
  data: {
    loading: true,
    saving: false,
    voices: [],
    voiceGroups: [],
    groupIndex: 0,
    groupNames: [],
    selectedVoice: "",
    selectedVoiceName: "",
    models: [],
    selectedModel: "",
    deviceBound: false,
    agentReady: false,
    bindCode: "",
    binding: false,
    error: "",
    dirty: false,
  },

  onShow() {
    pageSession.show(this);
    const current = pageSession.capture(this);
    this.setData({ loading: true, saving: false, binding: false, bindCode: "", dirty: false,
      voices: [], voiceGroups: [], groupNames: [], models: [], selectedVoice: "",
      selectedVoiceName: "", selectedModel: "", deviceBound: false, agentReady: false, error: "" });
    phoneGate.ensurePhone(api, current).then((ok) => {
      if (!current()) return;
      if (ok) this.refresh();
      else if (ok === false) this.setData({ loading: false, error: "账号信息读取失败，请重试" });
      else this.setData({ loading: false, error: "未登录或账号未读取：可先浏览，授权登录请到「我的」页" });
    });
  },

  onHide() { pageSession.hide(this); },
  onUnload() { this.onHide(); },

  refresh() {
    const current = pageSession.capture(this);
    this.setData({ loading: true, error: "" });
    api
      .request("/api/settings/xiaopai")
      .then((data) => {
        if (!current()) return;
        const groups = this._groupVoices(data.voices || []);
        const groupNames = groups.map((g) => g.label);
        const currentVoice = data.tts_voice || "";
        let selectedVoiceName = "";
        groups.forEach((g) => {
          g.items.forEach((v) => {
            if (v.voice_id === currentVoice) selectedVoiceName = v.voice_name;
          });
        });
        this.setData({
          loading: false,
          voices: data.voices || [],
          voiceGroups: groups,
          groupNames,
          groupIndex: Math.max(0, groups.findIndex((g) => g.lang === "zh")),
          selectedVoice: currentVoice,
          selectedVoiceName,
          models: (data.models || []).map((m) => ({
            name: m.name,
            description: m.description,
            label: MODEL_LABELS[m.name] || m.name,
          })),
          selectedModel: data.llm_model || "",
          deviceBound: !!data.device_bound,
          agentReady: !!data.agent_ready,
          dirty: false,
        });
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({
          loading: false,
          error: (err && err.detail) || "设置加载失败",
        });
      });
  },

  _groupVoices(voices) {
    const LANG_LABELS = {
      zh: "中文（普通话）",
      yue: "粤语",
      shanghai: "上海话",
      en: "英语",
      ja: "日语",
    };
    const order = {};
    voices.forEach((v, i) => {
      if (order[v.language] === undefined) order[v.language] = i;
    });
    return Object.keys(order)
      .sort((a, b) => order[a] - order[b])
      .map((lang) => ({
        lang,
        label: LANG_LABELS[lang] || lang,
        items: voices.filter((v) => v.language === lang),
      }));
  },

  onGroupChange(e) {
    this.setData({ groupIndex: Number(e.detail.value) || 0 });
  },

  onPickVoice(e) {
    if (this.data.loading || this.data.saving) return;
    const vid = e.currentTarget.dataset.vid;
    const vname = e.currentTarget.dataset.vname;
    if (vid === this.data.selectedVoice) return;
    this.setData({ selectedVoice: vid, selectedVoiceName: vname, dirty: true });
  },

  onPickModel(e) {
    if (this.data.loading || this.data.saving) return;
    const name = e.currentTarget.dataset.name;
    if (name === this.data.selectedModel) return;
    this.setData({ selectedModel: name, dirty: true });
  },

  onCodeInput(e) {
    if (this.data.binding) return;
    const digits = String(e.detail.value || "").replace(/\D/g, "").slice(0, 6);
    this.setData({ bindCode: digits });
  },

  save() {
    if (this.data.loading || this.data.saving || !this.data.dirty) return;
    const current = pageSession.capture(this);
    const patch = {};
    if (this.data.selectedVoice) patch.tts_voice = this.data.selectedVoice;
    if (this.data.selectedModel) patch.llm_model = this.data.selectedModel;
    if (!Object.keys(patch).length) {
      wx.showToast({ title: "先选音色或模型", icon: "none" });
      return;
    }
    this.setData({ saving: true });
    api
      .request("/api/settings/xiaopai", { method: "POST", data: patch })
      .then(() => {
        if (!current()) return;
        this.setData({ saving: false, dirty: false });
        wx.showToast({ title: "已保存，下次对话生效", icon: "none", duration: 2200 });
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ saving: false });
        wx.showToast({ title: (err && err.detail) || "保存失败", icon: "none" });
        if (err && err.status === 503) this.refresh();
      });
  },

  bindDevice() {
    if (this.data.binding) return;
    const current = pageSession.capture(this);
    if (this.data.bindCode.length !== 6) {
      wx.showToast({ title: "请输入设备上的 6 位验证码", icon: "none" });
      return;
    }
    this.setData({ binding: true });
    api
      .request("/api/settings/xiaopai/bind", {
        method: "POST",
        data: { code: this.data.bindCode },
      })
      .then(() => {
        if (!current()) return;
        this.setData({ binding: false, deviceBound: true, bindCode: "" });
        wx.showToast({ title: "设备已绑定", icon: "none" });
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ binding: false });
        wx.showToast({ title: (err && err.detail) || "绑定失败", icon: "none" });
      });
  },

  unbindHint() {
    wx.showModal({
      title: "如何获取验证码",
      content:
        "在设备屏幕进入 设置→设备验证码，读取 6 位数字输入此处。绑定后这台设备会使用你在此页选择的音色与模型。",
      showCancel: false,
    });
  },
});
