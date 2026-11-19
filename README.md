# oura-mcp

A read-only MCP server for your Oura Ring data (Oura API v2).

## 1. Get a token
Create a personal access token at https://cloud.ouraring.com/personal-access-tokens
(or use an OAuth2 access token from an Oura API app with the `personal daily heartrate workout tag session spo2` scopes).

## 2. Add it to Claude Code
```bash
claude mcp add oura -e OURA_ACCESS_TOKEN=YOUR_TOKEN -- uv run --directory /path/to/oura-mcp oura-mcp
```

Or in Claude Desktop's `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "oura": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/oura-mcp", "oura-mcp"],
      "env": { "OURA_ACCESS_TOKEN": "YOUR_TOKEN" }
    }
  }
}
```

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
`python test_smoke.py` runs every tool over MCP stdio against a fake Oura API.
