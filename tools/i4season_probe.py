#!/usr/bin/env python3
"""
Probe / proof-of-concept client for i4season Wi-Fi otoscopes
(SoulEar app, com.i4season.bkCamera_soulear, e.g. Hopefox "Find T").

Reverse-engineered from libWifiCamera.so (SoulEar 1.0.120). Standard library only.

Usage (laptop joined to the otoscope's Wi-Fi):
    python i4season_probe.py                 # discover via broadcast, stream ~15 s, save JPEGs
    python i4season_probe.py --ip 192.168.1.1 --seconds 30
    python i4season_probe.py --legacy        # additionally poke the two other families the lib supports

On Windows allow Python through the firewall (private + public) the first time,
otherwise the camera's UDP replies are silently dropped.
"""
import argparse
import math
import os
import select
import socket
import struct
import sys
import time

MAGIC = 0xFFEEFFEE
CMD_PORT = 10005        # devinfo, licence, parameters
VIDEO_CMD_PORT = 10006  # open video, change port, low-power
NOTIFY_PORT = 10007     # camera -> app announcements / events

CMD_DEVINFO = 0x0001
CMD_LICGET = 0x0002
CMD_OPEN_VIDEO = 0x0004
CMD_SCREEN_PARAMS = 0x000D


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Control:
    def __init__(self, target):
        self.target = target
        self.seq = 0

    def request(self, cmd, payload=b"", port=CMD_PORT, length=None, retries=20, timeout=0.1):
        """Send one command, wait for the reply with matching magic+seq. Returns (addr, status, payload)."""
        seq = self.seq
        self.seq = (self.seq + 1) & 0xFFFF
        ln = len(payload) if length is None else length
        pkt = struct.pack("<IHHBBH", MAGIC, seq, cmd, 1, 0, ln) + payload
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        try:
            for _ in range(retries):
                s.sendto(pkt, (self.target, port))
                try:
                    data, addr = s.recvfrom(4096)
                except (socket.timeout, ConnectionResetError):
                    # Windows surfaces ICMP port-unreachable as a reset on UDP sockets.
                    time.sleep(timeout)
                    continue
                if len(data) >= 12:
                    magic, rseq, rcmd, _dir, status, _ln = struct.unpack_from("<IHHBBH", data)
                    if magic == MAGIC and rseq == seq:
                        return addr, status, data[12:]
            return None
        finally:
            s.close()


def cstr(b):
    return b.split(b"\0", 1)[0].decode("latin1", "replace")


def parse_devinfo(p):
    d = {
        "proto": p[0],
        "vendor": cstr(p[0x01:0x21]),
        "product": cstr(p[0x21:0x41]),
        "version": cstr(p[0x41:0x51]),
        "ssid": cstr(p[0x51:0x71]),
        "mac": p[0x71:0x77].hex(":"),
    }
    if len(p) >= 0x80:
        st = struct.unpack_from("<H", p, 0x77)[0]
        d.update(
            status_raw=hex(st),
            battery=st >> 9,
            status_bit8=(st >> 8) & 1,
            status_low=(st >> 1) & 0x7F,
            capacity=hex(p[0x79]),
            flags=hex(p[0x7C]),
        )
    return d


def decode_angle(g):
    """Packed 3-axis accelerometer in header bytes 6..9 (present when flags bit0). Returns (angle, slope) in degrees."""
    # 9-bit magnitude + separate sign bit per axis; ~130 counts per g. x is the probe's long axis.
    x = ((g >> 20) & 0x1FF) * (-1 if g >> 29 & 1 else 1)
    y = ((g >> 10) & 0x1FF) * (-1 if g >> 19 & 1 else 1)
    z = (g & 0x1FF) * (-1 if g >> 9 & 1 else 1)
    if y == 0 and z == 0:
        return None, None
    # Equivalent to the vendor's atan(y/z) + quadrant fix-ups; it keeps the old angle when z == 0.
    a = math.degrees(math.atan2(y, z)) % 360
    slope = math.degrees(math.atan2(x, math.hypot(y, z)))
    return a, slope


class Assembler:
    def __init__(self, outdir, max_frames):
        self.outdir = outdir
        self.max_frames = max_frames
        self.fid = None
        self.chunks = []
        self.last_seq = None
        self.broken = False
        self.frames = 0
        self.bad = {}
        self.saved = 0
        self.first_header = None

    def feed(self, pkt):
        t = pkt[0]
        if t not in (1, 6):
            return
        hl = 16 if t == 1 else 28
        if len(pkt) < hl:
            return
        seq, fid, last, cnt, fl = pkt[1], pkt[2], pkt[3], pkt[4], pkt[5]
        if self.first_header is None:
            self.first_header = pkt[:hl]
            g = struct.unpack_from("<I", pkt, 6)[0]
            w, h = struct.unpack_from("<hh", pkt, 12)
            log(f"first video packet: type={t} hdr={pkt[:hl].hex(' ')}")
            log(f"  size={w}x{h} flags=0x{fl:02x} has_gsensor={fl & 1} g=0x{g:08x} gmac={pkt[10:12].hex()}")
        if fid != self.fid:
            self.fid, self.chunks, self.broken = fid, [], False
        elif self.last_seq is not None and seq != (self.last_seq + 1) & 0xFF:
            self.broken = True
        self.last_seq = seq
        self.chunks.append(pkt[hl:])
        if last:
            data = b"".join(self.chunks)
            if self.broken:
                reason = "seq-gap"
            elif len(self.chunks) != cnt:
                reason = f"chunks {len(self.chunks)}!={cnt}"
            elif data[:2] != b"\xff\xd8":
                reason = "no-SOI"
            else:
                reason = None
            self.frames += 1
            if reason:
                self.bad[reason] = self.bad.get(reason, 0) + 1
            elif self.saved < self.max_frames:
                path = os.path.join(self.outdir, f"frame_{self.frames:05d}.jpg")
                with open(path, "wb") as f:
                    f.write(data)
                self.saved += 1
            if self.frames % 15 == 1 and fl & 1:
                g = struct.unpack_from("<I", pkt, 6)[0]
                a, s = decode_angle(g)
                xyz = f"x={(g >> 20) & 0x1FF}{'-' if g >> 29 & 1 else '+'} " \
                      f"y={(g >> 10) & 0x1FF}{'-' if g >> 19 & 1 else '+'} " \
                      f"z={g & 0x1FF}{'-' if g >> 9 & 1 else '+'}"
                angle = "n/a (z=0)" if a is None else f"{a:.1f} slope={s:.1f}"
                log(f"frame {self.frames}: g=0x{g:08x} {xyz} angle={angle}")
            self.fid, self.chunks = None, []


def stream(ctl, seconds, outdir, max_frames):
    os.makedirs(outdir, exist_ok=True)
    vsock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    vsock.bind(("0.0.0.0", 0))
    vsock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
    picport = vsock.getsockname()[1]
    log(f"video socket bound on UDP {picport}")
    user_update = int(time.time()) & 0xFFFFFFFF
    asm = Assembler(outdir, max_frames)

    def open_video():
        r = ctl.request(CMD_OPEN_VIDEO, struct.pack("<HHI", picport, 0, user_update), port=VIDEO_CMD_PORT)
        log("open video ->", "no reply" if r is None else f"status={r[1]} from {r[0][0]}")

    open_video()
    t0 = last_pkt = last_open = time.time()
    packets = 0
    other_types = {}
    while time.time() - t0 < seconds:
        r, _, _ = select.select([vsock], [], [], 0.2)
        now = time.time()
        if r:
            try:
                pkt, _ = vsock.recvfrom(65536)
            except ConnectionResetError:
                continue
            packets += 1
            last_pkt = now
            if pkt[0] in (1, 6):
                asm.feed(pkt)
            else:
                other_types[pkt[0]] = other_types.get(pkt[0], 0) + 1
        elif now - last_pkt > 1 and now - last_open > 1:
            open_video()  # the app re-sends this every second while no frames arrive
            last_open = now
    dt = time.time() - t0
    log(f"done: {packets} packets, {asm.frames} frames ({asm.frames / dt:.1f} fps), "
        f"bad={asm.bad}, {asm.saved} JPEGs saved to {outdir}")
    if other_types:
        log(f"other packet types seen: {other_types}")
    vsock.close()
    return asm.frames


def listen_notify(duration):
    """Camera announces itself / pushes events to UDP 10007."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", NOTIFY_PORT))
    except OSError as e:
        log(f"cannot bind notify port {NOTIFY_PORT}: {e}")
        return None
    s.settimeout(0.2)
    found = None
    end = time.time() + duration
    while time.time() < end:
        try:
            data, addr = s.recvfrom(4096)
        except (socket.timeout, ConnectionResetError):
            continue
        log(f"notify from {addr[0]}:{addr[1]} ({len(data)} B): {data[:48].hex(' ')}")
        if len(data) >= 13 and struct.unpack_from("<I", data)[0] == MAGIC and data[12] == 3:
            found = addr[0]
    s.close()
    return found


def legacy_probe():
    """The same lib also speaks two older families; report whether anything answers."""
    log("legacy probe: family B (UDP 44506/52219/52220, broadcast)")
    socks = {}
    for p in (44506, 52219, 52220):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            s.bind(("0.0.0.0", p))
        except OSError as e:
            log(f"  bind {p} failed: {e}")
            continue
        socks[p] = s
    sends = [(52220, bytes.fromhex("66300101"), ("192.168.1.255", 46526)),
             (52219, bytes.fromhex("860601"), ("192.168.1.255", 52219)),
             (44506, bytes.fromhex("2036"), ("192.168.1.255", 44506))]
    log("legacy probe: family C (192.168.1.1 UDP 6080/6090)")
    c1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    end = time.time() + 4
    while time.time() < end:
        for lp, data, dst in sends:
            if lp in socks:
                socks[lp].sendto(data, dst)
        c1.sendto(bytes.fromhex("2336000000003699"), ("192.168.1.1", 6080))
        c2.sendto(bytes.fromhex("6600010000000199"), ("192.168.1.1", 6090))
        all_s = list(socks.values()) + [c1, c2]
        r, _, _ = select.select(all_s, [], [], 0.5)
        for s in r:
            try:
                data, addr = s.recvfrom(65536)
            except ConnectionResetError:
                continue
            if addr[0].endswith(".255"):
                continue
            log(f"  reply on local {s.getsockname()[1]} from {addr[0]}:{addr[1]} "
                f"({len(data)} B): {data[:32].hex(' ')}")
    for s in list(socks.values()) + [c1, c2]:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", help="camera IP (default: broadcast discovery)")
    ap.add_argument("--broadcast", default="192.168.1.255")
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--out", default="frames")
    ap.add_argument("--max-frames", type=int, default=40)
    ap.add_argument("--legacy", action="store_true")
    args = ap.parse_args()

    log("listening 2 s for announcements on UDP 10007 ...")
    announced = listen_notify(2)
    target = args.ip or announced or args.broadcast
    log(f"sending DEVINFO to {target}:{CMD_PORT}")
    r = Control(target).request(CMD_DEVINFO)
    if r is None and not args.ip:
        log("no reply on broadcast, trying 255.255.255.255 and 192.168.1.1")
        for t in ("255.255.255.255", "192.168.1.1", "192.168.0.1"):
            r = Control(t).request(CMD_DEVINFO, retries=5)
            if r:
                break
    if r is None:
        log("no i4season DEVINFO reply. Wrong family, wrong subnet, or firewall.")
        if args.legacy:
            legacy_probe()
        return 1

    cam_ip, status, payload = r[0][0], r[1], r[2]
    log(f"DEVINFO reply from {cam_ip}, status={status}, {len(payload)} B payload")
    info = parse_devinfo(payload)
    for k, v in info.items():
        log(f"  {k:14s} {v}")
    with open("devinfo.bin", "wb") as f:
        f.write(payload)

    ctl = Control(cam_ip)
    ctl.seq = 1
    lic = ctl.request(CMD_LICGET, retries=8, timeout=0.5)
    if lic:
        log(f"LICGET status={lic[1]} sn={cstr(lic[2][:0x20])!r} ({len(lic[2])} B)")
    if int(info.get("capacity", "0x0"), 16) & 0x40:
        sp = ctl.request(CMD_SCREEN_PARAMS)
        log("SCREEN_PARAMS ->", "no reply" if sp is None else f"status={sp[1]} {sp[2][:16].hex(' ')}")

    frames = stream(ctl, args.seconds, args.out, args.max_frames)
    if frames == 0 and args.legacy:
        legacy_probe()
    return 0 if frames else 2


if __name__ == "__main__":
    sys.exit(main())
