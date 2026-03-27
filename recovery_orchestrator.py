"""Recovery Orchestrator: integrates Mechanical Judge + Strategic Judge to decide actions.

Decision flow:
    CONTINUE       — healthy, do nothing
    RESTART        — process dead, auto-restart from current_task.md
    RETRY          — same task failed, retry (up to max_retries)
    ESCALATE_TO_HUMAN — needs human intervention (3x needs_revision, etc.)
    PAUSE          — cost limit exceeded or manual pause
    COMPLETED      — all tasks done
    ABORT          — fatal error, stop and notify
"""

import logging
import os
from datetime import datetime

import models
import cli_controller
import notifier
import quality_gate
import process_monitor

logger = logging.getLogger("oscar2.recovery")

# Track retry counts per project
_retry_counts = {}

# Default max retries before escalation
DEFAULT_MAX_RETRIES = 3


def evaluate(project_config, oscar_config):
    """Evaluate a project and return a decision.

    Returns dict:
        action: CONTINUE | RESTART | RETRY | ESCALATE_TO_HUMAN | PAUSE | COMPLETED | ABORT
        detail: str
    """
    project_id = project_config["id"]
    max_retries = project_config.get("max_retries", DEFAULT_MAX_RETRIES)

    result = {
        "project_id": project_id,
        "action": "CONTINUE",
        "detail": "",
        "timestamp": datetime.now().isoformat(),
    }

    # Step 1: Mechanical Judge — process health
    health = quality_gate.check_project_health(project_config, oscar_config)

    # Step 2: Strategic Judge — quality/cost checks
    strategic = quality_gate.strategic_judge(project_config, oscar_config)

    # --- Decision logic ---

    # Priority 1: Strategic Judge overrides
    if strategic["action"] == "PAUSE":
        result["action"] = "PAUSE"
        result["detail"] = strategic["detail"]
        return result

    if strategic["action"] == "ESCALATE_TO_HUMAN":
        result["action"] = "ESCALATE_TO_HUMAN"
        result["detail"] = strategic["detail"]
        return result

    # Priority 2: Check process status
    project_path = os.path.join(oscar_config["base_path"], project_config["path"])
    status, pid = process_monitor.check(project_config, oscar_config)

    if status == "COMPLETED":
        result["action"] = "COMPLETED"
        result["detail"] = "Project marked as completed"
        return result

    if status == "DEAD":
        if not project_config.get("auto_restart", False):
            result["action"] = "ABORT"
            result["detail"] = "Process dead, auto_restart disabled"
            return result

        # Check retry count
        retries = _retry_counts.get(project_id, 0)
        if retries >= max_retries:
            result["action"] = "ESCALATE_TO_HUMAN"
            result["detail"] = f"Max retries ({max_retries}) exceeded"
            _retry_counts[project_id] = 0  # reset for next cycle
            return result

        result["action"] = "RESTART"
        result["detail"] = f"Process dead, attempting restart (retry {retries + 1}/{max_retries})"
        return result

    if status == "STALLED":
        retries = _retry_counts.get(project_id, 0)
        if retries >= max_retries:
            result["action"] = "ESCALATE_TO_HUMAN"
            result["detail"] = f"Stalled {max_retries} times, escalating"
            _retry_counts[project_id] = 0
            return result

        result["action"] = "RETRY"
        result["detail"] = f"Process stalled, retrying (attempt {retries + 1}/{max_retries})"
        return result

    # Priority 3: Quality verdict check for running process
    if strategic["verdict"] == "needs_revision":
        result["action"] = "CONTINUE"  # still running, just note the issue
        result["detail"] = f"Running but quality needs_revision ({strategic['detail']})"
        return result

    # All good
    if not health["healthy"]:
        # Some checks failed but process is running
        failed = [k for k, v in health["checks"].items() if not v["passed"]]
        result["action"] = "CONTINUE"
        result["detail"] = f"Running with warnings: {', '.join(failed)}"
        return result

    result["action"] = "CONTINUE"
    result["detail"] = f"Healthy, PID={pid}"
    return result


def execute(decision, project_config, oscar_config):
    """Execute the recovery decision.

    Returns True if action was taken, False otherwise.
    """
    project_id = project_config["id"]
    action = decision["action"]
    detail = decision["detail"]

    logger.info(f"[{project_id}] Decision: {action} — {detail}")
    models.log_event(project_id, action, detail)

    if action == "CONTINUE":
        return False

    elif action == "RESTART":
        _retry_counts[project_id] = _retry_counts.get(project_id, 0) + 1
        new_pid = cli_controller.restart(project_config, oscar_config)
        if new_pid:
            logger.info(f"[{project_id}] Restarted with PID={new_pid}")
        else:
            logger.error(f"[{project_id}] Restart failed")
            notifier.notify(
                "OSCAR2 Error",
                f"{project_config['name']} の再起動に失敗",
                event_type="ERROR",
                project_id=project_id,
            )
        return True

    elif action == "RETRY":
        _retry_counts[project_id] = _retry_counts.get(project_id, 0) + 1
        new_pid = cli_controller.restart(project_config, oscar_config)
        if new_pid:
            logger.info(f"[{project_id}] Retrying with PID={new_pid}")
        return True

    elif action == "ESCALATE_TO_HUMAN":
        notifier.notify(
            "OSCAR2 ESCALATE",
            f"{project_config['name']}: {detail}",
            event_type="ESCALATE",
            project_id=project_id,
        )
        logger.warning(f"[{project_id}] ESCALATED TO HUMAN: {detail}")
        return True

    elif action == "PAUSE":
        # Stop the process if running
        cli_controller.stop(project_config, oscar_config)
        notifier.notify(
            "OSCAR2 PAUSE",
            f"{project_config['name']}: {detail}",
            event_type="PAUSE",
            project_id=project_id,
        )
        logger.warning(f"[{project_id}] PAUSED: {detail}")
        return True

    elif action == "COMPLETED":
        _retry_counts.pop(project_id, None)
        notifier.notify(
            "OSCAR2 Complete",
            f"{project_config['name']} 全タスク完了",
            event_type="COMPLETED",
            project_id=project_id,
        )
        logger.info(f"[{project_id}] COMPLETED")
        return True

    elif action == "ABORT":
        cli_controller.stop(project_config, oscar_config)
        notifier.notify(
            "OSCAR2 ABORT",
            f"{project_config['name']}: {detail}",
            event_type="ABORT",
            project_id=project_id,
        )
        logger.error(f"[{project_id}] ABORTED: {detail}")
        return True

    return False


def reset_retries(project_id):
    """Reset retry counter for a project (e.g., after successful run)."""
    _retry_counts.pop(project_id, None)
