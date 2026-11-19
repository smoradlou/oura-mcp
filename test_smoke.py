"""Smoke test: a fake Oura API + OAuth server, then the full flow:
`login` -> tokens saved -> MCP server over stdio -> expired token refreshed
(single-use refresh token rotated) -> every tool called."""
import asyncio, json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, urlencode

FAKE = {
    "personal_info": {"id": "u1", "age": 34},
    "daily_sleep": [{"day": "2026-11-18", "score": 82}],
    "daily_readiness": [{"day": "2026-11-18", "score": 77, "temperature_deviation": -0.1}],
    "daily_activity": [{"day": "2026-11-18", "score": 90, "steps": 9500, "active_calories": 400, "met": {"items": [1] * 1440}}],
    "daily_stress": [{"day": "2026-11-18", "stress_high": 3600, "recovery_high": 1800, "day_summary": "normal"}],
    "sleep": [{"day": "2026-11-18", "type": "long_sleep", "total_sleep_duration": 27000, "average_hrv": 45, "lowest_heart_rate": 52, "heart_rate": {"items": [1]}}],
    "heartrate": [{"bpm": 60, "source": "awake", "timestamp": "2026-11-18T10:00:00+00:00"},
                  {"bpm": 50, "source": "rest", "timestamp": "2026-11-18T03:00:00+00:00"}],
}
VALID_REFRESH = {"r1"}
stats = {"refreshes": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/oauth/authorize":
            assert q["client_id"] == ["cid"] and "heartrate" in q["scope"][0]
            loc = q["redirect_uri"][0] + "?" + urlencode({"code": "authcode", "state": q["state"][0]})
            self.send_response(302); self.send_header("Location", loc); self.end_headers(); return
        if self.headers.get("Authorization") != "Bearer good-token":
            return self._json(401, {"detail": "bad token"})
        data = FAKE.get(u.path.rsplit("/", 1)[-1], [])
        if isinstance(data, list):
            if "next_token" not in q and len(data) > 1:
                body = {"data": data[:1], "next_token": "p2"}
            elif "next_token" in q:
                body = {"data": data[1:], "next_token": None}
            else:
                body = {"data": data, "next_token": None}
        else:
            body = data
        self._json(200, body)

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        assert form["client_id"] == ["cid"] and form["client_secret"] == ["secret"]
        if form["grant_type"] == ["authorization_code"]:
            assert form["code"] == ["authcode"]
            # expires immediately, so the server must refresh on first use
            return self._json(200, {"access_token": "stale-token", "refresh_token": "r1", "expires_in": 0, "scope": "daily heartrate"})
        rt = form["refresh_token"][0]
        if rt not in VALID_REFRESH:
            return self._json(400, {"error": "invalid_grant"})
        VALID_REFRESH.discard(rt)  # single-use
        stats["refreshes"] += 1
        new_rt = f"r{stats['refreshes'] + 1}"
        VALID_REFRESH.add(new_rt)
        self._json(200, {"access_token": "good-token", "refresh_token": new_rt, "expires_in": 86400})


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
fake = f"http://127.0.0.1:{srv.server_port}"
tmp = tempfile.mkdtemp()
env = {
    "OURA_TOKEN_FILE": f"{tmp}/tokens.json",
    "OURA_AUTHORIZE_URL": f"{fake}/oauth/authorize",
    "OURA_TOKEN_URL": f"{fake}/oauth/token",
    "OURA_API_BASE": f"{fake}/v2/usercollection",
    "OURA_REDIRECT_URI": "http://127.0.0.1:8799/callback",
    "OURA_CLIENT_ID": "cid", "OURA_CLIENT_SECRET": "secret",
    "NO_PROXY": "127.0.0.1,localhost",
}
os.environ.update(env)
os.environ.pop("OURA_ACCESS_TOKEN", None)

# --- 1. login (simulated browser follows the authorize redirect) ---
import httpx, webbrowser
import oura_auth
webbrowser.open = lambda url: threading.Timer(0.5, lambda: httpx.get(url, follow_redirects=True)).start()
oura_auth.login([])
saved = json.load(open(env["OURA_TOKEN_FILE"]))
assert saved["refresh_token"] == "r1"
assert oct(os.stat(env["OURA_TOKEN_FILE"]).st_mode)[-3:] == "600"

# --- 2. MCP server over stdio ---
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=["oura_mcp.py"], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print(len(tools), "tools")
            for t in tools:
                args = {"start_date": "2026-11-18", "end_date": "2026-11-18"} if "start_date" in json.dumps(t.input_schema) else {}
                res = await s.call_tool(t.name, args)
                assert not res.is_error, (t.name, res)
            res = await s.call_tool("get_daily_overview", {})
            print("get_daily_overview:", res.content[0].text[:200].replace("\n", " "))

asyncio.run(main())
saved = json.load(open(env["OURA_TOKEN_FILE"]))
assert stats["refreshes"] == 1, stats  # refreshed once, then reused
assert saved["refresh_token"] == "r2" and saved["access_token"] == "good-token"
print("OK: login, auto-refresh with rotated refresh token, all tools")

# --- 3. concurrent calls on an expired token refresh exactly once ---
saved["expires_at"] = 0
oura_auth.save_tokens(saved)
tm = oura_auth.TokenManager()
async def burst():
    return await asyncio.gather(*[tm.access_token() for _ in range(5)])
assert set(asyncio.run(burst())) == {"good-token"}
assert stats["refreshes"] == 2, stats
print("OK: concurrent refresh happens once")
