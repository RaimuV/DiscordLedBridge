"""Milestone B - communication test with the SIDE-KEYBOARD.

Verifies the SDCX protocol on Windows (hidapi):
  1. finds the vendor interface (usage page 0xFF00)
  2. reads device config, light config, key infos, keymap, per-key colors
  3. identifies the real keys and tries to light up ONE LED in Custom mode
  4. restores the previous light mode and colors

Usage:  python keyboard_test.py [--probe-only] [--no-write]
"""

import argparse
import json
import sys
import time

import hid

VID = 0x0816
PID = 0x246E
USAGE_PAGE = 0xFF00
USAGE = 0x02
REPORT_SIZE = 64
BACKUP_PATH = "led_backup.json"

G = 0x06  # config group

C_GET_CONFIG = 5
C_GET_KEY_INFOS = 7
C_GET_KEYMAP = 8
C_GET_LIGHT = 10
C_SET_LIGHT = 11
C_GET_RGB = 19
C_SET_RGB_SINGLE = 20
C_SET_RGB_BULK = 18
C_GET_MODE_DEFAULTS = 22

READ_CHUNK = 56


class HidTransport:
    """64-byte transport over vendor interface, report ID 0.

    hidapi on Windows expects the report ID as the first byte: the 0x00 byte
    (unnumbered report) is stripped before WriteFile, so [0x00] + 64 bytes
    are passed. On read, the report ID is removed.
    """

    def __init__(self, path):
        self.dev = hid.device()
        self.dev.open_path(path)
        self.dev.set_nonblocking(False)

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def write(self, payload):
        data = bytes(payload).ljust(REPORT_SIZE, b"\0")
        self.dev.write(b"\x00" + data)

    def read(self, timeout_ms=1000):
        data = self.dev.read(REPORT_SIZE, timeout_ms)
        if not data:
            raise TimeoutError("no response from device")
        return bytes(data)

    def request(self, payload):
        self.write(payload)
        while True:
            data = self.read()
            if len(data) >= 2 and data[0] == 0xAA and data[1] == 0xFA:
                print(f"  [skip light event AA FA: {data[:8].hex(' ')}]")
                continue
            return data


def find_config_interface():
    for d in hid.enumerate():
        if d["vendor_id"] == VID and d["product_id"] == PID:
            if d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE:
                return d
    return None


def u16le(value):
    return [value & 0xFF, (value >> 8) & 0xFF]


def u16(lo, hi):
    return lo | (hi << 8)


def parse_config(resp):
    length = resp[2]
    p = resp[5:43]
    serial = ""
    if length >= 40:
        serial = "".join(chr(b) for b in resp[21:43] if b)
    return {
        "version": u16(p[0], p[1]),
        "pid": u16(p[2], p[3]),
        "firmware": u16(p[4], p[5]),
        "work_mode": p[6],
        "link_status": p[7],
        "profile_count": p[10],
        "profile": p[11],
        "layer_count": p[12],
        "layer": p[13],
        "auto_sleep": u16(p[14], p[15]) if length >= 16 else 0,
        "serial": serial,
    }


def parse_light(resp):
    p = resp[5:16]
    return {
        "type": p[0],
        "mode": p[2],
        "brightness": p[3],
        "speed": p[4],
        "direction": p[5],
        "color": p[6],
        "single_color_index": p[7],
        "h": p[8] * 360 // 255,
        "s": p[9] * 100 // 255,
        "v": p[10] * 100 // 255,
    }


def light_block(cfg):
    block = [
        cfg["type"], 0, cfg["mode"], cfg["brightness"], cfg["speed"],
        cfg["direction"], cfg["color"], cfg.get("single_color_index", 0),
        cfg["h"] * 255 // 360, cfg["s"] * 255 // 100, cfg["v"] * 255 // 100,
    ]
    if cfg["mode"] == 0:
        block[6] = 0
    return block


def save_backup(light, colors):
    data = {"light": light, "colors": {str(k): list(v) for k, v in colors.items()}}
    with open(BACKUP_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Light state backup saved to {BACKUP_PATH}")


def restore_backup(tr):
    try:
        with open(BACKUP_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        print(f"No backup found ({BACKUP_PATH}).")
        sys.exit(1)
    tr.request([G, C_SET_LIGHT, 11, 0, 0, *light_block(data["light"])])
    for idx, (cr, cg, cb) in data["colors"].items():
        tr.write([G, C_SET_RGB_SINGLE, 3, *u16le(int(idx) * 3), 0, 0, 0, cr, cg, cb])
        time.sleep(0.02)
    print("Restored light mode and colors from backup.")


def read_key_infos(tr):
    data = bytearray()
    offset = 0
    while len(data) < 576:
        resp = tr.request([G, C_GET_KEY_INFOS, READ_CHUNK, *u16le(offset)])
        data.extend(resp[8:64])
        offset += READ_CHUNK
    return bytes(data[:576])


def read_keymap(tr, span, layer=0):
    data = bytearray()
    offset = 0
    while len(data) < span:
        resp = tr.request([G, C_GET_KEYMAP, 58, *u16le(offset), 0, layer])
        data.extend(resp[8:64])
        offset += READ_CHUNK
    return bytes(data[:span])


def read_key_colors(tr, span):
    data = bytearray()
    offset = 0
    while len(data) < span:
        resp = tr.request([G, C_GET_RGB, 58, *u16le(offset)])
        data.extend(resp[8:64])
        offset += READ_CHUNK
    return bytes(data[:span])


def keymap_name(entry):
    t, c1, c2, c3 = entry
    names = {
        0: "unset", 16: "mouse", 19: "disabled", 31: "control",
        32: "keyboard", 48: "consumer", 64: "system", 96: "macro",
        128: "website", 255: "combination",
    }
    return f"{names.get(t, t)} c={c1},{c2},{c3}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-only", action="store_true", help="read only, no LED writes")
    ap.add_argument("--restore", action="store_true",
                    help="restore light mode and colors from the saved backup")
    ap.add_argument("--key", type=int, default=None, help="key index to test (default: auto)")
    ap.add_argument("--color", default="#00F0FF", help="test color in #rrggbb")
    ap.add_argument("--keep", action="store_true",
                    help="after the test keep the LEDs on instead of restoring")
    args = ap.parse_args()

    info = find_config_interface()
    if info is None:
        print(f"ERROR: vendor interface {VID:04x}:{PID:04x} "
              f"(usage {USAGE_PAGE:04x}/{USAGE:02x}) not found")
        sys.exit(1)

    print("Interface found:")
    for k in ("path", "product_string", "manufacturer_string", "serial_number",
              "usage_page", "usage", "interface_number"):
        print(f"  {k}: {info[k]}")
    print()

    with HidTransport(info["path"]) as tr:
        if args.restore:
            restore_backup(tr)
            return

        # 1. config device
        resp = tr.request([G, C_GET_CONFIG])
        cfg = parse_config(resp)
        print("Device config:")
        for k, v in cfg.items():
            print(f"  {k}: {v}")
        print()

        # 2. current light config (to be restored at test end)
        light = parse_light(tr.request([G, C_GET_LIGHT]))
        print("Current light config:", light)
        print()

        # 3. key infos (factory table, 4 bytes/key, covers indices 0..143)
        raw = read_key_infos(tr)
        mapped = []
        for idx in range(0, len(raw) // 4):
            entry = bytes(raw[4 * idx : 4 * idx + 4])
            if entry != b"\0\0\0\0":
                mapped.append((idx, entry))
        print(f"Keys mapped from the factory table ({len(mapped)}):")
        for idx, entry in mapped:
            print(f"  index {idx:>3}: {keymap_name(entry)}")
        print()

        if not mapped:
            print("No keys mapped: device does not respond as expected.")
            sys.exit(1)

        max_idx = max(i for i, _ in mapped)

        # 4. layer 0 keymap for the same indices
        span_map = (max_idx + 1) * 4
        km = read_keymap(tr, span_map)
        print("Keymap layer 0:")
        for idx, _ in mapped:
            entry = bytes(km[4 * idx : 4 * idx + 4])
            print(f"  index {idx:>3}: {keymap_name(entry)}")
        print()

        # 5. current per-key colors (to be restored)
        span_rgb = (max_idx + 1) * 3
        rgb = read_key_colors(tr, span_rgb)
        cur_colors = {i: tuple(rgb[3 * i : 3 * i + 3]) for i, _ in mapped}
        print("Current colors:", {k: f"#{r:02x}{g:02x}{b:02x}" for k, (r, g, b) in cur_colors.items()})
        print()

        if args.probe_only:
            print("READ-ONLY PROBE: no writes performed.")
            return

        # 6. pick the 3 LED keys: among the main keys (indices 0-5)
        #    prefer consumer/media ones, otherwise the first 3
        main = [i for i, _ in mapped if i <= 5]
        media = [i for i, e in mapped if i <= 5 and e[0] == 48]
        if len(media) >= 3:
            chosen = media[:3]
            reason = "consumer/media keys"
        else:
            chosen = main[:3] if len(main) >= 3 else [m[0] for m in mapped[:3]]
            reason = "first mapped indices"
        print(f"Keys chosen for LEDs: {chosen} ({reason})")

        key = args.key if args.key is not None else chosen[0]
        r, g, b = [int(args.color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
        print(f"Test: key {key} -> #{r:02x}{g:02x}{b:02x}")

        save_backup(light, cur_colors)

        # 7. Custom mode (5) with firmware defaults
        resp = tr.request([G, C_GET_MODE_DEFAULTS, 0, 0, 0, 1, 0, 5])
        defaults = parse_light(resp)
        defaults["mode"] = 5
        block = [
            defaults["type"], 0, defaults["mode"], defaults["brightness"],
            defaults["speed"], defaults["direction"], defaults["color"], 0,
            defaults["h"] * 255 // 360, defaults["s"] * 255 // 100, defaults["v"] * 255 // 100,
        ]
        if defaults["mode"] == 0:
            block[6] = 0
        print("Setting Custom mode:", defaults)
        tr.request([G, C_SET_LIGHT, 11, 0, 0, *block])

        # 8. write color and verify read-back from the per-key table
        def set_key_color(idx, rgb):
            r_, g_, b_ = rgb
            tr.write([G, C_SET_RGB_SINGLE, 3, *u16le(idx * 3), 0, 0, 0, r_, g_, b_])

        set_key_color(key, (r, g, b))
        # NB: the read-back [19] is unreliable on odd indices (returns
        # spurious data); the real confirmation is visual on the keycaps.
        time.sleep(0.25)
        got = read_key_colors(tr, span_rgb)[3 * key : 3 * key + 3]
        ok = got == bytes((r, g, b))
        print(f"Read-back key {key}: {tuple(got)} (expected {(r, g, b)}) -> "
              f"{'OK' if ok else 'unreliable, visual confirmation'}")
        print(f"Color written to key {key}.")

        if args.keep:
            for idx in chosen:
                set_key_color(idx, (r, g, b))
                time.sleep(0.25)
            print(f"LEDs left on on {chosen} ({args.color}). "
                  "To restore the previous light:")
            print("  python keyboard_test.py --restore")
            return

        # 9. restore: previous light mode + original colors
        restore_backup(tr)


if __name__ == "__main__":
    main()