"""Mechanical Judge: validates project health via process survival and file checks."""

import os
from datetime import datetime, timedelta
from process_monitor import find_claude_process


def check_project_health(project_config, oscar_config):
    """Run all quality checks on a project. Returns dict of check results."""
    project_id = project_config["id"]
    project_path = os.path.join(
        oscar_config["base_path"], project_config["path"]
    )

    results = {
        "project_id": project_id,
        "checks": {},
        "healthy": True,
        "timestamp": datetime.now().isoformat(),
    }

    # Check 1: Project directory exists
    dir_exists = os.path.isdir(project_path)
    results["checks"]["directory_exists"] = {
        "passed": dir_exists,
        "detail": project_path,
    }
    if not dir_exists:
        results["healthy"] = False
        return results

    # Check 2: Process is alive
    pid = find_claude_process(project_path)
    results["checks"]["process_alive"] = {
        "passed": pid is not None,
        "detail": f"PID={pid}" if pid else "No process found",
    }
    if pid is None:
        results["healthy"] = False

    # Check 3: CLAUDE.md exists (project has instructions)
    claude_md = os.path.join(project_path, "CLAUDE.md")
    results["checks"]["claude_md_exists"] = {
        "passed": os.path.exists(claude_md),
        "detail": claude_md,
    }

    # Check 4: current_task.md freshness
    task_file = os.path.join(project_path, "current_task.md")
    if os.path.exists(task_file):
        mtime = datetime.fromtimestamp(os.path.getmtime(task_file))
        age = datetime.now() - mtime
        stall_limit = timedelta(minutes=project_config.get("stall_timeout_minutes", 30))
        fresh = age < stall_limit
        results["checks"]["task_file_fresh"] = {
            "passed": fresh,
            "detail": f"Last modified: {mtime.isoformat()}, age: {age}",
        }
        if not fresh:
            results["healthy"] = False
    else:
        results["checks"]["task_file_fresh"] = {
            "passed": True,
            "detail": "No task file (OK for new projects)",
        }

    return results
