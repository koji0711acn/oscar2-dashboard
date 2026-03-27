"""OSCAR2 Core: main monitoring loop for Agent Teams."""

import json
import time
import signal
import sys
import os
import logging

import process_monitor
import cli_controller
import models
import notifier
import recovery_orchestrator

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

    # Execute the decision
    recovery_orchestrator.execute(decision, project_config, oscar_config)

    # Reset retries on successful running
    if status == "RUNNING" and action == "CONTINUE":
        recovery_orchestrator.reset_retries(project_id)


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


if __name__ == "__main__":
    run()
