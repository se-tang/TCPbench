# TCP Bench

在自己的 VPS 上测一下到全球主流站点的 TCP 握手延迟，快速判断这条线路的出海质量。

```bash
curl -sL https://tcpbench.com/run.sh | bash
```

跑完会打印一个报告链接，打开就能看到每个站点的延迟表格，直接分享给别人看也没问题。

# 或者从 GitHub 直接跑
curl -sL https://raw.githubusercontent.com/你的用户名/tcpbench/main/backend/scripts/run.sh | BACKEND_URL=https://tcpbench.com bash

## 这是干什么用的

买 VPS 最容易踩坑的地方之一就是线路质量——同样标着"洛杉矶机房"，走 CN2 GIA 和走普通 BGP 出来的访问体验能差好几倍。TCP Bench 做的事情很简单：对 Google、GitHub、Netflix、Cloudflare 等约 60 个全球主流站点发起 TCP 握手（端口 443），记录每一次连接建立的耗时，最后给出一份延迟报告。

延迟低、丢包少，说明这条线路出海通畅；延迟高或者大量超时，基本能判断线路绕路严重或者被限速了。

## 用起来什么样

1. 在你的 VPS 上执行上面那条命令
2. 脚本会依次测试约 60 个站点，每个站点采样 60 次，过程中会实时打印进度
3. 测完自动把结果上传，返回一个报告链接，例如 `https://tcpbench.com/r/xxxxxxxx`
4. 打开链接能看到：整体平均延迟、可达站点数、最快/最慢站点，以及每个站点的详细延迟表格和趋势图
5. 报告页右上角有个「导出图片」按钮，可以把整份报告存成一张 PNG，方便发论坛或者群里

## 关于隐私

- 脚本在你自己的 VPS 上本地执行，**不需要你提供任何账号、密码或密钥**
- 上传结果前会自动对 IP 后两段做打码处理（如 `1.2.*.*`），服务端不保存完整 IP
- 上传内容只包含各站点的延迟数据和打码后的 IP，不涉及任何其他信息
- 脚本源码完全公开，见 [`/run.sh`](https://tcpbench.com/run.sh)，不放心的话可以先看一遍代码再决定要不要执行

## 环境要求

- Linux（绝大多数发行版都行），bash 版本 3.2 以上
- 不需要安装 Python、curl 之外的任何依赖
- 出站网络需要能访问 443 端口（这也是测试本身依赖的前提）

## 自己部署一套

想在自己的服务器上搭一套同款服务（比如换个域名、加自己的品牌），可以直接 fork 这个仓库，
部署步骤见 [DEPLOY.md](./DEPLOY.md)。技术栈是 FastAPI + PostgreSQL，纯 Bash 测试脚本，
不依赖任何第三方付费服务。

## License

MIT
