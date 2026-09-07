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
        const jobs = (data.jobs || []).map((j) => ({
          jobId: j.job_id,
          previewUrl: j.preview_url,
          sourceName: j.source_name || "作品",
          lines: j.lines || "-",
          createdAt: (j.created_at || "").replace("T", " ").slice(0, 16),
        }));
        this.setData({ jobs, loading: false });
        if (done) done();
      })
      .catch(() => {
        this.setData({ loading: false });
        if (done) done();
      });
  },

  onPreview(e) {
    wx.previewImage({ urls: [e.currentTarget.dataset.url] });
  },

  onSend(e) {
    const jobId = e.currentTarget.dataset.id;
    wx.showModal({
      title: "发送到写字机",
      content: "写字机屏幕出现预览后点「开始画」",
      success: (res) => {
        if (!res.confirm) return;
        api
          .request(`/api/draw/${jobId}/send`, { method: "POST" })
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
