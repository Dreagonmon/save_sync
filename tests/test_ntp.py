"""ntp 模块单元测试：mock socket，不依赖真实网络。"""
from __future__ import annotations

import struct
import unittest
from datetime import datetime, timezone
from unittest import mock

from savesync import ntp


def response(secs: int) -> bytes:
    """48 字节 SNTP 应答， Transmit Timestamp 秒域在 [40:44]。"""
    return (b"\0" * 40 + struct.pack("!I", secs + ntp._NTP_EPOCH_OFFSET)
            + b"\0" * 4)


class TestNtpNow(unittest.TestCase):
    def fake_socket(self, recv_result, exc=None):
        inst = mock.MagicMock()
        if exc is not None:
            inst.sendto.side_effect = exc
            inst.recvfrom.side_effect = exc
        else:
            inst.recvfrom.return_value = (recv_result, ("1.2.3.4", 123))
        cm = mock.MagicMock()
        cm.__enter__.return_value = inst
        cm.__exit__.return_value = False
        return mock.patch.object(ntp.socket, "socket", return_value=cm), inst

    def test_success_parses_transmit_timestamp(self):
        patcher, _ = self.fake_socket(response(1_762_000_000))
        with patcher:
            dt = ntp.ntp_now()
        self.assertEqual(dt, datetime.fromtimestamp(1_762_000_000,
                                                    tz=timezone.utc))

    def test_retries_then_gives_up(self):
        patcher, inst = self.fake_socket(None, exc=OSError("timeout"))
        with patcher:
            self.assertIsNone(ntp.ntp_now())
        self.assertEqual(inst.sendto.call_count, ntp.MAX_ATTEMPTS)

    def test_short_response_not_crash(self):
        patcher, _ = self.fake_socket(b"\0" * 10)  # 不足 44 字节
        with patcher:
            self.assertIsNone(ntp.ntp_now())


class TestNowWithSource(unittest.TestCase):
    def test_ntp_available(self):
        dt = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
        with mock.patch.object(ntp, "ntp_now", return_value=dt):
            got, src = ntp.now_with_source()
        self.assertEqual((got, src), (dt, "ntp"))

    def test_fallback_local(self):
        with mock.patch.object(ntp, "ntp_now", return_value=None):
            got, src = ntp.now_with_source()
        self.assertEqual(src, "local")
        self.assertIsNotNone(got.tzinfo)  # 本地时间也带时区


if __name__ == "__main__":
    unittest.main()
