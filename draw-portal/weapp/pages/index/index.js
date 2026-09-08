const api = require("../../utils/api");

Page({
  data: {
    description: "",
    generating: false,
    sending: false,
    uploading: false,
    job: null,
    quotaLeft: null,
    binding: null,
  },

  onShow() {
    this.refreshStatus();
  },

  onLoad() {
    // 轮询句柄与作废令牌挂在页面实例上：不放进 data，避免无关字段触发渲染
    this._refineTimer = null;
    this._refineToken = 0;
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

  onDescInput(e) {
    this.setData({ description: e.detail.value });
  },

  refreshStatus() {
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
          app.ensureSession().then(() => this.refreshStatus());
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

  onPreviewTap() {
    const job = this.data.job;
    if (!job) return;
    // 放大图跟随预览卡当前显示的版本（线稿 / AI 精修）
    const url = job.useRefined && job.refined ? job.refined.previewUrl : job.previewUrl;
    wx.previewImage({ urls: [url] });
  },

  onSend() {
    const job = this.data.job;
    if (!job) return;
    // 发预览卡上选中的版本：精修就绪且选中精修版时发精修 job
    // （后端眼里精修 job 就是普通 job，走同一个 send 接口）
    const jobId = job.useRefined && job.refined ? job.refined.jobId : job.jobId;
    this.sendJob(jobId);
  },


  sendJob(jobId) {
    if (this.data.sending) return;
    this.setData({ sending: true });
    api
      .request(`/api/draw/${jobId}/send`, { method: "POST" })
      .then((res) => {
        this.setData({ sending: false });
        if (res.status === "sent") {
          wx.showModal({
            title: "已发送",
            content: "写字机屏幕上出现预览后，点「开始画」即可",
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

  // 上传/生成响应统一落 job：照片类任务后端会异步跑 AI 精修（i2i，约 1-3 分钟），
  // refined.status === "pending" 时先展示 Canny 线稿版，同时开轮询等精修版
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
      useRefined: false,
      refineFailed: false,
    };
    if (data.refined && data.refined.status === "pending") {
      job.refining = true;
    }
    this.setData({ job });
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
            // 失败不弹窗打扰：线稿版本来就在手里，灰字说明即可
            finish({ "job.refineFailed": true });
            return;
          }
          if (round >= 36) {
            finish(); // 超时静默放弃，理由同上
            return;
          }
          this._refineTimer = setTimeout(poll, 5000);
        })
        .catch(() => {
          if (token !== this._refineToken) return;
          // 网络抖动不算精修失败；轮次照样累计，保证极端弱网下 3 分钟内也必停
          if (round >= 36) {
            finish();
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

  onPickVersion(e) {
    const job = this.data.job;
    if (!job || !job.refined) return;
    const useRefined = e.currentTarget.dataset.version === "refined";
    if (useRefined !== job.useRefined) {
      this.setData({ "job.useRefined": useRefined });
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
        this.setData({ uploading: true });
        api
          .uploadFile("/api/draw/upload", file.path, "file")
          .then((data) => {
            // 与 AI 生成同一预览卡：预览图 + 行数 + 发送钮（此前只存 jobId 不存 preview_url，
            // 用户看不到转换效果——2026-09-07「转换后没有预览」实证）
            this.setData({ uploading: false });
            this.adoptJob(data);
            wx.showToast({ title: "转换完成", icon: "success" });
          })
          .catch((err) => {
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
