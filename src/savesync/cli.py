"""CLI 薄适配层（DESIGN.md §9）。

savesync backup [game ...] [--path N] [--dry-run]     # 缺省 = all
savesync restore [game ...] [--path N]
savesync status [game ...]                             # 各游戏 L/R 版本与差异摘要
savesync games

流程（§4.4）：plan → resolve_decisions → (含 path 决策则重 plan) → execute
→ 渲染 SyncReport。交互与渲染只发生在本模块。

退出码：0 成功(含跳过/aborted 提示后仍 0 由报告决定) / 1 错误 / 2 用法错误
/ 3 NeedsInputError（打印问题+建议参数）。
"""
from __future__ import annotations

import sys
from pathlib import Path

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NEEDS_INPUT = 0, 1, 2, 3


def app_root() -> Path:
    """软件根目录：--root 参数或 SAVESYNC_ROOT 环境变量，默认包安装目录下。"""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
