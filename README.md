# FLY

**FLY 是一个开源的 Mihomo 选择性代理 / 策略分流前端。**

面向已经拥有自己的 Clash/Mihomo 订阅或自建节点、只希望**特定的区域限定应用、游戏或网站**走日本线路的用户。凡是未命中所选配置的流量，一律通过 `MATCH,DIRECT` 走用户自己的正常本地网络。

FLY **不提供代理节点、VPS、会员、账号、付费、兑换码或任何网络服务**。

## 为什么做 FLY

普通代理客户端也能分流，但用户往往要自己理解规则语法、进程匹配和 TUN 行为。FLY 把这些封装成可复用的配置（profile）：

```text
PC
└─ FLY / Mihomo
   ├─ 碧蓝幻想 / DMM / 勾选的应用 → FLY-JP
   └─ 其余全部流量                → DIRECT
```

目标是**正确的选择性分流**，而不是承诺把低质量节点变快。

## 主要特性

- **显式分流，DIRECT 兜底**
  - 只有域名、关键词、IP/CIDR、进程、端口的显式匹配才会走 `FLY-JP`；
  - 未命中的流量永远落到 `MATCH,DIRECT`；
  - **唯一例外**：明确标注「整浏览器」的配置（见下文）。
- **通用分流配置**
  - 配置不限于游戏；内置/社区配置在 `rules\` 目录；
  - 私人自定义配置存放在 `private\custom_profiles.json`，永不提交。
- **按服务测速选线**
  - 每个配置可声明 `latency_test_urls`；
  - 选线时按所选配置的服务目标 + 通用回退目标测试候选节点，取最保守的成功结果排序；
  - 日常保活检测使用轻量 204 端点，避免重页面推高延迟数字造成换线抖动。
- **出口稳定（防风控）**
  - 记住上次使用的节点，只要它存活且延迟不超过阈值（默认 1000ms，可在设置中调整）就沿用；
  - 运行中每分钟静默检测，仅在连续 3 次失败时才自动换线。
- **更安全的更新**
  - 不从可变的 `main` 分支或第三方镜像下载程序代码；
  - 更新只来自 GitHub Release，`FLY-update.zip` 必须带匹配的 `FLY-update.zip.sha256`；
  - 校验不一致或缺失时直接跳过更新；`private\`、`core\`、`runtime\` 永不被替换。

## 分流选择器

一个配置可包含以下任意组合：

- `domains`：域名后缀，如 `example.jp`
- `keywords`：域名关键词，适合 CDN 主机名
- `ip_cidrs`：目标 IP/CIDR 段
- `processes`：Windows 进程名；声明后该配置使用 TUN
- `ports`：目标端口；**注意这是全系统级匹配**（填 443 等于全部 HTTPS），请谨慎使用
- `latency_test_urls`：用于评估候选日本节点的服务端点
- `full_browser`：`true` 时为「整浏览器」配置（见下文）

示例：

```json
{
  "id": "my-jp-service",
  "name": "我的日区服务",
  "category": "Custom",
  "sort": 100,
  "launch_mode": "browser",
  "url": "https://example.jp/",
  "domains": ["example.jp"],
  "keywords": [],
  "ip_cidrs": [],
  "processes": [],
  "ports": [],
  "latency_test_urls": ["https://example.jp/"]
}
```

旧版规则 JSON 保持兼容。

### 进程类配置

包含 `processes` 的配置使用 Mihomo TUN，需要管理员权限；进程匹配会把该进程的全部流量送入代理组。

### 纯域名配置

若所选配置都不需要 TUN，FLY 会临时把 Windows 系统代理指向本地 Mihomo 混合端口。这**不**意味着浏览器被全局代理：Mihomo 仍按显式规则分流，未命中的请求走 `DIRECT`。停止时自动还原系统代理，异常退出后下次启动也会自动恢复。

### 整浏览器配置（显式例外）

DMM/FANZA 这类平台的页游本体从各游戏厂商自己的服务器/CDN 加载，域名无法穷举。为此保留一种**明确标注、勾选才生效**的配置：`full_browser: true`。

- 勾选后，浏览器的全部流量走日本线路（系统代理场景通过入站端口匹配；与 TUN 配置混选时按浏览器进程匹配），其余程序不受影响；
- 界面上此类配置带「整浏览器」标签，玩完请停止加速；
- 内置的「DMM / FANZA」仍是窄域名配置；需要玩站内页游时勾选「DMM / FANZA 页游（整浏览器）」。

## 安装

### Release 版本

从最新 GitHub Release 下载 `launcher.exe`，放进一个空文件夹运行。启动器会：

1. 检查最新 GitHub Release；
2. 仅当 `FLY-update.zip` 与 `FLY-update.zip.sha256` 同时存在且校验一致时才应用更新；
3. 检查 Python，缺失时自动从 python.org 静默安装；
4. 内核缺失时从 MetaCubeX 官方 Release 自动下载 Mihomo；
5. 无窗口启动 FLY。

### 源码运行

安装 Python 3.11+ 后：

```powershell
pyw main.py
```

需要调试输出时：

```powershell
py -3 main.py
```

## 节点来源

FLY 只使用用户自己提供的节点：

- Clash/Mihomo 订阅 URL；或
- `private\nodes.yaml`。

订阅 URL、UUID、凭据和本地自定义配置都保存在 `private\` 下，已被 `.gitignore` 排除。

日本节点按名称筛选：`Japan`、`JPN`、`JP`、`日本`、`Tokyo`、`Osaka`、`東京`、`大阪`、🇯🇵。FLY 无法保证服务商标注正确，也无法把拥挤的线路变成低延迟线路。

## 内置配置

**游戏 / 平台：**

- Granblue Fantasy（碧蓝幻想）
- 艦これ
- DMM / FANZA（窄域名入口）
- DMM / FANZA 页游（整浏览器，显式可选）
- ウマ娘（DMM版）
- NIKKE

**常用服务：**

- Twitter / X
- YouTube（含 Google 登录链路）
- Google 全家桶
- Telegram（域名 + 官方公布的 MTProto IP 段）
- Discord（进程级 TUN，语音也走线路，需管理员权限）
- Pixiv

提示：视频/图片类服务（YouTube、Pixiv 等）流量消耗大，会明显加快订阅流量的消耗；和游戏同时勾选时也会分走节点带宽，打游戏时建议只勾游戏。

## 社区配置

想贡献配置：往 `rules\` 添加 JSON 并提 PR，规则尽量收窄，不要使用兜底规则（整浏览器类配置需说明理由）。

私人实验请用应用内的「编辑本地自定义配置」，内容保存在 `private\custom_profiles.json`，不会被提交。

## 隐私与安全

- FLY 没有账号后端，不上传你的订阅 URL 或凭据；
- Mihomo 控制端口仅监听 `127.0.0.1` 并使用随机本地 secret；
- 应用更新按版本发布并经 SHA-256 校验；
- `private\`、`runtime\`、`core\` 不进入发布产物；
- 未命中所选配置的流量不可能被代理，最后一条规则永远是 `MATCH,DIRECT`（整浏览器配置除外，且它必须被明确勾选）。

## 发布打包

`scripts\MAKE_RELEASE_ZIP.ps1` 生成不含 `private\`、`runtime\`、`core\` 的源码/更新包。

v0.8+ 自更新发布需要同时上传：

```text
FLY-update.zip
FLY-update.zip.sha256
```

SHA-256 文件可以是裸的 64 位摘要，也可以是 `sha256sum` 风格文本。启动器 exe 用 `scripts\BUILD_LAUNCHER.ps1` 构建；推送修改 `VERSION` 的提交会由 CI 自动构建并发布 Release。

## 免责声明

本项目仅供学习与合法的选择性分流用途。使用者需自行提供并运营合法合规的网络接入，并遵守所在地法律法规及所访问服务的条款。

## License

项目代码以 [MIT 协议](LICENSE) 开源。

Mihomo 是独立项目，遵循其自身协议发布；FLY 在用户本机从 MetaCubeX 官方 Release 下载内核，不在本仓库内二次分发。
