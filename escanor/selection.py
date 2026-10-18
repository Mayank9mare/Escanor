"""
selection.py

"What's selected right now, in whatever app is in front?" -- for the
explain hotkey.

There's no general macOS API for another app's selection, so this does
what you would: press ⌘C, read the clipboard, then put your clipboard
back exactly as it was. Pressing ⌘C for you needs Accessibility
permission (System Settings → Privacy & Security → Accessibility); until
it's granted, copy first yourself and the hotkey explains the clipboard.

A file selected in Finder copies as a file URL: that comes back as a
path, for research_ingest to read.
"""
from __future__ import annotations
import ctypes
import time
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass
class Selection:
    text: str = ""
    path: str = ""
    from_clipboard: bool = False  # the old clipboard, because we couldn't press ⌘C


class Pasteboard(Protocol):
    def change_count(self) -> int: ...
    def snapshot(self) -> object: ...
    def restore(self, snapshot: object) -> None: ...
    def text(self) -> str: ...
    def file_paths(self) -> list[str]: ...


def read(pasteboard: Pasteboard) -> Selection | None:
    paths = pasteboard.file_paths()
    if paths:
        return Selection(path=paths[0])
    text = pasteboard.text()
    return Selection(text=text) if text.strip() else None


def grab(pasteboard: Pasteboard, press_copy: Callable[[], None], trusted: Callable[[], bool],
         wait: float = 0.6, sleep: Callable[[float], None] = time.sleep) -> Selection | None:
    """The front app's selection, or None if nothing is selected."""
    if not trusted():
        selection = read(pasteboard)
        if selection is not None:
            selection.from_clipboard = True
        return selection
    before = pasteboard.change_count()
    saved = pasteboard.snapshot()
    press_copy()
    waited = 0.0
    while pasteboard.change_count() == before:
        if waited >= wait:
            return None  # ⌘C copied nothing: there's no selection
        sleep(0.02)
        waited += 0.02
    try:
        return read(pasteboard)
    finally:
        pasteboard.restore(saved)


# ---------- the real Mac ----------

class MacPasteboard:
    def __init__(self) -> None:
        from AppKit import NSPasteboard
        self._pb = NSPasteboard.generalPasteboard()

    def change_count(self) -> int:
        return int(self._pb.changeCount())

    def snapshot(self) -> list[dict]:
        items = []
        for item in self._pb.pasteboardItems() or []:
            items.append({str(t): item.dataForType_(t) for t in item.types()})
        return items

    def restore(self, snapshot: list[dict]) -> None:
        from AppKit import NSPasteboardItem
        self._pb.clearContents()
        restored = []
        for saved in snapshot:
            item = NSPasteboardItem.alloc().init()
            for kind, data in saved.items():
                if data is not None:
                    item.setData_forType_(data, kind)
            restored.append(item)
        if restored:
            self._pb.writeObjects_(restored)

    def text(self) -> str:
        from AppKit import NSPasteboardTypeString
        return str(self._pb.stringForType_(NSPasteboardTypeString) or "")

    def file_paths(self) -> list[str]:
        from AppKit import NSURL, NSPasteboardURLReadingFileURLsOnlyKey
        urls = self._pb.readObjectsForClasses_options_([NSURL], {NSPasteboardURLReadingFileURLsOnlyKey: True})
        return [str(u.path()) for u in urls or []]


def _services():
    lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    lib.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    lib.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    lib.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    lib.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    lib.CFRelease.argtypes = [ctypes.c_void_p]
    lib.AXIsProcessTrusted.restype = ctypes.c_bool
    lib.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
    lib.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    return lib


def press_copy() -> None:
    """Post ⌘C to the front app (only cmd held, whatever keys are physically down)."""
    lib = _services()
    for down in (True, False):
        event = lib.CGEventCreateKeyboardEvent(None, 8, down)  # 8 = "c"
        lib.CGEventSetFlags(event, 0x100000)                   # kCGEventFlagMaskCommand
        lib.CGEventPost(0, event)                              # kCGHIDEventTap
        lib.CFRelease(event)


def accessibility_trusted(prompt: bool = False) -> bool:
    """May we press keys for you? prompt=True also shows macOS's
    "allow in Accessibility" dialog (once; it opens System Settings)."""
    lib = _services()
    if not prompt:
        return bool(lib.AXIsProcessTrusted())
    import objc
    from Foundation import NSDictionary
    options = NSDictionary.dictionaryWithObject_forKey_(True, "AXTrustedCheckOptionPrompt")
    return bool(lib.AXIsProcessTrustedWithOptions(objc.pyobjc_id(options)))
