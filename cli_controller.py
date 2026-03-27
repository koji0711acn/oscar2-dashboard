"""CLI controller: start/stop/restart Claude Code processes."""

import subprocess
import os
import signal
import sys
import time
import shutil

try:
    import psutil
except ImportError:
    psutil = None

from models import update_project_state, record_restart, log_event

# Track child processes by project_id
_processes = {}


def _find_claude_cmd():
    """Find the claude CLI executable, searching common Windows paths."""
    # Try direct lookup first
    found = shutil.which("claude")
    if found:
        return found

    # Windows: search npm global install directories
    if sys.platform == "win32":
        candidates = []
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(os.path.join(appdata, "npm", "claude.cmd"))
            candidates.append(os.path.join(appdata, "npm", "claude"))
        localappdata = os.environ.get("LOCALAPPDATA", "")
        if localappdata:
            candidates.append(os.path.join(localappdata, "npm", "claude.cmd"))
        # nvm / fnm / volta paths
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            candidates.append(os.path.join(userprofile, ".npm-global", "claude.cmd"))
            candidates.append(os.path.join(userprofile, "AppData", "Local", "fnm_multishells", "claude.cmd"))
        for c in candidates:
            if os.path.isfile(c):
                return c

    return None


def start(project_config, oscar_config, prompt_text=None):
    """Start claude CLI for a project. Returns PID or None.

    Args:
        project_config: Project configuration dict
        oscar_config: OSCAR global configuration dict
        prompt_text: Optional task text to pass via --print / -p flag.
                     If provided, this is sent as the initial prompt instead of --resume.
    """
    project_id = project_config["id"]
    project_path = os.path.join(
        oscar_config["base_path"], project_config["path"]
    )

    if not os.path.isdir(project_path):
        log_event(project_id, "ERROR", f"Project path does not exist: {project_path}")
        return None

    try:
        claude_bin = _find_claude_cmd()
        if not claude_bin:
            log_event(project_id, "ERROR", "claude CLI not found in PATH or npm global dirs")
            return None

        cmd = [claude_bin, "--dangerously-skip-permissions"]

        if prompt_text:
            # Use -p to pass the task text as initial prompt
            cmd.extend(["-p", prompt_text])
        else:
            # Check for CLAUDE.md to use --resume
            claude_md = os.path.join(project_path, "CLAUDE.md")
            if os.path.exists(claude_md):
                cmd.extend(["--resume"])

        # On Windows, .cmd files need shell=True
        use_shell = sys.platform == "win32" and claude_bin.endswith(".cmd")
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

        proc = subprocess.Popen(
            cmd,
            cwd=project_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            shell=use_shell,
            creationflags=creation_flags,
        )
        _processes[project_id] = proc
        update_project_state(project_id, "RUNNING", proc.pid)
        log_event(project_id, "STARTED", f"PID={proc.pid}, cmd={claude_bin}")
        return proc.pid
    except FileNotFoundError:
        log_event(project_id, "ERROR", f"claude CLI not found: {claude_bin}")
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
