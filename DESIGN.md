# save_sync 设计文档

游戏存档备份同步工具。Python 实现，运行于 Linux / macOS / Windows / Android(Termux)。
备份后端只保留**最新镜像**（无版本历史），以清单中的**自增版本号**做基于同步状态的冲突检测，
所有冲突场景人工确认。

## 1. 总体目录布局

```
<软件根目录>/
├── config/
│   ├── g-<id>.toml          # 一个游戏一个配置文件，文件名 stem 即游戏 id
│   └── s-<id>.toml          # 一个存储后端一个配置文件，stem 即后端 id
├── data/
│   └── <id>.json            # 本地状态清单，每游戏一份：唯一同步点（全局版本线）
└── <后端根>/                 # dir: 本地目录 / zip: 包内 / webdav: 远端前缀
    ├── <id>/                # 游戏数据目录：最新存档树的原样镜像（本工具独占）
    │   ├── SaveGame001.sav
    │   └── mods/config/options.txt
    └── <id>.json            # 远端清单（在数据目录之外）
```

- 游戏 id / 后端 id 取配置文件名去掉 `g-` / `s-` 前缀（`g-stardew.toml` → `stardew`），
  保证是安全的路径/URL/zip 成员名；中文显示名放在 toml 的 `name` 字段，不进入任何路径。
- 后端数据目录内**只存放本工具写入的文件**，删除操作直接以目录内容做差集，无需对照清单。

## 2. 配置 schema

### 2.1 后端 `config/s-<id>.toml`

```toml
type = "webdav"              # "dir" | "zip" | "webdav"

# type = "dir"
path = "~/savesync-data"

# type = "zip"
path = "~/savesync-data/backups.zip"

# type = "webdav"
url = "https://dav.example.com/dav/savesync"
username = "me"
password = "hunter2"               # 可选：明文（建议 chmod 600 config/）
password_env = "SAVESYNC_NAS_PW"   # 可选：从环境变量读取，不落盘
```

WebDAV 密码解析链：`password`（明文）> `password_env`（环境变量）> **首次 401 时
交互式输入**（getpass 隐藏输入，本次会话复用；匿名服务器不 401 则全程不需要）>
非交互模式抛 `NeedsInputError`（rc 3，提示如何配置）。

### 2.2 游戏 `config/g-<id>.toml`

```toml
name = "星露谷物语"
ignore = ["*.bak", "logs/**"]     # 见 §5

[paths.linux]                     # 同一平台支持多条路径条目：路径原文 = 备注
"/home/me/.config/.../Saves" = "Steam版"
"/mnt/x/.../Saves" = "外挂盘版"
"/sdcard/Android/.../files" = "Termux/Android 用"
[paths.macos]
"~/Library/Application Support/..." = "备注"
[paths.windows]
"%APPDATA%/.../Saves" = "备注"
```

- 平台键只有 `linux` / `macos` / `windows` 三种，**没有独立的 android 键**：
  Android（Termux）一律按 `linux` 对待，安卓存档路径直接配在 `[paths.linux]` 里。
- 每个平台表可配**多条**条目；备份/恢复时自动选择其中「存在」的唯一条目，
  不唯一则询问或要求 `--path N`，0 条的行为见 §7。
- **游戏不绑定后端**：任何游戏可备份到任意后端。备份/恢复时用 `--backend <id>`
  指定；缺省时仅一个后端自动选中，多个则交互选择（默认项 = 该游戏上次使用的后端），
  非交互模式无默认 → 退出码 3 并提示 `--backend`。
- 同一存储的不同访问路径（如 WebDAV 走公网 / 局域网 / 本地 frpc 转发）在配置里是
  不同后端条目，但**共享同一条本地版本线**（见 §3.2），切换访问路径不产生伪冲突。

路径字符串支持 `~`、`$VAR`、`%VAR%` 展开（自实现一层，跨平台统一）。

## 3. 清单 schema

### 3.1 远端清单 `<后端根>/<id>.json`

```json
{
  "game": "stardew",
  "version": 5,
  "upload_time": "2026-11-08T13:46:33+08:00",
  "time_source": "ntp",
  "source_platform": "linux",
  "files": [
    {"path": "SaveGame001.sav", "hash": "9f2a…", "size": 104857,
     "mtime": "2026-11-08T12:00:03+08:00"}
  ]
}
```

- `version`：自增整数，新版本 = `max(本地版本, 远端版本) + 1`，单调不回退。
- `mtime` 为**尽力而为的展示/恢复元数据，永不参与差集与冲突判定**（判定只认
  hash）。备份端写入本地文件的 mtime；恢复端 `os.utime` 尽力设置，失败或字段
  缺失（`""`，如无 mtime 的 WebDAV/zip 环境）则跳过。恢复后的状态条目直接记
  清单 mtime（与远端清单一致）：utime 失败时下次备份重算一次 hash 并以实际
  stat 自愈；无 mtime 记 0.0，两级过滤不命中、每次备份重算，仅慢不影响正确。
- `upload_time`：备份时从阿里云 NTP（`ntp.aliyun.com`）获取；超时 2s，失败重试 1 次，
  再失败回退本地时间。仅用于展示与排查（`time_source: "ntp" | "local"`），
  **冲突检测不依赖任何时钟**。
- `path` 一律为相对存档根的正斜杠路径。`hash` 为 SHA-256。

### 3.2 本地状态清单 `data/<id>.json`

```json
{
  "game": "stardew",
  "version": 5,
  "files": {
    "SaveGame001.sav": {"hash": "9f2a…", "size": 104857, "mtime": 1762000000.0}
  },
  "chosen_path": 1,
  "last_backend": "nas"
}
```

- **唯一同步点，不保留历史**：远端只存最新镜像，旧版本内容无处可寻，历史清单
  无回滚价值（只剩 hash 审计），故刻意去除，无 prune 命令。
- **版本线按游戏全局唯一，所有后端共享**：`version` 就是状态机里的 L。远端各
  镜像都对照这条全局线判前后——同一存储换访问路径（公网/局域网/frpc）不产生
  伪冲突；备份到落后镜像时按「远端较旧」正常确认。
- `files` 是两级 hash 过滤与恢复脏检测的依据，条目带 `size`/`mtime`
  （float 时间戳，始终来自本机实际 stat），二者与现状一致则沿用旧 hash 不重算。
- 恢复时保留在远端不存在的本地多余文件**不写入** `files`，从而在下次备份时被
  自然识别为新增文件上传。

## 4. 冲突状态机（以游戏为单位）

记：L = 本地状态清单版本（游戏全局、所有后端共享；不存在视为 0），
R = 远端清单版本（不存在视为无）。

### 4.1 备份 backup

| 情形 | 动作 |
|---|---|
| 远端清单不存在 | 全量上传，双方版本 → L+1 |
| R == L，本地 hash 与状态清单一致 | 无变化，跳过 |
| R == L，本地有变化 | 正常增量：上传 hash 变化/新增文件，删除远端多余文件，双方版本 → L+1 |
| R > L | 他处上传过 → **询问是否覆盖远端**。是 → 以本地为准做差集（含删除），双方版本 → R+1；否 → 中止，提示可先 restore |
| R < L | 远端被带外回退/替换 → **询问是否仍以本地覆盖**。是 → 同上，双方版本 → L+1；否 → 中止 |

### 4.2 恢复 restore

| 情形 | 动作 |
|---|---|
| 远端清单不存在 | 报错：无备份可恢复 |
| R > L | 远端为准。先查本地 hash 是否偏离状态清单，若本地有未备份修改 → **询问是否继续（或先 backup）**；恢复后本地版本 → R |
| R == L | 对比本地当前 hash vs 远端清单：全同 → 已同步，跳过；有差异 → 本地有未备份修改 → **询问是否覆盖本地** |
| R < L | 远端被覆盖过 → **询问是否覆盖本地**。是 → 恢复，本地版本 → R；否 → 中止 |

### 4.3 写入规则

- **备份**：覆盖所有 hash 变化的文件；删除远端游戏目录中本地扫描（应用 ignore 后）
  不存在的路径。删除以远端目录实际内容为准做差集，游戏目录视为本工具独占。
- **恢复**：只覆盖 hash 与本地不同的文件；**本地多出的文件保留不删**。
- **提交点**：备份先传/删数据文件、最后写远端清单；恢复先写本地文件、最后写
  `data/<id>.json`。中途崩溃后清单未动，下次运行按 hash 差集自动补齐（自愈）。
- **并发校验**：备份写完远端清单后重读，version 不等于预期值 → 视为并发写入者，
  报冲突退出。

### 4.4 执行模型：plan → 确认 → execute（两阶段）

核心逻辑不做任何交互（不 print、不读 stdin、无全局可变状态），
一切人工确认以**决策数据**返回给调用方，核心因此无状态、可恢复，
CLI 与未来 WebUI 都只是薄适配层：

1. `plan_backup / plan_restore` 返回 `Plan`：差集（upload / delete /
   overwrite_local / keep_local）、版本迁移（before → new）、以及
   `decisions: list[Decision]`。每个 `Decision` = id、问题、选项列表、
   默认项、非交互提示（建议参数）。**存档路径需要选择时，路径本身也是一个
   Decision（id="path"）**；0 匹配隐式跳过时 `Plan.skipped=True` 且无决策。
2. 调用方收集答案（`answers: Decision.id -> 选项下标`）：CLI 交互模式逐条
   询问；非交互模式存在未答决策 → 抛 `NeedsInputError`（退出码 3）；
   WebUI 渲染表单后随请求回传。
3. 含 path 决策时，作答后**重新 plan**（重扫成本可接受），直至 decisions
   为空，再 `execute_backup / execute_restore(plan, answers)`。
4. execute 返回 `SyncReport`（上传/删除/覆盖计数、前后版本、跳过原因），
   由调用方渲染；长任务可选 `progress` 回调供未来 WebUI 显示进度。
5. 用户选择「中止」不是异常：execute 返回 `ok=False, reason="aborted"` 的报告。

## 5. 忽略规则

`ignore` 模式以存档根为 `/` 匹配相对路径（统一转正斜杠），用 `fnmatch` 实现；
`*` 可跨越 `/`，故 `logs/*` 即匹配 logs 子树。匹配命中目录前缀时整树剪枝不再遍历。
后加的 ignore 规则会使远端对应文件在下次备份的差集删除中被移除（预期行为）。

## 6. 扫描与哈希

- 两级判断降低开销：`(size, mtime)` 与状态清单一致 → 直接沿用旧 hash；否则重算 SHA-256。
  备份路径记本机实际 stat；恢复路径记清单 mtime（utime 失败时下次备份以实际
  stat 自愈），与远端环境是否提供 mtime 无关。
- 符号链接跟随（防环：(dev,ino) 访问集；真实目录优先，链接重复目标跳过并 warn），
  断链跳过；空目录不保留（清单只记录文件）。
- 单文件超过阈值（默认 512MB，可配）经 warn 回调警告（core 不 print）。

## 7. 路径解析（存档来源）

取当前平台条目列表 → 过滤「存在」的路径（无权限/不存在一律视为不存在，Android 不特殊处理）：

- **恰好 1 条**：自动选中。
- **多条**：交互模式列出编号（默认高亮状态清单中的 `chosen_path`）供选择；
  非交互必须显式 `--path N`，否则报错退出码 3。
- **0 条**：
  - 备份：命令显式点名该游戏 → 报错；`all` 隐式包含 → 跳过并打印
    `skipped (no path found)`，不算失败。
  - 恢复：列出该平台**全部**配置路径（含不存在的）供选择，选中不存在的路径则
    创建目录后恢复；非交互必须 `--path N`。

平台判定：`sys.platform`；Android（Termux，`ANDROID_ROOT` / 相关环境变量）
归入 `linux`，配置与选择逻辑完全一致，无独立分支。

## 8. 后端实现

统一协议（`backends/base.py`）：

```python
class Backend(Protocol):
    def read_manifest(self, game_id) -> RemoteManifest | None: ...
    def write_manifest(self, manifest) -> None: ...      # 提交点
    def list_files(self, game_id) -> dict[str, FileMeta]: ...
    def get(self, game_id, relpath) -> Iterator[bytes]: ...
    def put(self, game_id, relpath, stream, size) -> None: ...
    def delete(self, game_id, relpath) -> None: ...
```

- **mtime 唯一来源是清单**：远端清单里的 `mtime` 记录的是最后一次备份时**备份机**
  提供的本地文件 mtime；恢复直接取清单值设置，绝不查询远端文件的原生修改时间
  （服务器 FS stat / zip 条目时间 / WebDAV `getlastmod`，往往与清单不一致）。
  `list_files` 返回的 `FileMeta` 仅供 status/调试展示，同步状态机不得依赖。

### 8.1 dir
`os.walk` 列举；`shutil.copyfileobj` 写入（tmp + `os.replace` 原子落盘）；`os.remove` 删除。

### 8.2 zip
单包多游戏，布局与后端根一致（`<id>/…` + `<id>.json`）。zip 不可原地改，
但**无 spool 缓冲**：`put` 把流直接写进新包（tmp，append 模式，每次写完即关，
盘上始终是合法 zip，提交前 `get`/`list_files` 读 tmp 优先）；`delete` 记删除集。
`write_manifest` 提交点：旧包中其余游戏条目、本游戏未动条目**搬运追加**进新包
（跳过差集删除与已被 put 覆盖的条目），写入新清单，`os.replace` 原子替换。
全程仅新包一个临时文件，峰值磁盘 = 旧包 + 新包；崩溃残留 tmp 在下次首次写入前
清理。同名重复 put 以最后一次为准。文件名 UTF-8（zipfile 默认），压缩
`ZIP_DEFLATED`。不做追加式增量（旧包条目搬运时解压→重压）。

### 8.3 webdav
`urllib.request` 裸实现（零第三方依赖）：`PROPFIND Depth:1` 递归列举、`MKCOL` 建目录、
`PUT` 上传、`DELETE` 删除；路径分段 URL 编码；Basic/Digest 认证。
不依赖服务器 `Last-Modified`/`ETag`/`COPY` 语义。
密码按 §2.1 解析链取；`password_provider` 回调在**首次收到 401** 时触发一次
（交互 getpass，结果缓存复用），匿名可达的服务器全程不询问。

## 9. CLI

```
savesync backup [game ...] [-B/--backend ID | --zip P | --dir P] [--path N] [--dry-run]
savesync restore [game ...] [-B/--backend ID | --zip P | --dir P] [--path P]
savesync status [game ...]          # 各游戏 × 各后端的 L/R 版本与路径状态
savesync games                      # 列出配置的后端与游戏
```

- 后端选择：`--backend` 指定 > 唯一后端自动选 > 交互选择（默认 = 上次使用的后端）；
  非交互且无默认 → 退出码 3，提示 `--backend <id>` 及可选值。
- **临时后端 `--zip P` / `--dir P`**（与 `-B` 互斥）：以压缩包/文件夹为后端，
  清单/版本线/冲突语义与配置后端完全一致，但无需 `s-*.toml`——用于快速打包
  存档、拷给另一台机器恢复。路径经 `~`/`$VAR`/`%VAR%` 展开为绝对路径后作为
  后端 id 记入状态（每个路径是独立镜像，重复用同一路径即增量）。

- **CLI 是薄适配层**：`plan → resolve_decisions（交互询问）→ 重 plan → execute
  → 渲染 SyncReport`，本模块之外（core/backends）禁止 print 与 input。
  未来 WebUI 以同样方式适配（plan 渲染表单、answers 回传、execute 后台跑）。
- **非交互约定**：stdin 非 tty 时，`Plan.decisions` 非空且无法自动作答 →
  打印问题与建议参数（`--path N`、先 backup 等）→ 退出码 3。
  方便 cron/Tasker 编排。
- 退出码：`0` 成功（含跳过）；`1` 错误；`2` 用法错误；`3` 需要人工输入。
- `--dry-run` 只打印将执行的上传/删除/覆盖清单。

## 10. 工程形态

- Python ≥ 3.11（`tomllib` 只读配置）。核心**零第三方依赖**：
  `zipfile / hashlib / pathlib / fnmatch / urllib / socket(SNTP) / getpass`。
- `pyproject.toml` + entry point `savesync = savesync.cli:main`，pipx 安装；
  Android 上 pip 安装（Termux）。
- 包结构（**core 层约定**：`config/paths/scanner/manifest/sync/ntp/backends` 为
  纯逻辑层，禁止 print/input/全局可变状态，结果一律 dataclass 或类型化异常；
  交互与渲染只发生在 `cli.py`，未来 `webui` 平行接入）：

```
src/savesync/
├── cli.py          # 薄适配层：参数解析、决策询问、报告渲染、退出码
├── errors.py       # 异常体系：NeedsInputError / ConfigError / BackendError / …
├── config.py       # g-*/s-* 加载与校验
├── paths.py        # 平台判定、路径展开与解析选择
├── scanner.py      # 存档树扫描、ignore、两级 hash
├── manifest.py     # 远端/本地清单读写（原子写）
├── sync.py         # §4 状态机：plan_* / execute_* 两阶段，含 Plan/Decision/SyncReport
├── ntp.py          # SNTP 客户端（阿里云，2 次失败回退本地）
├── prompt.py       # CLI 侧 Decision 作答（交互/非交互），仅被 cli 使用
└── backends/
    ├── base.py     # Backend 协议
    ├── dirfs.py
    ├── zipfs.py
    └── webdav.py
```

- 测试：dir 后端为参照实现，zip/webdav 后端对着同一套协议契约测试
  （webdav 用标准库 http.server 起假服务器）。

## 11. 已知取舍

- 无版本历史：后端只有最新版，误覆盖靠冲突询问防止，不提供历史回滚。
- 冲突检测基于「版本号 + 同步点 hash 指纹」，不信任跨设备时钟；NTP 仅供展示。
- 恢复不删除本地多余文件（保守）；备份删除远端多余文件（镜像语义）。
- Android（按 linux 平台对待）无 root 时 `/data/data` 下存档视为不存在，
  靠配置 `/sdcard` 可见路径解决。
