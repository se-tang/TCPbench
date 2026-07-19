# 部署指南

给想自己搭一套 TCP Bench 的人看的。如果你只是想测测自己 VPS 的线路质量，
直接看 [README](./README.md) 里的一条命令就够了，不用往下看。

VPS 网络线路质量自助测试。用户在自己的 VPS 上跑一条 curl 命令，脚本测完自动上传结果，
生成一个可分享的报告页；有排行榜，可以按延迟或时间排序浏览所有历史提交。不涉及 SSH、
不需要用户提供任何账号密码。

## 工作流程

```
用户 VPS                          服务器 (tcpbench.com)
────────                          ─────────────────────────────
curl run.sh | bash
  │
  ├─ 本地跑约 65 个站点的 TCP 握手延迟测试（纯 bash，无依赖）
  │
  └─ POST JSON 结果 ──────────────▶  FastAPI 接收、限流、存 PostgreSQL
                                        │
                     ◀──────────────── 返回 report_id + 带机主令牌的报告链接
终端打印报告链接
```

## 目录结构

```
tcpbench/
  backend/
    main.py                  FastAPI 主程序（路由、限流、报告/排行榜渲染）
    database.py               数据库连接
    models.py                  表结构（Report / SubmissionLog）
    schemas.py                  请求数据校验
    requirements.txt
    .env.example                 环境变量模板
    tcpbench.service              systemd 服务文件
    templates/                     首页 + 报告页 + 排行榜页 HTML
    static/favicon.svg              网站图标
    scripts/run.sh                   用户在自己 VPS 上跑的脚本
  nginx/tcpbench.conf              OpenResty/Nginx 反代配置示例
  LICENSE
```

## 系统要求

- Debian/Ubuntu 系 Linux（本文以此为例，其他发行版命令名可能不同）
- Python 3.10+（3.13 也可以，见下方「常见报错」里 psycopg2 那条）
- PostgreSQL（版本不敏感，`main.py` 启动时会自动建表）
- Nginx / OpenResty 均可，跟用 1Panel 还是宝塔这类面板无关，本质是同一套 Linux 组件

## 全新部署步骤

### 1. 建数据库

1Panel → 数据库 → PostgreSQL → 新建数据库，比如库名 `tcpbench`，账号密码自己定，记下来。

### 2. 上传代码到 VPS

```bash
cd /opt
git clone https://github.com/se-tang/tcpbench.git
```

（先 push 到 GitHub 仓库，再在 VPS 上 clone，比手动传文件夹干净，以后改代码同步也方便）

### 3. 建虚拟环境、装依赖

```bash
cd /opt/tcpbench/backend
python3 -m venv venv
```

如果这一步报 `ensurepip is not available`，说明系统没装 venv 模块，装一下再重试：

```bash
apt install -y python3.13-venv   # 版本号换成 python3 --version 看到的实际版本
rm -rf venv && python3 -m venv venv
```

激活并安装依赖：

```bash
source venv/bin/activate
pip install -r requirements.txt
```

`psycopg2-binary` 如果在这一步报编译错误，看本文最后的「常见报错」一节，别慌，是环境缺东西，不是代码问题。

### 4. 配置环境变量

```bash
cp .env.example .env
nano .env
```

至少要填对的两项：`DATABASE_URL`（账号密码要跟第 1 步建的库对上）、`SITE_URL`（你的域名）。

### 5. 先手动跑一下，确认没问题

```bash
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000
```

**另开一个终端窗口**（这个窗口留着别关，Ctrl+C 会停掉进程），执行：

```bash
curl http://127.0.0.1:8000/health
```

看到 `{"ok":true}` 就说明启动成功，数据库连接、依赖安装都没问题。第一次启动会自动建表，
不用手动跑 SQL。确认完，回到第一个窗口 `Ctrl+C` 停掉，准备用 systemd 常驻。

### 6. 配置 systemd

```bash
sudo cp tcpbench.service /etc/systemd/system/
sudo nano /etc/systemd/system/tcpbench.service   # 确认路径、User 对不对
sudo systemctl daemon-reload
sudo systemctl enable --now tcpbench
sudo systemctl status tcpbench
```

`enable` 这一步很重要——不加的话服务器重启后不会自动拉起服务。

### 7. 配置反代 + 域名

- 面板里给你的域名申请 SSL 证书
- 参考 `nginx/tcpbench.conf` 加一段反代配置，注意一定要带上
  `X-Real-IP` / `X-Forwarded-For`，不然后端限流会失效（所有请求会被当成同一个 IP）

### 8. 验证完整流程

在任意一台机器上（不一定是部署后端的这台）跑：

```bash
curl -sL https://你的域名/run.sh | bash
```

跑完应该会打印一个报告链接，打开能看到延迟表格；再访问 `/leaderboard` 确认排行榜正常。

---

## 迁移到新服务器

换服务器要迁移三块东西：**代码**、**数据库**、**服务配置**，光拷代码文件夹是不够的——
测试数据在 PostgreSQL 里，不在代码文件夹中；`.env` 也没进 Git，需要单独处理。

1. **新服务器先装好基础环境**（Python、PostgreSQL、Nginx/OpenResty），跟"全新部署"第 1-3 步一样走一遍
2. **代码**：`git clone`，别手动拷文件夹，干净且不会带上 `venv/`、`__pycache__/` 这些垃圾
3. **数据库**：
   ```bash
   # 旧服务器导出
   pg_dump -U 账号 -d tcpbench > tcpbench_backup.sql
   # 传到新服务器（在你自己电脑上执行）
   scp root@旧IP:tcpbench_backup.sql root@新IP:/opt/
   # 新服务器导入（先建好同名空数据库）
   psql -U 账号 -d tcpbench < /opt/tcpbench_backup.sql
   ```
   用 1Panel自带的数据库备份恢复功能也可以，效果一样，更省事。
4. **`.env`**：从旧服务器 scp 一份过去，或者手动 `nano` 重新填一遍，注意账号密码要跟新服务器上
   建的数据库账号对上
5. **systemd + 反代 + SSL**：在新服务器上完整走一遍"全新部署"的第 6、7 步，
   **这一步经常被漏掉**——迁移时最容易忘记新服务器上还没注册 `tcpbench.service`，
   导致 `curl 127.0.0.1:8000` 连不上，见下方常见报错
6. **切 DNS 前先绕开 DNS 测试**：
   ```bash
   curl -H "Host: 你的域名" https://新IP/health -k
   ```
   看到 `{"ok":true}` 说明反代和证书都配对了，再去域名商那里把 A 记录切过去
7. **观察几天确认稳定**，再去旧服务器上删网站配置、退订旧 VPS

### 数据库版本落后怎么办

如果恢复用的备份是很久以前做的，可能缺后来加的字段（比如 `owner_token`，
2026 年加的"机主令牌下载图片"功能用到的列）。启动服务后报 `Internal Server Error`，
看日志（`journalctl -u tcpbench -n 50`）如果提示某个 column 不存在，手动补上：

```
 docker exec -it 容器名 psql -U tcpbench
```

```sql
ALTER TABLE reports ADD COLUMN owner_token VARCHAR(32);
```

```
\q  #退出
```

`main.py` 里的 `Base.metadata.create_all()` 只会创建缺失的表，不会给已存在的表补充新列，
所以表结构升级目前都靠手动 `ALTER TABLE`，没有引入 Alembic 这类迁移工具（项目还小，暂时不需要）。

---

## 常见报错排查

### `The virtual environment was not created successfully because ensurepip is not available`

系统没装 `python3-venv`：
```bash
apt install -y python3.13-venv   # 换成你实际的 python3 版本号
rm -rf venv && python3 -m venv venv
```

### `pip install -r requirements.txt` 卡在编译 `psycopg2-binary`

这个包按理说该装预编译好的二进制包，不用编译。如果你的 Python 版本比较新
（比如 3.13），PyPI 上可能还没有对应的预编译包，pip 会退而求其次从源码编译，
编译就需要依次补上这几个系统依赖：

```bash
apt install -y libpq-dev        # 缺 pg_config
apt install -y build-essential   # 缺 gcc
apt install -y python3-dev        # 缺 Python.h
```

如果补齐这三个之后还报错，提示类似 `_PyInterpreterState_Get` 找不到，
说明是 `psycopg2-binary` 版本太老、没适配你的新版 Python，直接升级版本，
跳过编译（本仓库 `requirements.txt` 已经用的是 `>=2.9.10`，如果你 clone 的是旧版本，
照这个改一下就行）：

```bash
pip install "psycopg2-binary>=2.9.10"
```

### `systemctl restart tcpbench` 报 `Unit tcpbench.service not found`

新服务器上还没注册这个 systemd 服务（迁移时最容易漏的一步），照"全新部署"第 6 步走一遍：

```bash
sudo cp tcpbench.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tcpbench
```

### `curl -H "Host: 域名" https://IP/health` 报 `TLS unrecognized name`

这是正常的，不是配置问题——直接拿裸 IP 握手 HTTPS，服务器不知道该用哪张证书（SNI 机制），
用真实域名 `curl https://你的域名/health` 测才对。

### 页面 `Internal Server Error`，日志里是 Jinja2 模板报错

`journalctl -u tcpbench -n 50 --no-pager` 看具体是哪个模板变量的问题，
一般是模板用到了某个变量、但 `main.py` 传给模板的数据里没有这个字段——
检查对应路由函数（`main.py` 里 `TemplateResponse` 那一段）传的 context 字典，
和模板里 `{{ }}` / `{% %}` 用到的变量名是不是完全对得上。

---

## 关于防刷 / 限流

现在这版做了这几层：

- **请求体大小限制**：单次上报超过 2MB 直接拒绝，防止塞垃圾数据撑爆数据库
- **IP 频率限制**：默认每个 IP 每小时最多提交 5 次（`.env` 里的 `RATE_LIMIT_COUNT` / `RATE_LIMIT_WINDOW_MIN` 可调），
  记录存在 `submission_log` 表，没有引入 Redis，直接查表判断，逻辑简单，你自己也看得懂
- **数据结构校验**：`schemas.py` 里用 pydantic 限制了 `results` 数组长度、`samples` 长度、`loss_pct` 取值范围，
  乱传字段或者超大数组会直接被 422 拒绝
- **报告导出图片权限**：只有跑测试拿到的直链（带机主令牌）能看到「导出图片」按钮，
  排行榜里点进去的访客只能看不能下载

如果以后发现有人绕过限流刷榜（比如换 IP），可以加的下一层防护：

- 给 `run.sh` 里塞一个跟时间戳相关的签名（比如 HMAC），后端校验签名和时间戳，
  防止别人绕开脚本直接拿 curl 命令伪造数据 —— 不是绝对安全（脚本本身公开），
  但能挡住随手写个 for 循环刷接口的情况
- 按 `hostname + ip_masked` 做去重展示，同一台机器短时间内重复提交只保留最新一条

这两个先不做也没关系，等真的看到滥用了再加不迟。

## 备份

数据库出过一次事故——迁移时手滑删库，好在有前一天的备份才没全丢。建议加个定时任务，
别再只靠"记得手动导出":

```bash
crontab -e
```

加一行（账号密码换成实际的，路径确保存在且有写权限）：

```
0 3 * * * pg_dump -U tcpbench -d tcpbench > /opt/tcpbench_backups/backup_$(date +\%Y\%m\%d).sql
```

顺手加一条清理超过 30 天的旧备份，避免占满磁盘：

```
0 4 * * * find /opt/tcpbench_backups/ -name "*.sql" -mtime +30 -delete
```

## 后续可以加的功能

- **按站点筛选**：比如单独看"到 GitHub 延迟最低的机器排名"
- **历史对比**：同一个 hostname 多次提交时画一条延迟变化曲线（比如换线路前后对比）
- **数据库迁移工具**：如果以后表结构还要改，引入 Alembic 会比手动 `ALTER TABLE` 更省心

这些都是在现在的 `reports` 表基础上加查询接口和页面，不需要伤筋动骨。