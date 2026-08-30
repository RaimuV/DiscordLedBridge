"""Global low-level keyboard hook to catch the knob press (F17).

The knob is firmware-mapped to F17 (VK_F17 = 0x80). This module installs a
WH_KEYBOARD_LL hook and calls a callback every time F17 is pressed, so the app
can toggle the keycap LEDs. Uses pywin32 (already a dependency).

A WH_KEYBOARD_LL hook must run inside a thread that pumps messages, so we own a
dedicated daemon thread: it installs the hook, runs the message loop, and backs
out via WM_QUIT posted from uninstall().
"""

import ctypes
import threading

VK_F17 = 0x80
WM_KEYDOWN = 0x0100
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulong),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", ctypes.c_ulong),
        ("pt", ctypes.c_long * 2),
    ]


_ProcType = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_KBDLLHOOKSTRUCT)
)

_user32 = ctypes.windll.user32


class KeyboardHook:
    def __init__(self, callback):
        self._callback = callback
        self._hook = 0
        self._thread = None
        self._thread_id = None
        self._hook_ready = threading.Event()
        self._proc = _ProcType(self._low_level_proc)

    def _low_level_proc(self, n_code, w_param, l_param):
        if n_code >= 0:
            kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            if w_param == WM_KEYDOWN and kb.vkCode == VK_F17:
                self._callback()
        return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def install(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._hook_ready.wait(timeout=5)

    def _run(self):
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, 0, 0)
        if not self._hook:
            self._hook_ready.set()
            return
        self._hook_ready.set()
        msg = _MSG()
        while _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0):
            _user32.TranslateMessageW(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        _user32.UnhookWindowsHookEx(self._hook)
        self._hook = 0

    def uninstall(self):
        if self._thread is not None:
            if self._thread_id:
                _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2)
            self._thread = None
        self._hook = 0
