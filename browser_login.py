#!/usr/bin/env python3
"""用真浏览器(Playwright + Chromium)做微博扫码登录, 直接读 Cookie 写登录态.

背景: 纯 HTTP 的 SSO 换登录态拿到的 SUB/SUBP 不被 m.weibo.cn 接受
(login=false, 发文 401/403), 且拿不到 SCF; 云端浏览器又读不出 Cookie 值.
本脚本在本地真浏览器里走完整登录流程, 登录成功后直接从浏览器取全套 Cookie.

关键(对照开源实现 wedaren/agentic-weibo-cli):
- 登录页必须带 entry=wapsso(移动端 SSO 入口), 否则只下发 weibo.com 的登录态,
  m.weibo.cn 的接口会报 login=false、发文 100006 请求非法。
- 登录后要循环"逛业务首页 -> 取 Cookie -> 调 /api/config 探活", 直到探活
  login=true 才算成功, 不能只看 URL 或 SUB 出现就收工。
- Cookie 存域名, 供发文时按域优先级去重(m.weibo.cn > .weibo.com)。

用法: python3 browser_login.py
  - 在终端/日志里打印二维码图片位置, 扫码后自动等登录(最长约 7 分钟)
  - 登录成功后写 .local/weibo-session.json(0600), 只打印 Cookie 名字, 不打印值
  - 浏览器资料保存在 .local/browser-profile, 下次可直接复用登录态
"""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# entry=wapsso 是关键: 建立 m.weibo.cn(WAP) 登录态, 普通 signin 只给 web 态
LOGIN_URL = ("https://passport.weibo.com/sso/signin?entry=wapsso"
             "&source=wapssowb&url=https%3A%2F%2Fweibo.com")
HERE = Path(__file__).resolve().parent
LOCAL = HERE / ".local"
PROFILE_DIR = LOCAL / "browser-profile"
QR_PATH = LOCAL / "weibo-qrcode-browser.png"
SESSION_PATH = LOCAL / "weibo-session.json"
MAX_WAIT = 420  # 秒
CHAIN_PROXY = "http://127.0.0.1:18081"
DOMAIN_PRIORITY = ("m.weibo.cn", ".m.weibo.cn", "weibo.com", ".weibo.com",
                   "passport.weibo.com", ".passport.weibo.com")


def _qr_candidates(page):
    """找页面上像二维码的元素(img/canvas, 近似正方形, 100~400px)."""
    out = []
    for el in page.query_selector_all("img, canvas"):
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if not box or box["width"] < 100 or box["width"] > 420:
                continue
            if abs(box["width"] - box["height"]) > 40:
                continue
            out.append(el)
        except Exception:
            continue
    return out


def _shot_qr(page):
    """截一张二维码图; 返回内容 hash, 变化时才写盘."""
    els = _qr_candidates(page)
    data = None
    if els:
        try:
            data = els[0].screenshot()
        except Exception:
            data = None
    if data is None:
        data = page.screenshot(full_page=False)
    h = hashlib.md5(data).hexdigest()
    return h, data


def _domain_score(domain):
    try:
        return len(DOMAIN_PRIORITY) - DOMAIN_PRIORITY.index(domain)
    except ValueError:
        return 0


def _select_cookies(cookies):
    """按参考实现去重: 同名取域优先级高的, 再按名字排序."""
    sel = {}
    for c in cookies:
        if not c.get("value"):
            continue
        cur = sel.get(c["name"])
        if cur is None:
            sel[c["name"]] = c
            continue
        new_score = _domain_score(c.get("domain") or "")
        old_score = _domain_score(cur.get("domain") or "")
        if new_score > old_score:
            sel[c["name"]] = c
    return [sel[k] for k in sorted(sel)]


def _cookie_header(cookies):
    return "; ".join(f"{c['name']}={c['value']}" for c in _select_cookies(cookies))


def _probe_config(cookies):
    """调 m.weibo.cn/api/config 探活; 返回 (login, uid)。走中转代理, 避开出口 MITM。"""
    import requests
    r = requests.get(
        "https://m.weibo.cn/api/config",
        headers={"Cookie": _cookie_header(cookies),
                 "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 "
                                "like Mac OS X) AppleWebKit/605.1.15 "
                                "(KHTML, like Gecko) Version/17.0 "
                                "Mobile/15E148 Safari/604.1 weibo-cli/2.0"),
                 "Referer": "https://m.weibo.cn/",
                 "X-Requested-With": "XMLHttpRequest"},
        proxies={"https": CHAIN_PROXY, "http": CHAIN_PROXY},
        timeout=30)
    d = (r.json().get("data") or {})
    return d.get("login"), d.get("uid")


def _proxy():
    """走本地中转代理 127.0.0.1:18081: 它用最小 CONNECT 经出口代理建隧道,
    浏览器直连出口代理会被拒(出口代理在 CONNECT 后校验 TLS 指纹).
    中转代理由 /tmp/chain-proxy.py 提供, 启动前需确认它在跑."""
    return {"server": CHAIN_PROXY}


def main():
    from playwright.sync_api import sync_playwright

    LOCAL.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("正在打开微博登录页 ...", flush=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled",
                  "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0.0.0 Safari/537.36"],
            viewport={"width": 1280, "height": 900},
            proxy=_proxy(),
            ignore_https_errors=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # 先试复用已有的登录态(资料里已有 Cookie 时不用扫码)
        try:
            for url in ("https://weibo.com/", "https://m.weibo.cn/"):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                except Exception:
                    continue
            keep = [c for c in ctx.cookies()
                    if "weibo" in (c.get("domain") or "")]
            if any(c["name"] in ("SUB", "SUBP", "SCF") for c in keep):
                login, uid = _probe_config(keep)
                print(f"复用已有登录态探活: login={login} uid={uid}", flush=True)
                if login is True or uid:
                    return _save(ctx, keep, uid)
        except Exception as e:
            print(f"复用探活失败: {e}", flush=True)

        page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        last_hash = None
        t0 = time.time()
        while time.time() - t0 < MAX_WAIT:
            # 1) 登录成功判断: URL 离开 passport, 或二维码已消失(已扫码待确认)且有 SUB
            try:
                left_passport = "passport.weibo.com" not in page.url
                qr_gone = not _qr_candidates(page)
                names = {c["name"] for c in ctx.cookies()}
                if left_passport or (qr_gone and "SUB" in names):
                    for url in ("https://weibo.com/", "https://m.weibo.cn/"):
                        try:
                            page.goto(url, wait_until="domcontentloaded",
                                      timeout=30000)
                            page.wait_for_timeout(2000)
                        except Exception:
                            continue
                    keep = [c for c in ctx.cookies()
                            if "weibo" in (c.get("domain") or "")]
                    login, uid = _probe_config(keep)
                    print(f"探活: login={login} uid={uid}", flush=True)
                    if login is True or uid:
                        return _save(ctx, keep, uid)
                    print("探活未通过, 回登录页继续等 ...", flush=True)
                    page.goto(LOGIN_URL, wait_until="domcontentloaded",
                              timeout=30000)
            except Exception as e:
                print(f"探活失败: {e}", flush=True)
            # 2) 刷新二维码截图: 等二维码图片真正加载完再截, 否则是空白框
            try:
                try:
                    page.wait_for_function(
                        "() => { const imgs=[...document.querySelectorAll('img')];"
                        " return imgs.some(i => { const r=i.getBoundingClientRect();"
                        " return r.width>=100 && r.width<=420 && i.naturalWidth>0; }); }",
                        timeout=10000)
                except Exception:
                    page.wait_for_timeout(4000)
                h, data = _shot_qr(page)
                if h != last_hash:
                    QR_PATH.write_bytes(data)
                    last_hash = h
                    print(f"二维码已更新: {QR_PATH} ({datetime.now().strftime('%H:%M:%S')})",
                          flush=True)
            except Exception as e:
                print(f"截图失败: {e}", flush=True)
            page.wait_for_timeout(5000)

        print("登录超时: 7 分钟内未完成扫码确认, 请重新运行.", flush=True)
        ctx.close()
        return 3


def _save(ctx, keep, uid):
    names = sorted({c["name"] for c in keep})
    print(f"Cookie 名: {names}", flush=True)
    print(f"SCF: {'有' if 'SCF' in names else '无'}", flush=True)
    session = {
        "version": 2,
        "uid": str(uid or ""),
        "login_url": LOGIN_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "partial": "SCF" not in names,
        "missing": [] if "SCF" in names else ["SCF"],
        "cookies": [{"name": c["name"], "value": c["value"],
                     "domain": c.get("domain") or "",
                     "path": c.get("path") or "/",
                     "expires": c.get("expires")}
                    for c in keep if c.get("value")],
    }
    SESSION_PATH.write_text(json.dumps(session, ensure_ascii=False),
                            encoding="utf-8")
    SESSION_PATH.chmod(0o600)
    print(f"登录态已存: {SESSION_PATH} (只在本地, 不进仓库)", flush=True)
    ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
