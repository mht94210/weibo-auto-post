# 微博自动发文工具（研发验证版）

状态：**研发/验证阶段，未部署到华为云服务器，未登录任何微博账号，未真实发过微博。**
所有发文调用默认 `dry_run: true`，只写日志，不向微博发任何写请求。

服务器：华为云 Flexus，华东-上海一，Ubuntu 24.04，2核2GB，公网 121.37.152.204。
需求：1 个微博号先跑，一天 12 条；到点拉当时实时热搜前十，现写文案、现配 AI 图、马上发；
每个号内容不一样，蹭热搜话题，不发同一内容，不刷赞/评论/转发，不去别人图片的水印。

## 目录

| 文件 | 作用 |
|---|---|
| `config.yaml` | 调度（12 条/天、时段、间隔）、账号、dry-run、代理池配置 |
| `hotsearch.py` | 拉实时热搜：`GET https://weibo.com/ajax/side/hotSearch`（无登录），解析 `data.realtime` |
| `generator.py` | 模板文案生成（只用话题词+排名+热度，不编造新闻细节）；留 `generate_via_llm()` 口子接大模型 |
| `images.py` | 配图占位：PIL 生成话题标题卡（不爬别人图片、不去水印）；留免费图库口子 |
| `poster.py` | 发文模块：dry-run 写日志；真实发文用 `POST https://m.weibo.cn/api/statuses/update`（需扫码登录的 Cookie），默认关闭 |
| `proxy_pool.py` | 代理池：读 `~/workspace/proxy-api/proxies_cn.txt`，轮换 + 按账号固定分配；免费代理不稳定，发文前需逐个验活 |
| `scheduler.py` | 12 个发文时段 + 当天话题去重 + 间隔抖动 |
| `run_once.py` | 跑一个时段：拉热搜 → 选未用话题 → 生成文案 → 配图 → dry-run 发文日志 |
| `test_local.py` | 本地测试：热搜拉取、样例生成、dry-run 日志 |
| `RESEARCH.md` | 调研结论：热搜接口、发文方案、限流风控 |

## 快速验证（本地）

```bash
cd ~/workspace/weibo-auto-post
python3 test_local.py          # 拉实时热搜 + 生成样例 + dry-run 日志
python3 run_once.py --dry-run  # 跑一个时段
python3 run_once.py --dry-run --topic-rank 1
```

## 部署到华为服务器（待定，后面再定方式）

服务器 SSH（root@121.37.152.204，22 已放通）密码只有用户知道，研发阶段不部署。
部署时需要二选一：
1. **SSH 部署**：用户在自己的电脑上用 `ssh root@121.37.152.204` 登一次，把本目录传上去（`scp -r`），或给 Muse 一次性的 SSH 接入方式；密码绝不在聊天里发。
2. **CloudShell 部署**：华为云控制台 CloudShell（浏览器内终端）里粘贴部署命令，不用 SSH 密码。

部署后还要用户做的事（发文才需要）：
- 在服务器上跑扫码登录，拿登录态 Cookie（`SUB`/`SUBP`/`SCF`，存服务器本地 `.local/weibo-session.json`，绝不进仓库、不发回聊天）；
- 把 `config.yaml` 里的 `dry_run` 改成 `false` 才会真发；
- 先用 1 条真发测试，确认能发、链接正常，再开 12 条/天的定时任务。

## 安全红线

- 不刷赞/评论/转发；不发同一内容到多号；不去水印、不爬别人的图；
- **不发带政治内容的话题**（用户规则 2026-11-18，关键词过滤见 `content_filter.py`，命中即跳过该时段）；
- Cookie、密码、token 一律不进仓库、不进日志、不发聊天；
- 文案生成器不编造新闻事实，只用话题词本身 + 排名/热度 + 通用讨论句式。
