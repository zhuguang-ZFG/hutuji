const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");
const subscribe = require("../../utils/subscribe");
const pageSession = require("../../utils/page_session");
const paperSummary = require("../../utils/paper_summary");
const feedback = require("../../utils/operation_feedback");

// 精修等待窗口（2026-10-08 改为绝对时限）：从该 job 第一次开始等待起算 5 分钟，
// 覆盖免费层排队（常 >30s）与「约 10 秒～2 分钟」的产品承诺；onHide 停链、
// 回屏 resumeJobState 续轮只续剩余时间，切页不能把等待无限延长。
const REFINE_WINDOW_MS = 300000;

Page({
  onPaperSettings() { wx.navigateTo({ url: "/pages/paper/paper" }); },
  data: {
    mode: "draw",
    description: "",
    writeText: "",
    fonts: [],
    fontNames: [],
    fontIndex: 0,
    teachText: "",
    sizeMm: 12,
    lineSpacing: 1.6,
    pageIndex: 0,
    generating: false,
    layouting: false,
    aiWriting: false,
    // AI 写文（generate/polish）成功后置位，驱动预览卡「AI 生成」标识
    //（《人工智能生成合成内容标识办法》2025-09-01 施行，量产合规项）
    aiTextUsed: false,
    aiSuggestion: "",
    canUndoAiText: false,
    teaching: false,
    sending: false,
    saving: false,
    uploading: false,
    job: null,
    shareToken: null,
    shareLoading: false,
    shareError: "",
    quotaLeft: null,
    binding: null,
    paperSummary: { title: "纸张与方向", detail: "正在读取当前设备…", state: "loading" },
  },

  onShow() {
    pageSession.show(this);
    paperSummary.reset(this);
    const app = getApp();
    if (this._indexSession !== app._sessionGeneration) {
      this._indexSession = app._sessionGeneration;
      this._filePick = null;
      this._pendingJob = null;
      this._pendingJobExtra = null;
      this._pendingMessage = null;
      this.stopRefinePolling();
      this._refineDeadline = null;
      this._writeToken = (this._writeToken || 0) + 1;
      this._shareToken = null;
      this._aiBackup = null;
      this._writeEditVersion = 0;
      this.setData({ job: null, binding: null, quotaLeft: null, shareToken: null, shareLoading: false, shareError: "",
        description: "", writeText: "", teachText: "", aiTextUsed: false, aiSuggestion: "", canUndoAiText: false,
        generating: false, layouting: false, aiWriting: false, teaching: false,
        uploading: false, sending: false, saving: false });
    }
    const current = pageSession.capture(this);
    phoneGate.ensurePhone(api, current).then((me) => {
      if (!current()) return;
      if (!me) {
        this.setData({ paperSummary: { title: "纸张与方向", detail: "账号信息未读取，点此查看", state: "unavailable" } });
        return;
      }
      // 闸刚查过 /me，同一 onShow 内复用，不再重复请求。
      this.refreshStatus(me);
      this._consumePickedFile();
      // 交接结果先完成账号闸；退出登录时全局交接槽也会清空。
      if (app.globalData.pendingAdopt) {
        const data = app.globalData.pendingAdopt;
        app.globalData.pendingAdopt = null;
        this.adoptJob(data);
      } else if (this._pendingJob) {
        const data = this._pendingJob;
        const extra = this._pendingJobExtra;
        this._pendingJob = null;
        this._pendingJobExtra = null;
        this.adoptJob(data, extra || {});
      } else this.resumeJobState();
      this._prefetchShareToken(this.selectedJobId());
      if (this._pendingMessage) {
        wx.showModal(this._pendingMessage);
        this._pendingMessage = null;
      }
    });
  },

  // 精修回屏恢复（2026-09-09）：onHide 停链后 job 仍挂在 data 上，回到页面时
  // 一次性补查 job 状态——pending 续轮、ready 上屏、failed 标注；不再「切走就丢」。
  // 续轮受该 job 的绝对时限约束（见 startRefinePolling），窗口已过即判 timeout。
  resumeJobState() {
    const job = this.data.job;
    if (!job || !job.jobId) return;
    if (job.refined || job.refineFailed === "failed" || job.refining) return;
    if (this._refineResume && this._refineResume.current()) return;
    const visible = pageSession.capture(this);
    const current = () => visible() && this.data.job && this.data.job.jobId === job.jobId;
    const flight = this._refineResume = { current };
    this.setData({ "job.refineChecking": true });
    api
      .request("/api/draw/jobs/" + job.jobId)
      .then((res) => {
        if (!current()) return;
        const refined = res && res.refined;
        if (!refined) return;
        if (this._applyRefineState(refined) === "pending") {
          this.setData({ "job.refining": true, "job.refineFailed": false, "job.refineOffline": false });
          this.startRefinePolling(job.jobId);
        }
      })
      .catch(() => {}) // 补屏失败不打扰，下次 onShow 或主动查看再试。
      .finally(() => {
        if (this._refineResume === flight) this._refineResume = null;
        if (current()) this.setData({ "job.refineChecking": false });
      });
  },
  onRetryRefined() { return this.resumeJobState(); },

  // 网络失败时的只读核对：只 GET 既有 job 状态，不重新上传、不重新生成；
  // 轮询链照常继续（仍受绝对时限约束），核对结果走同一落屏入口。
  onRecheckRefined() {
    const job = this.data.job;
    if (!job || !job.jobId || job.refineChecking) return;
    const jobId = job.jobId;
    const visible = pageSession.capture(this);
    const current = () => visible() && this.data.job && this.data.job.jobId === jobId;
    this.setData({ "job.refineChecking": true });
    api
      .request("/api/draw/jobs/" + jobId)
      .then((res) => {
        if (!current()) return;
        const refined = res && res.refined;
        if (refined && this._applyRefineState(refined) === "pending") this.setData({ "job.refineOffline": false });
      })
      .catch(() => {}) // 仍失败：提示保留、不弹窗，轮询链自己继续
      .finally(() => { if (current()) this.setData({ "job.refineChecking": false }); });
  },

  // 精修回包落屏（回屏补查/轮询/只读核对共用）。ready 必须带 job_id——收藏、分享、
  // 发送都用该 id，畸形包不能变成「可发送」的精修版，一律按仍在等待处理。
  _applyRefineState(refined) {
    if (refined && refined.status === "ready" && refined.job_id) {
      // 就绪后默认切到精修版，用户仍可点缩略图切回线稿版
      this.stopRefinePolling();
      this.setData({
        "job.refined": { jobId: refined.job_id, previewUrl: refined.preview_url, gcodeUrl: refined.gcode_url },
        "job.useRefined": true,
        "job.refining": false,
        "job.refineFailed": false,
        "job.refineOffline": false,
      });
      this._prefetchShareToken(this.selectedJobId());
      wx.showToast({ title: "AI 精修版好了", icon: "success" });
      return "ready";
    }
    if (refined && refined.status === "failed") {
      // 失败不弹窗打扰：回落 Canny 草图版并标注（A 方案唯一草图出口）
      this.stopRefinePolling();
      this.setData({ "job.refining": false, "job.refineFailed": "failed", "job.refineOffline": false });
      return "failed";
    }
    return "pending";
  },


  onLoad() {
    subscribe.prepare();
    // 轮询句柄与作废令牌挂在页面实例上：不放进 data，避免无关字段触发渲染
    this._refineTimer = null;
    this._refineToken = 0;
    this._writeToken = 0;
  },

  onHide() {
    pageSession.hide(this);
    this.setData({ sending: false, saving: false, shareLoading: false });
    // 页面藏起来就停掉跟踪；链已停而 UI 还挂着"精修中"会变成永久等待的假象，
    // 一并退出等待态，回到页面由 resumeJobState 补查结果。
    this.stopRefinePolling();
    if (this.data.job && this.data.job.refining) {
      this.setData({ "job.refining": false });
    }
  },

  onUnload() {
    this._unloaded = true;
    this._filePick = null;
    this.onHide();
  },

  // 字库列表懒加载（§10.4.12 字体选择器）：首次进写字模式拉一次；
  // 失败静默——选择器隐藏，提交不带 font 字段走默认字库。
  loadFonts() {
    if (this.data.fonts.length || this._fontsLoading) return;
    this._fontsLoading = true;
    api
      .request("/api/fonts")
      .then((res) => {
        this._fontsLoading = false;
        if (res && res.fonts && res.fonts.length) {
          // 默认项不提交 font，让偏好与缺席回退继续由服务端决定。
          const fonts = [{ id: "", name: "默认字体" }].concat(res.fonts);
          this.setData({
            fonts,
            fontNames: fonts.map((f) => f.name),
            fontIndex: 0,
          });
        }
      })
      .catch(() => {
        this._fontsLoading = false;
      });
  },

  onFontChange(e) {
    this.setData({ fontIndex: Number(e.detail.value) || 0 });
  },

  onModeTap(e) {
    const mode = e.currentTarget.dataset.mode;
    if (!["draw", "write", "teach"].includes(mode) || mode === this.data.mode) return;
    if (this._creationBusy()) { wx.showToast({ title: "当前任务完成后再切换", icon: "none" }); return; }
    this._filePick = null;
    this._pendingJob = null;
    this._pendingJobExtra = null;
    this._pendingMessage = null;
    this.stopRefinePolling();
    // 作废在途排版/文档上传：回调靠令牌自灭，避免切模式后 adoptJob 把旧预览写回
    this._writeToken = (this._writeToken || 0) + 1;
    if (mode === "write") this.loadFonts();
    this.setData({
      mode,
      job: null,
      shareToken: null,
      generating: false,
      pageIndex: 0,
      layouting: false,
      aiWriting: false,
      teaching: false,
      uploading: false,
    });
  },

  onDescInput(e) {
    this.setData({ description: e.detail.value });
  },
  onWriteInput(e) {
    this._writeEditVersion = (this._writeEditVersion || 0) + 1;
    // 修改 AI 原文仍保留来源；清空全文后再手写才开始一份新稿。
    this.setData({ writeText: e.detail.value, aiTextUsed: this.data.aiTextUsed && !!e.detail.value.trim() });
  },

  onTeachInput(e) {
    this.setData({ teachText: e.detail.value });
  },

  _creationBusy() {
    return this.data.generating || this.data.layouting || this.data.aiWriting || this.data.teaching || this.data.uploading;
  },

  onExample(e) {
    if (this._creationBusy()) return;
    const text = String(e.currentTarget.dataset.text || "");
    const field = this.data.mode === "write" ? "writeText" : this.data.mode === "teach" ? "teachText" : "description";
    if (field === "writeText") this.onWriteInput({ detail: { value: text } });
    else this.setData({ [field]: text });
  },

  _applyAiText(text) {
    this._aiBackup = { text: this.data.writeText, aiTextUsed: this.data.aiTextUsed, after: text };
    this._writeEditVersion = (this._writeEditVersion || 0) + 1;
    this.setData({ writeText: text, aiTextUsed: true, aiSuggestion: "", canUndoAiText: true });
  },

  onApplyAiSuggestion() {
    if (!this._creationBusy() && this.data.aiSuggestion) this._applyAiText(this.data.aiSuggestion);
  },

  onUndoAiText() {
    const backup = this._aiBackup;
    if (!backup || this._creationBusy()) return;
    const current = pageSession.capture(this);
    const revision = this._writeEditVersion;
    const restore = () => {
      if (!current() || revision !== this._writeEditVersion || this._aiBackup !== backup) return;
      this._aiBackup = null;
      this._writeEditVersion = (this._writeEditVersion || 0) + 1;
      this.setData({ writeText: backup.text, aiTextUsed: backup.aiTextUsed, canUndoAiText: false, aiSuggestion: "" });
    };
    if (this.data.writeText === backup.after) restore();
    else wx.showModal({ title: "恢复AI处理前的文字？", content: "恢复会替换你之后编辑的内容。", confirmText: "恢复原稿",
      success: (res) => { if (res.confirm) restore(); } });
  },

  bumpSize(e) {
    const delta = Number(e.currentTarget.dataset.delta);
    const v = Math.min(40, Math.max(2, Math.round((this.data.sizeMm + delta) * 10) / 10));
    this.setData({ sizeMm: v });
  },

  bumpLine(e) {
    const delta = Number(e.currentTarget.dataset.delta);
    const v = Math.min(3, Math.max(1, Math.round((this.data.lineSpacing + delta) * 10) / 10));
    this.setData({ lineSpacing: v });
  },

  // me 在场时直接用（onShow 的手机号闸刚查过），否则自行请求。
  refreshStatus(me) {
    const app = getApp();
    const current = pageSession.capture(this);
    const apply = (me) => {
      if (!current()) return;
      app.globalData.user = me;
      this.setData({ binding: me.binding || null });
      if (me.quota && typeof me.quota.remaining === "number") this.setData({ quotaLeft: me.quota.remaining });
      return paperSummary.refresh(this);
    };
    if (me) return Promise.resolve(apply(me));
    return api.request("/api/auth/me").then(apply).catch(() => { if (current()) this.setData({ binding: null,
      paperSummary: { title: "纸张与方向", detail: "设备信息未读取，点此查看", state: "unavailable" } }); });
  },

  onGenerate() {
    if (this._creationBusy() || !this.data.description.trim()) return;
    const task = pageSession.captureTask(this);
    const token = this._writeToken;
    const current = () => task() && token === this._writeToken;
    subscribe.requestAtTap();
    this.setData({ generating: true });
    api
      .request("/api/draw/generate", {
        method: "POST",
        // 与 portal DRAW_PORTAL_GENERATE_TIMEOUT_SECONDS(300s) 对齐（2026-09-12
        // 忙批）：旧 150s 会在云端仍在生成时先超时弹「生成失败」，随后图又出现。
        timeout: feedback.CREATION_TIMEOUT_MS,
        data: { description: this.data.description },
      })
      .then((res) => {
        if (!current()) return;
        this.setData({ generating: false });
        this.adoptJob(res);
        if (this.data.quotaLeft !== null && this.data.quotaLeft > 0) {
          this.setData({ quotaLeft: this.data.quotaLeft - 1 });
        }
        if (this._visible !== false) wx.showToast({ title: "生成完成", icon: "success" });
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ generating: false });
        this._notifyCreation({
          title: err.status === 429 ? "通道忙" : "生成失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  onTeachGenerate() {
    if (this._creationBusy() || !this.data.teachText.trim()) return;
    const token = this._writeToken;
    const current = pageSession.captureTask(this);
    subscribe.requestAtTap();
    this.setData({ teaching: true });
    api
      .request("/api/draw/teach", {
        method: "POST",
        timeout: feedback.CREATION_TIMEOUT_MS,
        data: { text: this.data.teachText },
      })
      .then((res) => {
        if (!current() || token !== this._writeToken) return;
        this.setData({ teaching: false });
        this.adoptJob(res);
        if (this._visible !== false) wx.showToast({ title: "生成完成", icon: "success" });
      })
      .catch((err) => {
        if (!current() || token !== this._writeToken) return;
        this.setData({ teaching: false });
        this._notifyCreation({
          title: "生成失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  onAiGenerate() {
    this._runWriteAi("generate");
  },

  onAiPolish() {
    this._runWriteAi("polish");
  },

  _runWriteAi(mode) {
    if (this._creationBusy() || !this.data.writeText.trim()) return;
    const token = this._writeToken;
    const revision = this._writeEditVersion || 0;
    const current = pageSession.captureTask(this);
    const payload =
      mode === "generate"
        ? { mode: "generate", prompt: this.data.writeText }
        : { mode: "polish", text: this.data.writeText };
    this.setData({ aiWriting: true });
    api
      .request("/api/draw/write/ai", { method: "POST", data: payload, timeout: feedback.CREATION_TIMEOUT_MS })
      .then((res) => {
        if (!current() || token !== this._writeToken) return;
        const text = res && res.text;
        if (typeof text !== "string" || !text.trim()) throw { detail: "暂未生成有效文字，原稿已保留" };
        this.setData({ aiWriting: false });
        if ((this._writeEditVersion || 0) !== revision) {
          this.setData({ aiSuggestion: text });
          if (this._visible !== false) wx.showToast({ title: "新稿已备好，原文保留", icon: "none" });
          return;
        }
        this._applyAiText(text);
        if (this._visible !== false) wx.showToast({
          title: mode === "generate" ? "已生成，可再改" : "已润色，可再改",
          icon: "success",
        });
      })
      .catch((err) => {
        if (!current() || token !== this._writeToken) return;
        this.setData({ aiWriting: false });
        this._notifyCreation({
          title: mode === "generate" ? "生成失败" : "润色失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  onLayoutPreview() {
    if (this._creationBusy() || !this.data.writeText.trim()) return;
    const token = this._writeToken;
    const current = pageSession.captureTask(this);
    const data = {
      text: this.data.writeText,
      size_mm: this.data.sizeMm,
      line_spacing: this.data.lineSpacing,
    };
    const selectedFont = this.data.fonts[this.data.fontIndex];
    if (selectedFont && selectedFont.id) data.font = selectedFont.id;
    this.setData({ layouting: true });
    api
      .request("/api/draw/write", {
        method: "POST",
        timeout: feedback.CREATION_TIMEOUT_MS,
        data,
      })
      .then((res) => {
        if (!current() || token !== this._writeToken) return;
        this.setData({ layouting: false });
        this.adoptJob(res);
        if (this._visible !== false) wx.showToast({ title: "排版完成", icon: "success" });
      })
      .catch((err) => {
        if (!current() || token !== this._writeToken) return;
        this.setData({ layouting: false });
        this._notifyCreation({
          title: "排版失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  currentPreviewUrl() {
    const job = this.data.job;
    if (!job) return "";
    if (job.pages && job.pages.length) {
      const p = job.pages[this.data.pageIndex] || job.pages[0];
      return p.preview_url;
    }
    if (job.useRefined && job.refined) return job.refined.previewUrl;
    return job.previewUrl;
  },

  onPreviewTap() {
    const url = this.currentPreviewUrl();
    if (!url) return;
    const pages = this.data.job.pages;
    const urls = pages && pages.length ? pages.map((page) => api.mediaUrl(page.preview_url)) : [api.mediaUrl(url)];
    wx.previewImage({ current: api.mediaUrl(url), urls });
  },

  // 对照原图（2026-10-08）：本机临时文件在前、当前选中版本（草图/精修）预览在后，
  // 左右滑动比较；只有拍照/相册上传的 job 才有原图。
  onComparePhoto() {
    const job = this.data.job;
    const result = this.currentPreviewUrl();
    if (!job || !job.photoSource || !result) return;
    wx.previewImage({ current: job.photoSource, urls: [job.photoSource, api.mediaUrl(result)] });
  },

  selectedJobId() {
    const job = this.data.job;
    return job ? (job.useRefined && job.refined ? job.refined.jobId : job.jobId) : "";
  },

  onSelectVersion(e) {
    const job = this.data.job;
    if (!job || !job.refined || this.data.sending || this.data.saving) return;
    const useRefined = e.currentTarget.dataset.version === "refined";
    if (useRefined === !!job.useRefined) return;
    this.setData({ "job.useRefined": useRefined, shareToken: null });
    this._prefetchShareToken(this.selectedJobId());
  },

  onPrevPage() {
    if (this.data.pageIndex <= 0) return;
    this.setData({ pageIndex: this.data.pageIndex - 1 });
  },

  onNextPage() {
    const pages = this.data.job && this.data.job.pages;
    if (!pages || this.data.pageIndex >= pages.length - 1) return;
    this.setData({ pageIndex: this.data.pageIndex + 1 });
  },

  onSend() {
    const job = this.data.job;
    if (!job || this._creationBusy()) return;
    // 发预览卡上选中的版本：精修就绪且选中精修版时发精修 job
    // （后端眼里精修 job 就是普通 job，走同一个 send 接口）
    const jobId = job.useRefined && job.refined ? job.refined.jobId : job.jobId;
    this.sendJob(jobId);
  },

  onSave() {
    const job = this.data.job;
    if (!job || job.refining || this.data.saving || this._creationBusy()) return;
    const jobId = job.useRefined && job.refined ? job.refined.jobId : job.jobId;
    const current = pageSession.capture(this);
    this.setData({ saving: true });
    api
      .request(`/api/draw/${jobId}/save`, { method: "POST", data: {} })
      .then((res) => {
        if (!current()) return;
        this.setData({ saving: false });
        if (res.status === "saved") {
          wx.showToast({ title: "已收藏", icon: "success" });
        } else {
          wx.showToast({ title: res.detail || "收藏失败", icon: "none" });
        }
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ saving: false });
        wx.showModal({
          title: "收藏失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  sendJob(jobId) {
    if (this.data.sending) return;
    const current = pageSession.capture(this);
    this.setData({ sending: true });
    api
      .request(`/api/draw/${jobId}/send`, { method: "POST", timeout: feedback.SEND_TIMEOUT_MS })
      .then((res) => {
        if (!current()) return;
        this.setData({ sending: false });
        feedback.showSendResult(res, current);
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ sending: false });
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

  _notifyCreation(message) {
    if (this._visible === false) this._pendingMessage = message;
    else wx.showModal(message);
  },

  // 上传/生成响应统一落 job；隐藏页只保留结果，回屏后再预取分享和跟踪精修。
  // extra.photoSource：拍照/相册上传的本机临时文件路径，供「对照原图」使用；其它入口为空。
  adoptJob(data, extra = {}) {
    if (this._visible === false) { this._pendingJob = data; this._pendingJobExtra = extra; return; }
    // 新 job 换掉旧 job：旧轮询链必须停掉，否则两链并存、旧链还会改写新 job 的状态；
    // 等待窗口随 job 重新起算
    this.stopRefinePolling();
    this._refineDeadline = null;
    const job = {
      jobId: data.job_id,
      previewUrl: data.preview_url,
      gcodeUrl: data.gcode_url,
      lines: data.lines,
      refining: false,
      refined: null,
      refineFailed: false,
      refineOffline: false,
      photoSource: extra.photoSource || "",
      pages: data.pages || null,
      pageCount: data.page_count || null,
      truncated: !!data.truncated,
      inputTruncated: !!data.input_truncated,
      missingChars: data.missing_chars || [],
      missingCharsText: (data.missing_chars || []).slice(0, 10).join(""),
      charsTaught: data.chars_taught || null,
    };
    if (data.refined && data.refined.status === "ready" && data.refined.job_id) {
      job.refined = { jobId: data.refined.job_id, previewUrl: data.refined.preview_url, gcodeUrl: data.refined.gcode_url };
      job.useRefined = true;
    } else if (data.refined && (data.refined.status === "pending" || data.refined.status === "ready")) {
      // pending，或缺 job_id 的畸形 ready：都只能继续等（受绝对时限），不能当可发送结果
      job.refining = true;
    } else if (data.refined && data.refined.status === "failed") {
      job.refineFailed = "failed";
    }
    this.setData({ job, pageIndex: 0, shareToken: null });
    if (job.refining) {
      this.startRefinePolling(job.jobId);
    }
    this._prefetchShareToken(this.selectedJobId());
  },

  // 分享入口必须同步返回；按当前作品预备，回屏补齐中断请求，成功结果同账号内复用。
  _prefetchShareToken(jobId) {
    if (this._shareJobId === jobId && this._shareToken && this._shareOwner && this._shareOwner()) {
      this.setData({ shareToken: this._shareToken, shareLoading: false, shareError: "" });
      return;
    }
    if (this._shareFlight && this._shareFlight.jobId === jobId && this._shareFlight.current()) return;
    this._shareToken = null;
    this._shareJobId = jobId;
    this.setData({ shareToken: null, shareLoading: false, shareError: "" });
    if (!jobId || this._visible === false) return;
    const visible = pageSession.capture(this);
    const flight = this._shareFlight = { jobId, current: () => visible() &&
      this._shareFlight === flight && this.selectedJobId() === jobId };
    this.setData({ shareLoading: true });
    api
      .request(`/api/draw/${jobId}/share`, { method: "POST" })
      .then((res) => {
        if (!flight.current()) return;
        this._shareToken = (res && res.share_token) || null;
        this._shareOwner = pageSession.captureTask(this);
        this.setData({ shareToken: this._shareToken, shareError: this._shareToken ? "" : "分享暂未准备好，请重试" });
      })
      .catch(() => { if (flight.current()) this.setData({ shareError: "分享暂未准备好，请检查网络后重试" }); })
      .finally(() => {
        if (flight.current()) this.setData({ shareLoading: false });
        if (this._shareFlight === flight) this._shareFlight = null;
      });
  },
  onRetryShare() { this._prefetchShareToken(this.selectedJobId()); },

  onShareAppMessage() {
    const job = this.data.job;
    if (job && this.currentPreviewUrl() && this._shareToken && this._shareJobId === this.selectedJobId() &&
        this._shareOwner && this._shareOwner()) {
      return {
        title: "看看我刚让丹青画的作品",
        path: `/pages/share/share?share=${this._shareToken}`,
        imageUrl: api.mediaUrl(`/api/draw/share/${this._shareToken}/preview.png`),
      };
    }
    return { title: "动力巢科技", path: "/pages/index/index" };
  },

  // 精修轮询：退避 5s→10s 由 refineDelayMs 定；终止条件是该 job 的绝对截止时刻
  // （REFINE_WINDOW_MS，首次开始等待起算），不是本条链的轮次——onHide 停链、回屏
  // resumeJobState 续轮时只续剩余时间。用 setTimeout 链不用 setInterval：请求
  // 在途耗时不定，链式保证上一轮结束才排下一轮，绝不会并发轮询。
  startRefinePolling(jobId) {
    this.stopRefinePolling();
    const token = this._refineToken;
    const current = pageSession.capture(this);
    const deadline = this._refineDeadlineFor(jobId);
    let round = 0;
    // 终态收口：停链并退出"精修中"，各分支只补自己的增量字段
    const finish = (patch) => {
      if (!current() || token !== this._refineToken) return;
      this.stopRefinePolling();
      this.setData(Object.assign({ "job.refining": false, "job.refineOffline": false }, patch));
    };
    // 下一轮：窗口已尽即 timeout（回落草图，但说明 AI 还可能好）；最后一轮贴着截止时刻排
    const next = () => {
      clearTimeout(this._refineTimer);
      const left = deadline - Date.now();
      if (left <= 0) { finish({ "job.refineFailed": "timeout" }); return; }
      this._refineTimer = setTimeout(poll, Math.min(this.refineDelayMs(round), left));
    };
    const poll = () => {
      if (!current() || token !== this._refineToken) return;
      const left = deadline - Date.now();
      if (left <= 0) { finish({ "job.refineFailed": "timeout" }); return; }
      // 请求可能一直无应答：等待中的计时器守住原截止点，不把网络超时叠加到窗口上。
      this._refineTimer = setTimeout(() => finish({ "job.refineFailed": "timeout" }), left);
      round += 1;
      api
        .request("/api/draw/jobs/" + jobId)
        .then((res) => {
          // 令牌变了说明链已作废（换了新任务或离开页面），丢弃过期响应
          if (!current() || token !== this._refineToken) return;
          if (Date.now() >= deadline) { finish({ "job.refineFailed": "timeout" }); return; }
          if (this._applyRefineState(res && res.refined) !== "pending") return;
          // pending 或畸形回包：网络已通，清掉离线提示后继续等
          if (this.data.job.refineOffline) this.setData({ "job.refineOffline": false });
          next();
        })
        .catch(() => {
          if (!current() || token !== this._refineToken) return;
          // 网络抖动不算精修失败：给可见提示和只读核对入口，链照样续到绝对时限
          if (!this.data.job.refineOffline) this.setData({ "job.refineOffline": true });
          next();
        });
    };
    next();
  },

  // 精修轮询退避（2026-09-11）：前 6 轮 5s，之后 10s——免费层排队常 >30s，
  // 恒频 5s 拉满请求量换不来提前量。
  refineDelayMs(round) {
    return round <= 6 ? 5000 : 10000;
  },

  refineWindowMs() { return REFINE_WINDOW_MS; },

  // 同一 job 的绝对截止时刻只在第一次开始等待时起算；换 job（adoptJob）或换账号时清掉。
  _refineDeadlineFor(jobId) {
    if (!this._refineDeadline || this._refineDeadline.jobId !== jobId) {
      this._refineDeadline = { jobId, at: Date.now() + REFINE_WINDOW_MS };
    }
    return this._refineDeadline.at;
  },

  stopRefinePolling() {
    // 令牌单调递增：请求在途时 clearTimeout 够不着那条链，靠令牌让它的回调自灭
    this._refineToken = (this._refineToken || 0) + 1;
    if (this._refineTimer) {
      clearTimeout(this._refineTimer);
      this._refineTimer = null;
    }
  },

  onPickFile() {
    this._chooseFile(false);
  },

  // 拍照/相册绘图：chooseMedia → 压缩 → 走 /api/draw/upload（同传图链）
  onTakePhoto(e) {
    this._chooseFile(true, e.currentTarget.dataset.source === "album" ? "album" : "camera");
  },

  _chooseFile(photo, source) {
    if (this._creationBusy() || this._filePick) return;
    const pick = this._filePick = { photo, session: getApp()._sessionGeneration };
    const current = () => this._filePick === pick && pick.session === getApp()._sessionGeneration;
    const callbacks = {
      success: (res) => {
        if (!current()) return;
        pick.file = res.tempFiles && res.tempFiles[0];
        if (!pick.file) { this._filePick = null; return; }
        this._consumePickedFile();
      },
      fail: () => { if (current()) this._filePick = null; },
    };
    subscribe.requestAtTap();
    if (photo) wx.chooseMedia(Object.assign({ count: 1, mediaType: ["image"],
      sourceType: [source], sizeType: ["compressed"] }, callbacks));
    else wx.chooseMessageFile(Object.assign({ count: 1, type: "file" }, callbacks));
  },

  _consumePickedFile() {
    const pick = this._filePick;
    if (!pick || !pick.file || this._visible === false) return;
    this._filePick = null;
    if (pick.session !== getApp()._sessionGeneration) return;
    // 系统选择器会触发 hide/show；选择结果等页面回来再接手，上传使用新展示代次。
    // 切模式、卸载、退出登录会清掉选择归属，不能借该例外串到另一份稿件或账号。
    const task = pageSession.captureTask(this);
    const token = this._writeToken;
    const current = () => task() && token === this._writeToken;
    const file = pick.file;
    if (file.size > (pick.photo ? 10 : 4) * 1024 * 1024) {
      wx.showToast({ title: pick.photo ? "照片过大，请重拍" : "文件超过 4MB", icon: "none" });
      return;
    }
    const formData = !pick.photo && this.data.mode === "write"
      ? { size_mm: String(this.data.sizeMm), line_spacing: String(this.data.lineSpacing) } : undefined;
    this.setData({ uploading: true });
    // 照片保留本机临时路径供「对照原图」；原图只在本机，不上传到其它地方
    const extra = pick.photo ? { photoSource: file.tempFilePath || "" } : {};
    api.uploadFile("/api/draw/upload", pick.photo ? file.tempFilePath : file.path, "file", formData)
      .then((data) => {
        if (!current()) return;
        this.setData({ uploading: false });
        this.adoptJob(data, extra);
        if (this._visible !== false) wx.showToast({ title: "转换完成", icon: "success" });
      })
      .catch((err) => {
        if (!current()) return;
        this.setData({ uploading: false });
        this._notifyCreation({ title: "上传失败", content: err.detail || "不支持的格式或转换失败", showCancel: false });
      });
  },

  // 手绘画板：独立页涂鸦 → SVG → /api/draw/upload；回本页经 adoptJob 上预览
  onDrawPad() {
    if (this._creationBusy()) return;
    const session = getApp()._sessionGeneration;
    subscribe.requestAtTap();
    wx.navigateTo({
      url: "/pages/drawpad/drawpad",
      events: {
        adopt: (data) => {
          if (data && data.job_id && getApp()._sessionGeneration === session) {
            getApp().globalData.pendingAdopt = data;
          }
        },
      },
    });
  },

  onGoBind() {
    wx.navigateTo({ url: "/pages/bind/bind" });
  },

  onGoMarket() {
    wx.navigateTo({ url: "/pages/market/market" });
  },
});
