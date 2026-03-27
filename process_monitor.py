"""Process monitor: detect Claude Code process status per project."""

import os
import logging
import subprocess as _subprocess
from datetime import datetime, timedelta

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger("oscar2.process_monitor")


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
    """Find a claude CLI process associated with the given project path.

    Detects both direct 'claude' processes and node.js processes running
    Claude Code (which is how npm-installed claude works on Windows).
    """
    if psutil is None:
        return _find_claude_process_wmic(project_path)

    normalized = os.path.normpath(project_path).lower()

    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            cmdline_parts = info.get("cmdline") or []
            cmdline_str = " ".join(cmdline_parts).lower()
            cwd = (info.get("cwd") or "").lower()

            # Match 1: process name contains "claude"
            is_claude = "claude" in name

            # Match 2: node.js process running claude (npm global install)
            if not is_claude and ("node" in name or "node.exe" in name):
                is_claude = "claude" in cmdline_str

            # Match 3: cmd.exe running claude.cmd
            if not is_claude and ("cmd" in name):
                is_claude = "claude" in cmdline_str

            if not is_claude:
                continue

            # Check if cwd matches or cmdline references the project path
            if normalized in cwd or normalized in cmdline_str:
                logger.debug(f"Found claude process: PID={info['pid']}, name={name}, cwd={cwd}")
                return info["pid"]

            # Also check child processes' cwd
            try:
                parent = psutil.Process(info["pid"])
                for child in parent.children(recursive=True):
                    try:
                        child_cwd = (child.cwd() or "").lower()
                        if normalized in child_cwd:
                            logger.debug(f"Found claude via child: PID={info['pid']}, child_cwd={child_cwd}")
                            return info["pid"]
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def _find_claude_process_wmic(project_path):
    """Fallback: use wmic to find claude processes on Windows."""
    try:
        result = _subprocess.run(
            ["wmic", "process", "get", "processid,commandline,executablepath",
             "/format:csv"],
            capture_output=True, text=True, timeout=15
        )
        normalized = os.path.normpath(project_path).lower()
        for line in result.stdout.strip().split("\n"):
            lower = line.lower()
            if "claude" in lower and normalized in lower:
                parts = line.strip().split(",")
                if parts:
                    try:
                        return int(parts[-1])
                    except ValueError:
                        continue
    except Exception as e:
        logger.debug(f"wmic fallback failed: {e}")
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
