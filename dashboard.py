"""OSCAR2 Web Dashboard — Flask app on port 5001."""

import json
import os
import subprocess
from datetime import datetime
from flask import Flask, render_template, jsonify, request

import models
import cli_controller

app = Flask(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Track when monitoring started (for uptime calculation)
_start_time = datetime.now()


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_project_config(project_id):
    config = load_config()
    for p in config["projects"]:
        if p["id"] == project_id:
            return p, config["oscar"]
    return None, None


def _get_project_extra_info(project_config, oscar_config):
    """Get additional info for a project: current task, git log, test results, uptime, tokens."""
    project_path = os.path.join(oscar_config["base_path"], project_config["path"])
    info = {
        "current_task": None,
        "latest_commit": None,
        "test_summary": None,
        "uptime_seconds": (datetime.now() - _start_time).total_seconds(),
        "estimated_tokens": None,
    }

    # Current task from current_task.md
    task_file = os.path.join(project_path, "current_task.md")
    if os.path.exists(task_file):
        try:
            with open(task_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            # First non-empty line as task name
            for line in content.split("\n"):
                line = line.strip().lstrip("#").strip()
                if line:
                    info["current_task"] = line[:120]
                    break
        except OSError:
            pass

    # Latest git commit
    if os.path.isdir(project_path):
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                cwd=project_path,
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                info["latest_commit"] = result.stdout.strip()[:100]
        except Exception:
            pass

    # Test results summary - look for common test result files
    for test_file in ["test_results.txt", "test_results.json", ".test_output"]:
        tf = os.path.join(project_path, test_file)
        if os.path.exists(tf):
            try:
                with open(tf, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                info["test_summary"] = content[:200] if content else None
            except OSError:
                pass
            break

    # Estimate token consumption based on cost records
    state = models.get_project_state(project_config["id"])
    if state:
        today = datetime.now().strftime("%Y-%m-%d")
        cost = models.get_daily_cost(project_config["id"], today)
        # Rough: $0.015/1K input tokens, $0.075/1K output tokens -> avg ~$0.03/1K tokens
        if cost and cost > 0:
            info["estimated_tokens"] = int(cost / 0.00003)
        else:
            info["estimated_tokens"] = 0

    return info


def _build_project_list():
    """Build full project list with state and extra info."""
    config = load_config()
    states = models.get_all_project_states()
    state_map = {s["project_id"]: s for s in states}
    oscar_config = config["oscar"]

    projects = []
    for p in config["projects"]:
        state = state_map.get(p["id"], {})
        extra = _get_project_extra_info(p, oscar_config)
        projects.append({
            **p,
            "status": state.get("status", "UNKNOWN"),
            "pid": state.get("pid"),
            "last_check": state.get("last_check"),
            "last_restart": state.get("last_restart"),
            "restart_count": state.get("restart_count", 0),
            **extra,
        })
    return projects, config


@app.route("/")
def index():
    projects, config = _build_project_list()
    events = models.get_recent_events(50)
    notifications = models.get_recent_notifications(20)
    return render_template("dashboard.html", projects=projects, events=events,
                           notifications=notifications)


@app.route("/api/projects")
def api_projects():
    projects, _ = _build_project_list()
    return jsonify(projects)


@app.route("/api/project/<project_id>/start", methods=["POST"])
def api_start(project_id):
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    pid = cli_controller.start(project_config, oscar_config)
    return jsonify({"status": "started", "pid": pid})


@app.route("/api/project/<project_id>/stop", methods=["POST"])
def api_stop(project_id):
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    cli_controller.stop(project_config, oscar_config)
    return jsonify({"status": "stopped"})


@app.route("/api/project/<project_id>/restart", methods=["POST"])
def api_restart(project_id):
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    pid = cli_controller.restart(project_config, oscar_config)
    return jsonify({"status": "restarted", "pid": pid})


@app.route("/api/events")
def api_events():
    limit = request.args.get("limit", 100, type=int)
    project_id = request.args.get("project_id")
    event_type = request.args.get("event_type")
    events = models.get_filtered_events(limit=limit, project_id=project_id, event_type=event_type)
    return jsonify(events)


@app.route("/api/projects", methods=["POST"])
def api_add_project():
    """Add a new project to config."""
    data = request.get_json()
    if not data or not data.get("id") or not data.get("name") or not data.get("path"):
        return jsonify({"error": "id, name, and path are required"}), 400

    config = load_config()
    # Check for duplicate
    for p in config["projects"]:
        if p["id"] == data["id"]:
            return jsonify({"error": f"Project {data['id']} already exists"}), 409

    new_project = {
        "id": data["id"],
        "name": data["name"],
        "path": data["path"],
        "auto_restart": data.get("auto_restart", True),
        "stall_timeout_minutes": data.get("stall_timeout_minutes", 30),
        "max_cost_per_day_usd": data.get("max_cost_per_day_usd", 10),
    }
    config["projects"].append(new_project)
    save_config(config)
    models.log_event(data["id"], "STARTED", "Project added to OSCAR2")
    return jsonify({"status": "added", "project": new_project}), 201


@app.route("/api/projects/<project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    """Remove a project from config."""
    config = load_config()
    original_len = len(config["projects"])
    config["projects"] = [p for p in config["projects"] if p["id"] != project_id]
    if len(config["projects"]) == original_len:
        return jsonify({"error": "Project not found"}), 404
    save_config(config)
    models.log_event(project_id, "STOPPED", "Project removed from OSCAR2")
    return jsonify({"status": "deleted"})


@app.route("/api/notifications")
def api_notifications():
    limit = request.args.get("limit", 50, type=int)
    notifications = models.get_recent_notifications(limit)
    return jsonify(notifications)


@app.route("/api/charts/daily_cost")
def api_daily_cost():
    """Daily cost data for chart."""
    days = request.args.get("days", 30, type=int)
    data = models.get_daily_cost_history(days)
    return jsonify(data)


@app.route("/api/charts/publishable_rate")
def api_publishable_rate():
    """Publishable rate trend for chart."""
    days = request.args.get("days", 30, type=int)
    data = models.get_publishable_rate_history(days)
    return jsonify(data)


@app.route("/api/charts/event_breakdown")
def api_event_breakdown():
    """Event type breakdown for chart."""
    data = models.get_event_type_breakdown()
    return jsonify(data)


if __name__ == "__main__":
    models.init_db()
    config = load_config()
    port = config["oscar"].get("dashboard_port", 5001)
    print(f"OSCAR2 Dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
