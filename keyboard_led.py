"""SIDE-KEYBOARD LED control.

The device drops per-key writes that are too close together (~250ms), so every
write goes through a worker thread that processes it alone with a pause.
On startup it saves the current light mode and restores it at session end.
"""

import threading
import time

from keyboard_test import (C_GET_LIGHT, C_GET_MODE_DEFAULTS, C_SET_LIGHT,
                           C_SET_RGB_SINGLE, G, HidTransport,
                           find_config_interface, light_block, parse_light,
                           u16le)

DEFAULT_GAP = 0.25


class DeviceUnavailable(Exception):
    pass


class KeyboardLed:
    def __init__(self, gap=DEFAULT_GAP):
        self.gap = gap
        self.transport = None
        self._lock = threading.Lock()
        self._pending = {}          # {index: (r,g,b)} to apply
        self._last = {}             # last colors applied per index
        self._reaffirm = False      # re-write the colors once after a short delay
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._open()
        self._saved_light = self._read_light()
        self._set_custom_mode()
        self._worker.start()

    # -- apertura/chiusura -------------------------------------------------

    def _open(self):
        info = find_config_interface()
        if info is None:
            raise DeviceUnavailable("SIDE-KEYBOARD vendor interface not found")
        self.transport = HidTransport(info["path"])

    def close(self):
        self._running = False
        if self._worker.is_alive():
            self._worker.join(timeout=2)
        try:
            self._restore_light()
        except Exception:
            pass
        if self.transport is not None:
            self.transport.close()

    # -- stato luce --------------------------------------------------------

    def _read_light(self):
        if self.transport is None:
            return None
        try:
            return parse_light(self.transport.request([G, C_GET_LIGHT]))
        except Exception:
            return None

    def _set_custom_mode(self):
        resp = self.transport.request([G, C_GET_MODE_DEFAULTS, 0, 0, 0, 1, 0, 5])
        cfg = parse_light(resp)
        cfg["mode"] = 5
        self.transport.request([G, C_SET_LIGHT, 11, 0, 0, *light_block(cfg)])

    def _restore_light(self):
        if self._saved_light is None:
            return
        try:
            self.transport.request([G, C_SET_LIGHT, 11, 0, 0,
                                    *light_block(self._saved_light)])
        except Exception:
            pass

    # -- scrittura colori ---------------------------------------------------

    def _set_color_direct(self, index, rgb):
        r, g, b = rgb
        self.transport.write([G, C_SET_RGB_SINGLE, 3, *u16le(index * 3),
                              0, 0, 0, r, g, b])

    def set_colors(self, colors):
        """Queues colors {index: (r,g,b)}; applies only the changed indices."""
        with self._lock:
            for index, rgb in colors.items():
                if self._last.get(index) != rgb:
                    self._pending[index] = rgb

    def _run(self):
        while self._running:
            with self._lock:
                pending = dict(self._pending)
                self._pending.clear()
            if pending:
                try:
                    for index, rgb in pending.items():
                        if not self._running:
                            break
                        self._set_color_direct(index, rgb)
                        self._last[index] = rgb
                        time.sleep(self.gap)
                    # il device a volte droppa una scrittura isolata: riscrivi
                    # il gruppo dopo ~1s, ma solo se non e' arrivato nuovo stato
                    self._reaffirm = True
                except Exception:
                    # device sconnesso: tenta di riaprire, poi riapplica tutto
                    self._reopen()
            elif self._reaffirm:
                time.sleep(1.0)
                self._reaffirm = False
                with self._lock:
                    if self._pending:
                        continue  # nuovo stato nel frattempo: lo gestira' il prossimo giro
                    reaffirm = dict(self._last)
                try:
                    for index, rgb in reaffirm.items():
                        if not self._running:
                            break
                        self._set_color_direct(index, rgb)
                        time.sleep(self.gap)
                except Exception:
                    self._reopen()
            else:
                time.sleep(0.05)

    def _reopen(self):
        time.sleep(1.0)
        try:
            if self.transport is not None:
                self.transport.close()
        except Exception:
            pass
        self.transport = None
        for _ in range(30):
            if not self._running:
                return
            try:
                self._open()
                self._set_custom_mode()
                with self._lock:
                    self._pending.update(self._last)
                    self._last.clear()
                return
            except Exception:
                time.sleep(2.0)