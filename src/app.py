"""DiscordLedBridge - app.py

Coordinates DiscordMonitor (voice state) and KeyboardLed (keycap colors).
Discord state -> LED + (optional) tray icon.

Config: config.json in the project root (next to the app)
Credentials: %LOCALAPPDATA%\\DiscordLedBridge\\credentials.json (setup via discord_test.py)

Run:  python src/app.py [--no-tray] [--config PATH]
"""

import argparse
import json
import os
import sys
import threading

from PIL import Image, ImageDraw

from discord_monitor import DiscordMonitor
from keyboard_led import DeviceUnavailable, KeyboardLed

APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "DiscordLedBridge")
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")

DEFAULT_CONFIG = {
    "tray_icon": True,
    "mode": "global",
    "group_keys": [0, 1],
    "colors": {
        "ok": "#00F0FF",
        "mic_muted": "#FFFF00",
        "deafened": "#FF0000",
    },
    "idle_keys": [2, 3],
}

TRAY_COLORS = {
    (False, False): "#00F0FF",  # all ok
    (True, False):  "#FFFF00",  # mic muted only
    (True, True):   "#FF0000",  # audio muted
    (False, True):  "#FF0000",  # headphones muted only
}


def hex_rgb(value):
    text = str(value).lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _deep_merge(base, override):
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path):
    config = _deep_merge(DEFAULT_CONFIG, {})
    if os.path.exists(path):
        # utf-8-sig tolera il BOM che alcuni editor Windows (es. Notepad) aggiungono
        with open(path, encoding="utf-8-sig") as f:
            config = _deep_merge(config, json.load(f))
    return config


def save_config(config, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=color + "ff")
    return img


class App:
    def __init__(self, config, config_path, use_tray):
        self.config = config
        self.config_path = config_path
        self._config_mtime = (
            os.path.getmtime(config_path) if os.path.exists(config_path) else None
        )
        self._stop = threading.Event()
        self.use_tray = use_tray
        self.state = None
        self.led = KeyboardLed()
        self.tray = None
        self._log_lock = threading.Lock()
        if use_tray:
            try:
                import pystray
                self.pystray = pystray
                self.tray = pystray.Icon(
                    "DiscordLedBridge",
                    make_icon(TRAY_COLORS[(False, False)]),
                    "DiscordLedBridge",
                    menu=pystray.Menu(pystray.MenuItem("Exit", self._on_quit)),
                )
            except Exception as exc:
                print(f"tray icon unavailable ({exc}), continuing without it")
                self.use_tray = False
        self.monitor = DiscordMonitor(on_state=self._on_state, on_log=self._on_log)

    # -- log ----------------------------------------------------------------

    def _on_log(self, msg):
        with self._log_lock:
            print(f"[monitor] {msg}")

    # -- config hot-reload ---------------------------------------------------

    def _watch_config(self):
        while not self._stop.wait(timeout=1.0):
            try:
                mtime = os.path.getmtime(self.config_path)
            except OSError:
                continue
            if mtime == self._config_mtime:
                continue
            self._config_mtime = mtime
            try:
                new = load_config(self.config_path)
            except (OSError, ValueError) as exc:
                with self._log_lock:
                    print(f"[config] invalid JSON, keeping previous config ({exc})")
                continue
            self.config = new
            self._apply()
            with self._log_lock:
                print("[config] reloaded - settings applied")

    # -- stati ---------------------------------------------------------------

    def _on_state(self, state):
        self.state = state
        self._apply()

    def _apply(self):
        state = self.state or {"mute": False, "deaf": False}
        cfg = self.config
        if cfg["mode"] == "global":
            if state["deaf"]:
                color = cfg["colors"]["deafened"]
            elif state["mute"]:
                color = cfg["colors"]["mic_muted"]
            else:
                color = cfg["colors"]["ok"]
            colors = {idx: hex_rgb(color) for idx in cfg["group_keys"]}
            for idx in cfg.get("idle_keys", []):
                colors[idx] = (0, 0, 0)
        self.led.set_colors(colors)
        if self.tray is not None:
            self.tray.icon = make_icon(TRAY_COLORS[(state["mute"], state["deaf"])])
        with self._log_lock:
            if state["deaf"]:
                label = "AUDIO MUTED (deafen)"
            elif state["mute"]:
                label = "MIC MUTED"
            else:
                label = "all ok"
            print(f"[stato] {label}")

    # -- ciclo di vita --------------------------------------------------------

    def run(self):
        self._apply()
        self.monitor.start()
        threading.Thread(target=self._watch_config, daemon=True).start()
        if self.use_tray and self.tray is not None:
            self.tray.run()
        else:
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
        self.shutdown()

    def _on_quit(self, icon, item):
        icon.stop()

    def shutdown(self):
        self._stop.set()
        self.monitor.stop()
        try:
            self.led.close()
        except Exception:
            pass


def main():
    # senza console (pythonw.exe) stdout/stderr sono None: dirotta su file log
    if sys.stdout is None:
        os.makedirs(APP_DIR, exist_ok=True)
        log_path = os.path.join(APP_DIR, "app.log")
        # buffering=1 = line-buffered, cosi' i log sono visibili in tempo reale
        sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout

    ap = argparse.ArgumentParser(description="DiscordLedBridge")
    ap.add_argument("--no-tray", action="store_true", help="disable the tray icon")
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="config.json path")
    args = ap.parse_args()

    config = load_config(args.config)
    if not os.path.exists(args.config):
        save_config(config, args.config)
        print(f"Default config created at {args.config}")
    use_tray = config["tray_icon"] and not args.no_tray

    print(f"DiscordLedBridge started (config: {args.config})")
    try:
        App(config, args.config, use_tray).run()
    except DeviceUnavailable as exc:
        print(f"ERROR: {exc}")
        print("Connect the SIDE-KEYBOARD and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()