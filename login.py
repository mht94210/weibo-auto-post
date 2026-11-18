"""扫码登录微博, 拿 Cookie 登录态, 存 .local/weibo-session.json.

用法(在服务器 CloudShell 里):
  python3 login.py              # 取二维码 -> 终端打印 ASCII 码 -> 等扫码确认 -> 存登录态 -> 验登录态
  python3 login.py --check      # 只验现有登录态, 不取二维码
  python3 login.py --help       # 参数说明
  python3 login.py --self-test  # 离线自查(不联网): 解析/二维码渲染/存取, 不登录

登录流程(接口依据, 见 LOGIN.md; 取码一步已于 2026-11-18 在本地实测通过,
轮询/换登录态尚未真跑过):
  1. 取二维码:
     GET https://login.sina.com.cn/sso/qrcode/image?entry=sso&callback=STK_<毫秒戳>&size=180
     返回 JSONP `window.STK_xxx && STK_xxx({...});`(末尾带分号, 解析要宽容),
     成功 retcode=20000000, data 里有 qrid(轮询用) 和 image(二维码图地址,
     https://v2.qr.weibo.cn/inf/gen?...&data=https://passport.weibo.cn/signin/qrcode/scan?qr=...);
     微博登录页 https://passport.weibo.com/sso/signin?entry=wapsso 展示的也是同一张图.
     注意: size 必传, 不传返回 retcode=50114017 "size error"(2026-11-18 本地实测).
  2. 轮询扫码状态(每 2 秒):
     GET https://login.sina.com.cn/sso/qrcode/check?entry=sso&qrid=<qrid>&callback=STK_<毫秒戳>
     retcode: 50114001 未扫 / 50114002 已扫未确认 / 20000000 已确认 / 50114004 已失效.
  3. 已确认后拿 alt 换登录态:
     GET https://login.sina.com.cn/sso/login.php?entry=qrcodesso&returntype=TEXT
         &crossdomain=1&cdult=3&domain=weibo.com&alt=<alt>&savestate=30
         &callback=STK_<毫秒戳>&action=login
     再逐个 GET 返回里的跨域登录地址(含 passport.weibo.com/wbsso/login),
     浏览器/Session 里的 Cookie 即被置为登录态(SUB/SUBP/SCF 等).
     (另有 /sso/v2/qrcode/image、/sso/v2/qrcode/check; 2026-11-18 本地实测 v2 与 v1
     行为相同: 都要传 size=180, 成功都返回 image+qrid, 不是之前推测的 data.url.
     本脚本先走 v1 链路, 若确认返回里有 data.url 则改走它.)
  4. 存登录态: .local/weibo-session.json, 与 agentic-weibo-cli 相同的 version 2 结构,
     poster.py 的 _load_cookie() 只读其中 SUB/SUBP/SCF.
  5. 验登录态: 调 poster.WeiboPoster.check_session(), GET https://m.weibo.cn/api/config,
     只打印 login/uid.

二维码怎么给用户扫:
  - 终端打印 ASCII 二维码(半方块字符, 1 个字符 = 1 列模块, 2 行模块 = 1 行字,
    57 模块的码约 57 列宽, 80 列终端能放下); 同时把二维码存为 .local/weibo-qrcode.png,
    用户也可以把 PNG 传出来用手机扫(见 LOGIN.md).
  - 每张码最多等 120 秒, 过期自动刷新一次, 最多刷新 2 次(共 3 张码, 约 6 分钟).

安全红线:
  - 不向微博发任何写请求, 不碰账号密码, 不索要密码;
  - 二维码内容(qr token/扫码链接)、qrid、alt/ticket、Cookie 值一律不打印、不记日志;
    只打印登录状态、uid、Cookie 名字;
  - 登录态文件只存服务器本地(.local/ 已 gitignore), 权限 0600, 绝不进仓库、不回传.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from poster import WeiboPoster  # noqa: E402  (登录态文件格式与 poster.py 共用)

QR_IMAGE_URL = "https://login.sina.com.cn/sso/qrcode/image"
QR_CHECK_URL = "https://login.sina.com.cn/sso/qrcode/check"
SSO_LOGIN_URL = "https://login.sina.com.cn/sso/login.php"
LOGIN_PAGE_URL = ("https://passport.weibo.com/sso/signin?entry=wapsso"
                  "&source=wapssowb&url=https%3A%2F%2Fweibo.com")
DEFAULT_SESSION = BASE / ".local" / "weibo-session.json"
DEFAULT_QR_PNG = BASE / ".local" / "weibo-qrcode.png"

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")

# 轮询 retcode(见 LOGIN.md): 字符串/数字都可能出现, 统一按字符串比对.
RET_WAITING = "50114001"    # 二维码未扫描
RET_SCANNED = "50114002"    # 已扫描, 等手机上点确认
RET_CONFIRMED = "20000000"  # 已确认, 可拿 alt 换登录态
RET_EXPIRED = "50114004"    # 二维码已失效, 需刷新

AUTH_COOKIE_NAMES = ("SUB", "SUBP", "SCF")

EXIT_OK = 0
EXIT_ARG = 2
EXIT_AUTH = 3
EXIT_API = 4
EXIT_NET = 5


class LoginError(Exception):
    """登录流程失败(带明确原因, 不含任何敏感值)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stk() -> str:
    """JSONP 回调名, 形如 STK_<13 位毫秒戳>."""
    return f"STK_{int(time.time() * 1000)}"


def parse_jsonp(text: str) -> dict:
    """解析 STK_xxx({...}) 或裸 JSON, 返回 dict; 解析失败抛 LoginError.

    宽容写法: 直接取第一个 '{' 到最后一个 '}' 之间的 JSON.
    微博现在返回 `window.STK_xxx && STK_xxx({...});` 形式(末尾带分号),
    旧的 `(...)$` 正则会被分号顶掉而误判为非 JSON.
    """
    text = (text or "").strip()
    if not text:
        raise LoginError("接口返回为空")
    start = text.find("{")
    end = text.rfind("}")
    payload = text[start:end + 1] if 0 <= start < end else text
    try:
        data = json.loads(payload)
    except ValueError as e:
        raise LoginError(f"接口返回不是 JSON/JSONP(前 60 字: {text[:60]!r})") from e
    if not isinstance(data, dict):
        raise LoginError("接口返回不是 JSON 对象")
    return data


def _pick_str(data: dict, *keys: str) -> str | None:
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


class QrLogin:
    def __init__(self, session_path: Path = DEFAULT_SESSION,
                 qr_png: Path = DEFAULT_QR_PNG,
                 timeout: int = 120, max_refresh: int = 2,
                 poll_interval: int = 2, show_ascii: bool = True):
        self.session_path = session_path
        self.qr_png = qr_png
        self.timeout = timeout
        self.max_refresh = max_refresh
        self.poll_interval = poll_interval
        self.show_ascii = show_ascii
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": DESKTOP_UA,
            "Referer": "https://passport.weibo.com/sso/signin?entry=wapsso",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })

    # ---- 1. 取二维码 ----
    def fetch_qr(self) -> tuple[str, bytes]:
        """返回 (qrid, 二维码 PNG 字节). 失败抛 LoginError(不含敏感值)."""
        try:
            resp = self.http.get(
                QR_IMAGE_URL,
                # 注意: size 必传(2026-11-18 实测: 不传返回
                # retcode=50114017 "size error"); 180 是登录页常用尺寸.
                params={"entry": "sso", "callback": _stk(), "size": 180},
                timeout=30)
        except requests.RequestException as e:
            raise LoginError(f"取二维码网络失败: {e}") from e
        if resp.status_code != 200:
            raise LoginError(f"取二维码接口 HTTP {resp.status_code}")
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if ctype.startswith("image/"):
            # 极少数情况下接口直接返回图片: qrid 从 Set-Cookie 里取.
            qrid = self.http.cookies.get("qrid") or resp.cookies.get("qrid")
            if not qrid:
                raise LoginError("取二维码: 接口直接返回图片, 但未给 qrid, 无法轮询")
            return qrid, resp.content
        data = parse_jsonp(resp.text)
        ret = str(data.get("retcode", ""))
        if ret and ret != "20000000":
            # 非成功码直接报出来(只含码和 msg, 不含敏感值), 便于判断接口是否又变了.
            # 已知: 50114017 "size error" = 缺 size 参数.
            raise LoginError(
                f"取二维码被拒: retcode={ret} msg={data.get('msg')!r}")
        inner = data.get("data") or {}
        if not isinstance(inner, dict):
            raise LoginError("取二维码: 返回里没有 data 对象, 接口可能变了")
        qrid = _pick_str(inner, "qrid")
        img_url = _pick_str(inner, "image", "img", "qr_url", "url", "qrcode_url")
        if not qrid or not img_url:
            raise LoginError("取二维码: 返回里没有 qrid 或二维码图地址, 接口可能变了")
        try:
            img = self.http.get(img_url, timeout=30)
        except requests.RequestException as e:
            raise LoginError(f"下载二维码图网络失败: {e}") from e
        if img.status_code != 200 or not img.content:
            raise LoginError(f"下载二维码图失败 HTTP {img.status_code}")
        return qrid, img.content

    # ---- 2. 轮询 ----
    def poll_once(self, qrid: str) -> tuple[str, dict]:
        """一次轮询, 返回 (retcode, data). 网络失败抛 LoginError."""
        try:
            resp = self.http.get(
                QR_CHECK_URL,
                params={"entry": "sso", "qrid": qrid, "callback": _stk()},
                timeout=15)
        except requests.RequestException as e:
            raise LoginError(f"轮询扫码状态网络失败: {e}") from e
        if resp.status_code != 200:
            raise LoginError(f"轮询扫码状态 HTTP {resp.status_code}")
        data = parse_jsonp(resp.text)
        inner = data.get("data")
        if isinstance(inner, dict):
            ret = str(data.get("retcode", inner.get("retcode", "")))
            return ret, inner
        return str(data.get("retcode", "")), {}

    def wait_scan(self, qrid: str) -> dict:
        """等扫码确认, 返回确认后的 data(含 alt 或 url). 超时/失效抛 LoginError."""
        deadline = time.time() + self.timeout
        announced_scanned = False
        while time.time() < deadline:
            ret, inner = self.poll_once(qrid)
            if ret == RET_CONFIRMED:
                return inner
            if ret == RET_SCANNED:
                if not announced_scanned:
                    print("  已扫码, 请在手机微博 App 上点「确认登录」…", flush=True)
                    announced_scanned = True
            elif ret == RET_WAITING:
                pass
            elif ret == RET_EXPIRED:
                raise LoginError("二维码已失效")
            elif ret:
                raise LoginError(f"轮询返回未知 retcode={ret}, 接口可能变了")
            else:
                raise LoginError("轮询返回里没有 retcode, 接口可能变了")
            time.sleep(self.poll_interval)
        raise LoginError(f"等待扫码超时({self.timeout} 秒内无人确认)")

    # ---- 3. alt 换登录态 ----
    def finalize(self, confirmed: dict) -> None:
        """用确认结果换 Cookie, 写进当前 Session. 失败抛 LoginError."""
        alt = _pick_str(confirmed, "alt")
        direct_url = _pick_str(confirmed, "url")
        if direct_url and not alt:
            # 新版 v2 流程: 成功后直接给跳转地址, 跟过去即完成登录.
            self._follow_login_url(direct_url)
        elif alt:
            params = {"entry": "qrcodesso", "returntype": "TEXT",
                      "crossdomain": "1", "cdult": "3", "domain": "weibo.com",
                      "alt": alt, "savestate": "30",
                      "callback": _stk(), "action": "login"}
            try:
                resp = self.http.get(SSO_LOGIN_URL, params=params, timeout=30)
            except requests.RequestException as e:
                raise LoginError(f"换登录态网络失败: {e}") from e
            if resp.status_code != 200:
                raise LoginError(f"换登录态接口 HTTP {resp.status_code}")
            self._follow_crossdomain(resp.text)
        else:
            raise LoginError("扫码已确认, 但返回里既没有 alt 也没有跳转地址, 接口可能变了")
        names = {c.name for c in self.http.cookies}
        missing = [n for n in AUTH_COOKIE_NAMES if n not in names]
        if missing:
            raise LoginError(
                f"换登录态后 Cookie 里缺少 {','.join(missing)}"
                f"(已有: {','.join(sorted(names)) or '无'}), 请重新扫码")

    def _follow_login_url(self, url: str) -> None:
        try:
            self.http.get(url, timeout=30, allow_redirects=True)
        except requests.RequestException as e:
            raise LoginError(f"跟随登录跳转地址网络失败: {e}") from e

    def _follow_crossdomain(self, text: str) -> None:
        urls = re.findall(r"https?://[^\s\"'\\<>]+", text or "")
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            if "weibo" not in url and "sina" not in url:
                continue
            if not any(k in url for k in ("login", "sso", "crossdomain", "wbsso")):
                continue
            try:
                self.http.get(url, timeout=15)
            except requests.RequestException:
                continue  # 单个跨域地址失败不致命, 继续走下一个

    # ---- 4. 存登录态 ----
    def save_session(self, uid: str | None = None) -> Path:
        cookies = []
        for c in self.http.cookies:
            domain = (c.domain or "").lstrip(".")
            if "weibo" not in domain and "sina" not in domain:
                continue
            cookies.append({
                "name": c.name, "value": c.value,
                "domain": c.domain or "", "path": c.path or "/",
                "expires": c.expires,
                "secure": bool(c.secure),
                "http_only": bool(getattr(c, "_rest", {}).get("HttpOnly", False)),
            })
        if not any(c["name"] in AUTH_COOKIE_NAMES for c in cookies):
            raise LoginError("保存前检查: Cookie 里没有 SUB/SUBP/SCF, 请重新扫码")
        payload = {"version": 2, "uid": uid or "",
                   "login_url": LOGIN_PAGE_URL,
                   "updated_at": _now_iso(), "cookies": cookies}
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            self.session_path.chmod(0o600)
        except OSError:
            pass
        return self.session_path

    # ---- 5. 验登录态 ----
    def verify(self) -> dict:
        poster = WeiboPoster(session_path=self.session_path, dry_run=True)
        return poster.check_session()

    # ---- 主流程 ----
    def run(self) -> int:
        # 已有登录态先复用, 不用重复扫码.
        if self.session_path.exists():
            try:
                info = self.verify()
                if info.get("login"):
                    print(f"已有登录态可用: login={info.get('login')} "
                          f"uid={info.get('uid') or '未知'}, 不用重新扫码.")
                    return EXIT_OK
                print("已有登录态文件, 但探测显示未登录, 重新扫码…")
            except Exception as e:  # noqa: BLE001 - 任何旧登录态问题都走重新扫码
                print(f"已有登录态不可用({e}), 重新扫码…")
        for attempt in range(self.max_refresh + 1):
            try:
                qrid, png = self.fetch_qr()
            except LoginError as e:
                print(f"取二维码失败: {e}")
                return EXIT_API
            self.qr_png.parent.mkdir(parents=True, exist_ok=True)
            self.qr_png.write_bytes(png)
            print(f"\n第 {attempt + 1} 张二维码(有效期约 {self.timeout} 秒):")
            print(f"  图片已存: {self.qr_png}")
            if self.show_ascii:
                try:
                    print(render_qr_ascii(png))
                except Exception as e:  # noqa: BLE001 - 渲染失败不阻塞, PNG 还能扫
                    print(f"  (终端二维码渲染失败: {e}, 请用上面存的 PNG 图片扫码)")
            print("  请打开微博手机 App → 我的 → 扫一扫, 扫上面这个码, 并在手机上点确认登录.")
            try:
                confirmed = self.wait_scan(qrid)
            except LoginError as e:
                print(f"  {e}")
                if "已失效" in str(e) or "超时" in str(e):
                    if attempt < self.max_refresh:
                        print("  自动刷新一张新二维码…")
                        continue
                    print("  二维码已刷新 2 次仍无人确认, 请稍后重新运行 python3 login.py")
                    return EXIT_AUTH
                return EXIT_API
            try:
                self.finalize(confirmed)
                self.save_session()
                info = self.verify()
            except LoginError as e:
                print(f"登录失败: {e}")
                return EXIT_AUTH
            except Exception as e:  # noqa: BLE001 - 验登录态的网络/解析失败要说明
                print(f"登录态验证失败: {e}")
                return EXIT_NET
            print(f"\n登录成功! login={info.get('login')} uid={info.get('uid') or '未知'}")
            print(f"登录态已存: {self.session_path} (只在服务器本地, 不进仓库)")
            return EXIT_OK
        return EXIT_AUTH


# ---- 终端 ASCII 二维码(半方块字符, 不解码, 只按像素渲染) ----
def _module_count(binary: list[list[int]]) -> int:
    """从时序图案(第 7 行模块, 黑白交替)反推二维码边长(模块数).

    候选边长为 21+4k; 对每个候选按该边长采样第 7 行, 与理想黑白交替比对,
    命中率最高的即为真实边长. 识别失败返回 0.
    """
    h = len(binary)
    w = len(binary[0]) if h else 0
    if h < 21 or w < 21:
        return 0
    best_n, best_score = 0, 0.0
    for n in range(21, 180, 4):
        cell = w / n
        row = int(6 * cell + cell / 2)
        if row >= h:
            continue
        ok = tot = 0
        for col in range(8, n - 8):
            x = int(col * cell + cell / 2)
            if x >= w:
                break
            want_dark = ((col - 8) % 2 == 0)
            tot += 1
            if bool(binary[row][x]) == want_dark:
                ok += 1
        if tot and ok / tot > best_score:
            best_n, best_score = n, ok / tot
    return best_n if best_score >= 0.9 else 0


def qr_matrix_from_png(png: bytes) -> list[list[bool]]:
    """把二维码 PNG 转成布尔矩阵(True=黑模块). 识别失败时用 57 格最近邻采样兜底."""
    img = Image.open(__import__("io").BytesIO(png)).convert("L")
    w, h = img.size
    px = img.load()
    binary = [[1 if px[x, y] < 128 else 0 for x in range(w)] for y in range(h)]
    # 去静区: 黑像素外框.
    rows = [y for y in range(h) if any(binary[y])]
    cols = [x for x in range(w) if any(binary[y][x] for y in range(h))]
    if not rows or not cols:
        raise LoginError("二维码图片里没有黑色像素, 无法渲染")
    y0, y1, x0, x1 = rows[0], rows[-1], cols[0], cols[-1]
    crop = [row[x0:x1 + 1] for row in binary[y0:y1 + 1]]
    n = _module_count(crop)
    if n == 0:
        n = 57  # 兜底: 按常见边长最近邻采样(可能扫不出来, PNG 仍可用)
    cw, ch = len(crop[0]) / n, len(crop) / n
    return [[bool(crop[int((r + 0.5) * ch)][int((c + 0.5) * cw)])
             for c in range(n)] for r in range(n)]


def render_qr_ascii(png: bytes, quiet: int = 2) -> str:
    """布尔矩阵 -> 半方块字符画(1 字符宽 = 1 模块, 2 行模块 = 1 行字).

    '█' 上下都黑, '▀' 上黑下白, '▄' 上白下黑, ' ' 上下都白.
    外围留 quiet 个模块的白边(静区), 否则手机扫不出来.
    """
    m = qr_matrix_from_png(png)
    n = len(m)
    pad = [[False] * (n + 2 * quiet) for _ in range(quiet)]
    body = [[False] * quiet + row + [False] * quiet for row in m]
    grid = pad + body + [row[:] for row in pad]
    lines = []
    for r in range(0, len(grid), 2):
        top = grid[r]
        bot = grid[r + 1] if r + 1 < len(grid) else [False] * len(top)
        lines.append("".join(
            "█" if (a and b) else "▀" if a else "▄" if b else " "
            for a, b in zip(top, bot)))
    return "\n".join(lines)


# ---- 离线自查(不联网, 不登录) ----
def self_test() -> int:
    print("离线自查(不联网):")
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  [{'OK' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    # 1. JSONP 解析
    d = parse_jsonp('STK_1794963000000({"retcode":20000000,"data":{"alt":"x"}})')
    check("JSONP 解析(retcode/alt)", d.get("retcode") == 20000000
          and (d.get("data") or {}).get("alt") == "x")
    d2 = parse_jsonp('{"retcode":50114001,"data":{}}')
    check("裸 JSON 解析", str(d2.get("retcode")) == "50114001")
    # 真实返回形式(2026-11-18 实测): window.STK_xxx && STK_xxx({...}); 末尾带分号
    d3 = parse_jsonp('window.STK_1795002079412 && STK_1795002079412'
                     '({"retcode":50114017,"msg":"size error","data":{"errline":65}});')
    check("window.STK 包装 + 末尾分号解析",
          str(d3.get("retcode")) == "50114017" and d3.get("msg") == "size error")
    d4 = parse_jsonp('window.STK_1795002079412 && STK_1795002079412'
                     '({"retcode":20000000,"msg":"succ","data":{"qrid":"abc","image":"https://x/y.png"}});')
    check("成功返回解析(qrid/image)",
          str(d4.get("retcode")) == "20000000"
          and (d4.get("data") or {}).get("qrid") == "abc")
    try:
        parse_jsonp("not json at all {{{")
        check("非法返回抛错", False)
    except LoginError:
        check("非法返回抛错", True)

    # 2. retcode 常量
    check("retcode 常量齐全",
          {RET_WAITING, RET_SCANNED, RET_CONFIRMED, RET_EXPIRED}
          == {"50114001", "50114002", "20000000", "50114004"})

    # 3. 二维码渲染: 本地画一张带三定位图案+时序图案的假码(21x21, 外围留白), 不联网
    from PIL import Image as _Im
    n = 21
    S = 10
    img = _Im.new("L", (n * S + 40, n * S + 40), 255)
    px = img.load()

    def _set(r: int, c: int) -> None:
        for y in range(20 + r * S, 20 + (r + 1) * S):
            for x in range(20 + c * S, 20 + (c + 1) * S):
                px[x, y] = 0

    def _finder(r0: int, c0: int) -> None:
        for r in range(7):
            for c in range(7):
                if r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4):
                    _set(r0 + r, c0 + c)

    _finder(0, 0)
    _finder(0, n - 7)
    _finder(n - 7, 0)
    for i in range(8, n - 8):
        if (i - 8) % 2 == 0:
            _set(6, i)
            _set(i, 6)
    for (r, c) in [(10, 10), (12, 15), (15, 12), (18, 18)]:
        _set(r, c)
    import io as _io
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    mat = qr_matrix_from_png(buf.getvalue())
    check("二维码模块数识别(时序图案反推 21x21)",
          len(mat) == 21 and len(mat[0]) == 21
          and [int(v) for v in mat[6][8:15]] == [1, 0, 1, 0, 1, 0, 1])
    art = render_qr_ascii(buf.getvalue())
    check("ASCII 二维码渲染(半方块字符, 含静区)",
          "█" in art and "▀" in art and len(art.splitlines()) >= 10)

    # 4. 登录态存取 roundtrip(临时目录, 假 Cookie, 不联网)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / ".local"
        q = QrLogin(session_path=tmp / "weibo-session.json",
                    qr_png=tmp / "weibo-qrcode.png")
        q.http.cookies.set("SUB", "fake-sub", domain=".weibo.com", path="/")
        q.http.cookies.set("SUBP", "fake-subp", domain=".weibo.com", path="/")
        q.http.cookies.set("SCF", "fake-scf", domain=".weibo.cn", path="/")
        q.http.cookies.set("OTHER", "x", domain=".weibo.com", path="/")
        saved = q.save_session(uid="123456")
        raw = json.loads(saved.read_text(encoding="utf-8"))
        names = {c["name"] for c in raw["cookies"]}
        check("登录态写盘(version 2, 含 SUB/SUBP/SCF)",
              raw.get("version") == 2 and raw.get("uid") == "123456"
              and {"SUB", "SUBP", "SCF"} <= names)
        check("登录态文件权限 0600",
              (saved.stat().st_mode & 0o777) == 0o600)
        poster = WeiboPoster(session_path=saved, dry_run=True)
        check("poster 能读出登录态 Cookie(只拼 SUB/SUBP/SCF)",
              poster._load_cookie() == "SUB=fake-sub; SUBP=fake-subp; SCF=fake-scf")

    print("自查全部通过" if ok else "自查有 FAIL, 见上")
    return EXIT_OK if ok else EXIT_API


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="扫码登录微博, 把 Cookie 登录态存到 .local/weibo-session.json "
                    "(只存服务器本地; 不碰账号密码; 不发任何写请求).")
    ap.add_argument("--session", default=str(DEFAULT_SESSION),
                    help="登录态文件路径 (默认 .local/weibo-session.json)")
    ap.add_argument("--qr-png", default=str(DEFAULT_QR_PNG),
                    help="二维码图片保存路径 (默认 .local/weibo-qrcode.png)")
    ap.add_argument("--timeout", type=int, default=120,
                    help="每张二维码最长等待秒数 (默认 120)")
    ap.add_argument("--max-refresh", type=int, default=2,
                    help="二维码过期/超时后最多刷新次数 (默认 2, 共 3 张码)")
    ap.add_argument("--poll-interval", type=int, default=2,
                    help="轮询扫码状态间隔秒数 (默认 2)")
    ap.add_argument("--no-ascii", action="store_true",
                    help="不在终端打印 ASCII 二维码, 只保存 PNG")
    ap.add_argument("--check", action="store_true",
                    help="只验证现有登录态是否有效, 不取二维码、不登录")
    ap.add_argument("--self-test", action="store_true",
                    help="离线自查(不联网): 解析/渲染/存取, 不登录")
    args = ap.parse_args(argv)

    if args.timeout <= 0 or args.max_refresh < 0 or args.poll_interval <= 0:
        ap.error("timeout/max-refresh/poll-interval 须为正数")
    if args.self_test:
        return self_test()

    q = QrLogin(session_path=Path(args.session), qr_png=Path(args.qr_png),
                timeout=args.timeout, max_refresh=args.max_refresh,
                poll_interval=args.poll_interval, show_ascii=not args.no_ascii)
    if args.check:
        if not q.session_path.exists():
            print(f"没有登录态文件: {q.session_path}, 请先运行 python3 login.py 扫码登录")
            return EXIT_AUTH
        try:
            info = q.verify()
        except Exception as e:  # noqa: BLE001 - 验登录态失败原因直接告诉用户
            print(f"登录态验证失败: {e}")
            return EXIT_NET
        print(f"登录态有效: login={info.get('login')} uid={info.get('uid') or '未知'}")
        return EXIT_OK if info.get("login") else EXIT_AUTH
    return q.run()


if __name__ == "__main__":
    raise SystemExit(main())
