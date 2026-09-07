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
        this.setData({
          generating: false,
          job: {
            jobId: res.job_id,
            previewUrl: res.preview_url,
            gcodeUrl: res.gcode_url,
            lines: res.lines,
          },
        });
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
    if (!this.data.job) return;
    wx.previewImage({ urls: [this.data.job.previewUrl] });
  },

  onSend() {
    this.sendJob(this.data.job.jobId);
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
            this.setData({
              uploading: false,
              job: {
                jobId: data.job_id,
                previewUrl: data.preview_url,
                gcodeUrl: data.gcode_url,
                lines: data.lines,
              },
            });
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
            this.setData({
              uploading: false,
              job: {
                jobId: data.job_id,
                previewUrl: data.preview_url,
                gcodeUrl: data.gcode_url,
                lines: data.lines,
              },
            });
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
