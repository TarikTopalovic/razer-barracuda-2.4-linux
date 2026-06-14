#!/usr/bin/env python3
"""
Minimal btsnoop_hci.log decoder — extract HCI ACL payloads to/from the headset
to find the Razer EQ command. No deps.

btsnoop format: 16-byte file header ("btsnoop\\0" + ver + datalink),
then records: 24-byte header (orig_len, incl_len, flags, drops, ts) + packet.

Usage: python3 btsnoop_decode.py btsnoop_hci.log [hexfilter]
Prints every record's direction + HCI type + hex. Filter to ACL data (type 0x02)
and group by L2CAP CID to spot the control channel carrying EQ commands.
"""
import struct
import sys


def records(path):
    with open(path, "rb") as f:
        hdr = f.read(16)
        if not hdr.startswith(b"btsnoop\x00"):
            sys.exit("not a btsnoop file")
        while True:
            rh = f.read(24)
            if len(rh) < 24:
                return
            orig_len, incl_len, flags, drops, ts = struct.unpack(">IIIIq", rh)
            data = f.read(incl_len)
            if len(data) < incl_len:
                return
            # flags bit0: 0=sent(host->ctrl), 1=received
            direction = "RX" if (flags & 0x01) else "TX"
            yield ts, direction, data


def main():
    path = sys.argv[1]
    hexfilter = sys.argv[2].lower() if len(sys.argv) > 2 else None
    seen = {}
    for ts, d, pkt in records(path):
        if not pkt:
            continue
        htype = pkt[0]                      # 1=cmd 2=acl 3=sco 4=event
        body = pkt[1:]
        h = body.hex()
        if hexfilter and hexfilter not in h:
            continue
        tname = {1: "CMD", 2: "ACL", 3: "SCO", 4: "EVT"}.get(htype, f"0x{htype:02x}")
        # for ACL, peek L2CAP CID
        cid = ""
        if htype == 2 and len(body) >= 8:
            handle = struct.unpack("<H", body[0:2])[0] & 0x0FFF
            l2len, l2cid = struct.unpack("<HH", body[4:8])
            cid = f" h={handle} cid=0x{l2cid:04x}"
        line = f"{d} {tname}{cid}  {h[:80]}"
        # dedupe identical
        if line in seen:
            seen[line] += 1
            continue
        seen[line] = 1
        print(line)
    print(f"\n# {len(seen)} distinct records")


if __name__ == "__main__":
    main()
