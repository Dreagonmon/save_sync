"""SNTP 客户端（DESIGN.md §3.1）：阿里云 NTP，仅供 upload_time 展示。

裸 socket，零依赖。超时 2s，失败重试 1 次，再失败回退本地时间。
冲突检测不依赖本模块。
"""
from __future__ import annotations

import socket
import struct
from datetime import datetime, timezone

NTP_SERVER = "ntp.aliyun.com"
TIMEOUT_S = 2.0
MAX_ATTEMPTS = 2
_NTP_EPOCH_OFFSET = 2208988800  # 1900-01-01 → 1970-01-01 秒差


def ntp_now(server: str = NTP_SERVER, timeout: float = TIMEOUT_S) -> datetime | None:
    """成功返回 UTC aware datetime；MAX_ATTEMPTS 次失败返回 None。"""
    packet = b"\x1b" + b"\0" * 47  # LI=0 VN=3 Mode=3(client)
    for _ in range(MAX_ATTEMPTS):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(packet, (server, 123))
                data, _ = s.recvfrom(48)
            secs = struct.unpack("!I", data[40:44])[0] - _NTP_EPOCH_OFFSET
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OSError, struct.error):
            continue
    return None


def now_with_source() -> tuple[datetime, str]:
    """(时间, "ntp" | "local")，供清单 upload_time / time_source 字段。"""
    dt = ntp_now()
    if dt is not None:
        return dt, "ntp"
    return datetime.now().astimezone(), "local"
