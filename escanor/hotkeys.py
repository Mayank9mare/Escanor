"""
hotkeys.py

System-wide hotkeys for the menubar, through Carbon's RegisterEventHotKey
(via ctypes). It's what most Mac launchers use, because unlike watching
every keystroke it needs no Accessibility or Input Monitoring permission:
macOS only tells us about our own key combinations. It reports both key
down and key up, which push-to-talk needs.

Events arrive on the main thread's run loop, so this only works inside a
running Cocoa app (the rumps menubar). Since macOS 15, combos whose only
modifiers are option (or option+shift) are refused; include ctrl or cmd.
"""
from __future__ import annotations
import ctypes
import logging
from typing import Callable

log = logging.getLogger(__name__)

MODIFIERS = {
    "cmd": 0x0100, "command": 0x0100, "⌘": 0x0100,
    "shift": 0x0200, "⇧": 0x0200,
    "option": 0x0800, "opt": 0x0800, "alt": 0x0800, "⌥": 0x0800,
    "ctrl": 0x1000, "control": 0x1000, "⌃": 0x1000,
}

# macOS virtual key codes (ANSI layout).
KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "return": 36, "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43,
    "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50, "escape": 53, "esc": 53,
    "f5": 96, "f6": 97, "f7": 98, "f3": 99, "f8": 100, "f9": 101, "f11": 103, "f10": 109, "f12": 111,
    "f4": 118, "f2": 120, "f1": 122, "left": 123, "right": 124, "down": 125, "up": 126,
}

_SYMBOLS = {0x1000: "⌃", 0x0800: "⌥", 0x0200: "⇧", 0x0100: "⌘"}


def parse(combo: str) -> tuple[int, int]:
    """'ctrl+option+space' -> (key code, Carbon modifier mask). ValueError if unknown."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = parts
    if key not in KEYS:
        raise ValueError(f"unknown key '{key}' in hotkey '{combo}'")
    mask = 0
    for mod in mods:
        if mod not in MODIFIERS:
            raise ValueError(f"unknown modifier '{mod}' in hotkey '{combo}'")
        mask |= MODIFIERS[mod]
    if mask in (0, MODIFIERS["shift"], MODIFIERS["option"], MODIFIERS["option"] | MODIFIERS["shift"]):
        raise ValueError(f"hotkey '{combo}' needs ctrl or cmd (macOS refuses option/shift-only global keys)")
    return KEYS[key], mask


def label(combo: str) -> str:
    """'ctrl+option+space' -> '⌃⌥Space' (for menus)."""
    key, mask = parse(combo)
    name = next(k for k, v in KEYS.items() if v == key)
    return "".join(s for bit, s in _SYMBOLS.items() if mask & bit) + (name.upper() if len(name) == 1 else name.title())


def _fourcc(text: str) -> int:
    return int.from_bytes(text.encode("ascii"), "big")


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _HotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_PRESSED, _RELEASED = 5, 6
_SIGNATURE = _fourcc("ESCN")


class Hotkeys:
    """register(combo, on_press, on_release); callbacks run on the main thread."""

    def __init__(self) -> None:
        self._carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        c = self._carbon
        c.GetApplicationEventTarget.restype = ctypes.c_void_p
        c.InstallEventHandler.argtypes = [ctypes.c_void_p, _HANDLER, ctypes.c_uint32, ctypes.POINTER(_EventTypeSpec),
                                          ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        c.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _HotKeyID, ctypes.c_void_p,
                                          ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        c.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        c.GetEventKind.argtypes = [ctypes.c_void_p]
        c.GetEventKind.restype = ctypes.c_uint32
        c.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        self._callbacks: dict[int, tuple[Callable[[], None] | None, Callable[[], None] | None]] = {}
        self._refs: list[ctypes.c_void_p] = []
        self._handler = _HANDLER(self._on_event)   # keep a reference: Carbon holds only the pointer
        specs = (_EventTypeSpec * 2)((_fourcc("keyb"), _PRESSED), (_fourcc("keyb"), _RELEASED))
        handler_ref = ctypes.c_void_p()
        status = c.InstallEventHandler(c.GetApplicationEventTarget(), self._handler, 2, specs, None,
                                       ctypes.byref(handler_ref))
        if status != 0:
            raise RuntimeError(f"couldn't install the hotkey handler (Carbon error {status})")

    def register(self, combo: str, on_press: Callable[[], None] | None = None,
                 on_release: Callable[[], None] | None = None) -> None:
        """Raises ValueError for a bad combo, RuntimeError if macOS refuses it
        (usually: another app already uses it)."""
        key, mask = parse(combo)
        hotkey_id = len(self._callbacks) + 1
        ref = ctypes.c_void_p()
        status = self._carbon.RegisterEventHotKey(key, mask, _HotKeyID(_SIGNATURE, hotkey_id),
                                                  self._carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref))
        if status != 0:
            raise RuntimeError(f"macOS refused the hotkey {label(combo)} (error {status}); "
                               "another app may be using it -- pick another in [hotkeys]")
        self._callbacks[hotkey_id] = (on_press, on_release)
        self._refs.append(ref)

    def close(self) -> None:
        for ref in self._refs:
            self._carbon.UnregisterEventHotKey(ref)
        self._refs.clear()
        self._callbacks.clear()

    def _on_event(self, _call_ref, event, _user_data) -> int:
        try:
            hotkey = _HotKeyID()
            self._carbon.GetEventParameter(event, _fourcc("----"), _fourcc("hkid"), None,
                                           ctypes.sizeof(hotkey), None, ctypes.byref(hotkey))
            on_press, on_release = self._callbacks.get(hotkey.id, (None, None))
            callback = on_press if self._carbon.GetEventKind(event) == _PRESSED else on_release
            if callback is not None:
                callback()
        except Exception:  # noqa: BLE001 -- an exception must never cross back into Carbon
            log.exception("hotkey callback failed")
        return 0
