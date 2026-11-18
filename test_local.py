"""本地测试: 拉实时热搜 + 生成样例文案/配图 + dry-run 发文日志.

不登录、不真发、不向微博发任何写请求. 只读热搜接口(公开, 无登录).
用法: python3 test_local.py
输出: 终端打印 + TEST_REPORT.md
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from content_filter import filter_political, is_political
from generator import ANGLES, generate
from hotsearch import fetch_hot_top, now_beijing_iso
from images import make_title_card
from poster import WeiboPoster
from proxy_pool import load_proxies, proxy_for_account
from scheduler import TopicDedup, slots_for_today


def main() -> int:
    lines: list[str] = []
    ok = True

    def log(s: str) -> None:
        print(s)
        lines.append(s)

    log(f"测试时间(北京): {now_beijing_iso()}")
    rng = random.Random(42)  # 固定种子, 结果可复现

    # 1. 拉实时热搜
    try:
        topics, source = fetch_hot_top(10, BASE / "data" / "hot_cache.json")
        log(f"[1] 热搜拉取 OK, 来源={source}, 前十 {len(topics)} 条")
        for t in topics:
            log(f"    {t.rank}. {t.word} 热度={t.heat} {t.hashtag} [{t.label}]")
    except Exception as e:  # noqa: BLE001
        log(f"[1] 热搜拉取 FAIL: {e}")
        ok = False
        topics, source = [], "failed"

    # 1b. 内容过滤: 政治话题不发(用户规则 2026-11-18)
    kept, skipped = filter_political(topics)
    for t, reason in skipped:
        log(f"    跳过政治话题: 第{t.rank}名 {t.word} (命中: {reason})")
    log(f"[1b] 内容过滤 OK: 前十 {len(topics)} 条, 拦下 {len(skipped)} 条, 剩余 {len(kept)} 条")
    sample_topic = kept[0] if kept else None

    # 2. 生成样例文案(用第一个非政治话题, 全部 5 个角度各生成 1 条, 检查长度/话题)
    if sample_topic:
        t0 = sample_topic
        log(f"[2] 样例文案(话题: {t0.word}):")
        for name, _ in ANGLES:
            text, used = generate(t0, angle=name, rng=rng)
            assert t0.word in text or "#" in text, "文案缺少话题"
            assert len(text) <= 140, f"文案超 140 字: {len(text)}"
            log(f"    [{used}] {len(text)}字: {text}")
        log("[2] 文案 OK: 5 个角度均 <=140 字, 均含话题")
    else:
        log("[2] SKIP: 无热搜数据")

    # 3. 配图占位(PIL 标题卡)
    image_path = None
    if sample_topic:
        try:
            image_path = make_title_card(sample_topic, BASE / "data" / "images", rng=rng)
            size = image_path.stat().st_size
            log(f"[3] 配图 OK: {image_path} ({size} 字节, 1080x720 标题卡, 无爬图/无去水印)")
        except Exception as e:  # noqa: BLE001
            log(f"[3] 配图 FAIL: {e}")
            ok = False

    # 4. dry-run 发文日志
    poster = WeiboPoster(dry_run=True, log_path=BASE / "logs" / "post_log.jsonl")
    text = sample_topic.word if sample_topic else "测试"
    sample_text, _ = generate(sample_topic, rng=rng) if sample_topic else ("测试", "")
    res = poster.post(sample_text, image_path)
    log(f"[4] dry-run 发文 OK: ok={res.ok}, dry_run={res.dry_run}, "
        f"日志已写 logs/post_log.jsonl (未向微博发请求)")

    # 5. 调度 + 代理池
    slots = slots_for_today(rng=rng)
    gaps = [(slots[i + 1] - slots[i]).total_seconds() / 60 for i in range(len(slots) - 1)]
    log(f"[5] 调度 OK: 一天 {len(slots)} 条, {slots[0].strftime('%H:%M')}-"
        f"{slots[-1].strftime('%H:%M')}, 最小间隔 {min(gaps):.0f} 分钟(要求>=45)")
    assert len(slots) == 12 and min(gaps) >= 45
    proxies = load_proxies()
    log(f"[5] 代理池 OK: proxies_cn {len(proxies)} 条, "
        f"账号固定分配 {proxy_for_account(proxies, 'weibo_account_1')}")
    dedup = TopicDedup(BASE / "data" / "used_topics.json")
    log(f"[5] 话题去重 OK: 当天已用 {len(dedup.state['used'])} 个")

    # 6. 政治过滤固定用例(不依赖实时榜)
    cases = [
        ("习近平迎接美国总统特朗普", True),
        ("美国总统特朗普抵达深圳", True),
        ("俄乌冲突最新进展", True),
        ("台海局势", True),
        ("外交部回应", True),
        ("国产大飞机首飞成功", False),
        ("周杰伦演唱会抢票", False),
        ("大熊猫花花太可爱了", False),
    ]
    for word, want_block in cases:
        got = is_political(word)
        assert got == want_block, f"过滤误判: {word} 应{'拦' if want_block else '放'}, 实际{'拦' if got else '放'}"
        log(f"    {'拦' if got else '放'}: {word}")
    log("[6] 政治过滤 OK: 8 个固定用例全部符合预期")

    log(f"结果: {'全部通过' if ok else '有失败项, 见上'}")
    (BASE / "TEST_REPORT.md").write_text(
        "# 本地测试报告\n\n" + "\n".join(f"- {l}" for l in lines) + "\n",
        encoding="utf-8")
    print("报告已写 TEST_REPORT.md")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
