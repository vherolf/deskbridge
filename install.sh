#!/usr/bin/env bash
# Installs the latest deskbridge release into the current user's home
# directory: binary in ~/bin, config in ~/bin/deskbridge.env, systemd
# --user service, and (if missing) the /dev/uinput udev rule.
#
#   curl -fsSL https://raw.githubusercontent.com/vherolf/deskbridge/master/install.sh | bash
set -euo pipefail

REPO="vherolf/deskbridge"
BIN_DIR="$HOME/bin"
BIN_PATH="$BIN_DIR/deskbridge"
ENV_PATH="$BIN_DIR/deskbridge.env"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_PATH="$SERVICE_DIR/deskbridge.service"
UDEV_RULE_PATH="/etc/udev/rules.d/99-deskbridge-uinput.rules"
UDEV_GROUP="plugdev"

echo "==> Looking up latest release for $REPO"
DOWNLOAD_URL=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
  | grep '"browser_download_url"' \
  | grep '/deskbridge"' \
  | head -n1 \
  | cut -d '"' -f4)

if [ -z "$DOWNLOAD_URL" ]; then
  echo "error: could not find a 'deskbridge' binary asset in the latest release" >&2
  exit 1
fi

echo "==> Installing binary to $BIN_PATH"
mkdir -p "$BIN_DIR"
curl -fsSL "$DOWNLOAD_URL" -o "$BIN_PATH.new"
chmod +x "$BIN_PATH.new"
mv "$BIN_PATH.new" "$BIN_PATH"

if [ ! -f "$ENV_PATH" ]; then
  echo "==> Creating $ENV_PATH (edit MQTT_HOST etc. before starting the service)"
  cat > "$ENV_PATH" <<'ENVEOF'
MQTT_HOST=homeassistant.local
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
DEVICE_NAME=
UPDATE_INTERVAL=60
ENVEOF
else
  echo "==> $ENV_PATH already exists, leaving it untouched"
fi

echo "==> Installing systemd user service to $SERVICE_PATH"
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_PATH" <<SERVICEEOF
[Unit]
Description=deskbridge agent for Home Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$BIN_PATH
EnvironmentFile=$ENV_PATH
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
SERVICEEOF

systemctl --user daemon-reload
systemctl --user enable deskbridge.service

if [ -e /dev/uinput ]; then
  if [ -f "$UDEV_RULE_PATH" ]; then
    echo "==> udev rule already present at $UDEV_RULE_PATH, skipping"
  else
    echo "==> Adding udev rule so \$USER can access /dev/uinput without root (needs sudo)"
    echo "KERNEL==\"uinput\", GROUP=\"$UDEV_GROUP\", MODE=\"0660\"" | sudo tee "$UDEV_RULE_PATH" >/dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger --name-match=uinput
  fi
  if ! id -nG "$USER" | grep -qw "$UDEV_GROUP"; then
    echo "==> NOTE: your user is not in the '$UDEV_GROUP' group; volume/power buttons need:"
    echo "      sudo usermod -aG $UDEV_GROUP \$USER   (then log out/in)"
  fi
else
  echo "==> /dev/uinput not found on this system, skipping udev rule (volume/power buttons won't work)"
fi

echo
echo "==> Done."
echo "    1. Edit $ENV_PATH with your MQTT broker details"
echo "    2. systemctl --user start deskbridge.service"
echo "    3. loginctl enable-linger \"\$USER\"   # optional: keep running after logout"
