"""代理池(轮换 + 按账号固定分配).

来源: 本地免费代理列表, 默认读 ~/workspace/proxy-api/proxies_cn.txt
(1277 条, 2026-11-12 生成; 另有 proxies_best.txt 186 条严选).
免费代理不稳定、存活短: 发文前必须逐个验活, 失败就换下一个,
绝不能拿它做登录/支付; 每个账号固定用一个代理, 不要每次发都换 IP,
否则微博更容易判异常.
以后要接付费住宅代理时, 在这里加一个供应商(读环境变量里的 API key),
保持同样的接口: get_proxy(account_id) -> str | None.
"""
from __future__ import annotations

import hashlib
import os
import random
import urllib.request
from pathlib import Path

DEFAULT_PROXY_FILE = Path.home() / "workspace" / "proxy-api" / "proxies_cn.txt"
BEST_PROXY_FILE = Path.home() / "workspace" / "proxy-api" / "proxies_best.txt"


def load_proxies(path: Path | None = None) -> list[str]:
    path = path or DEFAULT_PROXY_FILE
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("http://") or line.startswith("https://"):
            out.append(line)
    # 去重保序
    return list(dict.fromkeys(out))


def proxy_for_account(proxies: list[str], account_id: str) -> str | None:
    """按账号 ID 稳定哈希, 同一账号总是分到同一个代理(避免频繁换 IP)."""
    if not proxies:
        return None
    idx = int(hashlib.sha256(account_id.encode()).hexdigest(), 16) % len(proxies)
    return proxies[idx]


def next_proxy(proxies: list[str], exclude: set[str] | None = None) -> str | None:
    """轮换取一个未排除的代理, 供失败重试时用."""
    pool = [p for p in proxies if not exclude or p not in exclude]
    return random.choice(pool) if pool else None


def check_proxy_alive(proxy: str, timeout: int = 8) -> bool:
    """验活: 经该代理 GET 微博热搜接口, 200 且能解析 JSON 才算活.
    注意: 这只是连通性检查, 不登录、不发文."""
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        req = urllib.request.Request(
            "https://weibo.com/ajax/side/hotSearch",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://weibo.com/"})
        with opener.open(req, timeout=timeout) as resp:
            return resp.status == 200 and b"realtime" in resp.read(2000)
    except Exception:  # noqa: BLE001 - 代理死活只看 True/False
        return False


def requests_proxies(proxy: str | None) -> dict[str, str] | None:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


if __name__ == "__main__":
    all_p = load_proxies()
    best_p = load_proxies(BEST_PROXY_FILE)
    print(f"proxies_cn: {len(all_p)} 条, proxies_best: {len(best_p)} 条")
    print("账号 demo 分配:", proxy_for_account(all_p, "weibo_account_1"))
    print("轮换示例:", next_proxy(all_p))
