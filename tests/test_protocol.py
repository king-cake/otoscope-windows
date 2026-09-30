"""Reference values from a Hopefox Find T (BK7231U-XRH-FBPRO, firmware HKV41B)."""

import pytest

from otoscope_win import protocol as p


def test_request_header_is_little_endian():
    pkt = p.request(seq=0x0102, cmd=p.CMD_OPEN_VIDEO, payload=bytes([9, 8]))
    assert pkt == bytes([0xEE, 0xFF, 0xEE, 0xFF, 0x02, 0x01, 0x04, 0x00, 0x01, 0x00, 0x02, 0x00, 9, 8])


def test_open_video_payload():
    assert p.open_video_payload(57087, 0x11223344) == bytes([0xFF, 0xDE, 0, 0, 0x44, 0x33, 0x22, 0x11])


def test_reply_round_trip():
    raw = bytearray(p.request(seq=7, cmd=1, payload=b"\x01\x02\x03"))
    raw[9] = 3
    reply = p.parse_reply(bytes(raw))
    assert (reply.seq, reply.cmd, reply.status, reply.payload) == (7, 1, 3, b"\x01\x02\x03")


def test_reply_without_magic_is_rejected():
    assert p.parse_reply(bytes(16)) is None
    assert p.parse_reply(bytes(4)) is None


def _devinfo_payload(status: int = 0xC801) -> bytes:
    b = bytearray(0x80)
    b[0] = 5
    b[0x01:0x04] = b"YPC"
    b[0x21:0x21 + 17] = b"BK7231U-XRH-FBPRO"
    b[0x41:0x47] = b"HKV41B"
    b[0x51:0x51 + 13] = b"Soulear-318eb"
    b[0x77:0x79] = status.to_bytes(2, "little")
    return bytes(b)


def test_devinfo_parses_find_t_layout():
    info = p.parse_devinfo(_devinfo_payload())
    assert info == p.DevInfo("YPC", "BK7231U-XRH-FBPRO", "HKV41B", "Soulear-318eb", 100)


def test_devinfo_battery_from_status_high_bits():
    assert p.parse_devinfo(_devinfo_payload(61 << 9)).battery_percent == 61


def test_short_devinfo_is_rejected():
    assert p.parse_devinfo(bytes(0x40)) is None


def test_led_payloads():
    assert p.led_write_payload(True) == bytes([0x11, 1, 100])
    assert p.led_write_payload(False) == bytes([0x11, 0, 0])
    assert p.led_read_payload() == bytes([0x01, 0, 0])
    assert p.parse_led(bytes([0x11, 1, 40])) == p.LedState(True, 40)
    assert p.parse_led(bytes([0x11, 1])) is None


def test_mount_offset():
    assert p.mount_offset_degrees("BK7231U-XRH-FBPRO") == 180.0
    assert p.mount_offset_degrees("BK7231U-XRH") == 0.0


@pytest.mark.parametrize("g, expected", [
    (0x00908E7F, 164.6),  # y=+35  z=-127
    (0x00F99635, 242.3),  # y=-101 z=-53
    (0x00A9B415, 280.9),  # y=-109 z=+21
    (0x0051F048, 59.9),   # y=+124 z=+72
    (0x2010D27A, 156.9),  # x negative, y=+52 z=-122
    (0x00423C00, 90.0),   # y=+143 z=0
])
def test_roll_matches_device_samples(g, expected):
    assert p.roll_degrees(g) == pytest.approx(expected, abs=0.1)


def test_roll_undefined_when_vertical():
    assert p.roll_degrees(0x07F00000) is None
