const api = require("../../utils/api");
const phoneGate = require("../../utils/phone_gate");

Page({
  data: {
    jobs: [],
    loading: true,
  },

  onShow() {
    phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
      if (ok) this.refresh();
      else this.setData({ loading: false });
    });
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

  // 长按删除（2026-09-09 补）：后端 DELETE /api/draw/saved/{id} 早已上线但前端无入口
  onLongPress(e) {
    const savedId = e.currentTarget.dataset.id;
    const idx = Number(e.currentTarget.dataset.idx);
    wx.showActionSheet({
      itemList: ["删除"],
      itemColor: "#FA5151",
      success: (res) => {
        if (res.tapIndex !== 0) return;
        wx.showModal({
          title: "删除这件作品",
          content: "删除后不能再发送到写字机，确定？",
          confirmText: "删除",
          confirmColor: "#FA5151",
          success: (r) => {
            if (!r.confirm) return;
            api
              .request(`/api/draw/saved/${savedId}`, { method: "DELETE" })
              .then(() => {
                const jobs = this.data.jobs.slice();
                jobs.splice(idx, 1);
                this.setData({ jobs });
                wx.showToast({ title: "已删除", icon: "success" });
              })
              .catch((err) => {
                wx.showModal({
                  title: "删除失败",
                  content: err.detail || "请稍后再试",
                  showCancel: false,
                });
              });
          },
        });
      },
    });
  },

  onSend(e) {
    const savedId = e.currentTarget.dataset.id;
    wx.showModal({
      title: "发送到写字机",
      content: "将直接开始画，无需再在小派上确认",
      success: (res) => {
        if (!res.confirm) return;
        api
          .request(`/api/draw/saved/${savedId}/send`, { method: "POST" })
          .then((r) => {
            if (r.status === "sent") {
              const started = /已自动开始画/.test(r.detail || "");
              wx.showToast({
                title: started ? "已开始画" : "已发送",
                icon: "success",
              });
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
