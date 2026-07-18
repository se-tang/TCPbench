# 部署指南

给想自己搭一套 TCP Bench 的人看的。如果你只是想测测自己 VPS 的线路质量，
直接看 [README](./README.md) 里的一条命令就够了，不用往下看。

VPS 网络线路质量自助测试。用户在自己的 VPS 上跑一条 curl 命令，脚本测完自动上传结果，
生成一个可分享的报告页。不涉及 SSH、不需要用户提供任何账号密码。

## 工作流程

```
用户 VPS                          你的服务器 (tcpbench.com)
────────                          ─────────────────────────────
curl run.sh | bash
  │
  ├─ 本地跑 60 个站点的 TCP 握手延迟测试（纯 bash，无依赖）
  │
  └─ POST JSON 结果 ──────────────▶  FastAPI 接收、限流、存 PostgreSQL
                                        │
                     ◀──────────────── 返回 report_id + 报告链接
终端打印报告链接
```

## 目录结构

```
tcpbench/
  backend/
    main.py              FastAPI 主程序（路由、限流、报告渲染）
    database.py           数据库连接
    models.py              表结构（Report / SubmissionLog）
    schemas.py              请求数据校验
    requirements.txt
    .env.example             环境变量模板
    tcpbench.service          systemd 服务文件
    templates/                首页 + 报告页 HTML
    scripts/run.sh             用户在自己 VPS 上跑的脚本
  nginx/tcpbench.conf         OpenResty 反代配置示例
```

## 部署步骤（按你现在 1Panel + PostgreSQL 的环境）

### 1. 建数据库

1Panel → 数据库 → PostgreSQL → 新建数据库，比如库名 `tcpbench`，账号密码自己定，记下来。

### 2. 上传代码到 VPS

把整个 `tcpbench/` 文件夹传到 VPS，比如 `/opt/tcpbench`（可以先传到 GitHub 仓库，
再在 VPS 上 `git clone`，这样以后改代码方便同步）。

### 3. 装依赖、建虚拟环境

```bash
cd /opt/tcpbench/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. 配置环境变量

```bash
cp .env.example .env
nano .env   # 填真实的 DATABASE_URL 和 SITE_URL
```

### 5. 先手动跑一下，确认没问题

```bash
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000
```

浏览器（或者本机 curl）访问 `http://VPS_IP:8000/health`，返回 `{"ok":true}` 就说明启动成功。
第一次启动会自动建表，不用手动跑 SQL。

`Ctrl+C` 停掉，准备用 systemd 常驻。

### 6. 配置 systemd（跟你部署 Telegram bot 是一个路子）

```bash
sudo cp tcpbench.service /etc/systemd/system/
sudo nano /etc/systemd/system/tcpbench.service   # 确认路径、User 对不对
sudo systemctl daemon-reload
sudo systemctl enable --now tcpbench
sudo systemctl status tcpbench
```

### 7. 配置 OpenResty 反代 + 域名

- 1Panel 里给 `tcpbench.com` 申请 SSL 证书
- 参考 `nginx/tcpbench.conf` 加一段反代配置，注意一定要带上
  `X-Real-IP` / `X-Forwarded-For`，不然后端限流会失效（所有请求会被当成同一个 IP）

### 8. 验证完整流程

在任意一台机器上（不一定是部署后端的这台）跑：

```bash
curl -sL https://tcpbench.com/run.sh | bash
```

跑完应该会打印一个报告链接，打开能看到延迟表格。

## 关于防刷 / 限流

现在这版做了这几层：

- **请求体大小限制**：单次上报超过 2MB 直接拒绝，防止塞垃圾数据撑爆数据库
- **IP 频率限制**：默认每个 IP 每小时最多提交 5 次（`.env` 里的 `RATE_LIMIT_COUNT` / `RATE_LIMIT_WINDOW_MIN` 可调），
  记录存在 `submission_log` 表，没有引入 Redis，直接查表判断，逻辑简单，你自己也看得懂
- **数据结构校验**：`schemas.py` 里用 pydantic 限制了 `results` 数组长度、`samples` 长度、`loss_pct` 取值范围，
  乱传字段或者超大数组会直接被 422 拒绝

如果以后发现有人绕过限流刷榜（比如换 IP），可以加的下一层防护：

- 给 `run.sh` 里塞一个跟时间戳相关的签名（比如 HMAC），后端校验签名和时间戳，
  防止别人绕开脚本直接拿 curl 命令伪造数据 —— 不是绝对安全（脚本本身公开），
  但能挡住随手写个 for 循环刷接口的情况
- 按 `hostname + ip_masked` 做去重展示，同一台机器短时间内重复提交只保留最新一条

这两个先不做也没关系，等真的看到滥用了再加不迟。

这些都是在现在的 `reports` 表基础上加查询接口和页面，不需要改数据结构。
