#!/usr/bin/env python3
import argparse
import json
import logging
import os
import re
import socket
import sys
import time
from pathlib import Path

import psutil
import paho.mqtt.client as mqtt

try:
    from evdev import UInput, ecodes
except ImportError:
    UInput = None
    ecodes = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("deskbridge")

DEFAULT_ENV_FILE = Path(__file__).resolve().parent / "deskbridge.env"

KEY_BUTTONS = ("volume_up", "volume_down", "mute", "power")


def parse_args():
    parser = argparse.ArgumentParser(description="deskbridge agent for Home Assistant")
    parser.add_argument(
        "-e", "--env-file",
        type=Path,
        default=None,
        help=f"Path to env file (default: {DEFAULT_ENV_FILE})",
    )
    return parser.parse_args()


def load_env_file(path, explicit):
    if not path.exists():
        if explicit:
            log.error("Env file not found: %s", path)
            sys.exit(1)
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


def read_battery():
    battery = psutil.sensors_battery()
    if battery is None:
        return None
    return {
        "percentage": round(battery.percent),
        "charging": battery.power_plugged,
    }


def create_media_key_device():
    if UInput is None:
        log.error("evdev is not installed; key buttons disabled (pip install evdev)")
        return None
    key_codes = {
        "volume_up": ecodes.KEY_VOLUMEUP,
        "volume_down": ecodes.KEY_VOLUMEDOWN,
        "mute": ecodes.KEY_MUTE,
        "power": ecodes.KEY_POWER,
    }
    try:
        device = UInput({ecodes.EV_KEY: list(key_codes.values())}, name="deskbridge-media-keys")
    except OSError:
        log.exception("Cannot open /dev/uinput; key buttons disabled")
        return None
    device.key_codes = key_codes
    return device


def press_media_key(media_key_device, button):
    code = media_key_device.key_codes[button]
    media_key_device.write(ecodes.EV_KEY, code, 1)
    media_key_device.syn()
    media_key_device.write(ecodes.EV_KEY, code, 0)
    media_key_device.syn()


def publish_discovery(client, topics, device_id, device_info):
    battery_config = {
        "name": "Battery",
        "unique_id": f"{device_id}_battery",
        "state_topic": topics["state"],
        "value_template": "{{ value_json.percentage }}",
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "availability_topic": topics["availability"],
        "device": device_info,
    }
    charging_config = {
        "name": "Charging",
        "unique_id": f"{device_id}_charging",
        "state_topic": topics["state"],
        "value_template": "{{ 'ON' if value_json.charging else 'OFF' }}",
        "device_class": "battery_charging",
        "availability_topic": topics["availability"],
        "device": device_info,
    }
    client.publish(topics["discovery_battery"], json.dumps(battery_config), retain=True)
    client.publish(topics["discovery_charging"], json.dumps(charging_config), retain=True)

    for button in KEY_BUTTONS:
        button_config = {
            "name": button.replace("_", " ").title(),
            "unique_id": f"{device_id}_{button}",
            "command_topic": topics[f"command_{button}"],
            "availability_topic": topics["availability"],
            "device": device_info,
        }
        client.publish(topics[f"discovery_{button}"], json.dumps(button_config), retain=True)


def main():
    args = parse_args()
    load_env_file(args.env_file or DEFAULT_ENV_FILE, explicit=args.env_file is not None)

    mqtt_host = os.environ["MQTT_HOST"]
    mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
    mqtt_username = os.environ.get("MQTT_USERNAME")
    mqtt_password = os.environ.get("MQTT_PASSWORD")
    device_name = os.environ.get("DEVICE_NAME", socket.gethostname())
    update_interval = int(os.environ.get("UPDATE_INTERVAL", "60"))

    device_id = re.sub(r"[^a-z0-9_]", "_", device_name.lower())
    topics = {
        "availability": f"deskbridge/{device_id}/status",
        "state": f"deskbridge/{device_id}/battery/state",
        "discovery_battery": f"homeassistant/sensor/{device_id}_battery/config",
        "discovery_charging": f"homeassistant/binary_sensor/{device_id}_charging/config",
    }
    for button in KEY_BUTTONS:
        topics[f"command_{button}"] = f"deskbridge/{device_id}/{button}/set"
        topics[f"discovery_{button}"] = f"homeassistant/button/{device_id}_{button}/config"
    device_info = {
        "identifiers": [device_id],
        "name": device_name,
        "model": "Linux PC",
        "manufacturer": "deskbridge",
    }

    if read_battery() is None:
        log.error("No battery detected on this system (psutil.sensors_battery() returned None)")
        sys.exit(1)

    media_key_device = create_media_key_device()
    command_topic_to_button = {topics[f"command_{b}"]: b for b in KEY_BUTTONS}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        log.info("Connected to MQTT broker (%s)", reason_code)
        publish_discovery(client, topics, device_id, device_info)
        client.publish(topics["availability"], "online", retain=True)
        if media_key_device is not None:
            for topic in command_topic_to_button:
                client.subscribe(topic)

    def on_message(client, userdata, msg):
        button = command_topic_to_button.get(msg.topic)
        if button is None:
            return
        log.info("Pressing media key: %s", button)
        press_media_key(media_key_device, button)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"deskbridge-{device_id}")
    if mqtt_username:
        client.username_pw_set(mqtt_username, mqtt_password)
    client.will_set(topics["availability"], "offline", retain=True)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(mqtt_host, mqtt_port)
    client.loop_start()

    try:
        while True:
            state = read_battery()
            if state is not None:
                client.publish(topics["state"], json.dumps(state), retain=True)
                log.info("Published battery state: %s", state)
            time.sleep(update_interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(topics["availability"], "offline", retain=True)
        client.loop_stop()
        client.disconnect()
        if media_key_device is not None:
            media_key_device.close()


if __name__ == "__main__":
    main()
