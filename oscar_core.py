"""OSCAR2 Core: main monitoring loop for Agent Teams."""

import json
import time
import signal
import sys
import os
import logging

from dotenv import load_dotenv
_env_path = os.path.join("C:\\Users\\koji3\\OneDrive\\デスクトップ\\blog_automation", ".env")
load_dotenv(_env_path, override=False)

import process_monitor
import cli_controller
import models
import notifier
import recovery_orchestrator
import task_backlog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("oscar2")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

_running = True


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _handle_completed_batch(project_id):
    """When a project completes, mark current running batch as completed."""
    running = task_backlog.get_running_batch(project_id)
    if running:
        task_backlog.mark_completed(running["id"])
        logger.info(f"[{project_id}] Batch '{running['batch_name']}' completed")
        models.log_event(project_id, "BATCH_COMPLETED", f"Batch: {running['batch_name']}")


def _inject_next_batch(project_config, oscar_config):
    """Try to start the next pending batch for a project.

    Returns True if a batch was started, False if no pending batches remain.
    """
    project_id = project_config["id"]
    next_batch = task_backlog.get_next_pending(project_id)

    if not next_batch:
        # No more batches — project is idle
        logger.info(f"[{project_id}] No pending batches, entering IDLE state")
        models.update_project_state(project_id, "IDLE", None)
        models.log_event(project_id, "IDLE", "All batches completed, no pending tasks")
        notifier.notify(
            "OSCAR2",
            f"{project_config['name']}: All batches completed",
            event_type="COMPLETED",
            project_id=project_id,
        )
        return False

    # Mark batch as running and start the process with the task text
    task_backlog.mark_running(next_batch["id"])
    logger.info(f"[{project_id}] Starting batch '{next_batch['batch_name']}' (id={next_batch['id']})")
    models.log_event(
        project_id, "BATCH_STARTED",
        f"Batch: {next_batch['batch_name']} (priority={next_batch['priority']})",
    )

    new_pid = cli_controller.start(project_config, oscar_config, prompt_text=next_batch["tasks_text"])
    if new_pid:
        logger.info(f"[{project_id}] Batch '{next_batch['batch_name']}' started, PID={new_pid}")
    else:
        logger.error(f"[{project_id}] Failed to start batch '{next_batch['batch_name']}'")
        task_backlog.mark_failed(next_batch["id"])
        notifier.notify(
            "OSCAR2 Error",
            f"{project_config['name']}: Failed to start batch {next_batch['batch_name']}",
            event_type="ERROR",
            project_id=project_id,
        )
    return True


def monitor_project(project_config, oscar_config):
    """Check one project via Recovery Orchestrator and take action."""
    project_id = project_config["id"]

    # Update process state in DB
    status, pid = process_monitor.check(project_config, oscar_config)
    models.update_project_state(project_id, status, pid)
    logger.info(f"[{project_id}] status={status} pid={pid}")

    # Use Recovery Orchestrator for decision
    decision = recovery_orchestrator.evaluate(project_config, oscar_config)
    action = decision["action"]
    detail = decision["detail"]

    logger.info(f"[{project_id}] decision={action}: {detail}")

    # Handle COMPLETED: mark current batch done, inject next batch
    if action == "COMPLETED":
        _handle_completed_batch(project_id)
        _inject_next_batch(project_config, oscar_config)
        return

    # For DEAD with no running process but pending batches, try auto-inject
    if status == "DEAD":
        running_batch = task_backlog.get_running_batch(project_id)
        if running_batch:
            # Running batch but process is dead — mark batch as failed and try restart
            task_backlog.mark_failed(running_batch["id"])
            models.log_event(project_id, "BATCH_FAILED", f"Batch: {running_batch['batch_name']}")

        # Check if there are pending batches to start
        next_pending = task_backlog.get_next_pending(project_id)
        if next_pending and project_config.get("auto_restart", False):
            _inject_next_batch(project_config, oscar_config)
            return

    # Execute the recovery decision for non-queue scenarios
    recovery_orchestrator.execute(decision, project_config, oscar_config)

    # Reset retries on successful running
    if status == "RUNNING" and action == "CONTINUE":
        recovery_orchestrator.reset_retries(project_id)

    # Send heartbeat to remote dashboard
    try:
        config = load_config()
        _send_heartbeat(config, project_config, status, pid)
    except Exception:
        pass


def run():
    """Main monitoring loop."""
    global _running

    def signal_handler(sig, frame):
        global _running
        logger.info("Shutdown signal received. Stopping...")
        _running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("=== OSCAR2 Monitoring System Started ===")
    models.init_db()

    config = load_config()
    oscar_config = config["oscar"]
    interval = oscar_config.get("check_interval_seconds", 60)

    logger.info(f"Monitoring {len(config['projects'])} project(s), interval={interval}s")

    while _running:
        try:
            config = load_config()
            oscar_config = config["oscar"]
            for project in config["projects"]:
                if not _running:
                    break
                monitor_project(project, oscar_config)
        except Exception as e:
            logger.error(f"Monitor loop error: {e}", exc_info=True)

        # Sleep in small increments for responsive shutdown
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)

    logger.info("=== OSCAR2 Monitoring System Stopped ===")


def _send_heartbeat(config, project_config, status, pid):
    """Send heartbeat to remote dashboard if configured."""
    remote_url = config.get("oscar", {}).get("remote_dashboard_url")
    if not remote_url:
        return

    try:
        import requests
        url = f"{remote_url.rstrip('/')}/api/heartbeat"
        payload = {
            "project_id": project_config["id"],
            "status": status,
            "pid": pid,
            "current_task": None,
            "last_event": f"status={status}",
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        }
        token = os.environ.get("DASHBOARD_TOKEN") or config.get("oscar", {}).get("dashboard_token")
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        requests.post(url, json=payload, headers=headers, timeout=5)
    except Exception as e:
        logger.debug(f"Heartbeat send failed: {e}")


if __name__ == "__main__":
    run()
