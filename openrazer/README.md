# OpenRazer integration — Razer Barracuda 2.4

These patches add the Barracuda 2.4 (`1532:053C`) to OpenRazer so it is
recognized as a headset in OpenRazer and Polychromatic (name, serial, firmware).

They do **not** add controllable features: the device has no RGB lighting, and
battery/EQ/sidetone are not reachable over the 2.4 dongle (see `../FINDINGS.txt`).
This is recognition only.

## What the patches do

- `01-razeraccessory_driver.h.patch` — define `USB_DEVICE_ID_RAZER_BARRACUDA_2_4`.
- `02-razeraccessory_driver.c.patch` — add the PID to the `razeraccessory` HID
  id table and a minimal probe branch: it registers basic nodes
  (`device_type` = "Razer Barracuda 2.4", `device_mode`, and **static**
  `device_serial` / `firmware_version`) and skips the RGB/effect files and the
  legacy "driver mode" write the device doesn't speak.
- `03-headsets.py.patch` — add a `RazerBarracuda24` daemon hardware class
  (`get_device_type_headset`).

## Applying

Against an OpenRazer source tree (e.g. the DKMS source at
`/usr/src/openrazer-driver-<ver>/` plus the daemon at your
`openrazer_daemon/hardware/`). Adjust paths to your distro.

```sh
# kernel driver (from the DKMS source dir; it contains driver/)
cd /usr/src/openrazer-driver-<ver>
sudo patch -p1 < 01-razeraccessory_driver.h.patch
sudo patch -p1 < 02-razeraccessory_driver.c.patch

# daemon class (path varies by distro; e.g. /usr/lib/pythonX/site-packages)
cd <openrazer_daemon parent>      # the dir that contains openrazer_daemon/
sudo patch -p3 < 03-headsets.py.patch   # -p3 strips daemon/openrazer_daemon/hardware/

# rebuild + reload the kernel module
sudo dkms build  openrazer-driver/<ver> --force
sudo dkms install openrazer-driver/<ver> --force
sudo reboot
```

## udev (required so the daemon accepts the device)

Upstream's `99-razer.rules` accessory list does not include `053C`, so the
device's sysfs attributes never get chowned to the `openrazer` group and the
daemon rejects it with *"file is not owned by openrazer"*. Install the rule from
`../tools/99-razer-barracuda.rules` (it replicates the OpenRazer chown +
`razer_mount` for `053C`, and keeps the raw hidraw node accessible):

```sh
sudo cp ../tools/99-razer-barracuda.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
# replug the dongle, or run razer_mount / reboot
```

## Note

This is a downstream interoperability patch. Upstream OpenRazer may add proper
support for this device; prefer that if/when available.
