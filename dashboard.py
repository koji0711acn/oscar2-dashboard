"""OSCAR2 Web Dashboard — Flask app on port 5001."""

import json
import os
from flask import Flask, render_template, jsonify, request

import models
import cli_controller

app = Flask(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_project_config(project_id):
    config = load_config()
    for p in config["projects"]:
        if p["id"] == project_id:
            return p, config["oscar"]
    return None, None


@app.route("/")
def index():
    config = load_config()
    states = models.get_all_project_states()
    state_map = {s["project_id"]: s for s in states}

    projects = []
    for p in config["projects"]:
        state = state_map.get(p["id"], {})
        projects.append({
            **p,
            "status": state.get("status", "UNKNOWN"),
            "pid": state.get("pid"),
            "last_check": state.get("last_check"),
            "last_restart": state.get("last_restart"),
            "restart_count": state.get("restart_count", 0),
        })

    events = models.get_recent_events(20)
    return render_template("dashboard.html", projects=projects, events=events)


@app.route("/api/projects")
def api_projects():
    config = load_config()
    states = models.get_all_project_states()
    state_map = {s["project_id"]: s for s in states}

    projects = []
    for p in config["projects"]:
        state = state_map.get(p["id"], {})
        projects.append({
            **p,
            "status": state.get("status", "UNKNOWN"),
            "pid": state.get("pid"),
            "last_check": state.get("last_check"),
            "last_restart": state.get("last_restart"),
            "restart_count": state.get("restart_count", 0),
        })
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
    limit = request.args.get("limit", 50, type=int)
    events = models.get_recent_events(limit)
    return jsonify(events)


if __name__ == "__main__":
    models.init_db()
    config = load_config()
    port = config["oscar"].get("dashboard_port", 5001)
    print(f"OSCAR2 Dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
