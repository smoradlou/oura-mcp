"""Smoke test: spin up a fake Oura API, then talk to the server over MCP stdio."""
import asyncio, json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

FAKE = {
    "personal_info": {"id": "u1", "age": 34},
    "daily_sleep": [{"day": "2026-11-18", "score": 82}],
    "daily_readiness": [{"day": "2026-11-18", "score": 77, "temperature_deviation": -0.1}],
    "daily_activity": [{"day": "2026-11-18", "score": 90, "steps": 9500, "active_calories": 400, "met": {"items": [1]*1440}}],
    "daily_stress": [{"day": "2026-11-18", "stress_high": 3600, "recovery_high": 1800, "day_summary": "normal"}],
    "sleep": [{"day": "2026-11-18", "type": "long_sleep", "total_sleep_duration": 27000, "average_hrv": 45, "lowest_heart_rate": 52, "heart_rate": {"items": [1]}}],
    "heartrate": [{"bpm": 60, "source": "awake", "timestamp": "2026-11-18T10:00:00+00:00"},
                  {"bpm": 50, "source": "rest", "timestamp": "2026-11-18T03:00:00+00:00"}],
}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        assert self.headers["Authorization"] == "Bearer test-token"
        u = urlparse(self.path); name = u.path.rsplit("/", 1)[-1]
        q = parse_qs(u.query)
        data = FAKE.get(name, [])
        if isinstance(data, list):
            # paginate: first page returns half + token
            if "next_token" not in q and len(data) > 1:
                body = {"data": data[:1], "next_token": "p2"}
            elif "next_token" in q:
                body = {"data": data[1:], "next_token": None}
            else:
                body = {"data": data, "next_token": None}
        else:
            body = data
        raw = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(raw)

srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable, args=["oura_mcp.py"], env={
        **os.environ, "OURA_ACCESS_TOKEN": "test-token", "NO_PROXY": "127.0.0.1",
        "OURA_API_BASE": f"http://127.0.0.1:{srv.server_port}/v2/usercollection"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print(len(tools), "tools:", ", ".join(t.name for t in tools))
            for name, args in [("get_personal_info", {}), ("get_daily_overview", {}),
                               ("get_heart_rate", {}), ("get_daily_activity", {"start_date": "2026-11-18", "end_date": "2026-11-18"})]:
                res = await s.call_tool(name, args)
                assert not res.is_error, res
                print(f"\n{name}:", res.content[0].text[:400])
asyncio.run(main())
