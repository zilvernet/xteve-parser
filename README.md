# xteve-parser

A toolkit of standalone Python scripts for managing an [xTeVe](https://github.com/xteve-project/xTeVe) IPTV proxy deployment: normalizing channel names and groups, preserving manual EPG mappings across refreshes, and monitoring channel health for failover.

All scripts are **zero-dependency** (Python 3.10+ stdlib) except `xteve-refresh.py`, which uses [`requests`](https://pypi.org/project/requests/).

> The companion Flask web UI lives in a separate repo: [xteve-parser-gui](https://github.com/zilvernet/xteve-parser-gui).

## Components

### Channel parser

| Script | What it does |
|---|---|
| `channelparser.py` | Main parser (v1.6). Reads xTeVe's `xepg.json` and rewrites it: renames groups, applies name match-rules, pins forced channel numbers, and saves/restores manual EPG metadata to/from `guide.ini`. Driven by `config.ini`. |
| `xepg-id-fix.py` | One-off utility to detect and repair duplicate/empty `x-channelID` values in `xepg.json`. |

### Failover & health monitoring

| Script | What it does |
|---|---|
| `failover_discovery.py` | Two-mode channel health system — a passive log monitor that learns from real usage, and a sparse scanner that tests failed channels against replacements one at a time. |
| `health_database.py` | SQLite wrapper for per-channel health tracking. Importable or standalone. |
| `m3u_index.py` | Parses M3U playlists into a searchable index (by name, group, or TVG ID). |

### xTeVe / tuner helpers

| Script | What it does |
|---|---|
| `xteve-refresh.py` | Triggers an xTeVe XEPG refresh via its HTTP API. |
| `xteve-logs.py` | Parses xTeVe logs to match active streams to channels/tuners using `xepg.json`. |
| `scripts/m3uFilter.sh` | Downloads and filters a provider M3U playlist down to selected groups. _(GPLv3, by LeeD — see file header.)_ |
| `cron/xteve` | Example crontab for scheduling the M3U filter. |

## Configuration

Copy the templates in `examples/` and fill in your own values:

```bash
cp examples/file.ini.example         file.ini
cp examples/guide.ini.example        guide.ini
cp examples/file_group.ini.example   file_group.ini
cp examples/custom_group.ini.example custom_group.ini
```

`config.ini` (already included) controls `channelparser.py`:

- `[Paths]` — locations of `file.ini`, `guide.ini`, `file_group.ini`, and `xepg.json`
- `[Thresholds] ignore_threshold` — channel IDs at/above this are left untouched
- `[Debug]` — verbose logging toggles

These scripts operate on an xTeVe config directory, typically `/docker_data/xteve-<instance>/config/`. The real `*.ini`, `*.json` state, `data/`, `backup/`, and logs are git-ignored — only your own copies belong on the host.

### Network settings

Deployment-specific addresses default to documentation placeholders (RFC 5737, `192.0.2.x`). Override before use:

- `xteve-refresh.py` — `XTEVE_IP` / `XTEVE_PORT` env vars
- `xteve-logs.py` — `XTEVE_LOG_FILE` / `XTEVE_XEPG_FILE` env vars

## Usage

```bash
# Parse / fix up the EPG (run from the xTeVe config dir)
python3 channelparser.py

# Passive health monitor, then a sparse failover scan
python3 failover_discovery.py /docker_data/xteve-mama/config monitor
python3 failover_discovery.py /docker_data/xteve-mama/config scan

# Initialize / report on the health database
python3 health_database.py /docker_data/xteve-mama/config --init
python3 health_database.py /docker_data/xteve-mama/config --report

# Trigger an xTeVe XEPG refresh
XTEVE_IP=10.0.0.5 XTEVE_PORT=34400 python3 xteve-refresh.py
```

## Notes

- `channelparser.py` modifies `xepg.json` in place — back it up first.
- `scripts/m3uFilter.sh` is GPLv3-licensed (original author LeeD); its license header is preserved in the file.
