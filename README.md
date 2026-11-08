# save_sync

游戏存档备份同步工具（Linux / macOS / Windows / Android-Termux）。

- 后端只保留最新镜像：本地目录 / 本地 zip（UTF-8）/ WebDAV
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

骨架阶段：模块契约已定义，实现按 DESIGN.md 推进中。
