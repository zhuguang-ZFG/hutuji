const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const pageSession = require("../../utils/page_session");
const paper = require("../../utils/paper_config");
const paperSummary = require("../../utils/paper_summary");

Page({
  data: { loading: true, loadError: "", saving: false, capable: false, online: false, form: null,
    deviceMac: "", marker: "", fitError: "", dimensions: null, paperWidth: 154, paperHeight: 220,
    presets: ["A4", "A3", "A2", "custom"], hint: "", advanced: false },
  onShow() {
    pageSession.show(this);
    const session = getApp()._sessionGeneration;
    if (this._session !== session || this._token !== api.getSession()) {
      this._session = session;
      this._token = api.getSession();
      this._before = null;
      this._pending = null;
      this.setData({ form: null, deviceMac: "", saving: false, hint: "", loadError: "" });
    }
    this._ready = false;
    const current = pageSession.capture(this);
    this.setData({ loading: true, loadError: "", saving: !!this._pending });
    return phoneGate.ensurePhoneOrRedirect(api, current).then((ok) => {
      if (!current()) return;
      if (!ok) { this.setData({ loading: false, loadError: "账号读取失败，请重试" }); return; }
      const mac = getApp().globalData.user.active_device_mac || "";
      this.setData({ deviceMac: mac });
      if (!mac) { this.setData({ loading: false, form: null, loadError: "连接写字机后即可设置纸张" }); return; }
      this._ready = true;
      if (this._pending && this._pending.mac === mac) { this.setData({ loading: false, saving: true }); return; }
      return this._load();
    });
  },
  onHide() { this._ready = false; pageSession.hide(this); },
  onUnload() { this._unloaded = true; this.onHide(); },
  onRetry() { return this.onShow(); },
  onBind() { wx.navigateTo({ url: "/pages/bind/bind" }); },
  onFirmware() { wx.navigateTo({ url: "/pages/firmware/firmware" }); },
  onAdvanced() { this.setData({ advanced: !this.data.advanced }); },
  _load() {
    const visible = pageSession.capture(this);
    const id = this._request = (this._request || 0) + 1;
    const mac = this.data.deviceMac;
    const current = () => visible() && id === this._request && this.data.deviceMac === mac;
    return api.request("/api/settings/paper?device_mac=" + encodeURIComponent(mac), { timeout: 25000 })
      .then((res) => { if (current()) this._accept(res); })
      .catch((err) => { if (current()) this.setData({ loadError: paper.requestError(err, "设备设置读取失败，请重试"), loading: false }); });
  },
  _accept(res) {
    if (!res || res.device_mac !== this.data.deviceMac || res.marker !== paper.marker(res.config)) throw new Error("设备纸张回包不匹配，请刷新");
    this._before = Object.assign({}, res.config);
    this.setData({ form: Object.assign({}, res.config), marker: res.marker, capable: res.capable === true,
      online: res.online === true, loading: false, loadError: "" });
    this._recompute();
  },
  _editable() { return this._ready && !this.data.loading && !this.data.saving && this.data.capable && this.data.online; },
  _recompute() {
    try {
      const value = paper.fromForm(this.data.form);
      const d = paper.dimensions(value);
      const scale = 220 / Math.max(d.width, d.height);
      this.setData({ fitError: "", dimensions: d, paperWidth: Math.round(d.width * scale), paperHeight: Math.round(d.height * scale) });
    } catch (err) { this.setData({ fitError: err.message, dimensions: null }); }
  },
  onPreset(e) {
    if (!this._editable()) return;
    const name = e.currentTarget.dataset.paper;
    if (!this.data.presets.includes(name)) return;
    const form = Object.assign({}, this.data.form, { paper: name });
    if (paper.PRESETS[name]) [form.width_mm, form.height_mm] = paper.PRESETS[name];
    this.setData({ form, hint: "" });
    this._recompute();
  },
  onInput(e) {
    if (!this._editable()) return;
    const field = e.currentTarget.dataset.field;
    if (!paper.NUMBERS[field]) return;
    this.setData({ ["form." + field]: e.detail.value, hint: "" });
    this._recompute();
  },
  onToggle(e) {
    if (!this._editable()) return;
    const field = e.currentTarget.dataset.field;
    if (!["landscape", "swap_xy"].includes(field)) return;
    this.setData({ ["form." + field]: e.detail.value === true, hint: "" });
    this._recompute();
  },
  onSave() {
    if (!this._editable() || this.data.fitError || !this._before || this._pending) return;
    let config;
    try { config = paper.fromForm(this.data.form); } catch (err) { this.setData({ fitError: err.message }); return; }
    const visible = pageSession.capture(this);
    if (paper.increased(config, this._before)) {
      this.setData({ saving: true });
      wx.showModal({ title: "确认实际可绘制范围", content: "将使用 X " + config.max_x_mm + " × Y " + config.max_y_mm +
        " 毫米。请确认这是从当前原点起已测量的可绘制范围，纸张大小不能代替机器行程。", confirmText: "已核对",
        success: (res) => {
          if (!visible()) return;
          this.setData({ saving: false });
          if (res.confirm) this._save(config, true);
        },
        fail: () => { if (visible()) this.setData({ saving: false }); } });
    } else return this._save(config, false);
  },
  _save(config, confirmed) {
    if (!this._editable() || this._pending) return;
    const action = this._pending = { mac: this.data.deviceMac, current: pageSession.captureTask(this) };
    const current = () => action.current() && this._pending === action;
    this.setData({ saving: true, hint: "正在保存并核对设备设置…" });
    return api.request("/api/settings/paper", { method: "POST", timeout: 45000,
      data: { device_mac: action.mac, config, expected_marker: this.data.marker, range_confirmed: confirmed } })
      .then((res) => {
        if (!current() || this.data.deviceMac !== action.mac) return;
        if (res.marker !== paper.marker(config)) throw new Error("设备未确认本次设置，请刷新");
        this._request = (this._request || 0) + 1;
        this._accept(res);
        paperSummary.invalidate();
        this.setData({ hint: "已保存，之后生成的作品使用此纸张" });
      }).catch((err) => {
        if (current() && this.data.deviceMac === action.mac) this.setData({ hint: paper.requestError(err, "保存失败，请重新读取设备设置") });
      }).finally(() => {
        if (!current()) return;
        this._pending = null;
        this.setData({ saving: false });
      });
  },
});
