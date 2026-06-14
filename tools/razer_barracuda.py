#!/usr/bin/env python3
"""
razer_barracuda.py — Linux control + telemetry tool for the Razer Barracuda 2.4
(USB 1532:053C wireless dongle). No dependencies; talks to the vendor HID node
directly. Reverse-engineered from Razer Synapse USB captures.

Transport (verified):
  - Commands OUT: 64-byte HID output report, report id 0x01.
  - Telemetry IN: 64-byte HID input report, report id 0x01, "PI" (50 49).
  - Frame: 01 80 <len> 50 41 <class=08> <txid=08> <subcmd> <param> [data...]
      subcmd 0x03 = GET (read a param), 0x04 = SET (write a param).

What works today:
  - battery % (0x21) / charging (0x2a) : GET after an RF refresh   [verified]
  - sidetone (mic monitoring)  : SET 0x98 on/off, 0x99 level   [verified-safe]
  - power-saving auto-off      : SET 0xac minutes (0=off)       [verified-safe]
  - status reads               : params 0x20/0x01 answer on demand

Battery note (corrected): there is NO "telemetry-enable" command. The dongle
answers a plain GET 0x21 even when cold-plugged. The catch is that Synapse sends
an RF-refresh frame (class 0x0e, param 0xe1) before each battery poll, which
pokes the dongle to fetch a fresh value from the headset over 2.4GHz. Send that
refresh first and battery/charging read back directly. Verified on a
power-cycled dongle via USBPcap capture (battery 0x1d = 29%, charging 0).

SAFETY: only the exact byte frames captured from Synapse are ever sent. No
opcode guessing — blind writes can drop the device into bootloader mode.

Usage:
  razer_barracuda.py battery [--json] [--watch]
  razer_barracuda.py status                 # dump every param that answers
  razer_barracuda.py sidetone on [LEVEL]    # LEVEL 0..16 (default 11)
  razer_barracuda.py sidetone off
  razer_barracuda.py power-saving MINUTES    # e.g. 15 ; or 'off'
"""
import glob
import json
import os
import select
import sys
import time

VID_PID = "00001532:0000053C"
CLASS = 0x08
RF_CLASS = 0x0e       # RF/link class (battery/charging refresh lives here)
TXID = 0x08            # matches the captured frames exactly
SUB_GET = 0x03
SUB_SET = 0x04


# ----------------------------------------------------------------- device I/O
def find_node():
    for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ue = open(os.path.join(h, "device/uevent")).read().upper()
        except OSError:
            continue
        if VID_PID in ue:
            return "/dev/" + os.path.basename(h)
    return None


def open_dev():
    node = find_node()
    if not node:
        sys.exit("Razer Barracuda 2.4 (1532:053c) not found. Is the dongle plugged in?")
    try:
        return os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        sys.exit(f"No permission for {node}. Install the udev rule or run with sudo.")


def _drain(fd):
    while select.select([fd], [], [], 0)[0]:
        try:
            os.read(fd, 256)
        except OSError:
            return


def _frame(subcmd, param, length, data=b""):
    b = bytearray(64)
    b[0] = 0x01            # report id
    b[1] = 0x80            # magic
    b[2] = length          # meaningful payload length
    b[3] = 0x50            # 'P'
    b[4] = 0x41            # 'A'
    b[5] = CLASS
    b[6] = TXID
    b[7] = subcmd
    b[8] = param
    for i, v in enumerate(data):
        b[9 + i] = v
    return bytes(b)


def get_param(fd, param, timeout=0.5):
    """Send a GET and return the value byte of the matching PI reply, or None."""
    _drain(fd)
    try:
        os.write(fd, _frame(SUB_GET, param, 0x08))
    except OSError:
        return None
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not select.select([fd], [], [], 0.05)[0]:
            continue
        try:
            d = os.read(fd, 256)
        except OSError:
            break
        if len(d) >= 17 and d[3] == 0x50 and d[4] == 0x49 and d[13] == param:
            return d[16]
    return None


def set_param(fd, param, value):
    """Send the exact captured SET frame: 01 80 09 50 41 08 08 04 <param> 00 01 <value>."""
    os.write(fd, _frame(SUB_SET, param, 0x09, bytes([0x00, 0x01, value & 0xff])))


def rf_refresh(fd):
    """Poke the dongle to pull fresh battery/charging from the headset over 2.4GHz.
    Captured frame: 01 80 07 50 41 0e 08 02 e1 01. Synapse sends this before each
    battery poll; without it a cold dongle has no fresh value to return."""
    b = bytearray(64)
    b[0] = 0x01           # report id
    b[1] = 0x80           # magic
    b[2] = 0x07           # payload length
    b[3] = 0x50           # 'P'
    b[4] = 0x41           # 'A'
    b[5] = RF_CLASS       # 0x0e
    b[6] = TXID
    b[7] = 0x02           # subcmd
    b[8] = 0xe1           # param
    b[9] = 0x01           # value
    try:
        os.write(fd, bytes(b))
    except OSError:
        pass


# ----------------------------------------------------------------- commands
def cmd_battery(args):
    fd = open_dev()
    watch = "--watch" in args
    as_json = "--json" in args
    try:
        def read():
            rf_refresh(fd)            # poke dongle to fetch fresh values over RF
            time.sleep(0.05)
            return get_param(fd, 0x21), get_param(fd, 0x2a)
        if watch:
            last = None
            while True:
                b, c = read()
                if (b, c) != last:
                    last = (b, c)
                    _emit(b, c, as_json)
                time.sleep(2.0)
        else:
            b, c = read()
            _emit(b, c, as_json)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)


def _emit(batt, chg, as_json):
    if as_json:
        print(json.dumps({"battery": batt,
                          "charging": (bool(chg) if chg is not None else None)}))
    elif batt is None:
        print("battery: unavailable — headset may be off/unlinked or asleep.")
    else:
        print(f"{batt}%" + (" (charging)" if chg else ""))


def cmd_status(args):
    fd = open_dev()
    try:
        rf_refresh(fd)            # surface fresh battery/charging before sweeping
        time.sleep(0.05)
        print("params answering right now:")
        any_hit = False
        for p in range(0x100):
            v = get_param(fd, p, timeout=0.12)
            if v is not None:
                any_hit = True
                print(f"  {p:#04x} = {v} (0x{v:02x})")
        if not any_hit:
            print("  none — headset may be unlinked/asleep.")
    finally:
        os.close(fd)


def cmd_sidetone(args):
    if not args or args[0] not in ("on", "off"):
        sys.exit("usage: sidetone on [LEVEL 0..16] | sidetone off")
    fd = open_dev()
    try:
        if args[0] == "off":
            set_param(fd, 0x98, 0)            # disable
            print("sidetone off")
        else:
            level = int(args[1]) if len(args) > 1 else 11
            level = max(0, min(16, level))
            set_param(fd, 0x98, 1)            # enable (Synapse sends both)
            time.sleep(0.02)
            set_param(fd, 0x99, level)        # level
            print(f"sidetone on, level {level}")
    finally:
        os.close(fd)


def cmd_power_saving(args):
    if not args:
        sys.exit("usage: power-saving MINUTES | off")
    mins = 0 if args[0] == "off" else max(0, min(0xff, int(args[0])))
    fd = open_dev()
    try:
        set_param(fd, 0xac, mins)
        print(f"power-saving auto-off: {'off' if mins == 0 else str(mins) + ' min'}")
    finally:
        os.close(fd)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    table = {
        "battery": cmd_battery,
        "status": cmd_status,
        "sidetone": cmd_sidetone,
        "power-saving": cmd_power_saving,
    }
    fn = table.get(cmd)
    if not fn:
        print(__doc__)
        sys.exit(f"unknown command: {cmd}")
    fn(rest)


if __name__ == "__main__":
    main()
