# oura-mcp

A read-only MCP server for your Oura Ring data (Oura API v2).

Oura retired personal access tokens in December 2025, so this server signs in with OAuth2.
You sign in once and tokens refresh automatically after that.

## 1. Register an Oura API application
In Oura's developer portal, create an application (it's self-serve, no approval needed for personal use):
- **Redirect URI:** `http://localhost:8765/callback`
- Note the **Client ID** and **Client Secret**.

If the portal rejects a plain-http localhost redirect, register any HTTPS URL you control
(e.g. `https://example.com/oura-callback`) and set `OURA_REDIRECT_URI` to it in step 2.
Login will then ask you to paste the URL your browser lands on.

## 2. Sign in once
```bash
git clone https://github.com/smoradlou/oura-mcp && cd oura-mcp
OURA_CLIENT_ID=... OURA_CLIENT_SECRET=... uv run oura-mcp login
```
A browser opens, you approve access, and tokens are saved to `~/.config/oura-mcp/tokens.json`
(readable only by you). Oura refresh tokens are single-use; the server saves each new one.

## 3. Add it to Claude Code
```bash
claude mcp add oura -- uv run --directory "$PWD" oura-mcp
```

Or in Claude Desktop's `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "oura": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/oura-mcp", "oura-mcp"]
    }
  }
}
```

If you ever see "Run `oura-mcp login` again", repeat step 2.

| Env var | Purpose |
|---|---|
| `OURA_CLIENT_ID`, `OURA_CLIENT_SECRET` | Used by `login` (prompted for if unset) |
| `OURA_REDIRECT_URI` | Default `http://localhost:8765/callback` |
| `OURA_TOKEN_FILE` | Default `~/.config/oura-mcp/tokens.json` |
| `OURA_ACCESS_TOKEN` | Use this access token directly and skip the token file |

## Tools
| Tool | What it returns |
|---|---|
| `get_daily_overview` | One row per day: sleep/readiness/activity scores, steps, sleep hours, HRV, resting HR, temp, stress |
| `get_daily_sleep` / `get_daily_readiness` / `get_daily_activity` | Scores and contributors |
| `get_daily_stress` / `get_daily_resilience` | Stress and recovery time, resilience level |
| `get_daily_spo2` / `get_cardiovascular_age` / `get_vo2_max` | Health metrics |
| `get_sleep_periods` | Each sleep/nap with stages, HR, HRV (`include_timeseries` for 5-min series) |
| `get_sleep_time_recommendations` | Oura's suggested bedtime window |
| `get_heart_rate` | HR samples, summarized per day/source by default |
| `get_workouts` / `get_sessions` / `get_tags` / `get_rest_mode_periods` | Logged events |
| `get_personal_info` / `get_ring_configuration` | Profile and ring details |

Date tools take `start_date` / `end_date` (YYYY-MM-DD) and default to the last 7 days.

## Test
`python test_smoke.py` runs the login flow, token refresh (including single-use refresh token rotation) and every tool over MCP stdio against a fake Oura API and OAuth server.
