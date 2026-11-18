"""文案生成(模板版).

红线: 只用话题词本身 + 排名/热度, 不编造新闻事实、不写没发生的细节、
不引战造谣. 每条换角度模板, 同一话题当天只用一次(去重在 scheduler 里做).
字数: 正文控制在 140 字以内(含话题标签), 超了自动截断到句末.

以后接大模型: 实现 generate_via_llm(topic, angle) 即可, 保持同样的
输入( HotTopic )和输出(str), 并在提示词里同样要求"只基于话题词本身,
不编造事实", 再把本模块的模板作为无模型时的 fallback.
"""
from __future__ import annotations

import random

from hotsearch import HotTopic

ANGLES = [
    ("快讯讨论", "【热搜第{rank}】{tag}\n这个话题冲上热搜了，热度 {heat}，大家都在讨论。你怎么看？"),
    ("围观提问", "{tag}\n热搜第{rank}名，热度 {heat}。这个话题你关注到了吗？来说说你的看法。"),
    ("热度点评", "热搜第{rank}：{tag}\n热度 {heat}，讨论度还在涨。相关话题值得持续看看后续。"),
    ("话题互动", "今天的热搜第{rank}是{tag}，热度 {heat}。\n你身边有人在聊这个吗？评论区聊聊。"),
    ("榜单播报", "实时热搜第{rank}：{tag}（热度 {heat}）。\n榜单变化很快，稍后继续跟进热门话题。"),
]

MAX_LEN = 140


def heat_str(heat: int) -> str:
    if heat >= 10_000:
        return f"{heat / 10_000:.1f}万"
    return str(heat)


def generate(topic: HotTopic, angle: str | None = None,
             rng: random.Random | None = None) -> tuple[str, str]:
    """返回 (正文, 角度名). angle 为 None 时随机选一个角度."""
    rng = rng or random
    if angle:
        name, tmpl = next((a for a in ANGLES if a[0] == angle), ANGLES[0])
    else:
        name, tmpl = rng.choice(ANGLES)
    text = tmpl.format(rank=topic.rank, tag=topic.hashtag,
                       word=topic.word, heat=heat_str(topic.heat))
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN].rstrip("，。、；：")
    return text, name


def generate_via_llm(topic: HotTopic, angle: str | None = None) -> str:
    """【占位, 未实现】以后接大模型生成时在这里实现.

    约定:
    - 入参: HotTopic(话题词/排名/热度) + 角度; 出参: 正文 str(<=140 字).
    - 提示词必须要求: 只基于话题词本身写讨论, 不编造新闻事实、时间、
      地点、人物言行和数据; 不引战、不造谣、不带商业推广.
    - 失败时调用方 fallback 到 generate().
    - 需要的环境变量(部署时再填): LLM_API_URL / LLM_API_KEY / LLM_MODEL.
    """
    raise NotImplementedError(
        "LLM 生成未接入: 部署时填 LLM_API_URL/LLM_API_KEY/LLM_MODEL 后再实现; "
        "当前用模板 generate() 兜底.")


if __name__ == "__main__":
    from hotsearch import HotTopic as _T
    t = _T(rank=1, word="美国总统特朗普抵达深圳", heat=2511532,
           scheme="#美国总统特朗普抵达深圳#", label="新")
    for name, _ in ANGLES:
        text, used = generate(t, angle=name)
        print(f"[{used}] {len(text)}字: {text}")
