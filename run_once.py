"""跑一个发文时段: 拉热搜 -> 选未用话题 -> 生成文案 -> 配图 -> dry-run 发文.

默认 dry-run, 不登录、不真发. 用法:
  python3 run_once.py --dry-run                 # 随机挑当天未用话题
  python3 run_once.py --dry-run --topic-rank 1  # 指定用热搜第 1 名
  python3 run_once.py --dry-run --no-image      # 纯文字(跳过配图)
真发(部署后, 登录态就绪才可用):
  python3 run_once.py --live --topic-rank 1
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from generator import generate
from hotsearch import fetch_hot_top, now_beijing_iso
from images import make_title_card
from poster import WeiboPoster
from proxy_pool import load_proxies, proxy_for_account
from scheduler import TopicDedup

import yaml  # type: ignore[import-untyped]


def load_config() -> dict:
    with (BASE / "config.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", action="store_true",
                    help="真发! 需要登录态文件, 并把 config.yaml 的 dry_run 改为 false")
    ap.add_argument("--topic-rank", type=int, default=None)
    ap.add_argument("--no-image", action="store_true")
    args = ap.parse_args()
    dry_run = not args.live

    cfg = load_config()
    account = (cfg.get("account") or {}).get("id", "weibo_account_1")
    rng = random.Random()

    # 1. 拉实时热搜(发文时才拉, 保证是当时的榜)
    cache = BASE / "data" / "hot_cache.json"
    topics, source = fetch_hot_top(10, cache)
    print(f"[{now_beijing_iso()}] 热搜来源: {source}, 共 {len(topics)} 条")

    # 2. 选话题: 指定排名优先, 否则挑当天未用的第一条
    dedup = TopicDedup(BASE / "data" / "used_topics.json")
    if args.topic_rank:
        topic = next((t for t in topics if t.rank == args.topic_rank), None)
        if topic is None:
            print(f"榜上没有第 {args.topic_rank} 名")
            return 1
    else:
        topic = dedup.pick_unused(topics)
        if topic is None:
            print("今日前十话题都已用过, 本时段跳过")
            return 0
    print(f"选用话题: 第{topic.rank}名 {topic.word} (热度 {topic.heat}) {topic.hashtag}")

    # 3. 现写文案(发的时候才写)
    text, angle = generate(topic, rng=rng)
    print(f"角度: {angle}\n文案({len(text)}字): {text}")

    # 4. 现配图(PIL 标题卡占位; 不爬图、不去水印)
    image_path = None
    if not args.no_image:
        try:
            image_path = make_title_card(topic, BASE / "data" / "images", rng=rng)
            print(f"配图: {image_path}")
        except Exception as e:  # noqa: BLE001 - 无 Pillow 时降级为纯文字
            print(f"配图失败, 降级为纯文字: {e}")

    # 5. 代理(按账号固定分配; dry-run 只做分配展示, 不走代理发请求)
    proxies = None
    proxy_url = None
    if (cfg.get("proxy") or {}).get("enabled"):
        pool = load_proxies()
        proxy_url = proxy_for_account(pool, account)
        print(f"代理池: 共 {len(pool)} 条, 本账号固定用 {proxy_url}")
        if not dry_run and proxy_url:
            from proxy_pool import requests_proxies
            proxies = requests_proxies(proxy_url)

    # 6. 发文(dry-run 只写日志)
    poster = WeiboPoster(
        session_path=BASE / ".local" / "weibo-session.json",
        dry_run=dry_run, log_path=BASE / "logs" / "post_log.jsonl",
        proxies=proxies)
    if dry_run:
        result = poster.post(text, image_path)
        print(f"DRY-RUN: 未向微博发请求, 日志已写 logs/post_log.jsonl")
    else:
        if image_path is not None:
            print("带图真发未验证, 本次只发纯文字(见 RESEARCH.md)")
            image_path = None
        result = poster.post(text, image_path)
        print(f"真发结果: ok={result.ok} id={result.weibo_id} url={result.url} err={result.error}")
        if not result.ok:
            return 2

    dedup.mark(topic.word)
    draft = {"ts_beijing": now_beijing_iso(), "topic": topic.word,
             "rank": topic.rank, "heat": topic.heat, "angle": angle,
             "text": text, "image": str(image_path) if image_path else None,
             "dry_run": dry_run, "proxy": proxy_url, "source": source}
    (BASE / "data" / "last_draft.json").write_text(
        json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"样例已存 data/last_draft.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
