#!/usr/bin/env python3
"""Capture one full framebuffer from a VNC server (raw encoding).

Used to *prove* the stream is not black without relying on a GUI viewer.

  python3 scripts/vnc_host_snapshot.py [host] [port] [out.ppm]

Default: 127.0.0.1:5901 → /tmp/vnc_host_capture.ppm
Exit 0 only if enough non-black pixels are present.
"""
from __future__ import annotations

import socket
import struct
import sys


def rexact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError(f"need {n} bytes, got {len(buf)}")
        buf += chunk
    return buf


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 5901
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/vnc_host_capture.ppm"

    s = socket.create_connection((host, port), timeout=10)
    print("server_ver", rexact(s, 12))
    s.sendall(b"RFB 003.008\n")
    n = rexact(s, 1)[0]
    types = rexact(s, n)
    if 1 not in types:
        print("FAIL: server has no security type None")
        return 2
    s.sendall(bytes([1]))
    if struct.unpack("!I", rexact(s, 4))[0] != 0:
        print("FAIL: security result")
        return 2
    s.sendall(bytes([1]))  # shared

    w, h = struct.unpack("!HH", rexact(s, 4))
    pf = rexact(s, 16)
    bpp = pf[0]
    rsh, gsh, bsh = pf[10], pf[11], pf[12]
    nl = struct.unpack("!I", rexact(s, 4))[0]
    name = rexact(s, nl)
    print(f"desktop {w}x{h} bpp={bpp} name={name!r}")

    s.sendall(struct.pack("!BxH", 2, 1) + struct.pack("!i", 0))  # raw
    s.sendall(struct.pack("!BBHHHH", 3, 0, 0, 0, w, h))

    bypp = max(1, bpp // 8)
    pixels = bytearray(w * h * bypp)
    filled = 0
    for _ in range(30):
        t = rexact(s, 1)[0]
        if t == 3:
            rexact(s, 3)
            ln = struct.unpack("!I", rexact(s, 4))[0]
            rexact(s, ln)
            continue
        if t == 2:
            continue
        if t != 0:
            print("unexpected msg", t)
            return 3
        rexact(s, 1)
        nrects = struct.unpack("!H", rexact(s, 2))[0]
        for _r in range(nrects):
            rx, ry, rw, rh = struct.unpack("!HHHH", rexact(s, 8))
            enc = struct.unpack("!i", rexact(s, 4))[0]
            if enc != 0:
                print("FAIL: non-raw encoding", enc)
                return 3
            data = rexact(s, rw * rh * bypp)
            for row in range(rh):
                src = row * rw * bypp
                dst = ((ry + row) * w + rx) * bypp
                pixels[dst : dst + rw * bypp] = data[src : src + rw * bypp]
            filled += rw * rh
        if filled >= w * h:
            break
        s.sendall(struct.pack("!BBHHHH", 3, 1, 0, 0, w, h))

    def rgb(x: int, y: int) -> tuple[int, int, int]:
        i = (y * w + x) * bypp
        if bypp >= 4 and rsh == 16 and gsh == 8 and bsh == 0:
            return (pixels[i + 2], pixels[i + 1], pixels[i])
        return (pixels[i], pixels[i + 1], pixels[i + 2] if bypp > 2 else pixels[i])

    with open(out, "wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode())
        for y in range(h):
            for x in range(w):
                r, g, b = rgb(x, y)
                f.write(bytes((r & 255, g & 255, b & 255)))

    nonblack = 0
    for y in range(0, h, 5):
        for x in range(0, w, 5):
            r, g, b = rgb(x, y)
            if r + g + b > 40:
                nonblack += 1
    print(f"wrote {out} filled={filled} nonblack_grid={nonblack}")
    print("samples origin/mid", rgb(0, 0), rgb(w // 2, h // 2))
    s.close()
    if nonblack < 100:
        print("FAIL: framebuffer looks empty/black")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
