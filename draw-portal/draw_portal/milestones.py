"""作品里程碑与连续打卡判定（纯函数；粘合性通知 §10.4.18）。

只做「数字 → 文案/是否达标」的判定，不发通知不碰库：
- 里程碑：累计 ready 作品数跨过阈值时触发最高已跨过的一个；
  是否已发过由 store.engagement_events 决定（INSERT OR IGNORE 抢占）。
- 连续打卡：按本地日历日（服务器时区）取 distinct 日期，窗口止于今天；
  今天没有 ready 作品则 streak 归零（不越天追溯）。
阈值与文案在此钉死，阈值变更属产品决策，改这里必须同步 §10.4.18。
"""
from __future__ import annotations

from datetime import date

# 一次性订阅消息 phrase2 ≤5 字，统一用同一短语；thing1 ≤20 字。
ENGAGEMENT_PHRASE = "里程碑"

# 累计作品数阈值 → 通知文案（按用户观感排序，1 是最关键的 onboarding 时刻）
DRAW_MILESTONE_TEXTS: dict[int, str] = {
    1: "第一幅作品",
    10: "第 10 幅作品",
    50: "第 50 幅作品",
    100: "第 100 幅作品",
    500: "第 500 幅作品",
}

STREAK_MILESTONES: tuple[int, ...] = (3, 7, 30)


def milestone_for_count(count: int) -> int | None:
    """最高已跨过且在阈值表内的里程碑；不足 1 或未达任一阈值返回 None。"""
    crossed = [n for n in DRAW_MILESTONE_TEXTS if count >= n]
    return max(crossed) if crossed else None


def draw_milestone_text(n: int) -> str:
    return DRAW_MILESTONE_TEXTS[n]


def streak_for_dates(dates: set[date], today: date) -> int:
    """今天有记录时，从今天往回数连续天数；断档或今天缺席归零。"""
    if today not in dates:
        return 0
    streak = 0
    day = today
    while day in dates:
        streak += 1
        day = date.fromordinal(day.toordinal() - 1)
    return streak


def streak_milestone_for(streak: int) -> int | None:
    crossed = [n for n in STREAK_MILESTONES if streak >= n]
    return max(crossed) if crossed else None


def streak_milestone_text(n: int) -> str:
    return f"连续打卡 {n} 天"
