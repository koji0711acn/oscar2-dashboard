"""CLI controller: start/stop/restart Claude Code processes."""

import subprocess
import os
import signal
import time

try:
    import psutil
except ImportError:
    psutil = None

from models import update_project_state, record_restart, log_event

# Track child processes by project_id
_processes = {}


def start(project_config, oscar_config):
    """Start claude CLI for a project. Returns PID or None."""
    project_id = project_config["id"]
    project_path = os.path.join(
        oscar_config["base_path"], project_config["path"]
    )

    if not os.path.isdir(project_path):
        log_event(project_id, "ERROR", f"Project path does not exist: {project_path}")
        return None

    # Check for CLAUDE.md to use as prompt source
    claude_md = os.path.join(project_path, "CLAUDE.md")
    resume_flag = os.path.exists(claude_md)

    try:
        cmd = ["claude", "--dangerously-skip-permissions"]
        if resume_flag:
            cmd.extend(["--resume"])

        proc = subprocess.Popen(
            cmd,
            cwd=project_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        _processes[project_id] = proc
        update_project_state(project_id, "RUNNING", proc.pid)
        log_event(project_id, "STARTED", f"PID={proc.pid}")
        return proc.pid
    except FileNotFoundError:
        log_event(project_id, "ERROR", "claude CLI not found in PATH")
        return None
    except Exception as e:
        log_event(project_id, "ERROR", f"Failed to start: {e}")
        return None


def stop(project_config, oscar_config=None):
    """Stop claude CLI for a project."""
    project_id = project_config["id"]

    # Try tracked process first
    proc = _processes.pop(project_id, None)
    if proc and proc.poll() is None:
        _terminate_process(proc.pid)
        update_project_state(project_id, "DEAD", None)
        log_event(project_id, "STOPPED", f"PID={proc.pid}")
        return True

    # Try to find and kill by project path
    if oscar_config:
        from process_monitor import find_claude_process
        project_path = os.path.join(
            oscar_config["base_path"], project_config["path"]
        )
        pid = find_claude_process(project_path)
        if pid:
            _terminate_process(pid)
            update_project_state(project_id, "DEAD", None)
            log_event(project_id, "STOPPED", f"PID={pid}")
            return True

    update_project_state(project_id, "DEAD", None)
    return False


def restart(project_config, oscar_config):
    """Restart claude CLI for a project."""
    project_id = project_config["id"]
    stop(project_config, oscar_config)
    time.sleep(2)
    pid = start(project_config, oscar_config)
    if pid:
        record_restart(project_id)
        log_event(project_id, "RESTARTED", f"New PID={pid}")
    return pid


def _terminate_process(pid):
    """Terminate a process and its children."""
    if psutil:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            parent.terminate()
            gone, alive = psutil.wait_procs([parent] + children, timeout=5)
            for p in alive:
                p.kill()
        except psutil.NoSuchProcess:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
