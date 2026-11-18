# 微博自动发文工具 — 调研结论（2026-11-18 实测）

## 1. 热搜榜怎么拉 — 已实测可用

主接口：`GET https://weibo.com/ajax/side/hotSearch`，**无需登录**，返回 JSON。

实测（2026-11-18，VM 直接跑 `urllib`，无 Cookie）：
- HTTP 200，`ok=1`，`data.realtime` 共 52 条；前十已存 `data/hot_top10_20261118.json`。
- 当时前三：1 美国总统特朗普抵达深圳（热度 2511532）、2 迎接特朗普仪式（1188479）、3 APEC多场密集对话释放大量前沿信息（992359）。
- 必带请求头：`User-Agent: Mozilla/5.0 ... Chrome/...` + `Referer: https://weibo.com/` + `Accept: application/json`；缺 Referer 会被判非法请求。
- 字段：`word`（话题词）、`num`（热度）、`word_scheme`（带 # 的话题，个别条目为 null 时自行拼 `#word#`）、`label_name`（新/热/空）、`flag`、`is_ad`（=1 为广告，**必须过滤**）、`rank` 字段有重复值，排序请用列表顺序（`realpos`），不要用 `rank`。
- 限流：同 IP 1 分钟内超过约 10 次请求会被临时封禁。工具只在发文时段拉一次（一天 12 次），远低于上限；另加 1–3 秒随机延时和重试退避。
- 失败备用（按顺序）：
  1. 重试 3 次，间隔 2/5/10 秒，换 UA；
  2. 备用接口 `https://60s.viki.moe/v2/weibo`（第三方聚合镜像，免登录，返回热搜列表；只作备用，不依赖它）；
  3. 再失败则用 `data/` 下最近一次缓存 + 跳过本时段（宁可少发一条，不发过期话题），并写日志告警。
- 以下接口已验证**不可直接用**：`s.weibo.com/top/summary?cate=realtimehot` 和 `m.weibo.cn/api/container/getIndex?...filter_type=realtimehot` 都会 302 跳到新浪访客系统（Sina Visitor System），需先过访客验证，不适合服务器定时任务。

## 2. 发文的可行办法

### 方案 A（推荐）：m.weibo.cn Cookie 发文 + 扫码登录（社区 CLI 已验证的链路）

- 链路来源：开源项目 `wedaren/agentic-weibo-cli`（GitHub，2026-04-04 创建，0 star；
  注意：有博客称它是“微博官方 2026 年 6 月开源”，**与仓库实际信息不符**，请按第三方社区工具对待，上服务器前必须先读代码审计，不要把它当官方背书）。
- 已验证（该项目 README，2026-04-04 本地实测）：扫码登录、`post` 真实发布成功并返回微博 ID/BID/链接、`list`/`show` 可读。
- 发文接口（已读该项目 `scripts/weibo_cli/_svc_write.py`、`api_client.py` 源码核实，无需登录即可读代码）：
  - `POST https://m.weibo.cn/api/statuses/update`
  - Form：`content=<正文>`；头：`Content-Type: application/x-www-form-urlencoded; charset=UTF-8`、`X-Requested-With: XMLHttpRequest`、`Origin: https://m.weibo.cn`、`Referer: https://m.weibo.cn/compose/`、UA 为 iPhone Safari；
  - 登录态：Cookie 里 `SUB`/`SUBP`/`SCF` 至少其一；登录态探测：`GET https://m.weibo.cn/api/config`，看 `data.login`/`data.uid`。
  - 登录流程：服务器上跑 `login`，自动打开本地 Chrome 到微博登录页，**用户用微博 App 扫码**，工具自动提取 Cookie 写到服务器本地 `.local/weibo-session.json`（已 gitignore，绝不回传）。Cookie 失效后重新扫码。
  - 该 CLI 的 `post_weibo()` **只支持纯文字**，源码里没有图片上传逻辑。
- 图片发文（**未验证**）：m.weibo.cn 的传图接口（传图后再带 pic_id 发文）在公开资料中有多种写法、未在本机核实，**不要直接按传闻写**。v1 先跑纯文字；上图片前必须在登录态就绪后做一次 1 条真发测试，确认传图接口和字段后再接。备选是走官方 CLI（见方案 B），它声称支持单图/多图，同样需实测。

### 方案 B（备选，正规但门槛高）：微博开放平台 / 官方 weibo-cli

- 官方 CLI：`cli.weibo.com`，OAuth 2.1 + PKCE，MCP 端点 `https://cli.weibo.com/mcp`；`weibo-cli auth login`（浏览器授权或设备码），`statuses/update.json` 发微博（`POST`，支持纯文字、单图、多图）。需要用户在浏览器里完成一次微博网页授权，token 由客户端保存刷新。
- 开放平台（open.weibo.com）传统路线门槛很高：应用需审核，通过前只能授权 15 个测试账号，且测试账号发的帖子**不进入公共信息流**；文档限额：单个授权用户每天 100 次调用、发帖接口每用户每小时 30 次、单个 IP 每小时 15000 次。个人号想靠这条路稳定发 12 条/天，要先过应用审核，周期和结果都不保证。**结论：先走方案 A 验证，方案 B 只作长期备选。**

### 方案 C（不推荐）：网页版 weibo.com 自动化发文

- 用 Playwright/Selenium 模拟浏览器在 weibo.com 上发文：能跑，但最吃风控（滑块、验证码、设备指纹），服务器无头环境最容易触发验证；只作为 A/B 都失败时的最后手段。

### 用户需要做的事（只在真发阶段）

1. 在服务器上扫码登录一次微博（方案 A）或在浏览器里做一次微博网页授权（方案 B）；不提供微博账号密码，密码绝不在聊天里发。
2. 先真发 1 条测试，确认链接和图片正常，再开定时任务。
3. 20 个号以后再加：每个号单独扫码、单独存登录态、单独走代理 IP；不要在同一个登录态下混用多号。

## 3. 一天 12 条的风险评估

- 微博创作者中心 2025-04-09 公告：**个人账号单日发博超过 50 条后**，超出的博文限制进入推荐流、热搜流等公域分发（私域不受影响）。12 条/天在 50 条红线之下，不会因此直接限流，但仍高于常规建议的 1–3 条/天。
- 真正的风险点是质量和行为模式，不是单纯条数：同一话题重复发、文案高度雷同、短时间内连发、带商业推广标签、引战造谣，都会触发限流/验证码/封号。
- 应对（已做到工具里）：时段拉开到约 60–90 分钟一条（07:30–22:30），每条间隔加 ±10 分钟抖动；当天同一话题只用一次；每条换角度模板；只发原创讨论，不做点赞/评论/转发；发现验证码或 403 立即停发并告警，不自动重试登录。
- 另外：12 条/天对 2Mbps/100GB 流量包没有压力（纯文字 + 小图，月流量可忽略）。

## 4. 配图

- 坚决不做：爬热搜配图、去水印、盗用他人图片。
- v1：PIL 生成话题标题卡（话题词 + 排名 + 热度 + 账号水印自己的号名，可选），纯原创，无版权问题。
- 以后可接：Pexels/Unsplash 免费图库 API（需申请 API key，用有明确授权的图），或接大模型生图（需模型 API）。接口已在 `images.py` 里留口子。
