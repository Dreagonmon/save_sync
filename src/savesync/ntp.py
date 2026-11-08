"""SNTP 客户端（DESIGN.md §3.1）：阿里云 NTP，仅供 upload_time 展示。

裸 socket，零依赖。超时 2s，失败重试 1 次，再失败回退本地时间。
冲突检测不依赖本模块。
"""
from __future__ import annotations

from datetime import datetime

NTP_SERVER = "ntp.aliyun.com"
TIMEOUT_S = 2.0
MAX_ATTEMPTS = 2


def ntp_now() -> datetime | None:
    """成功返回带时区的 UTC-aware datetime；两次失败返回 None。"""
    raise NotImplementedError


def now_with_source() -> tuple[datetime, str]:
    """(时间, "ntp" | "local")，供清单 time_source 字段。"""
    raise NotImplementedError
