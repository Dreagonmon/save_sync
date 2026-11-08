# save_sync

游戏存档备份同步工具（Linux / macOS / Windows / Android-Termux）。

- 后端只保留最新镜像：本地目录 / 本地 zip（UTF-8）/ WebDAV
- 游戏不绑定后端：任意游戏可备份到任意后端，操作时 `--backend` 指定或交互选择；
  同一存储的不同访问路径（公网/局域网/frpc WebDAV）共享同一条本地版本线
- 增量：按 SHA-256 差集只传变化文件，备份时删除远端多余文件
- 冲突检测：清单自增版本号 + 同步点 hash 指纹，不依赖跨设备时钟；一切冲突人工确认
- 零第三方依赖（Python ≥ 3.11）

完整设计（配置/清单 schema、冲突状态机、后端契约）见 [DESIGN.md](DESIGN.md)。

## 安装（开发态）

```bash
pip install -e .
savesync games
```

## 状态

- ✅ 核心状态机（两阶段 plan/execute、全局版本令牌冲突检测）+ 本地目录后端 + CLI
  已实现，88 项 unittest 全绿（每模块专属测试文件）
  （`PYTHONPATH=src python3 -m unittest discover -s tests`）
- ✅ zip 后端已实现（单包多游戏，提交时整包重写合并其余游戏，spool 缓冲不占内存）
- ⏳ webdav 后端为契约桩，按 DESIGN.md §8 推进中
