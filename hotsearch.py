"""拉微博实时热搜（无需登录）.

主接口: GET https://weibo.com/ajax/side/hotSearch
必带请求头: 浏览器 UA + Referer: https://weibo.com/
返回 data.realtime 列表, 每项: word / num(热度) / word_scheme(带#话题) /
label_name(新/热) / is_ad(=1 为广告, 必须过滤) / realpos(榜内顺序).
注意: `rank` 字段有重复值, 排序用列表顺序, 不要用 rank.
限流: 同 IP 1 分钟内 >约10 次会被临时封禁, 本工具每天只拉 12 次, 另加随机延时.
失败备用: 重试 -> 第三方镜像 https://60s.viki.moe/v2/weibo -> 最近缓存 -> 跳过本时段.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

HOT_URL = "https://weibo.com/ajax/side/hotSearch"
FALLBACK_URL = "https://60s.viki.moe/v2/weibo"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]
BEIJING = timezone(__import__("datetime").timedelta(hours=8))


@dataclass
class HotTopic:
    rank: int          # 榜内顺序(1-based, 按列表顺序, 不是接口 rank 字段)
    word: str          # 话题词
    heat: int          # 热度 num
    scheme: str        # 带 # 的话题, 无则由 word 拼出
    label: str = ""    # 新 / 热 / 空

    @property
    def hashtag(self) -> str:
        return self.scheme or f"#{self.word}#"


def _get_json(url: str, timeout: int = 20) -> dict:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://weibo.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.load(resp)


def _parse_realtime(payload: dict) -> list[HotTopic]:
    items = (payload.get("data") or {}).get("realtime") or []
    out: list[HotTopic] = []
    for pos, it in enumerate(items, 1):
        if it.get("is_ad"):
            continue  # 广告, 跳过
        word = (it.get("word") or "").strip()
        if not word:
            continue
        try:
            heat = int(it.get("num") or 0)
        except (TypeError, ValueError):
            heat = 0
        out.append(HotTopic(rank=pos, word=word, heat=heat,
                            scheme=(it.get("word_scheme") or "").strip(),
                            label=it.get("label_name") or ""))
        if len(out) >= 50:
            break
    return out


def _parse_fallback(payload: dict) -> list[HotTopic]:
    """60s.viki.moe/v2/weibo 的返回形状: {"data": [{"title":..., "hot":...}]} 等,
    做宽容解析, 拿不到就返回空列表, 让调用方走缓存."""
    data = payload.get("data")
    items = data if isinstance(data, list) else []
    out: list[HotTopic] = []
    for pos, it in enumerate(items, 1):
        if not isinstance(it, dict):
            continue
        word = (it.get("title") or it.get("word") or it.get("name") or "").strip()
        if not word:
            continue
        try:
            heat = int(str(it.get("hot") or it.get("num") or 0).replace(",", ""))
        except (TypeError, ValueError):
            heat = 0
        out.append(HotTopic(rank=pos, word=word, heat=heat, scheme=""))
        if len(out) >= 50:
            break
    return out


def fetch_hot_top(n: int = 10, cache_path: Path | None = None,
                  retries: int = 3) -> tuple[list[HotTopic], str]:
    """返回 (前 n 条, 来源说明). 来源: 'weibo-ajax' / 'fallback-mirror' / 'cache'."""
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(1, 3))  # 随机延时, 模拟人工, 避限流
            topics = _parse_realtime(_get_json(HOT_URL))
            if topics:
                _save_cache(topics, cache_path)
                return topics[:n], "weibo-ajax"
            last_err = RuntimeError("接口返回空列表")
        except Exception as e:  # noqa: BLE001 - 换备用, 记下原因即可
            last_err = e
            time.sleep(2 * (attempt + 1))
    # 备用 1: 第三方聚合镜像
    try:
        topics = _parse_fallback(_get_json(FALLBACK_URL))
        if topics:
            return topics[:n], "fallback-mirror"
    except Exception as e:  # noqa: BLE001
        last_err = e
    # 备用 2: 最近缓存
    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            topics = [HotTopic(**t) for t in cached.get("top50", [])]
            if topics:
                return topics[:n], "cache"
        except Exception:  # noqa: BLE001 - 缓存坏了就按无话题处理
            pass
    raise RuntimeError(f"热搜拉取失败(主接口+备用均失败): {last_err}")


def _save_cache(topics: list[HotTopic], cache_path: Path | None) -> None:
    if not cache_path:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(
        {"fetched_at_beijing": datetime.now(BEIJING).isoformat(),
         "top50": [asdict(t) for t in topics]},
        ensure_ascii=False, indent=2), encoding="utf-8")


def now_beijing_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


if __name__ == "__main__":
    topics, source = fetch_hot_top(10, Path(__file__).parent / "data" / "hot_cache.json")
    print(f"来源: {source}")
    for t in topics:
        print(f"{t.rank}. {t.word}  热度={t.heat}  {t.hashtag}  [{t.label}]")
