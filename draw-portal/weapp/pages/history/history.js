const api = require("../../utils/api");

Page({
  data: {
    jobs: [],
    loading: true,
  },

  onShow() {
    this.refresh();
  },

  onPullDownRefresh() {
    this.refresh(() => wx.stopPullDownRefresh());
  },

  refresh(done) {
    api
      .request("/api/draw/saved")
      .then((data) => {
        // 收藏列表契约：{items:[{id,title,saved_at,preview_url}]}，发送走 saved/{id}/send。
        // preview_url 是 secured 相对路径，<image> 直连必 401（带不上 x-session），
        // 先置空、逐条 downloadFile 落地临时文件后回填本地路径。
        const jobs = (data.items || []).map((it) => ({
          savedId: it.id,
          previewUrl: "",
          previewPath: it.preview_url,
          sourceName: it.title || "作品",
          createdAt: String(it.saved_at || "").replace("T", " ").slice(0, 16),
        }));
        this.setData({ jobs, loading: false });
        jobs.forEach((job, idx) => {
          if (!job.previewPath) return;
          api
            .downloadFile(job.previewPath)
            .then((local) => this.setData({ [`jobs[${idx}].previewUrl`]: local }))
            .catch(() => {}); // 单条失败留占位，不打扰整页
        });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loading: false });
        if (done) done();
      });
  },

  onPreview(e) {
    const url = e.currentTarget.dataset.url;
    if (!url) return; // 预览尚未下载完成
    wx.previewImage({ urls: [url] });
  },

  onSend(e) {
    const savedId = e.currentTarget.dataset.id;
    wx.showModal({
      title: "发送到写字机",
      content: "写字机屏幕出现预览后点「开始画」",
      success: (res) => {
        if (!res.confirm) return;
        api
          .request(`/api/draw/saved/${savedId}/send`, { method: "POST" })
          .then((r) => {
            if (r.status === "sent") {
              wx.showToast({ title: "已发送", icon: "success" });
            } else {
              wx.showModal({ title: "发送失败", content: r.detail, showCancel: false });
            }
          })
          .catch((err) => {
            wx.showModal({ title: "发送失败", content: err.detail, showCancel: false });
          });
      },
    });
  },
});
