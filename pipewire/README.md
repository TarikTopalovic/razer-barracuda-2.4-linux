# PipeWire EQ (live, no extra app)

A PipeWire `filter-chain` that gives the Barracuda a **10-band equalizer**
applied in the audio graph itself — no EasyEffects, nothing to crash, gains
change **live** (no restart), and it persists across reboots.

## Install
1. Find your Barracuda sink name:
   `pactl list short sinks | grep -i barracuda`
2. Edit `99-barracuda-eq.conf` and replace the `node.target` value with that
   exact sink name (the `XXXX` is your dongle's serial).
3. Copy both files into your PipeWire drop-in dir and restart PipeWire:
   ```
   mkdir -p ~/.config/pipewire/pipewire.conf.d
   cp 99-barracuda-eq.conf 98-low-latency.conf ~/.config/pipewire/pipewire.conf.d/
   systemctl --user restart pipewire pipewire-pulse wireplumber
   ```
4. Make the EQ sink your default output:
   `pactl set-default-sink barracuda_eq`

## Adjusting the EQ
- Live (no restart):  `pw-cli set-param <id> Props '{ params = [ "eq0:Gain" -6.0 ... ] }'`
  (get `<id>` from `pw-dump | grep barracuda_eq`)
- The GUI (`tools/razer_barracuda_gui.py`) does this for you and also writes the
  gains back into `99-barracuda-eq.conf` so they survive reboots.

## Microphone EQ (`97-barracuda-mic-eq.conf`)
Same idea for the mic: a `filter-chain` that exposes a virtual input source
**"Barracuda Mic (clean)"** with a fixed 75 Hz high-pass + a **10-band EQ**
(`mic0..mic9`). The high-pass kills the low-frequency rumble/plosive energy that
overdrives this boom mic and reads as a distorted *"brr"* on loud speech.

Install:
```
cp 97-barracuda-mic-eq.conf ~/.config/pipewire/pipewire.conf.d/
systemctl --user restart pipewire pipewire-pulse wireplumber
pactl set-default-source barracuda_mic
```
Then select **"Barracuda Mic (clean)"** as the input in your app (Discord/OBS
ignore the system default — pick it manually, and turn OFF Discord's own noise
suppression so it doesn't fight the chain).

Adjust live (or use the GUI's **Microphone** tab — same band sliders + presets
as the headphone EQ; gains persist back into this file):
```
pw-cli set-param <id> Props '{ params = [ "mic0:Gain" -2.0 "mic6:Gain" 3.0 ... ] }'
```
(`<id>` from `pw-dump | grep barracuda_mic_in`)

## Mic crackle / "brr" / weird sounds
Three independent causes, fix in this order:
1. **Clipping** (distorted *"brr"*, "goes out of bounds" on loud speech) — the
   mic ADC gain has no headroom. Drop it:
   `amixer -c<card> sset 'Mic' 57`  (≈ -5 dB; card from `aplay -l`). Lower more
   if it still clips.
2. **Boomy/rumbly low-end** — the mic EQ's fixed high-pass + a low-cut preset
   (try **Anti-Pop** or **Clarity**) handle it.
3. **Pop/crackle when an app opens the mic** — the capture node suspends when
   idle and pops on resume. Fixed by `../wireplumber/51-barracuda-mic.conf`
   (`session.suspend-timeout-seconds = 0`). Do NOT force `api.alsa.period-size`
   on this USB 1.1 full-speed device — non-native periods garble the
   isochronous packing and make it worse.

## Latency
`98-low-latency.conf` drops the buffer (quantum 512 ≈ 10 ms) for gaming. The
Barracuda is a **USB 1.1 full-speed** device, so total latency ≈ the 2.4 GHz
radio (~30 ms, fixed) + this buffer. Lower the quantum for less lag, raise it
(768/1024) if you get crackling; below ~256 USB 1.1 can't keep up and crackles.
Watch the **ERR** column in `pw-top` — climbing = underruns, back off.

## Random ~1-second dropouts
If audio cuts out for ~1s "every so often", check the kernel log at that moment:
```
journalctl -k -b | grep "cannot get freq"
```
`usb 1-1: 1:1: cannot get freq at ep 0x7` = **not** a USB disconnect (the device
stays enumerated). It's the audio driver failing to read the sample clock off the
dongle because the **2.4 GHz radio lost packets** for ~1s. Software mitigations
(all in `../wireplumber/51-barracuda-mic.conf` + the rate lock + power-saving):
- **never-suspend** the output *and* input nodes — every idle→resume re-inits the
  USB clock, which is exactly when that error fires;
- **lock the rate** to a single 48000 (`clock.allowed-rates = [ 48000 ]`) so it's
  never renegotiated;
- **output cushion** `api.alsa.headroom ≈ 2048` on the sink only — rides through a
  brief RF stall without touching gaming/input latency;
- **headset power-saving off** (`razer_barracuda.py power-saving off`) so the link
  never idles down (resets if the headset fully powers off).

Software can't stop the actual packet loss — that's physical: dongle on a USB-C
extension out in open air (off the case / away from USB3 ports), 2.4 GHz Wi-Fi on
a clear channel, line-of-sight to the headset. See `FINDINGS.txt` §11.
