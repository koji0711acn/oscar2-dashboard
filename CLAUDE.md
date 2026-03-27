# OSCAR2 — Agent Teams Monitoring & Recovery System

## Overview
OSCAR2 is a resident Python process that monitors multiple Claude Code Agent Teams projects.
It detects interruptions, auto-recovers processes, shows status on a web dashboard, and notifies humans.

## Architecture
- `oscar_core.py` — Main loop (60s interval), monitors all projects
- `process_monitor.py` — Checks process liveness and stall detection
- `cli_controller.py` — Starts/stops/restarts Claude CLI via subprocess
- `quality_gate.py` — Mechanical Judge: health checks
- `notifier.py` — Windows desktop notifications via plyer
- `dashboard.py` — Flask web UI on port 5001
- `models.py` — SQLite persistence (project state, events, costs)
- `config.json` — Project definitions and OSCAR settings

## Key Commands
- Start monitoring: `python oscar_core.py`
- Start dashboard: `python dashboard.py`
- Install deps: `pip install -r requirements.txt`

## Conventions
- All paths resolved via config.json `base_path` + project `path`
- Process detection uses psutil on Windows
- SQLite database auto-created at `oscar2.db`
