#!/usr/bin/env python3
"""
Barracuda 2.4 — full HID recorder / feature discovery.

Phase A: actively GET every parameter 0x00..0xff (safe PA read frames) and log
         which ones answer + their value. Finds hidden readable state.
Phase B: passively record EVERY input report the dongle emits while you exercise
         all physical controls. Finds hidden buttons / events / mode switches.

Everything is logged (raw hex + decode) to:
    /tmp/barracuda_capture.log

SAFE: only PA-framed GET (read) frames are sent — no writes, no bare opcodes.
Run in YOUR terminal (so you can interact):  python3 barracuda_record.py
"""
import glob
import os
import select
import sys
import time

VID_PID = "00001532:0000053C"
LOG = "/tmp/barracuda_capture.log"
PASSIVE_SECONDS = 240

CHECKLIST = """
While it records (watch for lines tagged 'NEW' and any 'PI param=0x21'):

  *** THE BIG ONE — Razer mobile app over Bluetooth ***
  A. Connect the headset to the Razer mobile app via Bluetooth (SmartSwitch /
     dual wireless — keep the 2.4 dongle connected to the PC at the same time).
  B. Open the app's BATTERY / device screen and leave it a moment.
  C. In the app, change a setting (EQ, sidetone, anything) and toggle battery view.
     -> if the app makes the headset start streaming battery, it shows up here.

  Then the physical controls, each once, a few seconds apart:
  1.  Mic-mute button (on, then off)
  2.  Volume wheel up / down
  3.  SmartSwitch short press, then LONG press (~2s)
  4.  Power button short press (NOT long — that powers off)
  5.  Bluetooth/pairing button if present
  6.  Plug the USB-C charger in, then out

Press ENTER (or wait for the timer) to stop.
"""


def node():
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            if VID_PID in open(os.path.join(h, "device/uevent")).read().upper():
                return "/dev/" + os.path.basename(h)
        except OSError:
            pass
    return None


def hexs(b, n=24):
    return " ".join(f"{x:02x}" for x in b[:n])


def decode(d):
    if len(d) >= 17 and d[3] == 0x50 and d[4] == 0x49:
        return f"PI param=0x{d[13]:02x} value={d[16]} (ch={d[14]})"
    if d and d[0] == 0x02:
        return "consumer/control report (volume/mic button)"
    return ""


def main():
    n = node()
    if not n:
        sys.exit("Barracuda not found (plug in the dongle).")
    fd = os.open(n, os.O_RDWR | os.O_NONBLOCK)
    log = open(LOG, "w")

    def w(line):
        print(line)
        log.write(line + "\n")
        log.flush()

    w(f"# Razer Barracuda 2.4 capture  ({n})")
    w(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # ---------- Phase A: active param sweep ----------
    w("=== PHASE A: parameter GET sweep (0x00..0xff) ===")

    def drain():
        while select.select([fd], [], [], 0)[0]:
            try: os.read(fd, 256)
            except OSError: return

    def get(param, txid):
        drain()
        os.write(fd, bytes([0x01, 0x80, 0x08, 0x50, 0x41, 0x08, txid, 0x03, param] + [0]*55))
        end = time.monotonic() + 0.12
        while time.monotonic() < end:
            if select.select([fd], [], [], 0.02)[0]:
                try: d = os.read(fd, 256)
                except OSError: break
                if len(d) >= 17 and d[3] == 0x50 and d[4] == 0x49:
                    return bytes(d)
        return None

    answered = 0
    for p in range(0x100):
        r = get(p, (p % 0x7e) + 1)
        if r is not None:
            answered += 1
            w(f"  param 0x{p:02x} -> value={r[16]:3d} (0x{r[16]:02x})  raw={hexs(r,18)}")
        time.sleep(0.004)
    w(f"  [{answered} parameters answered]\n")

    # ---------- Phase B: passive event capture ----------
    w("=== PHASE B: passive capture — EXERCISE EVERY CONTROL NOW ===")
    print(CHECKLIST)
    w(f"# recording up to {PASSIVE_SECONDS}s ...")
    start = time.monotonic()
    seen = {}
    last_poll = 0.0
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    # also watch stdin for ENTER to stop early
    poller.register(sys.stdin.fileno(), select.POLLIN)
    while time.monotonic() - start < PASSIVE_SECONDS:
        # light active poll of battery/charging every 2s (catch via poll too)
        if time.monotonic() - last_poll > 2.0:
            last_poll = time.monotonic()
            for pp in (0x21, 0x2a):
                try:
                    os.write(fd, bytes([0x01, 0x80, 0x08, 0x50, 0x41, 0x08, 0x40, 0x03, pp] + [0]*55))
                except OSError:
                    pass
        for fdno, _ in poller.poll(500):
            if fdno == sys.stdin.fileno():
                sys.stdin.readline()
                w("# stopped by user")
                start -= PASSIVE_SECONDS  # break outer
                break
            try:
                d = os.read(fd, 256)
            except OSError:
                continue
            if not d:
                continue
            t = time.monotonic() - start
            key = bytes(d[:20])
            tag = "NEW" if key not in seen else "   "
            seen[key] = seen.get(key, 0) + 1
            dec = decode(d)
            w(f"  {tag} {t:6.2f}s id={d[0]:02x} len={len(d):3d}  {hexs(d)}  {dec}")

    os.close(fd)
    w(f"\n# done. {len(seen)} distinct reports captured.")
    w(f"# full log saved: {LOG}")
    log.close()


if __name__ == "__main__":
    main()
