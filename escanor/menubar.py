"""
menubar.py

`escanor menubar` -- a sun in the macOS menu bar.

The icon shows what Escanor is doing (from the server's /api/status,
polled every second):

    ☀︎  idle (day)    ☾  idle (night)    ◐  thinking    ✺  speaking    ◌  not running

Menu: open the HUD, stop talking, quiet for an hour, switch model, and
start/stop the Escanor server. If no server is running when the menubar
starts, it starts `escanor serve` itself (logs in ~/.escanor/logs/).
It only talks to 127.0.0.1, sending no Origin, so the server's
cross-site check lets it through.
"""
from __future__ import annotations
import json
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

ICONS = {"idle_day": "☀︎", "idle_night": "☾", "thinking": "◐", "speaking": "✺", "offline": "◌"}


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

    def _call(self, method: str, path: str) -> Any:
        request = urllib.request.Request(self.base_url + path, method=method, data=b"" if method == "POST" else None)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read() or b"null")

    def status(self) -> dict | None:
        try:
            return self._call("GET", "/api/status")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def post(self, path: str) -> Any:
        try:
            return self._call("POST", path)
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
    from . import config

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
            models = rumps.MenuItem("Model")
            for alias, spec in settings["models"]["aliases"].items():
                if alias != "mock":
                    models.add(rumps.MenuItem(f"{alias} · {spec}", callback=self._switcher(alias)))
            self.menu = [
                self.status_item, None,
                rumps.MenuItem("Open Escanor", callback=self.open_hud, key="o"),
                rumps.MenuItem("Stop talking", callback=self.stop_talking),
                self.quiet_item, models, None,
                self.server_item,
                rumps.MenuItem("Quit", callback=self.quit_all, key="q"),
            ]
            self.last: dict | None = None
            if start_server and client.status() is None:
                self.start_server()
            rumps.Timer(self.refresh, 1).start()

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
            self.stop_server()
            rumps.quit_application()

        # ---- polling ----
        def refresh(self, _timer) -> None:
            status = client.status()
            self.last = status
            self.title = icon_for(status)
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
