# Razer Barracuda 2.4 on Linux

Reverse-engineering notes, tools, and an OpenRazer integration for the **Razer
Barracuda 2.4** wireless headset (`1532:053C`) on Linux.

This documents the headset's two control paths (the 2.4 GHz USB dongle and
Bluetooth), the full HID/BLE command protocol, the built-in equalizer command,
and exactly what is and isn't reachable over each path — learned by capturing
both the USB dongle traffic and the Razer mobile app's Bluetooth traffic.

> Full technical write-up: [`FINDINGS.txt`](FINDINGS.txt)

## TL;DR — what works where

| Feature | 2.4 GHz dongle (USB) | Bluetooth (GATT) |
|---|---|---|
| Audio playback | ✅ (USB audio class) | ✅ |
| Device recognition (OpenRazer/Polychromatic) | ✅ | — |
| Connection-state read (`param 0x20`) | ✅ | ✅ |
| **Battery %** (`param 0x21`) | ✅ (after RF refresh) | ✅ |
| **Charging** (`param 0x2a`) | ✅ (after RF refresh) | ✅ |
| **Equalizer** (`param 0x93`) | ❌ no effect | ✅ |
| **Sidetone** (`0x98/0x99`) / power-saving (`0xac`) | ❌ no effect | ✅ |
| Mic mute / volume | system mixer (USB audio) | — |

**Battery/charging read over the 2.4 dongle — no Bluetooth needed.** Send the
RF-refresh frame (`01 80 07 50 41 0e 08 02 e1 01`, class `0x0e` / param `0xe1`)
to make the dongle pull a fresh value from the headset, then `GET 0x21` / `0x2a`.
(Earlier notes claimed "not relayed" — that was a missing-refresh bug, now fixed
in `tools/razer_barracuda.py`.) EQ and sidetone still have no effect over 2.4
(Synapse applies EQ as PC-side DSP), so those remain Bluetooth-only.

## The protocol (short version)

Both paths share the same command shape: `<param> 00 01 <value>`.

- **2.4 dongle (HID, 64-byte reports, report id `0x01`):**
  `01 80 <len> 50 41 08 <txid> <subcmd> <param> [data]`
  — `50 41` = "PA", `subcmd` `0x03`=GET / `0x04`=SET. Telemetry comes back as
  `50 49` ("PI") reports: `param@13`, `value@16`.
- **Bluetooth (BLE/ATT):** ATT Write to handle `0x0014`, value
  `<param> 00 01 <value>`.

### Equalizer command (`param 0x93`)
The headset has a **built-in hardware EQ**. The mobile app selects a preset by
writing `93 00 01 <preset>`:

| Preset | value |
|---|---|
| Default | `0x00` |
| Game | `0x01` |
| Movie | `0x03` |
| Music | `0x02` |
| Custom | `0xff` |

## Tools (`tools/`)

| File | What |
|---|---|
| `razer_barracuda_gui.py` | PyQt6 control panel (connection, sidetone, power-saving, EQ presets, software-EQ launch) |
| `razer_barracuda.py` | CLI (`battery` / `status` / `sidetone` / `power-saving`) over the 2.4 dongle |
| `barracuda_record.py` | Full HID recorder: param sweep + passive event capture |
| `btsnoop_decode.py` | Minimal `btsnoop_hci.log` decoder for Bluetooth captures |
| `razer-barracuda-eq.json` | EasyEffects 10-band preset (PC-side software EQ) |
| `99-razer-barracuda.rules` | udev rule (OpenRazer group + non-root hidraw access) |
| `barracuda-control`, `razer-barracuda.desktop` | launcher + app-menu entry |

> The GUI's write controls (sidetone/power-saving/EQ) only take effect over
> **Bluetooth**, so on a PC with no Bluetooth adapter they are inert. They're
> kept ready for a BLE backend (see below).

### udev (non-root access)
```sh
sudo cp tools/99-razer-barracuda.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
# replug the dongle
```

## OpenRazer / Polychromatic integration (`openrazer/`)

Patches that add the Barracuda to OpenRazer so it appears as a recognized
headset (name, serial, firmware) in OpenRazer and Polychromatic. It does **not**
add controls — the device has no RGB, and battery isn't reachable over the
dongle, so there's nothing OpenRazer can expose to control. See
[`openrazer/README.md`](openrazer/README.md).

## Getting full control (battery + EQ + sidetone)

These live on **Bluetooth**. With a Bluetooth adapter on the PC, a BLE client
(BlueZ / `bleak`) can connect to the headset's GATT and write/read handle
`0x0014` exactly like the phone app — giving EQ (`0x93`), sidetone (`0x98/0x99`),
power-saving (`0xac`), and live battery (`0x21`). A BLE backend for the included
GUI is the natural next step. (PRs welcome.)

## Capturing the Bluetooth protocol yourself

See [`FINDINGS.txt` §6](FINDINGS.txt) — enable Android "Bluetooth HCI snoop log",
pull it via `adb bugreport`, decode with `tools/btsnoop_decode.py`.

## ⚠️ Safety

Send **only** correctly-framed `50 41` ("PA") frames. A blind, unframed bare-
opcode sweep can hit an "enter bootloader" command — the dongle flips to PID
`0x5020` ("Macronix"), audio dies, and recovery needs a firmware re-flash via
Synapse on Windows. Details in `FINDINGS.txt` §8.

## License

MIT for the original tools and documentation (see `LICENSE`). The files under
`openrazer/` are patches against [OpenRazer](https://github.com/openrazer/openrazer)
and are derivative works licensed **GPL-2.0-or-later**, matching OpenRazer.

## Disclaimer

Unofficial, not affiliated with or endorsed by Razer. Reverse-engineered for
interoperability on Linux. Use at your own risk.
