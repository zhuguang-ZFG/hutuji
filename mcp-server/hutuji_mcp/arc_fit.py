"""落笔 G1 折线 → G2/G3 圆弧后处理（2026-09-05 提速批）。

动机：写字/描摹产物的笔画是贝塞尔细分折线（gfont cubic 24 步/曲线），逐行
Telnet + 逐行 ok 的传输口径下实测 8.8 行/s ≈113ms/行——时间几乎正比于**行数**
而非运动距离（画图/写字两类内容同测 8.8 行/s 为证，见 protocol §4.2）。把连续
落笔折线拟合成圆弧可直接砍行数：ArcWelderLib 官方实测移动命令 −75%~−96%
（Bambu Studio 默认开启同款），字库曲线段 24 步细分理论上 24→1~3。

§5 契约侧零改动：校验器早已放行 G0/G1/G2/G3 + I/J/R 并做圆弧包围盒限幅
（`_ALLOWED_G_CODES`/`_arc_bbox`，为「外部 G-code 限幅回归」预留）。本模块只在
vpype 产物写回前跑一道，所有产物链（draw/write/teach/table）共用。

设计约束（全部有实证依据）：
- 只拟合**落笔态**连续 G1 XY 行；抬笔/G0/笔控行是 run 边界，绝不跨越。
- 每段弧的终点**逐点取自原折线顶点**（不是重采样点），位置链零漂移。
- 容差叠加在 linesimplify 0.05 之后：0.05+0.02=0.07mm，远小于笔尖线宽。
- 近共线折线直接塌成一条 G1（直线比大半径弧更省：Grbl $12 会把弧再细分）。
- 半径/扫掠角护栏对应 Grbl error:34（近零半径/整圆非法）。
- 换行风格原样保留（CRLF 不得塌成 LF），理由同 `_collapse_short_pen_down_segments`。
"""

from __future__ import annotations

import math

# 弧拟合径向容差（顶点+弦中点）：叠加在 linesimplify 0.05mm 之后总偏差
# ≤0.10mm（笔尖线宽 ~0.5mm 的五分之一）。顶点过圆是构造性精确，真正的
# 形状闸是弦中点（弧相对折线的鼓出在弦中点最大）；实测（12mm 字 22 字页，
# 2026-09-05）：tol 0.05 与 0.1 砍行几乎相同，故取更严的 0.05。
ARC_FIT_TOLERANCE_MM = 0.05
# 弦中点用放宽闸：拐角「抄近道」是弧拟合唯一的真实形状损失，0.15mm 以内
# 的拐角圆化在 0.5mm 笔迹下不可见；取 0.05 则 12mm 字形笔画拐角几乎全军
# 覆没（实测砍行 29%→8%），提速意义尽失。
ARC_FIT_CORNER_TOLERANCE_MM = 0.15
# 至少 2 段折线出弧：3 点圆是构造性精确，形状由弦中点闸兜底（见 _try_arc）。
# 实测 min_seg 3→2 是收益大头（同页 9%→29%）——笔画本来就短，2 段最常见。
ARC_FIT_MIN_SEGMENTS = 2
# 大半径弧不如直线：Grbl 内部按 $12 容差把弧细分成密 chord，直线本身零开销。
ARC_FIT_MAX_RADIUS_MM = 200.0
# 近零半径触发 Grbl error:34（start==end 或半径不可表示），留一个数量级余量。
ARC_FIT_MIN_RADIUS_MM = 0.05
# Grbl 不支持整圆（起点==终点），扫掠角留 10° 余量。
ARC_FIT_MAX_SWEEP_RAD = math.radians(350.0)
_COLLINEAR_EPS = 1e-9
# 角度单调性判定的余量：顶点贴近圆心时 atan2 抖动大，放宽到 1e-6 rad。
_ANGLE_EPS = 1e-6
# 有向转角：逆行判定余量（数值抖动）与「转角总量≈扫掠角」的判差。
# 真弧两者恒等；判差 15° 只放行拟合容差内的抖动，远小于能酿事故的 180° 级错配。
_TURN_EPS = 1e-6
_TURN_SUM_TOLERANCE_RAD = math.radians(15.0)


def _circle_through(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float
) -> tuple[float, float, float] | None:
    """过三点的圆，返回 (cx, cy, r)；共线/退化返回 None。"""
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < _COLLINEAR_EPS:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _try_arc(
    pts: list[tuple[float, float]], start: int, end: int, tol: float
) -> tuple[float, float, float, bool] | None:
    """pts[start..end] 能否用一条弧覆盖。

    圆由首/中/末三点确定（ArcWelder 同款增量式），全体顶点做径向容差 +
    角度单调性 + 扫掠角上限三重校验。返回 (cx, cy, r, clockwise) 或 None。
    """
    n = end - start + 1
    if n < 3:
        return None
    ax, ay = pts[start]
    bx, by = pts[start + n // 2]
    cx_, cy_ = pts[end]
    circle = _circle_through(ax, ay, bx, by, cx_, cy_)
    if circle is None:
        return None
    ux, uy, r = circle
    # 方向由三点外积符号定（y 向上坐标系：负 = 顺时针 = G2）。
    cross = (bx - ax) * (cy_ - ay) - (by - ay) * (cx_ - ax)
    if abs(cross) < _COLLINEAR_EPS:
        return None
    clockwise = cross < 0.0
    # 径向容差 + 角度单调推进（防 S 形/回折被径向距离蒙混过关）。
    # 顺时针先取负号镜像成逆时针，再以起点角为基准展开到 [0, 2π)。
    # 顶点过圆是构造性精确的（顶点闸形同虚设），真正的形状闸是**弦中点**：
    # 弧相对折线的鼓出量在弦中点最大；顶点 tol=0.05，弦中点走放宽的
    # 拐角容差（0.15mm，常量注释有实测依据）。
    base = math.atan2(ay - uy, ax - ux)
    if clockwise:
        base = -base
    prev_rel = 0.0
    for k in range(start, end):
        px, py = pts[k]
        qx, qy = pts[k + 1]
        if abs(math.hypot(px - ux, py - uy) - r) > tol:
            return None
        mx, my = (px + qx) / 2.0, (py + qy) / 2.0
        if abs(math.hypot(mx - ux, my - uy) - r) > ARC_FIT_CORNER_TOLERANCE_MM:
            return None
        if k == start:
            continue
        ang = math.atan2(py - uy, px - ux)
        if clockwise:
            ang = -ang
        rel = (ang - base) % (2.0 * math.pi)
        if rel < prev_rel - _ANGLE_EPS:
            return None  # 角度回退：回折/整圆环绕，不是一条单调弧
        prev_rel = rel
    # 末点角度收尾（上面循环只推进到 end-1）。
    ex0, ey0 = pts[end]
    ang = math.atan2(ey0 - uy, ex0 - ux)
    if clockwise:
        ang = -ang
    rel = (ang - base) % (2.0 * math.pi)
    if rel < prev_rel - _ANGLE_EPS:
        return None
    prev_rel = rel
    if prev_rel > ARC_FIT_MAX_SWEEP_RAD:
        return None
    # 有向转角闸（2026-09-05 实测事故修复）：折线的有向转角总量必须 ≈ 扫掠角。
    # 顶点/弦中点闸对「急回折 V 字」全瞎——1mm 短弦上弧与弦几乎重合，
    # 但弦间 342° 的长弧毫无采样点，会被画成近整圆（实测偏 5.5mm）。
    # 真弧上逐顶点有向转角之和恒等于扫掠角，V 字则符号相反或量级不符。
    turn_sum = 0.0
    for k in range(start + 1, end):
        h0 = math.atan2(pts[k][1] - pts[k - 1][1], pts[k][0] - pts[k - 1][0])
        h1 = math.atan2(pts[k + 1][1] - pts[k][1], pts[k + 1][0] - pts[k][0])
        turn = (h1 - h0 + math.pi) % (2.0 * math.pi) - math.pi
        if clockwise:
            turn = -turn
        if turn < -_TURN_EPS:
            return None  # 逆着弧向的弯折：回折/锯齿
        turn_sum += max(turn, 0.0)
    if abs(turn_sum - prev_rel) > _TURN_SUM_TOLERANCE_RAD:
        return None
    return ux, uy, r, clockwise


def _line_deviation_ok(
    pts: list[tuple[float, float]], start: int, end: int, tol: float
) -> bool:
    """pts[start..end] 是否全在 pts[start]→pts[end] 直线的 tol 带内。"""
    ax, ay = pts[start]
    bx, by = pts[end]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return False
    for k in range(start + 1, end):
        px, py = pts[k]
        dist = abs((px - ax) * dy - (py - ay) * dx) / length
        if dist > tol:
            return False
    return True


def _fit_run(
    pts: list[tuple[float, float]], feeds: list[float]
) -> list[str]:
    """一条落笔 run（pts[0] 是 run 起点，即上一行的落点）→ G1/G2/G3 行列表。

    贪心最长前缀：先试弧，弧不够长再试直线塌缩，两者都退不过就单段 G1。
    feeds[i] 是到达 pts[i] 那一行的进给（run 内已保证一致，见调用方）。
    """
    out: list[str] = []
    n = len(pts)
    i = 1  # pts[0] 已由上一行（落笔/G0/上一段弧终点）落位
    while i < n:
        best_arc: tuple[int, tuple[float, float, float, bool]] | None = None
        j = i + ARC_FIT_MIN_SEGMENTS - 1
        while j < n:
            fit = _try_arc(pts, i - 1, j, ARC_FIT_TOLERANCE_MM)
            if fit is None:
                break
            best_arc = (j, fit)
            j += 1
        # 直线塌缩能覆盖的最远下标
        line_end = i
        while line_end + 1 < n and _line_deviation_ok(pts, i - 1, line_end + 1, ARC_FIT_TOLERANCE_MM):
            line_end += 1
        if best_arc is not None and best_arc[0] >= line_end:
            end_idx, (_, _, _, clockwise) = best_arc
            ex, ey = pts[end_idx]
            sx, sy = pts[i - 1]
            cx, cy, r, _ = best_arc[1]
            code = "G2" if clockwise else "G3"
            out.append(
                f"{code} X{ex:.3f}Y{ey:.3f}I{cx - sx:.3f}J{cy - sy:.3f}F{feeds[end_idx - 1]:g}"
            )
            i = end_idx + 1
        elif line_end > i:
            ex, ey = pts[line_end]
            out.append(f"G1 X{ex:.3f}Y{ey:.3f}F{feeds[line_end - 1]:g}")
            i = line_end + 1
        else:
            ex, ey = pts[i]
            out.append(f"G1 X{ex:.3f}Y{ey:.3f}F{feeds[i - 1]:g}")
            i += 1
    return out


def fit_gcode_arcs(text: str) -> str:
    """把落笔态连续 G1 折线拟合成 G2/G3（近共线段塌成单条 G1），其余行原样。

    只动「G1 X..Y..F.. 且无 Z」的落笔行序列；行序、换行风格、非运动行全部
    保持。输入不满足生产形状（无运动行/缺头尾）时按原样返回——形状闸是
    `validate_gcode` 的活，本函数不兼职。
    """
    from hutuji_mcp.gcode_gen import _WORD_RE, _xy_from_words, _z_from_words, _f_from_words

    nl = "\r\n" if "\r\n" in text else "\n"
    ends_with_nl = text.endswith("\n")
    raw_lines = text.splitlines()
    out: list[str] = []
    cur_x: float | None = None
    cur_y: float | None = None
    pen_down = False
    # 当前累积的落笔 run：顶点 + 每点对应行进给（run 内 F 变化即截断，
    # 生产产物全行 F24000，一般不触发；防御为主）。
    run_pts: list[tuple[float, float]] = []
    run_feeds: list[float] = []

    def _flush_run() -> None:
        nonlocal run_pts, run_feeds
        if len(run_pts) >= 2:
            out.extend(_fit_run(run_pts, run_feeds))
        elif run_pts:
            # 单点 run（只有起点没落笔行，理论到不了这里）什么都不产。
            pass
        run_pts, run_feeds = [], []

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            _flush_run()
            out.append(raw)
            continue
        code_part = line.split(";", 1)[0].strip()
        upper = code_part.upper()
        words = _WORD_RE.findall(upper)
        letters = {letter for letter, _ in words}
        z_val = _z_from_words(upper)
        x_val, y_val = _xy_from_words(upper)
        g_vals = [float(num) for letter, num in words if letter == "G"]
        is_g1 = any(abs(v - 1.0) < 1e-9 for v in g_vals)
        is_g0 = any(abs(v) < 1e-9 for v in g_vals)

        # 笔控行：Z5 落笔 / Z0 抬笔，都是 run 边界。
        if is_g1 and z_val is not None and abs(z_val - 5.0) < 1e-6:
            _flush_run()
            pen_down = True
            out.append(raw)
            continue
        if is_g1 and z_val is not None and abs(z_val - 0.0) < 1e-6:
            _flush_run()
            pen_down = False
            out.append(raw)
            continue

        # 落笔态纯 XY G1：进 run。首点必须先有已知当前位置。
        if pen_down and is_g1 and "Z" not in letters and (x_val is not None or y_val is not None):
            nx = cur_x if x_val is None else x_val
            ny = cur_y if y_val is None else y_val
            if nx is None or ny is None or cur_x is None or cur_y is None:
                # 当前位置未知（理论上 vpype 产物落笔前必有 G0 定位）：不进 run，
                # 行原样放行，形状合不合规交 validate_gcode 判。
                _flush_run()
                out.append(raw)
                continue
            feed = _f_from_words(words)
            feed_val = feed if feed is not None else (run_feeds[-1] if run_feeds else 8000.0)
            if not run_pts:
                run_pts = [(cur_x, cur_y)]
                run_feeds = []
            elif run_feeds and abs(feed_val - run_feeds[-1]) > 1e-9:
                _flush_run()
                run_pts = [(cur_x, cur_y)]
                run_feeds = []
            run_pts.append((nx, ny))
            run_feeds.append(feed_val)
            cur_x, cur_y = nx, ny
            continue

        # 其余一切行（G0 空走/模态/注释）：run 边界 + 位置跟踪。
        _flush_run()
        if is_g0 or is_g1:
            if x_val is not None:
                cur_x = x_val
            if y_val is not None:
                cur_y = y_val
        out.append(raw)

    _flush_run()
    body = nl.join(out)
    if ends_with_nl:
        if not body.endswith(nl):
            body += nl
    return body
