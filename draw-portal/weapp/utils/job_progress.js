// draw-portal/weapp/utils/job_progress.js
const POLL_MS_IDLE = 5000;
const POLL_MS_BUSY = 1500;
// 短动作通常不到一轮空闲轮询；仍以设备回包解锁，绝不按计时猜完成。
const POLL_MS_CONTROL = 500;
const TIMELINE_LABELS = ["空闲", "预览", "待确认", "准备中", "出图中", "换纸", "结束"];

const BUSY_STATES = {
  downloading: 1,
  verifying: 1,
  streaming: 1,
  paused: 1,
  paper_change: 1,
};

function timelineIndex(state, paperChanging) {
  const s = state == null ? "" : String(state);
  if (paperChanging === "on" || s === "paper_change") return 5;
  if (s === "previewing") return 1;
  if (s === "awaiting_confirmation") return 2;
  if (s === "downloading" || s === "verifying") return 3;
  if (s === "streaming" || s === "paused") return 4;
  if (s === "done" || s === "error" || s === "aborted") return 6;
  return 0;
}

function pollIntervalMs(state, otaState) {
  const s = state == null ? "" : String(state);
  if (s === "manual" || s === "pen_test") return POLL_MS_CONTROL;
  if (BUSY_STATES[s]) return POLL_MS_BUSY;
  const o = otaState == null ? "idle" : String(otaState);
  if (o && o !== "idle") return POLL_MS_BUSY;
  return POLL_MS_IDLE;
}

function formatDurationMs(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "";
  const sec = Math.floor(n / 1000);
  const m = Math.floor(sec / 60);
  const r = sec % 60;
  return m + ":" + (r < 10 ? "0" : "") + r;
}

function pageProgress(page, pages) {
  const total = Number(pages);
  if (!Number.isFinite(total) || total <= 0) {
    return { show: false, label: "", percent: 0 };
  }
  const cur = Number(page);
  const p = Number.isFinite(cur) && cur > 0 ? cur : 1;
  const percent = Math.min(100, Math.max(0, Math.round((100 * p) / total)));
  return { show: true, label: p + " / " + total, percent: percent };
}

function deriveProgressView(status) {
  const st = status || {};
  const state = st.state || st.job_state || "";
  const paperChanging = st.paper_changing || "";
  const idx = timelineIndex(state, paperChanging);
  const ota = st.ota || {};
  const otaState = ota.state || "idle";
  let timelineHint = "";
  if (state === "paused") timelineHint = "已暂停";
  if (state === "error" || state === "aborted") timelineHint = state === "error" ? "出错" : "已停止";
  if (state === "done") timelineHint = "完成";
  const page = pageProgress(st.page, st.pages);
  let otaLine = "";
  if (otaState && otaState !== "idle") {
    const prog = Number.isFinite(ota.progress) ? Math.max(0, Math.min(100, ota.progress)) : 0;
    const failures = { download_failed: "下载失败，请确认联网后到固件页检查更新",
      verify_failed: "固件校验失败，请到固件页检查更新", write_failed: "固件写入失败",
      status_timeout: "升级结果待确认，请到固件页核对" };
    otaLine = otaState === "failed" ? (failures[ota.reason] || "升级失败，请到固件页查看") :
      otaState === "rolled_back" ? "已回滚到旧版，请到固件页检查更新" :
      otaState === "upgrading" ? (prog >= 100 ? "等待设备重启并核对版本" : "固件升级中 " + prog + "%") :
      "升级状态待确认，请到固件页核对";
  }
  // 固件在任务结束后仍累加计时，终态不能把空闲时间显示为绘制耗时。
  const showTiming = !!BUSY_STATES[state];
  const elapsedLabel = showTiming ? formatDurationMs(st.elapsed_ms) : "";
  const etaLabel = showTiming ? formatDurationMs(st.eta_ms) : "";
  return {
    timelineIndex: idx,
    timelineLabels: TIMELINE_LABELS.slice(),
    timelineHint: timelineHint,
    pollMs: pollIntervalMs(state, otaState),
    pageShow: page.show,
    pageLabel: page.label,
    pagePercent: page.percent,
    elapsedLabel: elapsedLabel,
    etaLabel: etaLabel,
    otaLine: otaLine,
  };
}

module.exports = {
  POLL_MS_IDLE,
  POLL_MS_BUSY,
  POLL_MS_CONTROL,
  TIMELINE_LABELS,
  timelineIndex,
  pollIntervalMs,
  formatDurationMs,
  pageProgress,
  deriveProgressView,
};
