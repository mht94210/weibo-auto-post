"""内容过滤: 不发带政治内容的话题.

用户规则(2026-11-18): 热搜话题里带政治内容的, 一律不发, 直接跳过该时段换下一条.
判断用关键词命中(子串匹配), 宁可误拦、不可漏放: 命中即跳过, 并在日志里记下命中的词.
注意: 单独的国家名(美国/中国/日本等)不拦, 只有和政治词连用才拦,
例如 "美国旅游" 不拦, "美国总统/中美关系/台海" 拦.
"""
from __future__ import annotations

# 领导人/政要名
LEADER_NAMES = [
    "习近平", "特朗普", "普京", "拜登", "泽连斯基", "金正恩", "莫迪",
    "马克龙", "朔尔茨", "石破茂", "李在明", "赖清德", "蔡英文", "马英九",
    "哈里斯", "万斯", "卢比奥", "布林肯", "奥巴马", "希拉里", "默克尔",
    "约翰逊", "苏纳克", "斯塔默", "梅洛尼", "朔尔茨", "冯德莱恩",
    "内塔尼亚胡", "哈梅内伊", "阿萨德", "埃尔多安", "小马科斯", "杜特尔特",
    "安瓦尔", "普拉博沃", "尹锡悦", "文在寅", "岸田文雄", "菅义伟",
]

# 政治机构/概念/行为
POLITICAL_TERMS = [
    "政治", "政府", "外交", "外交部", "国防", "军事", "军队", "战争",
    "冲突", "制裁", "关税", "贸易战", "脱钩", "政党", "党员", "人大",
    "政协", "国会", "议会", "选举", "投票", "总统", "主席", "总理",
    "部长", "省长", "市长", "书记", "领导人", "元首", "首脑", "峰会",
    "会晤", "国事访问", "白宫", "克里姆林宫", "中南海", "台独", "统一",
    "主权", "领土", "边界", "军演", "导弹", "核武", "核武器", "间谍",
    "情报", "政变", "抗议", "游行示威", "罢工", "暴乱", "恐怖袭击",
    "中美关系", "中美", "美方", "中方", "俄乌", "台海", "南海",
    "朝鲜", "以色列", "巴勒斯坦", "加沙", "伊朗", "乌克兰", "俄罗斯",
    "台湾问题", "涉台", "涉港", "涉疆", "涉藏", "一国两制", "九二共识",
    "民主", "独裁", "专制", "人权", "言论自由", "审查", "封杀",
]

POLITICAL_KEYWORDS = LEADER_NAMES + POLITICAL_TERMS


def political_reason(word: str) -> str | None:
    """命中则返回命中的关键词, 未命中返回 None."""
    if not word:
        return None
    for kw in POLITICAL_KEYWORDS:
        if kw and kw in word:
            return kw
    return None


def is_political(word: str) -> bool:
    return political_reason(word) is not None


def filter_political(topics):
    """把话题列表分成 (可用, 被拦).

    返回 (kept, skipped), skipped 每项是 (topic, 命中的关键词).
    """
    kept, skipped = [], []
    for t in topics:
        word = getattr(t, "word", t) if not isinstance(t, str) else t
        reason = political_reason(word)
        if reason:
            skipped.append((t, reason))
        else:
            kept.append(t)
    return kept, skipped
