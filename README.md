# deskbridge

Reports system status from Linux PCs to Home Assistant over MQTT, and
accepts commands back (volume, mute, power) — using MQTT discovery so each
machine shows up automatically with no YAML configuration needed on the HA
side.

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/vherolf/deskbridge/master/install.sh | bash
```

This downloads the latest release binary to `~/bin/deskbridge`, creates
`~/bin/deskbridge.env` if you don't already have one, installs the
`systemd --user` service, and adds the `/dev/uinput` udev rule (for the
volume/power buttons) if it's not already there.

Now edit `deskbridge.env` and add your MQTT broker details:

```bash
vim ~/bin/deskbridge.env
```

```bash
# restart systemctl
systemctl --user restart deskbridge.service
loginctl enable-linger "$USER"   # optional: keep running after logout
```

### Add it to Home Assistant

Make sure Home Assistant's MQTT integration is already set up against the
same broker deskbridge is pointed at — Settings → Devices & services → Add
integration → MQTT, if you haven't done that yet.

Once deskbridge is running, it announces itself via MQTT discovery, so
there's no YAML to write. Within a few seconds a new device should show up
under Settings → Devices & services → MQTT → Devices, named after the
machine's hostname (or `DEVICE_NAME`), already carrying the volume/power
buttons — plus battery/charging sensors too, if the machine has a battery.

To put the buttons on a dashboard: open the device page and use **Add to
dashboard**, or add a manual button/tile card pointing at entities like
`button.<host>_volume_up`, `button.<host>_volume_down`, `button.<host>_mute`,
and `button.<host>_power`.

See [Manual install](#manual-install) below for building from source
instead.


## Status

- [x] Battery percentage + charging state
- [x] Commands: volume control (up/down/mute)
- [x] Commands: power button (emulated hardware key)
- [ ] CPU / memory usage
- [ ] Wi-Fi signal strength / SSID
- [ ] Commands: poweroff/reboot (direct, not key-based)

## How it works

`deskbridge.py` runs as a long-lived process per machine. On startup it:

1. Publishes MQTT discovery config for four `button` entities (volume up,
   volume down, mute, power), plus a `sensor` (battery %) and
   `binary_sensor` (charging) if the machine actually has a battery,
   under `homeassistant/.../config`.
2. Publishes `online` to `deskbridge/<device>/status` (retained), with an
   `offline` Last-Will-and-Testament so Home Assistant marks the device
   unavailable if it disconnects unexpectedly.
3. Every `UPDATE_INTERVAL` seconds, reads the battery via `psutil` and
   publishes `{"percentage": ..., "charging": ...}` to
   `deskbridge/<device>/battery/state`.
4. Subscribes to the four button command topics
   (`deskbridge/<device>/{volume_up,volume_down,mute,power}/set`). When
   Home Assistant presses a button, deskbridge emits the corresponding
   `KEY_VOLUMEUP` / `KEY_VOLUMEDOWN` / `KEY_MUTE` / `KEY_POWER` event
   through a virtual `evdev`/`uinput` keyboard device — the same event a
   real hardware key would send, so it triggers the desktop's normal
   volume OSD and works under both X11 and Wayland. What `KEY_POWER`
   actually does depends on your desktop/logind power-key configuration
   (e.g. `HandlePowerKey` in `logind.conf`) — deskbridge only emits the
   keypress, it doesn't decide the action.

Each machine is identified by `DEVICE_NAME` (defaults to its hostname), so
the same script/service runs unmodified on every PC.

## Requirements

- Ubuntu (or any Linux)
- Python 3
- No battery required — desktops without one just skip the battery/charging
  sensors and still get the volume/power buttons
- An MQTT broker reachable from the PC (e.g. the Mosquitto broker add-on
  in Home Assistant)
- Home Assistant's MQTT integration configured against that broker
- For the volume/power buttons: write access to `/dev/uinput` (see the
  udev rule step below). Without it, deskbridge logs an error at startup
  and keeps running with the battery sensors only — the buttons are just
  skipped.

## Manual install

Prebuilt Linux x86_64 binaries (no Python/venv needed) are attached to each
[GitHub release](https://github.com/vherolf/deskbridge/releases) — download
one, drop a `deskbridge.env` next to it, and skip straight to "Run it
manually to test" below. Otherwise, clone the repo and set up a virtualenv:

```bash
git clone <this-repo-url> deskbridge
cd deskbridge
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Allow write access to `/dev/uinput` so deskbridge can emit media-key events
without running as root (needed only for the volume/power buttons; safe to
skip if you don't want them). This grants access to the `plugdev` group — check
`groups` and swap in a different group, or add yourself to `plugdev`, if
your user isn't already a member:

```bash
echo 'KERNEL=="uinput", GROUP="plugdev", MODE="0660"' | sudo tee /etc/udev/rules.d/99-deskbridge-uinput.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput

# verify: should show `crw-rw---- root plugdev`
ls -la /dev/uinput
```

Configure the MQTT connection:

```bash
cp deskbridge.env.example deskbridge.env
# edit deskbridge.env: set MQTT_HOST, MQTT_USERNAME/MQTT_PASSWORD if needed,
# and optionally DEVICE_NAME (defaults to the machine's hostname)
```

Run it manually to test (uses `deskbridge.env` in the same directory by
default, or pass `-e /path/to/other.env`):

```bash
venv/bin/python3 deskbridge.py
```

Install and start it as a user systemd service (runs without root). Edit
`ExecStart`/`EnvironmentFile` in `deskbridge.service` first if you cloned
the repo somewhere other than the path they currently point at:

```bash
mkdir -p ~/.config/systemd/user
cp deskbridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now deskbridge.service

# optional but recommended on laptops: keep running even when logged out
loginctl enable-linger "$USER"
```

Check it's working:

```bash
systemctl --user status deskbridge.service
journalctl --user -u deskbridge.service -f
```

Home Assistant should pick up four new button entities automatically (via
MQTT discovery) named after the device's hostname, e.g.
`button.<host>_volume_up`, `button.<host>_volume_down`, `button.<host>_mute`,
`button.<host>_power` — plus `sensor.<host>_battery` and
`binary_sensor.<host>_charging` if the machine has a battery.

## Building a standalone executable

For deploying to another PC without setting up a venv there, you can build
a single self-contained binary (Python + all dependencies included) with
[PyInstaller](https://pyinstaller.org/):

```bash
venv/bin/pip install -r requirements-build.txt
venv/bin/pyinstaller --onefile --name deskbridge deskbridge.py
```

This produces `dist/deskbridge`. Copy that one file (plus a
`deskbridge.env` alongside it) to the target machine and run it directly —
no Python or venv required there:

```bash
./deskbridge
```

The binary is Linux-x86_64-specific (built for the architecture/distro you
ran PyInstaller on), so build it on a machine similar to your target, or
build separately per architecture. If you go this route, point
`ExecStart`/`EnvironmentFile` in `deskbridge.service` at the binary and its
directory instead of `venv/bin/python3 .../deskbridge.py`.

## Repeating on another PC

The service is identical on every machine — either re-run the
[Quick install](#quick-install) one-liner (fastest: it handles the binary,
`deskbridge.env`, service, and udev rule in one go), or repeat the manual
steps above. Either way, give each machine its own `DEVICE_NAME`. No
changes needed on the Home Assistant side; new devices appear automatically
via MQTT discovery.

## Configuration reference (`deskbridge.env`)

| Variable          | Required | Default             | Description                          |
|-------------------|----------|----------------------|--------------------------------------|
| `MQTT_HOST`       | yes      | —                    | MQTT broker hostname/IP              |
| `MQTT_PORT`       | no       | `1883`               | MQTT broker port                     |
| `MQTT_USERNAME`   | no       | —                    | MQTT username, if auth is enabled    |
| `MQTT_PASSWORD`   | no       | —                    | MQTT password, if auth is enabled    |
| `DEVICE_NAME`     | no       | system hostname      | Friendly name/identifier in HA       |
| `UPDATE_INTERVAL` | no       | `60`                 | Seconds between state updates        |
