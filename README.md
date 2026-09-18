# FLY

类似岛风Go / ACGP 的**选择性日本游戏加速器**：填入你自己的 Clash/Mihomo 订阅，勾选要玩的游戏，一键加速 —— 只有勾选游戏的流量走日本节点，**其余流量全部走你自己的正常网络**（`MATCH,DIRECT`）。

内核为 [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo)。本项目**不提供任何节点**，需要你自备 Clash 订阅或自有节点。

## 特性

- **自选节点来源**：订阅 URL 或本地 `private\nodes.yaml`，凭据只存在你本机，永不上传
- **自动选线**：启动时筛选日本节点（Japan / JPN / JP / 日本 / Tokyo / Osaka / 🇯🇵）并并行测速，自动切到延迟最低的一个；订阅信息项（剩余流量、到期）不会被误选
- **多游戏同时加速**：勾选任意组合，规则合并进同一内核
- **两种加速方式**：
  - 浏览器游戏（GBF / 艦これ / DMM）：加速期间自动设置系统代理并在停止时还原，用你自己的浏览器直接玩；程序异常退出后下次启动会自动还原残留代理
  - 客户端游戏（ウマ娘 DMM版 / NIKKE）：TUN + 进程/域名分流，需管理员权限
- **线路优化**：
  - 国内优先 DNS（223.5.5.5 / 119.29.29.29 / doh.pub），直连流量不受节点影响
  - TUN 下 SNI 嗅探，按 IP 直连的游戏进程也能正确命中域名规则
  - 拦截游戏域名的 QUIC（UDP 443），强制回落 TCP 走代理，避免节点 UDP 不佳时卡顿
  - 连接保活 + 节点看门狗：运行中每分钟静默检测当前节点，连挂 3 次自动重新测速换线
- **游戏列表可扩展**：往 `rules\` 丢一个 JSON 就能加游戏，无需改代码

## 安装

**只需要一个文件**：从 [Releases](../../releases/latest) 下载 `launcher.exe`，放进一个**空文件夹**双击。它是图形界面（全程无黑窗），每次启动自动完成：

1. 可视化检查/拉取本仓库的最新程序文件（GitHub 打不开时自动走镜像站）
2. 检测不到 Python 时，自动从国内镜像下载并静默安装（约 26MB，仅当前用户，无需管理员权限）
3. 以无窗口方式自动启动 FLY

首次使用在界面里点 **Install / Update Core** 下载 mihomo 内核（进度显示在日志区，无弹窗），在 **Node / App Settings** 填入你的订阅 URL，勾选游戏，点「一键加速」。

排查问题：崩溃信息写入 `runtime\error.log`；需要看实时控制台日志时用 `START_FLY_DEBUG.bat`。

**源码方式**：自装 [Python 3.11+](https://www.python.org/downloads/) 后 `pyw main.py`（或 `py -3 launcher.py`）。

## 自动更新

`launcher.exe`（源码 `launcher.py`）启动时比对仓库里的 `VERSION`，有新版本就拉取覆盖代码文件；你的 `private\`（订阅等凭据）、`core\`（内核）、`runtime\` 永远不会被更新触碰。更新失败时会直接启动当前版本，不会卡住。

## 添加新游戏

在 `rules\` 下新建 `<id>.json`：

```json
{
  "id": "mygame",
  "name": "显示名称",
  "sort": 60,
  "launch_mode": "browser 或 tun",
  "url": "浏览器游戏的入口页（日志提示用）",
  "domains": ["example.jp"],
  "keywords": ["可选，按关键词匹配域名（抓 CDN）"],
  "ip_cidrs": ["可选，1.2.3.0/24"],
  "processes": ["可选，game.exe（TUN 模式）"],
  "full_browser": false
}
```

`full_browser: true`（如 DMM / FANZA 入口）表示该模式下**浏览器全部流量**走日本节点 —— 用于游戏本体从各厂商自己服务器加载、域名无法穷举的平台型站点。此时不走系统代理的程序（含 TUN 游戏分流）不受影响，但浏览器里逛别的网站也会经过日本节点，玩完记得停止加速。

欢迎提 PR 补充游戏规则。

## 隐私与安全

- 订阅 URL、节点凭据只保存在本机 `private\` 目录，已被 `.gitignore` 排除
- 本地打包分享请用 `scripts\MAKE_RELEASE_ZIP.ps1`，它不会把你的凭据打进压缩包
- mihomo 控制端口仅监听 127.0.0.1 并带随机 secret

## 免责声明

本项目仅供学习与技术交流，不提供任何网络代理服务；使用者需自备合规的网络环境，并遵守所在地法律法规及相关游戏、平台的服务条款。由使用本项目产生的一切后果由使用者自行承担。

## License

代码以 [MIT](LICENSE) 协议开源。内核 mihomo 遵循其自身的 GPL-3.0 协议，请通过 `INSTALL_CORE.bat` 自行下载，勿随本项目二次分发。
