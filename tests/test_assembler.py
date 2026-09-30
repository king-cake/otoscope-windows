from otoscope_win.assembler import DROPPED, HEADER_V1, HEADER_V6, Frame, FrameAssembler

JPEG_HEAD = bytes([0xFF, 0xD8, 0x11, 0x22])
JPEG_TAIL = bytes([0x33, 0xFF, 0xD9, 0, 0, 0])  # zero padding after EOI


def packet(*, seq, frame, last, count, payload, type_=1, flags=1, g=0):
    header = bytearray(HEADER_V6 if type_ == 6 else HEADER_V1)
    header[0:6] = bytes([type_, seq & 0xFF, frame, 1 if last else 0, count, flags])
    header[6:10] = g.to_bytes(4, "little")
    header[10:12] = bytes([0x6E, 0xB0])
    header[12:16] = (640).to_bytes(2, "little") + (480).to_bytes(2, "little")
    return bytes(header) + payload


def test_multi_chunk_frame_reassembles_and_trims_padding():
    a = FrameAssembler()
    assert a.feed(packet(seq=10, frame=3, last=False, count=2, payload=JPEG_HEAD, g=0x00908E7F)) is None
    frame = a.feed(packet(seq=11, frame=3, last=True, count=2, payload=JPEG_TAIL, g=0x00908E7F))
    assert frame == Frame(JPEG_HEAD + JPEG_TAIL[:3], 0x00908E7F, 640, 480)


def test_sequence_wraps_without_breaking_frame():
    a = FrameAssembler()
    a.feed(packet(seq=255, frame=1, last=False, count=2, payload=JPEG_HEAD))
    assert isinstance(a.feed(packet(seq=0, frame=1, last=True, count=2, payload=JPEG_TAIL)), Frame)


def test_sequence_gap_drops_frame():
    a = FrameAssembler()
    a.feed(packet(seq=1, frame=2, last=False, count=3, payload=JPEG_HEAD))
    assert a.feed(packet(seq=3, frame=2, last=True, count=3, payload=JPEG_TAIL)) is DROPPED


def test_joining_mid_frame_drops_on_count():
    assert FrameAssembler().feed(packet(seq=50, frame=7, last=True, count=14, payload=JPEG_TAIL)) is DROPPED


def test_new_frame_id_reports_old_one_dropped():
    a = FrameAssembler()
    a.feed(packet(seq=1, frame=4, last=False, count=2, payload=JPEG_HEAD))
    assert a.feed(packet(seq=2, frame=5, last=False, count=2, payload=JPEG_HEAD)) is DROPPED
    assert isinstance(a.feed(packet(seq=3, frame=5, last=True, count=2, payload=JPEG_TAIL)), Frame)


def test_non_jpeg_is_dropped():
    assert FrameAssembler().feed(packet(seq=1, frame=1, last=True, count=1, payload=b"\x01\x02\x03\x04")) is DROPPED


def test_gsensor_none_when_flag_clear():
    frame = FrameAssembler().feed(packet(seq=1, frame=1, last=True, count=1, flags=0, payload=JPEG_HEAD + JPEG_TAIL))
    assert frame.gsensor is None


def test_type_6_skips_28_byte_header():
    frame = FrameAssembler().feed(packet(type_=6, seq=1, frame=1, last=True, count=1, payload=JPEG_HEAD + JPEG_TAIL))
    assert frame.jpeg == JPEG_HEAD + JPEG_TAIL[:3]


def test_unknown_types_and_short_packets_are_ignored():
    a = FrameAssembler()
    assert a.feed(bytes([5]) + bytes(39)) is None
    assert a.feed(bytes([1]) + bytes(9)) is None
    assert a.feed(b"") is None
