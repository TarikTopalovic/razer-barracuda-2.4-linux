# What Synapse actually sends over the 2.4 dongle (UI-driven capture)

Drove every Razer Synapse (Beta, Synapse 4) control for the Barracuda while
capturing the dongle's USB traffic (USBPcap, whole hub), then matched each
click to the bytes on `OUT` ep `0x03`. This is the authoritative test of which
features are real headset commands vs PC-side audio DSP — it watches the
official app, not a hand-built writer.

## Result

| Synapse control | Dongle traffic on change | Verdict |
|-----------------|--------------------------|---------|
| SOUND ▸ Audio EQ — preset Game/Movie/Music/Custom | only `SET 0x9e=00` (+ `GET 0x1e`) | **PC-side DSP** — no EQ data sent |
| SOUND ▸ THX Spatial / Stereo toggle | none | **PC-side DSP** |
| MIC ▸ Mic Monitoring (Sidetone) ON | `SET 0x98=01` then `SET 0x99=0b` | **real device command** |
| MIC ▸ Mic Noise Cancellation | none | **PC-side DSP** |
| POWER ▸ Power Saving slider | `SET 0xac=<minutes>` (e.g. `0x25`=37, `0x34`=52, `0x00`=off) | **real device command** |

`0x9e` is a "sound settings touched" housekeeping ping Synapse fires on every EQ
change; it carries no band/preset payload, and the EQ itself is applied in the
Windows audio pipeline (THX APO), never sent to the headset over 2.4.

## Bottom line — what you can control over the dongle without Synapse

- **Read**: battery `0x21`, charging `0x2a`, connection `0x20`, mic-mute `0x55`.
- **Write (real headset commands, Synapse uses these exact frames over 2.4)**:
  - Sidetone: `01 80 09 50 41 08 08 04 98 00 01 <0|1>` then level
    `... 04 99 00 01 <0..16>` (Synapse sent level `0x0b`=11 on enable).
  - Power-saving auto-off: `01 80 09 50 41 08 08 04 ac 00 01 <minutes>`
    (`0`=off, observed 15..60).
- **Not device commands at all** (replicate in software, e.g. EasyEffects on
  Linux): Audio EQ, THX Spatial, Mic Noise Cancellation. There is no 2.4 (or,
  for these, even a BLE band-gain) frame to send — Synapse does them host-side.

## Correction to earlier notes

An earlier pass concluded settings were "inert over 2.4" because a hand-built
HID writer showed no change on `GET` readback and no new USB audio stream. That
signal was wrong: sidetone is mixed inside the headset (little/no USB stream)
and these params are write-only over 2.4 (`GET` returns 0). The UI-driven
capture settles it — sidetone (`0x98/0x99`) and power-saving (`0xac`) ARE real
over-dongle commands. The EQ-over-2.4 negative result stands (it's DSP).

## Method

Captured at 1920×1080 with vision-guided clicks (screenshot → locate control →
click → verify), USBPcap on the dongle's root hub, decoded with tshark by
matching click timestamps to `50 41` (`PA`) `SET` (subcmd `0x04`) frames on
ep `0x03`. Raw logs: `syn_sound`/`syn_mic`/`syn_power` captures (not committed).
