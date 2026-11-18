"""发文模块.

默认 dry_run=True: 只写日志, 不向微博发任何请求(不登录、不发文、不探登录态).
真实发文(需显式 dry_run=False + 本地登录态文件):
  POST https://m.weibo.cn/api/statuses/update
  Form: content=<正文>
  头: Content-Type application/x-www-form-urlencoded; charset=UTF-8,
      X-Requested-With: XMLHttpRequest,
      Origin: https://m.weibo.cn, Referer: https://m.weibo.cn/compose/,
      UA: iPhone Safari (见 agentic-weibo-cli 的 api_client.py 源码).
  登录态: 本地 .local/weibo-session.json, Cookie 含 SUB/SUBP/SCF 至少其一;
  登录态探测: GET https://m.weibo.cn/api/config, 看 data.login / data.uid.
  登录方式: 服务器上扫码登录(用户用微博 App 扫码), Cookie 只存服务器本地,
  绝不进仓库、不进日志、不发回聊天.
  图片发文: 该 CLI 的 post_weibo() 只支持纯文字, 传图接口未验证,
  本模块暂不支持带图真发, 带图时抛 NotImplementedError(见 post() 注释).
  失败处理: 401/403(登录态失效)或验证码 -> 立即停发并告警, 不自动重试登录;
  其他 5xx -> 最多重试 1 次, 仍失败则记日志跳过本时段.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

POST_URL = "https://m.weibo.cn/api/statuses/update"
CONFIG_URL = "https://m.weibo.cn/api/config"
BEIJING = timezone(__import__("datetime").timedelta(hours=8))
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.0 Mobile/15E148 Safari/604.1 weibo-cli/2.0")


@dataclass
class PostResult:
    dry_run: bool
    ok: bool
    text: str
    image_path: str | None = None
    weibo_id: str | None = None
    url: str | None = None
    error: str | None = None


class WeiboPoster:
    def __init__(self, session_path: Path | None = None,
                 dry_run: bool = True, log_path: Path | None = None,
                 proxies: dict[str, str] | None = None):
        self.session_path = session_path
        self.dry_run = dry_run
        self.log_path = log_path
        self.proxies = proxies
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": IPHONE_UA})

    def _log(self, record: dict) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load_cookie(self) -> str:
        if not self.session_path or not self.session_path.exists():
            raise RuntimeError(
                "未找到登录态文件(默认 .local/weibo-session.json): "
                "请先在服务器上扫码登录; dry-run 模式不需要登录态.")
        data = json.loads(self.session_path.read_text(encoding="utf-8"))
        cookies = data.get("cookies") or []
        parts = [f"{c['name']}={c['value']}" for c in cookies
                 if c.get("name") in ("SUB", "SUBP", "SCF") and c.get("value")]
        if not parts:
            raise RuntimeError("登录态文件里没有 SUB/SUBP/SCF, 请重新扫码登录.")
        return "; ".join(parts)

    def check_session(self) -> dict:
        """登录态探测(只在非 dry-run 时调用): GET /api/config."""
        cookie = self._load_cookie()
        resp = self.http.get(CONFIG_URL, headers={"Cookie": cookie},
                             proxies=self.proxies, timeout=30)
        resp.raise_for_status()
        data = (resp.json().get("data") or {})
        return {"login": data.get("login"), "uid": data.get("uid")}

    def post(self, text: str, image_path: str | None = None) -> PostResult:
        record = {"ts_beijing": datetime.now(BEIJING).isoformat(timespec="seconds"),
                  "dry_run": self.dry_run, "text": text,
                  "image": str(image_path) if image_path else None}
        if self.dry_run:
            record.update({"ok": True, "note": "dry-run: 未向微博发请求"})
            self._log(record)
            return PostResult(dry_run=True, ok=True, text=text, image_path=image_path)
        if image_path is not None:
            # 传图接口未验证(见 RESEARCH.md): 宁可先只发文字, 也不按传闻硬发.
            raise NotImplementedError(
                "带图真发未验证: m.weibo.cn 传图接口字段未核实, "
                "需登录态就绪后做 1 条真发测试确认后再接; 当前只能发纯文字.")
        cookie = self._load_cookie()
        headers = {"Cookie": cookie,
                   "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "X-Requested-With": "XMLHttpRequest",
                   "Origin": "https://m.weibo.cn",
                   "Referer": "https://m.weibo.cn/compose/"}
        try:
            resp = self.http.post(POST_URL, headers=headers,
                                  data={"content": text},
                                  proxies=self.proxies, timeout=30)
        except requests.RequestException as e:
            record.update({"ok": False, "error": f"网络失败: {e}"})
            self._log(record)
            return PostResult(dry_run=False, ok=False, text=text, error=str(e))
        if resp.status_code in (401, 403):
            record.update({"ok": False, "error": f"登录态失效 HTTP {resp.status_code}, 需重新扫码"})
            self._log(record)
            return PostResult(dry_run=False, ok=False, text=text,
                              error="登录态失效, 需重新扫码登录")
        try:
            payload = resp.json()
        except ValueError:
            record.update({"ok": False, "error": "接口返回非 JSON"})
            self._log(record)
            return PostResult(dry_run=False, ok=False, text=text, error="接口返回非 JSON")
        if payload.get("ok") != 1:
            record.update({"ok": False, "error": str(payload)[:300]})
            self._log(record)
            return PostResult(dry_run=False, ok=False, text=text,
                              error=f"发文失败: {str(payload)[:200]}")
        data = payload.get("data") or {}
        weibo_id = str(data.get("id") or "")
        bid = data.get("bid") or ""
        url = f"https://m.weibo.cn/status/{bid}" if bid else None
        record.update({"ok": True, "weibo_id": weibo_id, "url": url})
        self._log(record)
        return PostResult(dry_run=False, ok=True, text=text,
                          weibo_id=weibo_id, url=url)
