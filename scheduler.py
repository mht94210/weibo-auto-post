"""定时调度: 一天 12 条.

默认时段 07:30-22:30, 约 70-80 分钟一条, 每条加 +-10 分钟抖动,
相邻两条永不小于 45 分钟. 当天同一话题词只用一次(used_topics.json),
用过的第二天清零. 发文间隔、时段、条数都在 config.yaml 里改.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

DEFAULT_SLOTS = ["07:30", "08:50", "10:10", "11:30", "12:50", "14:10",
                 "15:30", "16:50", "18:10", "19:30", "20:50", "22:10"]


def slots_for_today(slots: list[str] | None = None,
                    rng: random.Random | None = None) -> list[datetime]:
    rng = rng or random
    today = date.today()
    out: list[datetime] = []
    prev: datetime | None = None
    for s in (slots or DEFAULT_SLOTS):
        h, m = map(int, s.split(":"))
        dt = datetime.combine(today, time(h, m)) + timedelta(
            minutes=rng.randint(-10, 10))
        if prev and (dt - prev) < timedelta(minutes=45):
            dt = prev + timedelta(minutes=45)
        out.append(dt)
        prev = dt
    return out


class TopicDedup:
    """当天话题去重, 按北京日期分文件存."""

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = {"date": str(date.today()), "used": []}
        if self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text(encoding="utf-8"))
                if saved.get("date") == str(date.today()):
                    self.state = saved
            except (ValueError, KeyError):
                pass

    def used(self, word: str) -> bool:
        return word in self.state["used"]

    def mark(self, word: str) -> None:
        if word not in self.state["used"]:
            self.state["used"].append(word)
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False),
                                   encoding="utf-8")

    def pick_unused(self, topics) -> object | None:
        for t in topics:
            if not self.used(t.word):
                return t
        return None
