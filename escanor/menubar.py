"""
menubar.py

`escanor menubar` -- a sun in the macOS menu bar.

The icon shows what Escanor is doing (from the server's /api/status,
polled every second):

    ☀︎  idle (day)   ☾  idle (night)   ◉  listening   ◐  thinking   ✺  speaking   ◌  not running

Menu: open the HUD, stop talking, quiet for an hour, switch model, and
start/stop the Escanor server. If no server is running when the menubar
starts, it starts `escanor serve` itself (logs in ~/.escanor/logs/).
It only talks to 127.0.0.1, sending no Origin, so the server's
cross-site check lets it through.

It also owns the global hotkeys ([hotkeys], hotkeys.py):
  ⌃⌥Space  hold to talk, let go to send (a tap just stops Escanor talking)
  ⌃⌥E      explain the selected text, or the file selected in Finder, in the HUD
Requests go through one worker thread, in order, so a key-up can't
overtake its key-down and the menu never freezes on a slow call.
"""
from __future__ import annotations
import json
import queue
import subprocess
import threading
import sys
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

ICONS = {"idle_day": "☀︎", "idle_night": "☾", "listening": "◉", "thinking": "◐", "speaking": "✺", "offline": "◌"}


def icon_for(status: dict | None, now: datetime | None = None) -> str:
    if status is None:
        return ICONS["offline"]
    state = status.get("state", "idle")
    if state == "idle":
        hour = (now or datetime.now()).hour
        return ICONS["idle_day"] if 6 <= hour < 18 else ICONS["idle_night"]
    return ICONS.get(state, ICONS["idle_day"])


class Client:
    """Tiny HTTP client for the local server's control API."""

    def __init__(self, base_url: str, timeout: float = 0.6):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = urllib.request.Request(self.base_url + path, method=method, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            return json.loads(response.read() or b"null")

    def status(self) -> dict | None:
        try:
            return self._call("GET", "/api/status")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def post(self, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        try:
            return self._call("POST", path, body, timeout)
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except ValueError:
                return {"ok": False, "error": str(e)}
        except (urllib.error.URLError, OSError) as e:
            return {"ok": False, "error": str(e)}


def run(settings: dict, start_server: bool = True) -> None:
    try:
        import rumps
    except ImportError:
        raise RuntimeError("Install the menubar extra (`uv pip install -e '.[menubar]'`) for the menu-bar icon.")
    from urllib.parse import quote
    from . import config, selection
    from . import hotkeys as hk

    host, port = settings["server"]["host"], int(settings["server"]["port"])
    url = f"http://{host}:{port}"
    client = Client(url)
    logs = config.data_dir() / "logs"

    class EscanorBar(rumps.App):
        def __init__(self) -> None:
            super().__init__("Escanor", title=ICONS["offline"], quit_button=None)
            self.server: subprocess.Popen | None = None
            self.status_item = rumps.MenuItem("Escanor · starting…")
            self.quiet_item = rumps.MenuItem("Quiet for 1 hour", callback=self.toggle_quiet)
            self.server_item = rumps.MenuItem("Start Escanor", callback=self.toggle_server)
            self.jobs: queue.Queue = queue.Queue()
            threading.Thread(target=self._work, daemon=True).start()
            self.talking = False
            self.asked_accessibility = False
            self.keys = None
            models = rumps.MenuItem("Model")
            for alias, spec in settings["models"]["aliases"].items():
                if alias != "mock":
                    models.add(rumps.MenuItem(f"{alias} · {spec}", callback=self._switcher(alias)))
            self.menu = [
                self.status_item, None,
                rumps.MenuItem("Open Escanor", callback=self.open_hud, key="o"),
                rumps.MenuItem("Stop talking", callback=self.stop_talking),
                self.quiet_item, models, None,
                *self._hotkey_rows(), None,
                self.server_item,
                rumps.MenuItem("Quit", callback=self.quit_all, key="q"),
            ]
            self.last: dict | None = None
            if start_server and client.status() is None:
                self.start_server()
            self._setup_hotkeys()
            rumps.Timer(self.refresh, 1).start()

        # ---- hotkeys ----
        def _hotkey_rows(self) -> list:
            rows = []
            for name, text in (("talk", "Hold {} to talk"), ("explain", "{} explains the selection")):
                try:
                    rows.append(rumps.MenuItem(text.format(hk.label(settings["hotkeys"][name]))))
                except (ValueError, KeyError):
                    pass
            return rows

        def _setup_hotkeys(self) -> None:
            keys = settings["hotkeys"]
            try:
                self.keys = hk.Hotkeys()
            except (OSError, RuntimeError) as e:
                rumps.notification("Escanor", "Hotkeys unavailable", str(e))
                return
            for combo, press, release in ((keys.get("talk"), self.talk_down, self.talk_up),
                                          (keys.get("explain"), None, self.explain)):
                if not combo:
                    continue
                try:
                    self.keys.register(combo, press, release)
                except (ValueError, RuntimeError) as e:
                    rumps.notification("Escanor", "Hotkey not set", str(e))

        def _work(self) -> None:
            while True:
                job = self.jobs.get()
                try:
                    job()
                except Exception as e:  # noqa: BLE001 -- keep the worker alive
                    rumps.notification("Escanor", "Something went wrong", str(e))

        def _check(self, result, what: str) -> bool:
            if result and result.get("ok"):
                return True
            rumps.notification("Escanor", what, (result or {}).get("error", "Is Escanor running?"))
            return False

        def talk_down(self) -> None:
            if self.talking:
                return
            self.talking = True
            self.title = ICONS["listening"]
            self.jobs.put(lambda: self._check(client.post("/api/talk/start", timeout=5), "Couldn't start listening"))

        def talk_up(self) -> None:
            if not self.talking:
                return
            self.talking = False
            self.title = ICONS["thinking"]
            # transcribing can take a few seconds on a slow Mac
            self.jobs.put(lambda: self._check(client.post("/api/talk/stop", timeout=60), "Couldn't hear that"))

        def explain(self) -> None:
            self.jobs.put(self._explain)

        def _explain(self) -> None:
            trusted = selection.accessibility_trusted()
            if not trusted and not self.asked_accessibility:
                self.asked_accessibility = True
                selection.accessibility_trusted(prompt=True)
                rumps.notification("Escanor", "One-key explain needs Accessibility",
                                   "Allow it in System Settings. Until then, copy (⌘C) first, then press the key.")
            picked = selection.grab(selection.MacPasteboard(), selection.press_copy, lambda: trusted)
            if picked is None:
                rumps.notification("Escanor", "Nothing selected", "Select some text, or a file in Finder, first.")
                return
            result = client.post("/api/explain", {"text": picked.text, "path": picked.path}, timeout=5)
            if self._check(result, "Couldn't explain that") and result.get("viewers") == 0:
                webbrowser.open(url)   # the answer appears there

        # ---- server lifecycle ----
        def start_server(self) -> None:
            logs.mkdir(parents=True, exist_ok=True)
            log = (logs / "serve.log").open("a")
            self.server = subprocess.Popen([sys.executable, "-m", "escanor", "serve"], stdout=log, stderr=log,
                                           cwd=str(Path.home()))

        def stop_server(self) -> None:
            if self.server is not None:
                self.server.terminate()
                try:
                    self.server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.server.kill()
                self.server = None

        def toggle_server(self, _sender) -> None:
            if self.last is None and self.server is None:
                self.start_server()
            else:
                self.stop_server()

        # ---- actions ----
        def open_hud(self, _sender) -> None:
            webbrowser.open(url)

        def stop_talking(self, _sender) -> None:
            client.post("/api/interrupt")

        def toggle_quiet(self, _sender) -> None:
            quiet = bool(self.last and self.last.get("quiet_until"))
            result = client.post(f"/api/quiet?minutes={0 if quiet else 60}")
            if not result or not result.get("ok", False):
                rumps.notification("Escanor", "Couldn't change quiet mode", (result or {}).get("error", ""))

        def _switcher(self, alias: str):
            def switch(_sender) -> None:
                result = client.post(f"/api/model?name={quote(alias)}")
                if not result or not result.get("ok"):
                    rumps.notification("Escanor", f"Couldn't switch to {alias}", (result or {}).get("error", ""))
            return switch

        def quit_all(self, _sender) -> None:
            if self.keys is not None:
                self.keys.close()
            self.stop_server()
            rumps.quit_application()

        # ---- polling ----
        def refresh(self, _timer) -> None:
            status = client.status()
            self.last = status
            self.title = ICONS["listening"] if self.talking else icon_for(status)
            if status is None:
                starting = self.server is not None and self.server.poll() is None
                self.status_item.title = "Escanor · starting…" if starting else "Escanor · not running"
                self.server_item.title = "Stop Escanor" if starting else "Start Escanor"
                return
            line = f"Escanor · {status['state']} · {status['model'].split(':', 1)[-1]}"
            if status.get("quiet_until"):
                line += f" · quiet until {status['quiet_until']}"
            self.status_item.title = line
            self.quiet_item.title = "Resume announcements" if status.get("quiet_until") else "Quiet for 1 hour"
            self.server_item.title = "Stop Escanor" if self.server is not None else "Escanor runs elsewhere"

    EscanorBar().run()
