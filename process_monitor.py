"""Process monitor: detect Claude Code process status per project via PID files."""

import os
import logging
from datetime import datetime, timedelta

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger("oscar2.process_monitor")


def check(project_config, oscar_config):
    """Check project status via PID file. Returns: RUNNING, STALLED, DEAD, or COMPLETED."""
    project_id = project_config["id"]
    project_path = os.path.join(
        oscar_config["base_path"], project_config["path"]
    )
    stall_timeout = project_config.get("stall_timeout_minutes", 30)

    # Import here to avoid circular import
    from cli_controller import read_pid_file, _remove_pid_file

    pid = read_pid_file(project_id)

    if pid is None:
        # No PID file — check if completed
        if _is_completed(project_path):
            return "COMPLETED", None
        return "DEAD", None

    # PID file exists — check if process is alive
    if not _is_process_alive(pid):
        # Process died but PID file remains — clean up
        _remove_pid_file(project_id)
        if _is_completed(project_path):
            return "COMPLETED", None
        return "DEAD", None

    # Process is alive — check for stall
    if _is_stalled(project_path, stall_timeout):
        return "STALLED", pid

    return "RUNNING", pid


def _is_process_alive(pid):
    """Check if a process with the given PID is alive."""
    if psutil:
        try:
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
    else:
        # Fallback: try os.kill with signal 0 (no-op, just checks existence)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


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
