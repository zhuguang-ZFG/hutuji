// 画稿保存自己的纸面坐标；屏幕缩放不改成稿，换纸张需显式等比适配。
const WIDTH = 380;
const HEIGHT = 554;
const DEFAULT_VIEWPORT = { width: WIDTH, height: HEIGHT };
const MAX_STROKES = 400;
const MAX_POINTS = 6000;
const DRAFT_KEY = 'drawpad_draft_v2';
const TOOLS = ['pen', 'line', 'rect', 'ellipse', 'star', 'heart', 'flower'];
const round = (n) => Math.round(n * 10) / 10;
const clamp = (n, low, high) => Math.min(high, Math.max(low, n));

function validViewport(viewport) {
  return viewport && [viewport.width, viewport.height].every((n) => Number.isFinite(n) && n >= 1 && n <= 4096);
}

function viewportFor(width, height) {
  if (![width, height].every((n) => Number.isFinite(n) && n >= 20 && n <= 2000)) throw new Error('画纸尺寸无效');
  const scale = Math.min(WIDTH / width, HEIGHT / height);
  return { width: round(width * scale), height: round(height * scale) };
}

function validStroke(stroke, viewport = DEFAULT_VIEWPORT) {
  return !!stroke && TOOLS.includes(stroke.tool) && Array.isArray(stroke.pts) && stroke.pts.length >= 2 &&
    (stroke.tool === 'pen' || stroke.pts.length === 2) && stroke.pts.every((point) =>
      Array.isArray(point) && point.length === 2 && point.every(Number.isFinite) &&
      point[0] >= 0 && point[0] <= viewport.width && point[1] >= 0 && point[1] <= viewport.height);
}

function shapePoints(stroke) {
  if (stroke.tool === 'pen' || stroke.tool === 'line') return stroke.pts;
  const [a, b] = stroke.pts;
  const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
  const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
  if (stroke.tool === 'rect') return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
  const result = [];
  const count = stroke.tool === 'star' ? 10 : stroke.tool === 'ellipse' ? 32 : 64;
  for (let i = 0; i <= count; i++) {
    const angle = i * Math.PI * 2 / count - Math.PI / 2;
    let x, y;
    if (stroke.tool === 'heart') {
      const t = i * Math.PI * 2 / count;
      x = Math.pow(Math.sin(t), 3);
      y = -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)) / 17;
    } else {
      const radius = stroke.tool === 'star' ? (i % 2 ? 0.43 : 1) :
        stroke.tool === 'flower' ? 0.72 + 0.28 * Math.cos(5 * (angle + Math.PI / 2)) : 1;
      x = radius * Math.cos(angle); y = radius * Math.sin(angle);
    }
    result.push([(x0 + x1) / 2 + (x1 - x0) / 2 * x, (y0 + y1) / 2 + (y1 - y0) / 2 * y]);
  }
  return result;
}

function commands(stroke) {
  const pts = shapePoints(stroke);
  if (!pts || pts.length < 2) return [];
  const out = [['M', ...pts[0]]];
  if (stroke.tool === 'pen' && pts.length > 2) {
    // 相邻点中点作二次曲线端点，保住首尾；渲染和导出都消费此路径。
    for (let i = 1; i < pts.length - 1; i++) {
      out.push(['Q', ...pts[i], (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2]);
    }
  } else {
    for (let i = 1; i < pts.length - 1; i++) out.push(['L', ...pts[i]]);
  }
  out.push(['L', ...pts[pts.length - 1]]);
  return out.map((cmd) => [cmd[0], ...cmd.slice(1).map(round)]);
}

function pointCount(strokes) {
  return strokes.reduce((count, stroke) => count + shapePoints(stroke).length, 0);
}

function buildSvg(strokes, viewport = DEFAULT_VIEWPORT) {
  if (!validViewport(viewport) || !Array.isArray(strokes) || !strokes.every((stroke) => validStroke(stroke, viewport))) throw new Error('笔迹坐标无效，请撤销后重画');
  if (strokes.length > MAX_STROKES || pointCount(strokes) > MAX_POINTS) throw new Error('画稿较复杂，请擦除一些笔画');
  const paths = strokes.map((stroke) => `<path d="${commands(stroke).map((cmd) => cmd[0] + cmd.slice(1).join(' ')).join('')}"/>`);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${viewport.width}" height="${viewport.height}" viewBox="0 0 ${viewport.width} ${viewport.height}">` +
    '<g fill="none" stroke="#000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    paths.join('') + '</g></svg>';
  // ASCII 路径字节数等于字符数，给服务端 256KiB 闸保留余量。
  if (svg.length > 240 * 1024) throw new Error('画稿较复杂，请擦除一些笔画');
  return svg;
}

function pointDistance(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = dx || dy ? clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy), 0, 1) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

function segmentsNear(a, b, c, d, radius) {
  const cross = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  if (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) return true;
  return Math.min(pointDistance(a, c, d), pointDistance(b, c, d),
    pointDistance(c, a, b), pointDistance(d, a, b)) <= radius;
}

function hitsStroke(stroke, from, to, radius) {
  let previous;
  for (const cmd of commands(stroke)) {
    if (cmd[0] === 'M') { previous = cmd.slice(1); continue; }
    const end = cmd.slice(-2);
    if (cmd[0] === 'Q') {
      const start = previous;
      // 对画面同一条曲线取短段；扫过两次触摸之间的线段，快擦也不会漏线。
      for (let i = 1; i <= 8; i++) {
        const t = i / 8, u = 1 - t;
        const point = [u * u * start[0] + 2 * u * t * cmd[1] + t * t * end[0],
          u * u * start[1] + 2 * u * t * cmd[2] + t * t * end[1]];
        if (segmentsNear(from, to, previous, point, radius)) return true;
        previous = point;
      }
    } else {
      if (segmentsNear(from, to, previous, end, radius)) return true;
      previous = end;
    }
  }
  return false;
}

function readDraft(draft, owner) {
  if (!owner || !draft || ![2, 3].includes(draft.version) || draft.owner !== owner ||
    !Array.isArray(draft.strokes) || draft.strokes.length > MAX_STROKES) return null;
  const viewport = draft.version === 2 ? DEFAULT_VIEWPORT : draft.viewport;
  if (!validViewport(viewport)) return null;
  if (draft.version === 3 && (typeof draft.paperMarker !== 'string' || draft.paperMarker.length > 128 ||
    typeof draft.deviceMac !== 'string' || draft.deviceMac.length > 17)) return null;
  const strokes = [];
  let count = 0;
  for (const stroke of draft.strokes) {
    if (!validStroke(stroke, viewport)) return null;
    count += shapePoints(stroke).length;
    if (count > MAX_POINTS) return null;
    const pts = [];
    for (const point of stroke.pts) {
      pts.push(point.slice());
    }
    strokes.push({ tool: stroke.tool, pts });
  }
  return { strokes, viewport: Object.assign({}, viewport),
    paperMarker: draft.version === 3 ? draft.paperMarker : '', deviceMac: draft.version === 3 ? draft.deviceMac : '' };
}

function restoreDraft(draft, owner) {
  const value = readDraft(draft, owner);
  return value ? value.strokes : null;
}

function fitStrokes(strokes, from, to) {
  buildSvg(strokes, from);
  if (!validViewport(to)) throw new Error('画纸尺寸无效');
  const scale = Math.min(to.width / from.width, to.height / from.height);
  const x = (to.width - from.width * scale) / 2, y = (to.height - from.height * scale) / 2;
  return strokes.map((stroke) => ({ tool: stroke.tool, pts: stroke.pts.map((p) => [round(p[0] * scale + x), round(p[1] * scale + y)]) }));
}

function radialPoint(point, viewport) {
  const x = point[0] - viewport.width / 2, y = point[1] - viewport.height / 2;
  const radius = Math.max(0, Math.min(viewport.width, viewport.height) / 2 - 2);
  const ratio = Math.min(1, radius / Math.max(0.001, Math.hypot(x, y)));
  return [viewport.width / 2 + x * ratio, viewport.height / 2 + y * ratio];
}

function symmetryStrokes(stroke, symmetry, viewport) {
  if (symmetry === 'mirror') return [stroke, { tool: stroke.tool,
    pts: stroke.pts.map((p) => [viewport.width - p[0], p[1]]) }];
  if (symmetry !== 'radial' || stroke.tool !== 'pen') return [stroke];
  return Array.from({ length: 6 }, (_, index) => {
    const angle = index * Math.PI / 3, cos = Math.cos(angle), sin = Math.sin(angle);
    return { tool: 'pen', pts: stroke.pts.map((point) => {
      const p = radialPoint(point, viewport), x = p[0] - viewport.width / 2, y = p[1] - viewport.height / 2;
      return [round(viewport.width / 2 + x * cos - y * sin), round(viewport.height / 2 + x * sin + y * cos)];
    }) };
  });
}

function moveStroke(stroke, viewport, dx, dy, scale = 1) {
  const xs = stroke.pts.map((p) => p[0]), ys = stroke.pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const halfW = (x1 - x0) * scale / 2, halfH = (y1 - y0) * scale / 2;
  if (halfW * 2 > viewport.width || halfH * 2 > viewport.height) throw new Error('图形已接近画纸边缘');
  const x = clamp(cx + dx, halfW, viewport.width - halfW), y = clamp(cy + dy, halfH, viewport.height - halfH);
  return { tool: stroke.tool, pts: stroke.pts.map((p) => [round(x + (p[0] - cx) * scale), round(y + (p[1] - cy) * scale)]) };
}

module.exports = { WIDTH, HEIGHT, DEFAULT_VIEWPORT, MAX_STROKES, MAX_POINTS, DRAFT_KEY, TOOLS,
  clamp, commands, shapePoints, pointCount, buildSvg, hitsStroke, restoreDraft, readDraft,
  viewportFor, fitStrokes, radialPoint, symmetryStrokes, moveStroke };
