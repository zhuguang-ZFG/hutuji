// 与云端及设备同一十分之一毫米契约；纸张选择不能隐式增大机器行程。
const PRESETS = { A4: [210, 297], A3: [297, 420], A2: [420, 594] };
const NUMBERS = { width_mm: [50, 2000], height_mm: [50, 2000], margin_mm: [0, 50],
  max_x_mm: [10, 2000], max_y_mm: [10, 2000] };
const FIELDS = ["paper", "width_mm", "height_mm", "landscape", "margin_mm", "swap_xy", "max_x_mm", "max_y_mm"];

function dimensions(paper) {
  const width = paper.landscape ? paper.height_mm : paper.width_mm;
  const height = paper.landscape ? paper.width_mm : paper.height_mm;
  const w = (Math.round(width * 10) - Math.round(paper.margin_mm * 10) * 2) / 10;
  const h = (Math.round(height * 10) - Math.round(paper.margin_mm * 10) * 2) / 10;
  return { width, height, pageWidth: w, pageHeight: h, x: paper.swap_xy ? h : w, y: paper.swap_xy ? w : h };
}
function validate(paper) {
  if (!paper || Object.keys(paper).sort().join() !== FIELDS.slice().sort().join()) throw new Error("纸张记录不完整，请刷新");
  if (!Object.prototype.hasOwnProperty.call(PRESETS, paper.paper) && paper.paper !== "custom") throw new Error("请选择纸张");
  if (typeof paper.landscape !== "boolean" || typeof paper.swap_xy !== "boolean") throw new Error("方向设置无效");
  for (const field of Object.keys(NUMBERS)) {
    const value = paper[field], [low, high] = NUMBERS[field];
    if (typeof value !== "number" || !Number.isFinite(value) || value < low || value > high ||
        Math.abs(value * 10 - Math.round(value * 10)) > 1e-7) throw new Error("尺寸请填写范围内的毫米数，最多一位小数");
  }
  if (PRESETS[paper.paper] && (paper.width_mm !== PRESETS[paper.paper][0] || paper.height_mm !== PRESETS[paper.paper][1])) throw new Error("纸张名称与尺寸不一致");
  const d = dimensions(paper);
  if (Math.min(d.pageWidth, d.pageHeight) < 20) throw new Error("留边后每边至少需要 20 毫米");
  if (d.x > paper.max_x_mm || d.y > paper.max_y_mm) throw new Error("本纸张需要 X " + d.x + " × Y " + d.y + " 毫米，当前可绘制范围放不下");
  return paper;
}
function fromForm(form) {
  const result = Object.assign({}, form);
  for (const field of Object.keys(NUMBERS)) {
    if (typeof result[field] === "string") {
      if (!/^\d+(\.\d)?$/.test(result[field])) throw new Error("请填写完整尺寸，最多一位小数");
      result[field] = Number(result[field]);
    }
  }
  return validate(result);
}
function marker(paper) {
  validate(paper);
  return ["p1", paper.paper, Math.round(paper.width_mm * 10), Math.round(paper.height_mm * 10),
    Number(paper.landscape), Math.round(paper.margin_mm * 10), Number(paper.swap_xy),
    Math.round(paper.max_x_mm * 10), Math.round(paper.max_y_mm * 10)].join(":");
}
function defaultFor(machine) {
  return { paper: "A4", width_mm: 210, height_mm: 297, landscape: false, margin_mm: 10,
    swap_xy: machine !== "nopaper", max_x_mm: machine === "nopaper" ? 205 : 277, max_y_mm: machine === "nopaper" ? 290 : 190 };
}
function increased(paper, before) { return paper.max_x_mm > before.max_x_mm || paper.max_y_mm > before.max_y_mm; }

function requestError(error, fallback) {
  if (error && [404, 405].includes(error.status)) return '纸张服务暂不可用，请稍后重试';
  const detail = error && (error.detail || error.message);
  return typeof detail === 'string' && /[\u3400-\u9fff]/.test(detail) ? detail : fallback;
}

module.exports = { PRESETS, NUMBERS, dimensions, validate, fromForm, marker, defaultFor, increased, requestError };
