const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");

Page({
  data: {
    mode: "draw",
    description: "",
    writeText: "",
    teachText: "",
    sizeMm: 12,
    lineSpacing: 1.6,
    pageIndex: 0,
    generating: false,
    layouting: false,
    aiWriting: false,
    teaching: false,
    sending: false,
    saving: false,
    uploading: false,
    job: null,
    quotaLeft: null,
    binding: null,
  },

  onShow() {
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (ok) this.refreshStatus();
    });
    this.resumeJobState();
  },

  // 精修回屏恢复（2026-09-09）：onHide 停链后 job 仍挂在 data 上，回到页面时
  // 一次性补查 job 状态——pending 续轮、ready 上屏、failed 标注；不再「切走就丢」。
  resumeJobState() {
    const job = this.data.job;
    if (!job || !job.jobId) return;
    if (job.refined || job.refineFailed || job.refining) return; // 已有终态或链在跑
    api
      .request("/api/draw/jobs/" + job.jobId)
      .then((res) => {
        const refined = res && res.refined;
        if (!refined) return;
        if (refined.status === "ready") {
          this.setData({
            "job.refined": {
              jobId: refined.job_id,
              previewUrl: refined.preview_url,
              gcodeUrl: refined.gcode_url,
            },
            "job.useRefined": true,
          });
          wx.showToast({ title: "AI 精修版好了", icon: "success" });
        } else if (refined.status === "failed") {
          this.setData({ "job.refineFailed": "failed" });
        } else if (refined.status === "pending") {
          this.setData({ "job.refining": true });
          this.startRefinePolling(job.jobId);
        }
      })
      .catch(() => {}); // 补屏失败不打扰，下次 onShow 再试
  },


  onLoad() {
    // 轮询句柄与作废令牌挂在页面实例上：不放进 data，避免无关字段触发渲染
    this._refineTimer = null;
    this._refineToken = 0;
    this._writeToken = 0;
  },

  onHide() {
    // 页面藏起来就停掉跟踪；链已停而 UI 还挂着"精修中"会变成永久等待的假象，
    // 一并退回纯线稿态（回到页面不续轮，接受看不到这次精修结果）
    this.stopRefinePolling();
    if (this.data.job && this.data.job.refining) {
      this.setData({ "job.refining": false });
    }
  },

  onUnload() {
    this.stopRefinePolling();
  },

  onModeTap(e) {
    const mode = e.currentTarget.dataset.mode;
    if (mode === this.data.mode) return;
    this.stopRefinePolling();
    // 作废在途排版/文档上传：回调靠令牌自灭，避免切模式后 adoptJob 把旧预览写回
    this._writeToken = (this._writeToken || 0) + 1;
    this.setData({
      mode,
      job: null,
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
    this.setData({ writeText: e.detail.value });
  },

  onTeachInput(e) {
    this.setData({ teachText: e.detail.value });
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

  refreshStatus(retried) {
    const app = getApp();
    const ready = () => {
      api
        .request("/api/auth/me")
        .then((me) => {
          app.globalData.user = me;
          this.setData({ binding: me.binding || null });
          if (me.quota && typeof me.quota.remaining === "number") {
            this.setData({ quotaLeft: me.quota.remaining });
          }
        })
        .catch(() => {
          // 失败只重登重试一次：无闸递归在弱网/后端 5xx 时会连环打请求
          if (retried) return;
          app.ensureSession().then(() => this.refreshStatus(true));
        });
    };
    if (!wx.getStorageSync("session_ok")) {
      app.ensureSession().then(ready);
    } else {
      ready();
    }
  },

  onGenerate() {
    if (this.data.generating || !this.data.description) return;
    this.setData({ generating: true });
    api
      .request("/api/draw/generate", {
        method: "POST",
        data: { description: this.data.description },
      })
      .then((res) => {
        this.setData({ generating: false });
        this.adoptJob(res);
        if (this.data.quotaLeft !== null && this.data.quotaLeft > 0) {
          this.setData({ quotaLeft: this.data.quotaLeft - 1 });
        }
        wx.showToast({ title: "生成完成", icon: "success" });
      })
      .catch((err) => {
        this.setData({ generating: false });
        wx.showModal({
          title: "生成失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  onTeachGenerate() {
    if (this.data.teaching || !this.data.teachText) return;
    const token = this._writeToken;
    this.setData({ teaching: true });
    api
      .request("/api/draw/teach", {
        method: "POST",
        data: { text: this.data.teachText },
      })
      .then((res) => {
        if (token !== this._writeToken) return;
        this.setData({ teaching: false });
        this.adoptJob(res);
        wx.showToast({ title: "生成完成", icon: "success" });
      })
      .catch((err) => {
        if (token !== this._writeToken) return;
        this.setData({ teaching: false });
        wx.showModal({
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
    if (this.data.aiWriting || this.data.layouting || !this.data.writeText) return;
    const token = this._writeToken;
    const payload =
      mode === "generate"
        ? { mode: "generate", prompt: this.data.writeText }
        : { mode: "polish", text: this.data.writeText };
    this.setData({ aiWriting: true });
    api
      .request("/api/draw/write/ai", { method: "POST", data: payload })
      .then((res) => {
        if (token !== this._writeToken) return;
        const text = (res && res.text) || "";
        this.setData({ aiWriting: false, writeText: text });
        wx.showToast({
          title: mode === "generate" ? "已生成，可再改" : "已润色，可再改",
          icon: "success",
        });
      })
      .catch((err) => {
        if (token !== this._writeToken) return;
        this.setData({ aiWriting: false });
        wx.showModal({
          title: mode === "generate" ? "生成失败" : "润色失败",
          content: err.detail || "请稍后再试",
          showCancel: false,
        });
      });
  },

  onLayoutPreview() {
    if (this.data.layouting || !this.data.writeText) return;
    const token = this._writeToken;
    this.setData({ layouting: true });
    api
      .request("/api/draw/write", {
        method: "POST",
        data: {
          text: this.data.writeText,
          size_mm: this.data.sizeMm,
          line_spacing: this.data.lineSpacing,
        },
      })
      .then((res) => {
        if (token !== this._writeToken) return;
        this.setData({ layouting: false });
        this.adoptJob(res);
        wx.showToast({ title: "排版完成", icon: "success" });
      })
      .catch((err) => {
        if (token !== this._writeToken) return;
        this.setData({ layouting: false });
        wx.showModal({
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
    wx.previewImage({ urls: [url] });
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
    if (!job) return;
    // 发预览卡上选中的版本：精修就绪且选中精修版时发精修 job
    // （后端眼里精修 job 就是普通 job，走同一个 send 接口）
    const jobId = job.useRefined && job.refined ? job.refined.jobId : job.jobId;
    this.sendJob(jobId);
  },

  onSave() {
    const job = this.data.job;
    if (!job || job.refining || this.data.saving) return;
    const jobId = job.useRefined && job.refined ? job.refined.jobId : job.jobId;
    this.setData({ saving: true });
    api
      .request(`/api/draw/${jobId}/save`, { method: "POST", data: {} })
      .then((res) => {
        this.setData({ saving: false });
        if (res.status === "saved") {
          wx.showToast({ title: "已收藏", icon: "success" });
        } else {
          wx.showToast({ title: res.detail || "收藏失败", icon: "none" });
        }
      })
      .catch((err) => {
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
    this.setData({ sending: true });
    api
      .request(`/api/draw/${jobId}/send`, { method: "POST" })
      .then((res) => {
        this.setData({ sending: false });
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

  // 上传/生成响应统一落 job：照片类任务后端异步跑 AI 精修（i2i，约 1-3 分钟）。
  // 2026-09-08 用户拍板 A 方案：pending 时页面只展示「AI 正在画」等待态，
  // Canny 草图不再先上屏；ready 上 AI 版，failed/超时才回落草图并标注。
  adoptJob(data) {
    // 新 job 换掉旧 job：旧轮询链必须停掉，否则两链并存、旧链还会改写新 job 的状态
    this.stopRefinePolling();
    const job = {
      jobId: data.job_id,
      previewUrl: data.preview_url,
      gcodeUrl: data.gcode_url,
      lines: data.lines,
      refining: false,
      refined: null,
      refineFailed: false,
      pages: data.pages || null,
      pageCount: data.page_count || null,
      truncated: !!data.truncated,
      inputTruncated: !!data.input_truncated,
      missingChars: data.missing_chars || [],
      missingCharsText: (data.missing_chars || []).slice(0, 10).join(""),
      charsTaught: data.chars_taught || null,
    };
    if (data.refined && data.refined.status === "pending") {
      job.refining = true;
    }
    this.setData({ job, pageIndex: 0 });
    if (job.refining) {
      this.startRefinePolling(job.jobId);
    }
  },

  // 精修轮询：5 秒一轮、最多 36 轮（≈3 分钟，覆盖免费层排队耗时）。
  // 用 setTimeout 链不用 setInterval：请求在途耗时不定，链式保证上一轮结束
  // 才排下一轮，绝不会并发轮询。
  startRefinePolling(jobId) {
    this.stopRefinePolling();
    const token = this._refineToken;
    let round = 0;
    // 终态收口：停链并退出"精修中"，各分支只补自己的增量字段
    const finish = (patch) => {
      this.stopRefinePolling();
      this.setData(Object.assign({ "job.refining": false }, patch));
    };
    const poll = () => {
      round += 1;
      api
        .request("/api/draw/jobs/" + jobId)
        .then((res) => {
          // 令牌变了说明链已作废（换了新任务或离开页面），丢弃过期响应
          if (token !== this._refineToken) return;
          const refined = res && res.refined;
          if (refined && refined.status === "ready") {
            // 就绪后默认切到精修版，用户仍可点缩略图切回线稿版
            finish({
              "job.refined": {
                jobId: refined.job_id,
                previewUrl: refined.preview_url,
                gcodeUrl: refined.gcode_url,
              },
              "job.useRefined": true,
            });
            wx.showToast({ title: "AI 精修版好了", icon: "success" });
            return;
          }
          if (refined && refined.status === "failed") {
            // 失败不弹窗打扰：回落 Canny 草图版并标注（A 方案唯一草图出口）
            finish({ "job.refineFailed": "failed" });
            return;
          }
          if (round >= 36) {
            finish({ "job.refineFailed": "timeout" }); // 超时同样回落草图，但说明 AI 还可能好
            return;
          }
          this._refineTimer = setTimeout(poll, 5000);
        })
        .catch(() => {
          if (token !== this._refineToken) return;
          // 网络抖动不算精修失败；轮次照样累计，保证极端弱网下 3 分钟内也必停
          if (round >= 36) {
            finish({ "job.refineFailed": "timeout" });
            return;
          }
          this._refineTimer = setTimeout(poll, 5000);
        });
    };
    this._refineTimer = setTimeout(poll, 5000);
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
    if (this.data.uploading) return;
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      success: (res) => {
        const file = res.tempFiles[0];
        if (file.size > 4 * 1024 * 1024) {
          wx.showToast({ title: "文件超过 4MB", icon: "none" });
          return;
        }
        const token = this._writeToken;
        this.setData({ uploading: true });
        const formData =
          this.data.mode === "write"
            ? {
                size_mm: String(this.data.sizeMm),
                line_spacing: String(this.data.lineSpacing),
              }
            : undefined;
        api
          .uploadFile("/api/draw/upload", file.path, "file", formData)
          .then((data) => {
            if (token !== this._writeToken) return;
            // 与 AI 生成同一预览卡：预览图 + 行数 + 发送钮（此前只存 jobId 不存 preview_url，
            // 用户看不到转换效果——2026-09-07「转换后没有预览」实证）
            this.setData({ uploading: false });
            this.adoptJob(data);
            wx.showToast({ title: "转换完成", icon: "success" });
          })
          .catch((err) => {
            if (token !== this._writeToken) return;
            this.setData({ uploading: false });
            wx.showModal({
              title: "上传失败",
              content: err.detail || "不支持的格式或转换失败",
              showCancel: false,
            });
          });
      },
    });
  },

  // 拍照/相册绘图：chooseMedia → 压缩 → 走 /api/draw/upload（同传图链）
  onTakePhoto(e) {
    if (this.data.uploading) return;
    const source = (e.currentTarget.dataset.source || "camera") === "album" ? ["album"] : ["camera"];
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: source,
      sizeType: ["compressed"],
      success: (res) => {
        const file = res.tempFiles[0];
        if (file.size > 10 * 1024 * 1024) {
          wx.showToast({ title: "照片过大，请重拍", icon: "none" });
          return;
        }
        this.setData({ uploading: true });
        api
          .uploadFile("/api/draw/upload", file.tempFilePath, "file")
          .then((data) => {
            this.setData({ uploading: false });
            this.adoptJob(data);
            wx.showToast({ title: "转换完成", icon: "success" });
          })
          .catch((err) => {
            this.setData({ uploading: false });
            wx.showModal({
              title: "上传失败",
              content: err.detail || "图片转换失败，换一张试试",
              showCancel: false,
            });
          });
      },
    });
  },

  onGoBind() {
    wx.navigateTo({ url: "/pages/bind/bind" });
  },
});
