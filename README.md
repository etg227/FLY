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
- **多订阅容灾**
  - 可填多条 Clash/Mihomo 订阅（设置里每行一个）：全部节点合并进同一选择池，一家的节点挂了，自动选线/看门狗会直接切到另一家的日本节点；
  - 启动前逐条探测订阅可用性：拉不下来的订阅若有本地缓存则用缓存启动，无缓存则跳过并在日志说明 —— 单条订阅故障永远不会阻塞启动。
- **流量监控**
  - 订阅模式下自动查询机场返回的 `subscription-userinfo`（剩余/总量/到期），多条订阅分别显示，低于 10% 高亮提醒；
  - 加速运行时每 2 秒刷新实时上下行速率与本次经内核的用量（含直连流量）。
- **诊断日志**
  - 所有运行日志（含内核输出）同步写入 `runtime\fly.log`（超 2MB 自动轮转为 fly.log.1），反馈问题时附上该文件即可。
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

包含 `processes` 的配置使用 Mihomo TUN。需要管理员权限时只对内核本体弹 UAC（见「UAC 信任边界」），FLY 程序自身保持普通权限；进程匹配会把该进程的全部流量送入代理组。

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

## 加速内核

FLY 使用 [mihomo](https://github.com/MetaCubeX/mihomo) 作为内核，当前固定为
**`v1.19.31 / mihomo-windows-amd64-v1-v1.19.31.zip`**。

内核采用 fail-closed 的固定信任链：

- 只从官方 GitHub Release 下载这一份精确 asset，不使用第三方镜像；
- 程序内固定该 zip 的 SHA-256：
  `d89c9bd746e8aacff89b2edf674813e25e8bd2dc565f4e12dc3b4526dd2b3177`；
- 下载后必须先通过固定 SHA-256，才允许解压；
- 已验证 zip 会保留为 `core\mihomo-verified.zip`；
- 每次真正执行 `mihomo.exe` 之前，FLY 会把当前 exe 与可信归档里的 exe 做内容校验；
- 完整性校验本身不会先执行 `mihomo.exe`，因此未知/被替换的 exe 不会因为“验证版本”而获得执行机会；
- 如果 exe 被杀软隔离、删除或修改，而可信归档仍正常，FLY 会直接离线恢复，不需要重新下载。

### 内核下载不了怎么办

可以手动下载**精确文件**：

<https://github.com/MetaCubeX/mihomo/releases/download/v1.19.31/mihomo-windows-amd64-v1-v1.19.31.zip>

把该 zip 原样保存为：

`core\mihomo-verified.zip`

然后重新启动 FLY。FLY 会先校验固定 SHA-256，再从归档自动恢复 `mihomo.exe`。
不要手动换成 compatible、go120/go12x、v2/v3 或其它 asset；它们即使版本号相同，字节内容也不同，
会被固定哈希拒绝。

### 升级内核版本

以下固定值在源码版和冻结 launcher 中各有一份，升级时必须一起更新，
`tests/test_core_integrity.py` 会检查一致性：

- `CORE_VERSION`
- `CORE_ASSET_NAME`
- `CORE_ZIP_SHA256`

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

**常用服务（默认加速，无需勾选）：**

- Twitter / X
- YouTube（含 Google 登录链路）
- Google 全家桶
- Telegram（域名 + 官方公布的 MTProto IP 段）
- Pixiv

以上服务由「默认加速常用服务」总开关控制（默认开启）：只要点了一键加速，它们就自动生效，不占勾选项。规则文件里对应 `always_on: true` 字段。Discord 是例外 —— 它需要进程级 TUN（管理员权限）才能覆盖语音，所以保留为普通勾选项。

提示：视频/图片类服务（YouTube、Pixiv 等）流量消耗大，会明显加快订阅流量的消耗；打游戏时若不想让它们分走节点带宽，可临时关掉总开关再点一键加速。

## 添加自己的加速网站

小众/个人需求不进仓库 —— 主界面点「**添加/管理加速网站...**」，**粘贴一个网址即可**：自动提取主域名生成分流配置（含 co.jp / com.cn 这类双层后缀的正确处理），立即出现在分流列表（带「自定义」标签），支持删除；需要更精细的规则（关键词/IP 段/进程等）可在同一窗口点「高级编辑 (JSON)」。所有自定义内容只保存在本机 `private\custom_profiles.json`，永不提交、永不上传。

配置分三层：

| 层级 | 位置 | 行为 |
|---|---|---|
| 常用服务 | `rules\`（`always_on`） | 总开关默认开，一键加速自动生效 |
| 内置游戏/平台 | `rules\` | 出现在勾选列表 |
| 用户自定义 | `private\custom_profiles.json` | 填网址即加，本机独有 |

## 社区配置

想贡献配置：面向大众的需求提 PR 到 `rules\`；个人/小众站点请直接用应用内的「添加加速网站」，不再收录进仓库。规则尽量收窄，不要使用兜底规则（整浏览器类配置需说明理由）。用户自行添加的站点由第三方运营，请遵守所在地法律与站点条款。

私人实验请用应用内的「编辑本地自定义配置」，内容保存在 `private\custom_profiles.json`，不会被提交。

## UAC 信任边界

TUN 模式需要管理员权限，但 v0.9.0 起 **FLY 的 Python 代码永远不提权**：

```
普通权限 GUI（main.py，可写目录里的脚本）
        │  ShellExecuteEx("runas") —— UAC 授权对象是内核本体
        ▼
管理员权限 mihomo.exe（固定 SHA-256；启动前在 Windows 文件锁内重新校验）
        │  exe / config / 路径目录从校验到 API ready 都禁止替换/rename
        │  仅负责 TUN 与流量转发
        ▲
        └─ GUI 通过带 secret 的本机 API 控制：选节点、拉日志流(/logs)、
           就绪检测；停止走提权时拿回的进程句柄 TerminateProcess
```

这样设计的意义：用户可写目录里的 `.py` 即使被同机恶意进程篡改，也只能以
普通权限运行——它可以尝试弹 UAC，但 UAC 授权对象是 mihomo.exe，而不是
「python.exe 执行任意脚本」。v0.9.0 还会在提权启动前对已生成配置保存内存
SHA-256，并用 Windows handle 同时锁住 mihomo.exe、config.yaml 以及两者从
应用根目录开始的路径目录：文件允许读但拒绝写/删除，目录允许正常子项 I/O
但拒绝 rename/delete。锁内再次校验二进制与配置，直到 Mihomo API 鉴权就绪
才释放，因此同用户进程若试图在 verify → runas 之间替换文件或路径，只会让
启动 fail-closed，而不能把未经验证的内容带进管理员进程。

**如实说明残余风险**（放在用户可写目录的软件无法完全消除）：

- `runtime\mihomo\config.yaml` 仍是普通权限可写的。mihomo 对 provider
  路径有 home 目录内的安全限制，恶意配置能做的事远小于任意代码，但并非零；
- 提权内核崩溃后若 Job 绑定失败，普通权限进程无法清理这个孤儿（端口预检
  会拦下下一次启动并提示手动处理）；
- 想要更强的边界，请把 FLY 安装到管理员才可写的目录（如 Program Files）。

## 隐私与安全

- FLY 没有账号后端，不上传你的订阅 URL 或凭据；
- Mihomo 控制端口仅监听 `127.0.0.1` 并使用随机本地 secret；
- 内核进程通过 Windows Job Object 与主程序绑定：即使主程序被强杀或崩溃，内核也会被系统同时结束，不会留下继续代理的孤儿进程；启动时还会按路径精确清理上次残留的内核（不影响用户自己的 Clash）；
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
