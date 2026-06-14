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

## Latency
`98-low-latency.conf` drops the buffer (quantum 512 ≈ 10 ms) for gaming. Raise
the quantum if you get crackling.
