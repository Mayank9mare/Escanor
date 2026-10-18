"""
server/remote.py

Escanor on your phone, safely.

The server only listens on 127.0.0.1. To reach it from a phone, Tailscale
(a private network between your own devices) proxies to it with HTTPS:

    tailscale serve --bg 8770      ->  https://<your-mac>.<tailnet>.ts.net

HTTPS matters: a phone browser only allows the microphone on a secure page.

Every request that didn't come straight from this Mac -- a Host other
than localhost, or proxy headers (Tailscale adds them) -- must carry the
pairing token, else it gets a "pair this device" page. `escanor pair`
creates the token and prints a link (and QR code) with it; opening the
link once stores it in an HttpOnly cookie and drops it from the address
bar. `escanor pair --rotate` unpairs every device; `--off` turns remote
access off (no token file = no remote access at all).

This also closes DNS rebinding: a web page whose own domain resolves to
127.0.0.1 arrives with its domain as Host, so it's "remote" and has no token.
"""
from __future__ import annotations
import hmac
import secrets
from pathlib import Path
from urllib.parse import urlparse
from .. import config

COOKIE = "escanor_pair"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded", "tailscale-user-login")
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookie lifetime at about 400 days


def token_path() -> Path:
    return config.data_dir() / "remote_token"


def load_token(path: Path | None = None) -> str | None:
    """The pairing token, or None when remote access is off."""
    try:
        token = (path or token_path()).read_text().strip()
    except OSError:
        return None
    return token or None


def new_token(path: Path | None = None) -> str:
    """Create (or replace: unpairs every device) the pairing token."""
    path = path or token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token + "\n")
    path.chmod(0o600)
    return token


def remove_token(path: Path | None = None) -> bool:
    try:
        (path or token_path()).unlink()
        return True
    except FileNotFoundError:
        return False


def _hostname(host: str) -> str:
    return (urlparse(f"//{host}").hostname or "").lower()


def request_host(headers) -> str:
    """The host the browser thinks it's talking to (behind a proxy, the public one)."""
    forwarded = headers.get("x-forwarded-host")
    return (forwarded.split(",")[0].strip() if forwarded else headers.get("host")) or ""


def is_local(headers) -> bool:
    """Straight from this Mac: a localhost Host and no proxy in between."""
    if any(headers.get(name) for name in PROXY_HEADERS):
        return False
    return _hostname(headers.get("host") or "") in LOOPBACK


def is_https(headers, scheme: str) -> bool:
    return (headers.get("x-forwarded-proto") or scheme).lower() in ("https", "wss")


def token_matches(given: str | None, token: str | None) -> bool:
    return bool(given and token) and hmac.compare_digest(given.encode(), token.encode())


def origin_allowed(origin: str | None, host: str | None, remote_ok: bool = False) -> bool:
    """Cross-site protection (B22). Browsers let any web page connect to
    127.0.0.1, so a page you visit could otherwise drive Escanor (and click
    "Allow" on its own confirmations). Only Escanor's own page may: its
    Origin must be the host it was served from -- localhost, or the paired
    remote address once the request has proved it holds the token.
    Non-browser clients (the menubar, curl) send no Origin and are allowed:
    they already run as you."""
    if not origin:
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https") or parsed.netloc != (host or ""):
        return False
    return remote_ok or (parsed.hostname or "") in LOOPBACK


PAIR_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair with Escanor</title>
<body style="background:#120a04;color:#f6d9a8;font:17px/1.5 -apple-system,system-ui,sans-serif;padding:32px 16px;max-width:30em;margin:auto">
<h1 style="font-weight:600;letter-spacing:.08em">☀︎ ESCANOR</h1>
<p>This device isn't paired yet.</p>
<p>On your Mac, run <code style="color:#ffb347">escanor pair</code> and open the link it shows
(or scan its QR code) on this device.</p></body>"""
