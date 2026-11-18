# 扫码登录说明（login.py）

## 在服务器上怎么跑

```bash
cd /root/weibo-auto-post
python3 login.py            # 取二维码 -> 终端打印 ASCII 码 -> 等扫码 -> 存登录态 -> 验登录态
python3 login.py --check    # 只验现有登录态是否还有效, 不取二维码
python3 login.py --help     # 参数说明
```

已有登录态且还有效时, `login.py` 直接复用, 不重复扫码。
登录成功后登录态存在服务器本地 `.local/weibo-session.json`（已 gitignore，权限 0600），
`poster.py` 发文时直接读它；`config.yaml` 不用改，`dry_run` 仍由发文命令控制。

## 二维码怎么展示给用户扫

服务器没有界面，二维码有两种给法：

1. **终端 ASCII 码（默认）**：`login.py` 把二维码图转成半方块字符画打印在 CloudShell
   终端里（1 个字符 = 1 列模块，2 行模块 = 1 行字；57 模块的码约 57 列宽，
   80 列终端能放下；外围留 2 个模块白边）。用户在 CloudShell 终端里直接拿手机扫。
   ⚠️ 未验证点：终端字体/行高会不会让码变形、手机好不好扫——第一次实测时如果
   ASCII 码扫不出来，改用第 2 种办法。
2. **PNG 图片**：每次取码都同时存为 `.local/weibo-qrcode.png`。把它从服务器传出来
   （例如用华为云控制台的文件管理下载，或 `python3 -m http.server` 临时取一次，
   用完即关），在手机/电脑上打开图片扫。PNG 是最稳的扫码方式。

扫码步骤（用户在手机上做）：打开微博 App → 我的 → 扫一扫 → 扫码 → 手机上点
「确认登录」。脚本轮询到“已扫未确认”时会提示，确认后自动往下走，不用再碰终端。

## 登录流程的接口依据

链路来自三处互相印证的来源（都没有在本工具上真跑过，见“未验证点”）：

1. **微博登录页本身**（2026-11-18 实测拉取
   `https://passport.weibo.com/sso/signin?entry=wapsso&source=wapssowb&url=https%3A%2F%2Fweibo.com`）：
   页面标题就是“扫描二维码登录”，二维码图地址是
   `https://v2.qr.weibo.cn/inf/gen?...&data=https%3A%2F%2Fpassport.weibo.cn%2Fsignin%2Fqrcode%2Fscan%3Fqr%3D...`，
   即码内容是 `passport.weibo.cn/signin/qrcode/scan?qr=<token>` 形式的扫码链接。
2. **掘金文章《Python扫码登录微博获取cookie》**（juejin.cn/post/7013238622115594276）：
   取码 `GET login.sina.com.cn/sso/qrcode/image?entry=sso&callback=STK_<戳>`，
   返回 JSONP 里有 `qrid` 和二维码图地址；轮询
   `GET login.sina.com.cn/sso/qrcode/check?entry=sso&qrid=<qrid>&callback=STK_<戳>`，
   retcode：`50114001` 未扫、`50114002` 已扫未确认、`20000000` 已确认、`50114004` 已失效；
   已确认后取 `data.alt`，再
   `GET login.sina.com.cn/sso/login.php?entry=qrcodesso&returntype=TEXT&crossdomain=1&cdult=3&domain=weibo.com&alt=<alt>&savestate=30&callback=STK_<戳>`
   （文中注明：第二个 url 后面要加 `&action=login` 才正确），
   逐个请求返回里的跨域登录地址，Cookie 即被置为登录态。
3. **findxyz/vb-weibo-plus 的调研文档**（2026-07-13，基于 Playwright 探针 + `ssologin.js` 分析，
   github.com/findxyz/vb-weibo-plus/blob/HEAD/docs/research/weibo-renewal.md）：
   扫码登录期间稳定观察到的关键请求正是
   `sso/qrcode/image` → `sso/qrcode/check` → `sso/login.php` →
   `passport.weibo.com/wbsso/login`；每轮登录稳定得到 15 个 Cookie，
   含 `.weibo.com` 的 `SUB`/`SUBP`；另注：当前页面业务包里还有新版
   `/sso/v2/qrcode/image`、`/sso/v2/qrcode/check`（成功后直接给 `data.url`），
   本脚本先走 2026-07 实测过的 v1 链路，若确认返回里有 `data.url` 则改走它。

对照：`agentic-weibo-cli` 的登录是另一条路——Playwright 打开本地 Chrome 到微博登录页，
用户在浏览器里扫码，工具从浏览器上下文提取 Cookie（见其 `browser_login.py`）。
服务器无界面、装 Playwright + Chromium 又重又容易触发风控，所以本工具改走上面
的纯 HTTP 二维码链路；若 v1 链路失效，再考虑 Playwright 方案。

## 登录态文件格式

与 `agentic-weibo-cli` 相同的 version 2 结构（`poster.py` 只读其中 `SUB`/`SUBP`/`SCF`）：

```json
{"version": 2, "uid": "1234567890",
 "login_url": "https://passport.weibo.com/sso/signin?entry=wapsso&source=wapssowb&url=https%3A%2F%2Fweibo.com",
 "updated_at": "2026-11-18T11:20:00+00:00",
 "cookies": [{"name": "SUB", "value": "...", "domain": ".weibo.com",
              "path": "/", "expires": 1777880106, "secure": true, "http_only": true}]}
```

验证登录态：`GET https://m.weibo.cn/api/config`，看 `data.login` / `data.uid`
（`poster.WeiboPoster.check_session()`，只在登录成功后调用一次）。

## 超时与刷新

- 每张二维码最多等 **120 秒**（`--timeout` 可改），超时或返回 `50114004`（已失效）
  自动刷新一张新码，最多刷新 **2 次**（`--max-refresh` 可改，共 3 张码，约 6 分钟）。
- 轮询间隔 2 秒（`--poll-interval` 可改）。不要把间隔改太小，会被限流。
- 3 张码都没人确认：脚本退出（exit 3），请稍后重新运行 `python3 login.py`。

## 依赖

无新增依赖：只用 `requests` + `Pillow`（`requirements.txt` 已有）。
ASCII 渲染不解码二维码，只按像素画半方块字符；二维码图片本身直接从微博下载，
不用 `qrcode` 库生成。

## 已知的未验证点（留到服务器上实测）

1. **v1 链路在 2026-11 是否还通**：取码/轮询/换登录态三步都只在纸面（2021 年文章 +
   2026-07 探针文档）上印证过，**没有在本工具上真跑过**。第一次跑 `login.py` 时，
   若取码返回里没有 `qrid`/图地址，或轮询 retcode 不在四种之内，脚本会直接报错
   说明原因（不含敏感值），再按 LOGIN.md 里的 v2 链路（`/sso/v2/qrcode/image`、
   `/sso/v2/qrcode/check`）或 Playwright 方案改。
2. **终端 ASCII 码好不好扫**：字体、行高、CloudShell 终端宽度都可能影响识别；
   扫不出来时用 `.local/weibo-qrcode.png` 图片扫。
   另：微博的二维码图中间嵌了微博 logo（`v2.qr.weibo.cn` 图片地址里带 `logo=` 参数），
   ASCII 画中间会有一块黑斑，纠错等级 M（地址里 `level=M`）可容忍，不影响扫码。
3. **扫码后 Cookie 字段是否齐全**：`finalize()` 要求换登录态后至少有 `SUB`/`SUBP`/`SCF`
   之一才存盘；若微博只给了其中一部分或给了新字段，脚本会报缺了哪个（只报名字），
   再按实测结果调 `poster.py` 的读取逻辑。
4. **登录态有效期**：扫码登录的 `SUB` 有效期以实测为准；失效后 `poster.py` 发文会报
   401/403，届时重新跑 `python3 login.py` 扫码即可，不要自动续期（2026-07 的调研
   结论：没有已验证的现代续期协议，401 时重新扫码是最可靠的办法）。
5. **扫码时可能要二次验证**：若账号触发风控，手机确认后可能要求短信/验证码，
   脚本轮询不到“已确认”会在 120 秒后超时；这时按手机上的提示完成验证后重新跑脚本。
