"""OAuth2 for the Oura API.

Oura retired personal access tokens in December 2025, so the server signs in
with OAuth2 (authorization code flow). `oura-mcp login` runs the flow once and
saves tokens to a local file; the server then refreshes them on its own.

Oura refresh tokens are single-use: every refresh returns a new one, which is
saved straight away.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
import asyncio
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTHORIZE_URL = os.environ.get("OURA_AUTHORIZE_URL", "https://cloud.ouraring.com/oauth/authorize")
TOKEN_URL = os.environ.get("OURA_TOKEN_URL", "https://api.ouraring.com/oauth/token")
SCOPES = "email personal daily heartrate workout tag session spo2"
DEFAULT_REDIRECT = "http://localhost:8765/callback"
REFRESH_MARGIN_S = 300


def token_path() -> Path:
    if p := os.environ.get("OURA_TOKEN_FILE"):
        return Path(p).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "oura-mcp" / "tokens.json"


def load_tokens() -> dict[str, Any] | None:
    path = token_path()
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_tokens(data: dict[str, Any]) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)


def _store(resp_json: dict[str, Any], client_id: str, client_secret: str) -> dict[str, Any]:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": resp_json["access_token"],
        "refresh_token": resp_json.get("refresh_token"),
        "expires_at": time.time() + int(resp_json.get("expires_in", 86400)),
        "scope": resp_json.get("scope"),
    }
    save_tokens(data)
    return data


class TokenManager:
    """Hands out a valid access token, refreshing when it is near expiry."""

    def __init__(self) -> None:
        self._env_token = os.environ.get("OURA_ACCESS_TOKEN") or os.environ.get("OURA_TOKEN")
        self._lock = asyncio.Lock()

    async def access_token(self, force_refresh: bool = False) -> str:
        if self._env_token:
            return self._env_token
        data = load_tokens()
        if not data:
            raise RuntimeError(
                f"Not signed in to Oura (no tokens at {token_path()}). "
                "Run `oura-mcp login` once in a terminal, then restart the MCP server."
            )
        if not (force_refresh or self._expiring(data)):
            return data["access_token"]
        async with self._lock:
            # Another call may have refreshed while we waited; refresh tokens are single-use.
            fresh = load_tokens() or data
            if fresh.get("refresh_token") != data.get("refresh_token") and not self._expiring(fresh):
                return fresh["access_token"]
            return (await self._refresh(fresh))["access_token"]

    @staticmethod
    def _expiring(data: dict[str, Any]) -> bool:
        return time.time() > data.get("expires_at", 0) - REFRESH_MARGIN_S

    @property
    def can_refresh(self) -> bool:
        return not self._env_token

    async def _refresh(self, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("refresh_token"):
            raise RuntimeError("Oura access token expired and no refresh token is saved. Run `oura-mcp login` again.")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": data["refresh_token"],
                    "client_id": data["client_id"],
                    "client_secret": data["client_secret"],
                },
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Refreshing the Oura token failed ({resp.status_code}: {resp.text[:200]}). "
                "Run `oura-mcp login` again."
            )
        return _store(resp.json(), data["client_id"], data["client_secret"])


# --- Interactive login ---------------------------------------------------------

def _wait_for_local_callback(redirect_uri: str, timeout_s: int = 300) -> str:
    """Listen on the redirect URI's localhost port and return the full callback URL."""
    parsed = urlparse(redirect_uri)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def do_GET(self) -> None:
            if urlparse(self.path).path != parsed.path:
                self.send_response(404)
                self.end_headers()
                return
            result["url"] = f"{parsed.scheme}://{parsed.netloc}{self.path}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Oura sign-in complete.</h2><p>You can close this tab.</p>")

    server = HTTPServer((parsed.hostname or "localhost", parsed.port or 80), Handler)
    server.timeout = 1
    deadline = time.time() + timeout_s
    while "url" not in result and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if "url" not in result:
        raise SystemExit("Timed out waiting for Oura to redirect back.")
    return result["url"]


def login(argv: list[str]) -> None:
    client_id = os.environ.get("OURA_CLIENT_ID") or input("Oura client ID: ").strip()
    client_secret = os.environ.get("OURA_CLIENT_SECRET") or input("Oura client secret: ").strip()
    redirect_uri = os.environ.get("OURA_REDIRECT_URI", DEFAULT_REDIRECT)
    state = secrets.token_urlsafe(16)

    url = AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    })
    print(f"\nOpen this URL to sign in to Oura:\n\n  {url}\n")
    webbrowser.open(url)

    host = urlparse(redirect_uri).hostname
    if host in ("localhost", "127.0.0.1") and urlparse(redirect_uri).scheme == "http":
        print(f"Waiting for the redirect to {redirect_uri} ...")
        callback = _wait_for_local_callback(redirect_uri)
    else:
        print("After approving, your browser goes to your redirect URI (the page may not load).")
        callback = input("Paste the full URL from the address bar here: ").strip()

    q = parse_qs(urlparse(callback).query)
    if "error" in q:
        raise SystemExit(f"Oura returned an error: {q['error'][0]}")
    if q.get("state", [None])[0] != state:
        raise SystemExit("State mismatch: the callback did not come from this login attempt.")
    code = q.get("code", [None])[0]
    if not code:
        raise SystemExit("No authorization code found in that URL.")

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")
    data = _store(resp.json(), client_id, client_secret)
    print(f"\nSigned in. Tokens saved to {token_path()} (scopes: {data.get('scope')}).")


if __name__ == "__main__":
    login(sys.argv[1:])
