# Razer Barracuda 2.4 — dongle parameter map (class 0x08 GET sweep)

Read-only sweep of every class-`0x08` parameter id `0x00`..`0xff` over the 2.4
dongle, sent as `GET` (subcmd `0x03`) and matched to the `PI` reply. Captured
with USBPcap while firing all 256 GETs, then decoded — no SET, no opcode
guessing. Reproduce on Linux with `tools/barracuda_param_sweep.py`. Raw output:
[`FINDINGS_dongle_param_dump.txt`](FINDINGS_dongle_param_dump.txt).

## Reply format

`PI` reply (interrupt IN ep `0x84`), 64-byte report:

```
01 80 <len> 50 49 08 <txid> <seq> <t> <t> 00 <ilen> 00 <param> <ch> <count/val> <data...>
 0  1   2    3  4  5    6     7    8  9  10   11   12   13     14      15         16+
```

- byte13 = parameter id (echoes the GET)
- byte14 = channel (`01` primary, `02` seen for battery/charging/connection)
- byte16 = scalar value (for simple params); for array params byte15 is a
  count and byte16+ are the elements.

## Coverage

168 param/channel replies, ids `0x00`–`0xa8`. Nothing answers above `0xa8`.
Most ids return a constant `0x01` — these read as generic "present/enabled"
flags, not distinct features, so treat a lone `val=1` as low-signal. The
interesting params are the ones with non-trivial or structured values below.

## Notable parameters

| Param | Value / payload | Read meaning (confidence) |
|------|------------------|---------------------------|
| `0x00` | ASCII `49 4e 32 34` = "IN24" | device/model string fragment (high) |
| `0x12` | `0x0a` (10) | scalar 10 — unknown (med) |
| `0x15` | count + `[5,5,5,5,5]` | flat 5-element array — EQ/tuning candidate (low) |
| `0x17` | count + `[5,6,7,7,8]` | rising 5-element array — EQ/tuning candidate (low) |
| `0x1e` | `0x0a` (10) | status scalar (known from earlier captures) |
| `0x20` | ch01=`1`, ch02=`0` | connection/link state (high) |
| `0x21` | ch01=ch02=`0x1c` (28) | **battery % = 28** (high) |
| `0x25` | `0x02` | unknown enum (low) |
| `0x2a` | ch01=ch02=`0` | **charging = 0/not charging** (high) |
| `0x2d` | `0x4b 0x13` (75,19) | unknown 2-byte (low) |
| `0x2e` | `0xc0 0xfd` | matches the pairing/link key fragment seen in pairing capture (med) |
| `0x33` | `0xd1` (209) | unknown (low) |
| `0x55` | `0` | mic-mute state (0=unmuted) (high, from earlier btn capture) |
| `0x56` | `0x02` | unknown enum (low) |
| `0x57` | `0x0d` (13) | unknown scalar (low) |
| `0x92`–`0x99` | `0` | settings block (EQ `0x93`, sidetone `0x98/0x99`) — read 0 over 2.4 |

## What this confirms

- **Telemetry round-trips over the dongle**: `0x20` (connection), `0x21`
  (battery), `0x2a` (charging), `0x55` (mic-mute) return live, meaningful
  values — no Bluetooth needed. Battery/charging require the RF-refresh frame
  first (see `FINDINGS.txt` §1A/2).
- **Settings do not apply over the dongle**: the settings block `0x92`–`0x99`
  (incl. EQ `0x93`, sidetone `0x98`/`0x99`) and power-saving `0xac` read `0`,
  and `SET` to them is ACK'd but ignored (value never changes). EQ/sidetone
  remain Bluetooth-only.
- **Array params `0x15`/`0x17`** are the only fresh leads for an EQ-over-2.4
  path. They expose count + small ascending byte arrays that *look* like band
  data, but this is unconfirmed — proving it needs a slider-correlation capture
  (change one band in Synapse/app, re-read, watch which element moves) and a
  way to write them that the firmware honors over 2.4 (none found yet).

## Caveats

- A constant `val=1` across a long contiguous id range almost certainly means
  "GET of an unimplemented id returns a default 1", not that each id is a real
  toggle. Don't over-read those.
- Values are a single snapshot; params that only change on events (button
  presses, connect/disconnect) read their idle state here.
- The dongle sometimes coalesces two PI reports into one interrupt transfer, so
  a reply can look like an oversized struct (e.g. a `0x62` reply with a `0x63`
  reply appended). Parse by the `PI` signature + inner length, not by transfer
  size, and don't mistake the trailing report for struct data.
