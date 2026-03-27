"""Process monitor: detect Claude Code process status per project."""

import os
import time
from datetime import datetime, timedelta

try:
    import psutil
except ImportError:
    psutil = None


def check(project_config, oscar_config):
    """Check project status. Returns: RUNNING, STALLED, DEAD, or COMPLETED."""
    project_id = project_config["id"]
    project_path = os.path.join(
        oscar_config["base_path"], project_config["path"]
    )
    stall_timeout = project_config.get("stall_timeout_minutes", 30)

    # Check if claude process is alive for this project
    pid = find_claude_process(project_path)

    if pid is None:
        # Check if completed (current_task.md contains COMPLETED marker)
        if _is_completed(project_path):
            return "COMPLETED", None
        return "DEAD", None

    # Process exists — check for stall via current_task.md timestamp
    if _is_stalled(project_path, stall_timeout):
        return "STALLED", pid

    return "RUNNING", pid


def find_claude_process(project_path):
    """Find a claude CLI process associated with the given project path."""
    if psutil is None:
        return _find_claude_process_wmic(project_path)

    normalized = os.path.normpath(project_path).lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            if "claude" not in name:
                continue
            # Check if cwd matches or cmdline references the path
            cwd = (info.get("cwd") or "").lower()
            cmdline = " ".join(info.get("cmdline") or []).lower()
            if normalized in cwd or normalized in cmdline:
                return info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def _find_claude_process_wmic(project_path):
    """Fallback: use wmic to find claude processes on Windows."""
    import subprocess

    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name like '%claude%'", "get",
             "processid,commandline", "/format:csv"],
            capture_output=True, text=True, timeout=10
        )
        normalized = os.path.normpath(project_path).lower()
        for line in result.stdout.strip().split("\n"):
            if normalized in line.lower():
                parts = line.strip().split(",")
                if parts:
                    try:
                        return int(parts[-1])
                    except ValueError:
                        continue
    except Exception:
        pass
    return None


def _is_stalled(project_path, timeout_minutes):
    """Check if current_task.md hasn't been updated within timeout."""
    task_file = os.path.join(project_path, "current_task.md")
    if not os.path.exists(task_file):
        return False
    try:
        mtime = os.path.getmtime(task_file)
        age = datetime.now() - datetime.fromtimestamp(mtime)
        return age > timedelta(minutes=timeout_minutes)
    except OSError:
        return False


def _is_completed(project_path):
    """Check if project has a completion marker."""
    task_file = os.path.join(project_path, "current_task.md")
    if not os.path.exists(task_file):
        return False
    try:
        with open(task_file, "r", encoding="utf-8") as f:
            content = f.read()
        return "COMPLETED" in content.upper()
    except OSError:
        return False
