"""Phone access: everything not straight from this Mac needs the pairing token."""
import pytest
from starlette.websockets import WebSocketDisconnect
from escanor.server import remote
from tests.test_server import _client

PHONE = "https://mac.tail1234.ts.net"


def _phone(store, pair=True):
    token = remote.new_token() if pair else None
    client, app, _ = _client(store, [], base_url=PHONE)
    return client, token


def test_remote_access_is_off_without_a_token(store):
    client, _ = _phone(store, pair=False)
    assert client.get("/").status_code == 403


def test_unpaired_device_gets_the_pair_page(store):
    client, _ = _phone(store)
    response = client.get("/")
    assert response.status_code == 401 and "escanor pair" in response.text
    assert client.get("/?pair=wrong").status_code == 401
    assert client.get("/static/app.js").status_code == 401


def test_pairing_link_sets_cookie_and_drops_token_from_url(store):
    client, token = _phone(store)
    response = client.get(f"/?pair={token}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Escanor" in client.get("/").text            # the cookie now does it
    assert client.get("/api/models").status_code == 200


def test_paired_phone_cannot_use_the_macs_controls(store):
    client, token = _phone(store)
    client.cookies.set(remote.COOKIE, token)
    assert client.get("/api/status").status_code == 403
    assert client.post("/api/talk/start", headers={"origin": PHONE}).status_code == 403
    assert client.post("/api/explain", json={"text": "x"}, headers={"origin": PHONE}).status_code == 403


def test_rotating_unpairs_devices(store):
    client, token = _phone(store)
    client.cookies.set(remote.COOKIE, token)
    remote.new_token()
    assert client.get("/").status_code == 401


def test_paired_websocket_works_and_unpaired_or_cross_site_does_not(store):
    client, token = _phone(store)
    cookie = {"cookie": f"{remote.COOKIE}={token}"}
    with client.websocket_connect("/ws?conversation=new", headers={**cookie, "origin": PHONE}) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["speech"] == "off"   # text-only test server
    for headers in ({"origin": PHONE}, {**cookie, "origin": "https://evil.example"}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers=headers) as ws:
                ws.receive_json()


def test_proxied_request_counts_as_remote_even_with_localhost_host(store):
    remote.new_token()
    client, _, _ = _client(store, [])
    assert client.get("/", headers={"x-forwarded-for": "100.64.0.7"}).status_code == 401
    assert client.get("/").status_code == 200


def test_dns_rebinding_page_is_treated_as_remote(store):
    client, _, _ = _client(store, [], base_url="http://attacker.example:8770")
    assert client.get("/api/status").status_code == 403


def test_manifest_start_url_pairs_the_home_screen_app(store):
    client, token = _phone(store)
    client.cookies.set(remote.COOKIE, token)
    assert client.get("/manifest.webmanifest").json()["start_url"] == f"/?pair={token}"
    local, _, _ = _client(store, [])
    assert local.get("/manifest.webmanifest").json()["start_url"] == "/"


def test_token_file_is_private_and_removable():
    token = remote.new_token()
    assert remote.load_token() == token and oct(remote.token_path().stat().st_mode)[-3:] == "600"
    assert remote.remove_token() and remote.load_token() is None


@pytest.mark.parametrize("headers,local", [
    ({"host": "127.0.0.1:8770"}, True),
    ({"host": "localhost:8770"}, True),
    ({"host": "[::1]:8770"}, True),
    ({"host": "mac.tail1234.ts.net"}, False),
    ({"host": "127.0.0.1:8770", "tailscale-user-login": "me@example.com"}, False),
])
def test_is_local(headers, local):
    assert remote.is_local(headers) is local
