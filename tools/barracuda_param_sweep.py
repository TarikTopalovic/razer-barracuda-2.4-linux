#!/usr/bin/env python3
"""
barracuda_param_sweep.py — read-only GET sweep of the Razer Barracuda 2.4 dongle.

Walks every class-0x08 parameter id 0x00..0xff, sends a GET, and records the
PI reply (value byte + full payload). Useful for mapping the dongle's telemetry
surface without Synapse. Read-only: sends only GET (subcmd 0x03) and the RF
refresh frame — no SET, no opcode guessing.

Output: a table of every param that answers, with per-channel value and the raw
reply bytes. See FINDINGS_dongle_param_map.md for an annotated dump.

Usage:  sudo ./barracuda_param_sweep.py            # full sweep 0x00..0xff
        sudo ./barracuda_param_sweep.py 0x10 0x30   # range
"""
import glob
import os
import select
import sys
import time

VID_PID = "00001532:0000053C"

# host -> dongle frames (interrupt OUT)
RF_REFRESH = bytes([0x01, 0x80, 0x07, 0x50, 0x41, 0x0e, 0x08, 0x02, 0xe1, 0x01])


def find_node():
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ue = open(os.path.join(h, "device/uevent")).read().upper()
        except OSError:
            continue
        if VID_PID in ue:
            return "/dev/" + os.path.basename(h)
    return None


def get_frame(param):
    b = bytearray(64)
    b[0:9] = bytes([0x01, 0x80, 0x08, 0x50, 0x41, 0x08, 0x08, 0x03, param])
    return bytes(b)


def drain(fd):
    while select.select([fd], [], [], 0)[0]:
        try:
            os.read(fd, 256)
        except OSError:
            return


def get_param(fd, param, timeout=0.15):
    """Return list of (channel, value, raw_bytes) PI replies echoing this param."""
    drain(fd)
    try:
        os.write(fd, get_frame(param))
    except OSError:
        return []
    hits, seen = [], set()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not select.select([fd], [], [], 0.03)[0]:
            continue
        try:
            d = os.read(fd, 256)
        except OSError:
            break
        if len(d) >= 17 and d[3] == 0x50 and d[4] == 0x49 and d[13] == param:
            ch = d[14]
            if ch not in seen:
                seen.add(ch)
                hits.append((ch, d[16], bytes(d[:24])))
    return hits


def main():
    node = find_node()
    if not node:
        sys.exit("Barracuda 2.4 (1532:053c) not found.")
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        sys.exit(f"no permission for {node}; install the udev rule or use sudo.")

    lo, hi = 0x00, 0xFF
    if len(sys.argv) >= 3:
        lo, hi = int(sys.argv[1], 0), int(sys.argv[2], 0)

    os.write(fd, RF_REFRESH)          # one refresh so battery/charging are fresh
    time.sleep(0.05)
    print(f"# Barracuda 2.4 param sweep {lo:#04x}..{hi:#04x}")
    responders = []
    for p in range(lo, hi + 1):
        for ch, val, raw in get_param(fd, p):
            responders.append(p)
            print("0x%02x ch%02x val=0x%02x(%d) [%s]"
                  % (p, ch, val, val, raw.hex()))
    print("# responders: %d" % len(set(responders)))
    os.close(fd)


if __name__ == "__main__":
    main()
