"""Milestone B - test di comunicazione con la SIDE-KEYBOARD.

Verifica il protocollo SDCX su Windows (hidapi):
  1. trova l'interfaccia vendor (usage page 0xFF00)
  2. legge config device, light config, key infos, keymap, colori per-key
  3. identifica i tasti reali e prova ad accendere UN LED in modalita' Custom
  4. ripristina modalita' luce e colori precedenti

Uso:  python keyboard_test.py [--probe-only] [--no-write]
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

G = 0x06  # gruppo config

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
    """Trasporto 64 byte su interfaccia vendor, report ID 0.

    hidapi su Windows vuole il report ID come primo byte: il byte 0x00
    (report non numerato) viene eliminato prima della WriteFile, quindi
    si passa [0x00] + 64 byte. In lettura il report ID viene rimosso.
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
            raise TimeoutError("nessuna risposta dal device")
        return bytes(data)

    def request(self, payload):
        self.write(payload)
        while True:
            data = self.read()
            if len(data) >= 2 and data[0] == 0xAA and data[1] == 0xFA:
                print(f"  [skip evento luce AA FA: {data[:8].hex(' ')}]")
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
    print(f"Backup stato luce salvato in {BACKUP_PATH}")


def restore_backup(tr):
    try:
        with open(BACKUP_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        print(f"Nessun backup trovato ({BACKUP_PATH}).")
        sys.exit(1)
    tr.request([G, C_SET_LIGHT, 11, 0, 0, *light_block(data["light"])])
    for idx, (cr, cg, cb) in data["colors"].items():
        tr.write([G, C_SET_RGB_SINGLE, 3, *u16le(int(idx) * 3), 0, 0, 0, cr, cg, cb])
        time.sleep(0.02)
    print("Ripristinata modalita' luce e colori dal backup.")


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
    ap.add_argument("--probe-only", action="store_true", help="solo lettura, nessuna scrittura LED")
    ap.add_argument("--restore", action="store_true",
                    help="ripristina modalita' luce e colori dal backup salvato")
    ap.add_argument("--key", type=int, default=None, help="indice tasto da provare (default: auto)")
    ap.add_argument("--color", default="#00F0FF", help="colore test in #rrggbb")
    ap.add_argument("--keep", action="store_true",
                    help="dopo il test lascia i LED accesi invece di ripristinare")
    args = ap.parse_args()

    info = find_config_interface()
    if info is None:
        print(f"ERRORE: interfaccia vendor {VID:04x}:{PID:04x} "
              f"(usage {USAGE_PAGE:04x}/{USAGE:02x}) non trovata")
        sys.exit(1)

    print("Interfaccia trovata:")
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
        print("Config device:")
        for k, v in cfg.items():
            print(f"  {k}: {v}")
        print()

        # 2. light config corrente (da ripristinare a fine test)
        light = parse_light(tr.request([G, C_GET_LIGHT]))
        print("Light config corrente:", light)
        print()

        # 3. key infos (tabella factory, 4 byte/tasto, copre indici 0..143)
        raw = read_key_infos(tr)
        mapped = []
        for idx in range(0, len(raw) // 4):
            entry = bytes(raw[4 * idx : 4 * idx + 4])
            if entry != b"\0\0\0\0":
                mapped.append((idx, entry))
        print(f"Tasti mappati dalla tabella factory ({len(mapped)}):")
        for idx, entry in mapped:
            print(f"  index {idx:>3}: {keymap_name(entry)}")
        print()

        if not mapped:
            print("Nessun tasto mappato: device non risponde come atteso.")
            sys.exit(1)

        max_idx = max(i for i, _ in mapped)

        # 4. keymap layer 0 per gli stessi indici
        span_map = (max_idx + 1) * 4
        km = read_keymap(tr, span_map)
        print("Keymap layer 0:")
        for idx, _ in mapped:
            entry = bytes(km[4 * idx : 4 * idx + 4])
            print(f"  index {idx:>3}: {keymap_name(entry)}")
        print()

        # 5. colori per-key correnti (da ripristinare)
        span_rgb = (max_idx + 1) * 3
        rgb = read_key_colors(tr, span_rgb)
        cur_colors = {i: tuple(rgb[3 * i : 3 * i + 3]) for i, _ in mapped}
        print("Colori correnti:", {k: f"#{r:02x}{g:02x}{b:02x}" for k, (r, g, b) in cur_colors.items()})
        print()

        if args.probe_only:
            print("PROBE SOLO LETTURA: nessuna scrittura eseguita.")
            return

        # 6. scelta dei 3 tasti LED: tra i tasti principali (indici 0-5)
        #    preferisci quelli consumer/media, altrimenti i primi 3
        main = [i for i, _ in mapped if i <= 5]
        media = [i for i, e in mapped if i <= 5 and e[0] == 48]
        if len(media) >= 3:
            chosen = media[:3]
            reason = "tasti consumer/media"
        else:
            chosen = main[:3] if len(main) >= 3 else [m[0] for m in mapped[:3]]
            reason = "primi indici mappati"
        print(f"Tasti scelti per i LED: {chosen} ({reason})")

        key = args.key if args.key is not None else chosen[0]
        r, g, b = [int(args.color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
        print(f"Test: tasto {key} -> #{r:02x}{g:02x}{b:02x}")

        save_backup(light, cur_colors)

        # 7. modalita' Custom (5) con i default del firmware
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
        print("Imposto modalita' Custom:", defaults)
        tr.request([G, C_SET_LIGHT, 11, 0, 0, *block])

        # 8. scrivi colore e verifica read-back dalla tabella per-key
        def set_key_color(idx, rgb):
            r_, g_, b_ = rgb
            tr.write([G, C_SET_RGB_SINGLE, 3, *u16le(idx * 3), 0, 0, 0, r_, g_, b_])

        set_key_color(key, (r, g, b))
        # NB: il read-back [19] e' inaffidabile su indici dispari (restituisce
        # dati spuri); la conferma reale e' visiva sui keycap.
        time.sleep(0.25)
        got = read_key_colors(tr, span_rgb)[3 * key : 3 * key + 3]
        ok = got == bytes((r, g, b))
        print(f"Read-back tasto {key}: {tuple(got)} (atteso {(r, g, b)}) -> "
              f"{'OK' if ok else 'inaffidabile, conferma visiva'}")
        print(f"Scritto colore su tasto {key}.")

        if args.keep:
            for idx in chosen:
                set_key_color(idx, (r, g, b))
                time.sleep(0.25)
            print(f"LED lasciati accesi su {chosen} ({args.color}). "
                  "Per ripristinare la luce precedente:")
            print("  python keyboard_test.py --restore")
            return

        # 9. ripristino: modalita' luce precedente + colori originali
        restore_backup(tr)


if __name__ == "__main__":
    main()