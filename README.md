# TCPBench

一条命令，评估 VPS 访问全球主流网站时的 TCP 网络质量。

TCPBench 在被测机器本地执行纯 Bash 测速，生成完整报告和排行榜。无需安装额外组件，不采集账号、SSH 或服务器文件。

## 快速开始

```bash
curl -sL https://tcpbench.com/run.sh | bash
```

支持 IPv4、IPv6 以及双栈顺序测试：

```bash
IP_MODE=v4   curl -sL https://tcpbench.com/run.sh | bash
IP_MODE=v6   curl -sL https://tcpbench.com/run.sh | bash
IP_MODE=both curl -sL https://tcpbench.com/run.sh | bash
```

## 核心能力

- IPv4 与 IPv6 独立测试、独立评分、独立排行榜。
- IPv4 覆盖 76 个目标站点，IPv6 覆盖 52 个支持 AAAA 的站点。
- 每个域名独立解析并固定目标 IP，减少 CDN 节点切换带来的误差。
- 每个站点保存 60 个正式 TCP 握手样本。
- 以 P50、丢包率和连接稳定性衡量线路质量。
- 测试者可补充机器所属商家、地区和套餐信息。
- 测试者专用链接可以下载完整测试图，公开链接不包含 token。
- 支持 v4、v6、旧版分榜和 Telegram 分享评分卡片。

## 测量逻辑

```text
检测地址族
  -> 解析候选 IP
  -> 预检候选地址
  -> 固定目标 IP
  -> 预热 1 次
  -> 正式连接 60 次
  -> 计算 P50、丢包率和趋势数据
  -> 上传报告
```

测试只测量 TCP 443 握手，不包含网页内容下载或带宽测试。

## 评分机制

评分以站点的绝对 P50 延迟为主，并结合历史基准和丢包率：

| 站点 P50 | 基础分 |
|:---:|:---:|
| ≤5ms | 5.0 |
| ≤10ms | 4.5 |
| ≤20ms | 4.0 |
| ≤40ms | 3.2 |
| ≤80ms | 2.2 |
| ≤150ms | 1.2 |
| ≤300ms | 0.5 |
| >300ms | 0 |

历史数据用于小幅修正，最终按丢包率扣分。基准按月校准，并逐步过渡到完全使用 v2 P50 数据。

| 等级 | 综合评分 | 定义 |
|:---:|:---:|:---|
| S | ≥80 | 极速冲浪节点 |
| A | ≥65 | 优秀体验 |
| B | ≥50 | 日常可用 |
| C | ≥35 | 将就使用 |
| D | <35 | 延迟偏高 |

## 报告与排行榜

- `/leaderboard`：按地址族查看公开排名。
- `/r/<id>`：查看完整站点评分和延迟趋势。
- 带 token：测试者专用链接，可补充机器信息并下载测试图。
- 不带 token：公开报告，任何访客均可查看。
- 机器信息填写窗口从第一份报告上传后开始计时，持续 30 分钟。
- 分享公开报告时，Telegram 等平台会展示综合评分卡片。

## 隐私

- 不上传 SSH、账号密码或网站内容。
- 公开机器地址隐藏后续部分：IPv4 显示为 `39.109.*.*`，IPv6 显示为 `2403:4fc0:*`。
- 报告中的目标 IP 是被测网站地址，不是 VPS 地址。
- 公开页面不包含测试者 token。

## 技术栈

- 后端：FastAPI、SQLAlchemy、PostgreSQL
- 前端：Jinja2、原生 CSS/JavaScript、html2canvas
- 测速：Bash、`/dev/tcp`、`getent`、`timeout`
- 分享卡片：Pillow、Noto Sans CJK、Open Graph
- 部署：systemd、OpenResty/Nginx

## 链接

- 网站：<https://tcpbench.com>
- GitHub：<https://github.com/se-tang/TCPbench>
- 博客：<https://lucklog.cc/>

## License

见 [LICENSE](LICENSE)。
