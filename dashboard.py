"""OSCAR2 Web Dashboard — Flask app on port 5001."""

import json
import os
import subprocess
import functools
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file, session, redirect, url_for

# Load .env (try local .env first, then blog_automation)
try:
    from dotenv import load_dotenv
    _base = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(_base, ".env"), override=False)
    _blog_env = os.path.join(os.environ.get("OSCAR_BASE_PATH", "C:\\Users\\koji3\\OneDrive\\デスクトップ"),
                             "blog_automation", ".env")
    if os.path.exists(_blog_env):
        load_dotenv(_blog_env, override=False)
except ImportError:
    pass  # dotenv optional on Railway (env vars set directly)

import models
import cli_controller
import task_backlog
import task_decomposer

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "oscar2-dev-secret-change-me-in-prod")
app.permanent_session_lifetime = __import__("datetime").timedelta(hours=24)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")

# Cloud mode: OSCAR_MODE=cloud disables local process monitoring
CLOUD_MODE = os.environ.get("OSCAR_MODE", "").lower() == "cloud"

# Default config for Railway (no config.json on disk)
_DEFAULT_CONFIG = {
    "projects": [
        {"id": "blog_automation", "name": "Blog Automation", "path": "blog_automation",
         "auto_restart": True, "stall_timeout_minutes": 30, "max_cost_per_day_usd": 10},
    ],
    "oscar": {
        "dashboard_port": 5001, "check_interval_seconds": 60,
        "base_path": os.environ.get("OSCAR_BASE_PATH", "/app"),
        "dashboard_token": None, "localhost_no_auth": True, "remote_dashboard_url": None,
    }
}

# Track when monitoring started (for uptime calculation)
_start_time = datetime.now()


# --- Authentication middleware ---

def _get_dashboard_password():
    """Get dashboard password from env or config."""
    pw = os.environ.get("DASHBOARD_PASSWORD")
    if pw:
        return pw
    try:
        config = load_config()
        return config.get("oscar", {}).get("dashboard_password")
    except Exception:
        return None


def _get_dashboard_token():
    """Get dashboard token from env or config."""
    token = os.environ.get("DASHBOARD_TOKEN")
    if token:
        return token
    try:
        config = load_config()
        return config.get("oscar", {}).get("dashboard_token")
    except Exception:
        return None


def _is_localhost():
    """Check if request is from localhost."""
    remote = request.remote_addr
    return remote in ("127.0.0.1", "::1", "localhost")


def _is_authenticated():
    """Check if current request is authenticated via session, token, or localhost."""
    # Session auth (browser login)
    if session.get("logged_in"):
        return True

    # Localhost bypass (only in local mode, not cloud)
    if not CLOUD_MODE:
        try:
            config = load_config()
            if config.get("oscar", {}).get("localhost_no_auth", True) and _is_localhost():
                return True
        except Exception:
            pass

    # Token auth (API)
    token = _get_dashboard_token()
    if token:
        auth_header = request.headers.get("Authorization", "")
        query_token = request.args.get("token", "")
        if auth_header == f"Bearer {token}" or query_token == token:
            return True

    # In cloud mode, always require auth (no open access)
    if CLOUD_MODE:
        return False

    # Local mode: no password/token configured = open access
    if not _get_dashboard_password() and not _get_dashboard_token():
        return True

    return False


def require_auth(f):
    """Decorator to require authentication on API endpoints."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if _is_authenticated():
            return f(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401
    return decorated


def load_config():
    """Load config from file, env var OSCAR_CONFIG, or use defaults."""
    # Try env var first (for Railway)
    env_config = os.environ.get("OSCAR_CONFIG")
    if env_config:
        try:
            return json.loads(env_config)
        except json.JSONDecodeError:
            pass

    # Try config.json file
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    # Generate default config
    return _DEFAULT_CONFIG.copy()


def save_config(config):
    """Save config to file. Skips if config.json doesn't exist (cloud mode)."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except OSError:
        pass  # Cloud mode: can't write config.json


def get_project_config(project_id):
    config = load_config()
    for p in config["projects"]:
        if p["id"] == project_id:
            return p, config["oscar"]
    return None, None


def _get_project_extra_info(project_config, oscar_config):
    """Get additional info for a project: current task, git log, test results, uptime, tokens."""
    project_path = os.path.join(oscar_config.get("base_path", "/app"), project_config["path"])
    info = {
        "current_task": None,
        "latest_commit": None,
        "test_summary": None,
        "uptime_seconds": (datetime.now() - _start_time).total_seconds(),
        "estimated_tokens": None,
    }

    # In cloud mode, skip local file checks
    if CLOUD_MODE:
        state = models.get_project_state(project_config["id"])
        if state:
            today = datetime.now().strftime("%Y-%m-%d")
            cost = models.get_daily_cost(project_config["id"], today)
            info["estimated_tokens"] = int(cost / 0.00003) if cost and cost > 0 else 0
        return info

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
    """Build full project list with state, extra info, and queue info."""
    config = load_config()
    states = models.get_all_project_states()
    state_map = {s["project_id"]: s for s in states}
    oscar_config = config["oscar"]

    projects = []
    for p in config["projects"]:
        state = state_map.get(p["id"], {})
        extra = _get_project_extra_info(p, oscar_config)

        # Queue info
        running_batch = task_backlog.get_running_batch(p["id"])
        counts = task_backlog.count_by_status(p["id"])

        raw_status = state.get("status", "UNKNOWN")
        # Derive IDLE: process dead/unknown + no running batch + no pending batches
        if raw_status in ("DEAD", "UNKNOWN") and not running_batch and counts.get("pending", 0) == 0:
            display_status = "IDLE"
        else:
            display_status = raw_status

        projects.append({
            **p,
            "status": display_status,
            "pid": state.get("pid"),
            "last_check": state.get("last_check"),
            "last_restart": state.get("last_restart"),
            "restart_count": state.get("restart_count", 0),
            "running_batch": running_batch["batch_name"] if running_batch else None,
            "pending_batches": counts.get("pending", 0),
            "completed_batches": counts.get("completed", 0),
            **extra,
        })
    return projects, config


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    expected = _get_dashboard_password()
    if request.method == "POST":
        password = request.form.get("password", "")
        if expected and password == expected:
            session.permanent = True
            session["logged_in"] = True
            return redirect(url_for("index"))
        if not expected and CLOUD_MODE:
            return render_template("login.html", error="DASHBOARD_PASSWORD not configured on server")
        return render_template("login.html", error="Incorrect password")
    if CLOUD_MODE and not expected:
        return render_template("login.html", error="DASHBOARD_PASSWORD not configured on server")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    # Cloud mode: always require authentication
    if CLOUD_MODE and not _is_authenticated():
        return redirect(url_for("login"))
    # Local mode: require auth only if password is set
    if _get_dashboard_password() and not _is_authenticated():
        return redirect(url_for("login"))

    projects, config = _build_project_list()
    events = models.get_recent_events(50)
    notifications = models.get_recent_notifications(20)

    # Add latest QA result to each project
    for p in projects:
        qa_records = models.get_recent_quality(p["id"], limit=1)
        if qa_records:
            p["qa_verdict"] = qa_records[0]["verdict"]
            p["qa_detail"] = qa_records[0]["detail"]
            p["qa_time"] = qa_records[0]["created_at"]
        else:
            p["qa_verdict"] = None

    return render_template("dashboard.html", projects=projects, events=events,
                           notifications=notifications)


@app.route("/api/projects")
@require_auth
def api_projects():
    projects, _ = _build_project_list()
    return jsonify(projects)


@app.route("/api/project/<project_id>/start", methods=["POST"])
@require_auth
def api_start(project_id):
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    pid = cli_controller.start(project_config, oscar_config)
    return jsonify({"status": "started", "pid": pid})


@app.route("/api/project/<project_id>/stop", methods=["POST"])
@require_auth
def api_stop(project_id):
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    cli_controller.stop(project_config, oscar_config)
    return jsonify({"status": "stopped"})


@app.route("/api/project/<project_id>/restart", methods=["POST"])
@require_auth
def api_restart(project_id):
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    pid = cli_controller.restart(project_config, oscar_config)
    return jsonify({"status": "restarted", "pid": pid})


@app.route("/api/events")
@require_auth
def api_events():
    limit = request.args.get("limit", 100, type=int)
    project_id = request.args.get("project_id")
    event_type = request.args.get("event_type")
    events = models.get_filtered_events(limit=limit, project_id=project_id, event_type=event_type)
    return jsonify(events)


@app.route("/api/projects", methods=["POST"])
@require_auth
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
@require_auth
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
@require_auth
def api_notifications():
    limit = request.args.get("limit", 50, type=int)
    event_type = request.args.get("event_type")
    if event_type:
        notifications = models.get_filtered_notifications(limit, event_type)
    else:
        notifications = models.get_recent_notifications(limit)
    return jsonify(notifications)


@app.route("/api/charts/daily_cost")
@require_auth
def api_daily_cost():
    """Daily cost data for chart."""
    days = request.args.get("days", 30, type=int)
    data = models.get_daily_cost_history(days)
    return jsonify(data)


@app.route("/api/charts/publishable_rate")
@require_auth
def api_publishable_rate():
    """Publishable rate trend for chart."""
    days = request.args.get("days", 30, type=int)
    data = models.get_publishable_rate_history(days)
    return jsonify(data)


@app.route("/api/charts/event_breakdown")
@require_auth
def api_event_breakdown():
    """Event type breakdown for chart."""
    data = models.get_event_type_breakdown()
    return jsonify(data)


# --- Task Queue API ---

@app.route("/api/queue")
@require_auth
def api_queue_all():
    """Get all queue items, optionally filtered by project_id."""
    project_id = request.args.get("project_id")
    items = task_backlog.list_all(project_id=project_id)
    return jsonify(items)


@app.route("/api/queue/<project_id>")
@require_auth
def api_queue_project(project_id):
    """Get queue items for a specific project."""
    items = task_backlog.list_all(project_id=project_id)
    return jsonify(items)


@app.route("/api/queue", methods=["POST"])
@require_auth
def api_queue_add():
    """Add a new batch to the queue."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    project_id = data.get("project_id")
    batch_name = data.get("batch_name")
    tasks_text = data.get("tasks_text")
    if not project_id or not batch_name or not tasks_text:
        return jsonify({"error": "project_id, batch_name, and tasks_text are required"}), 400

    description = data.get("description", "")
    priority = data.get("priority", 3)
    try:
        priority = int(priority)
        priority = max(1, min(5, priority))
    except (ValueError, TypeError):
        priority = 3

    row_id = task_backlog.add_batch(project_id, batch_name, tasks_text, description, priority)
    models.log_event(project_id, "BATCH_QUEUED", f"Batch: {batch_name} (priority={priority})")
    batch = task_backlog.get_batch(row_id)
    return jsonify({"status": "added", "batch": batch}), 201


@app.route("/api/queue/<int:batch_id>/priority", methods=["PUT"])
@require_auth
def api_queue_priority(batch_id):
    """Update the priority of a batch."""
    data = request.get_json()
    if not data or "priority" not in data:
        return jsonify({"error": "priority is required"}), 400
    try:
        new_priority = int(data["priority"])
        new_priority = max(1, min(5, new_priority))
    except (ValueError, TypeError):
        return jsonify({"error": "priority must be an integer 1-5"}), 400

    if task_backlog.update_priority(batch_id, new_priority):
        return jsonify({"status": "updated", "priority": new_priority})
    return jsonify({"error": "Batch not found"}), 404


@app.route("/api/queue/<int:batch_id>", methods=["DELETE"])
@require_auth
def api_queue_delete(batch_id):
    """Delete a batch from the queue."""
    if task_backlog.delete_batch(batch_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "Batch not found"}), 404


@app.route("/api/queue/<int:batch_id>/cancel", methods=["POST"])
@require_auth
def api_queue_cancel(batch_id):
    """Cancel a running or pending batch."""
    batch = task_backlog.get_batch(batch_id)
    if not batch:
        return jsonify({"error": "Batch not found"}), 404

    if task_backlog.cancel_batch(batch_id):
        models.log_event(batch["project_id"], "BATCH_CANCELLED", f"Batch: {batch['batch_name']}")
        return jsonify({"status": "cancelled"})
    return jsonify({"error": "Batch cannot be cancelled (already completed/failed)"}), 409


# --- Task Decomposer API ---

@app.route("/api/decompose", methods=["POST"])
@require_auth
def api_decompose():
    """Decompose a natural language request into structured child tasks."""
    data = request.get_json()
    if not data or not data.get("request_text"):
        return jsonify({"error": "request_text is required"}), 400

    project_context = data.get("project_context", "")
    result = task_decomposer.decompose(data["request_text"], project_context)
    return jsonify(result)


@app.route("/api/decompose/enqueue", methods=["POST"])
@require_auth
def api_decompose_enqueue():
    """Add decomposed tasks as a batch to the queue."""
    data = request.get_json()
    if not data or not data.get("project_id") or not data.get("tasks"):
        return jsonify({"error": "project_id and tasks are required"}), 400

    batch_name = data.get("batch_name", "AI Decomposed Tasks")
    tasks = data["tasks"]
    tasks_text = task_decomposer.tasks_to_batch_text(tasks)
    description = f"AI-decomposed: {len(tasks)} child tasks"
    priority = data.get("priority", 3)

    row_id = task_backlog.add_batch(
        data["project_id"], batch_name, tasks_text, description, priority
    )
    models.log_event(data["project_id"], "BATCH_QUEUED", f"AI decomposed: {batch_name} ({len(tasks)} tasks)")
    batch = task_backlog.get_batch(row_id)
    return jsonify({"status": "enqueued", "batch": batch}), 201


# --- Project Settings API ---

@app.route("/api/projects/<project_id>/settings", methods=["PUT"])
@require_auth
def api_update_project_settings(project_id):
    """Update project settings in config.json."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    config = load_config()
    for p in config["projects"]:
        if p["id"] == project_id:
            if "auto_restart" in data:
                p["auto_restart"] = bool(data["auto_restart"])
            if "stall_timeout_minutes" in data:
                p["stall_timeout_minutes"] = max(1, int(data["stall_timeout_minutes"]))
            if "max_cost_per_day_usd" in data:
                p["max_cost_per_day_usd"] = max(1, float(data["max_cost_per_day_usd"]))
            if "name" in data:
                p["name"] = str(data["name"])
            save_config(config)
            models.log_event(project_id, "CONFIG_CHANGED", f"Settings updated: {list(data.keys())}")
            return jsonify({"status": "updated", "project": p})
    return jsonify({"error": "Project not found"}), 404


# --- QA Check API ---

@app.route("/api/qa/check", methods=["POST"])
@require_auth
def api_qa_check():
    """Run QA check on a specific HTML file or project's latest output."""
    import quality_gate
    data = request.get_json()
    if not data or not data.get("project_id"):
        return jsonify({"error": "project_id required"}), 400

    project_config, oscar_config = get_project_config(data["project_id"])
    if not project_config:
        return jsonify({"error": "Project not found"}), 404

    result = quality_gate.run_qa_check_on_latest(project_config, oscar_config)
    if result is None:
        return jsonify({"error": "No HTML files found in output/"}), 404
    return jsonify(result)


@app.route("/api/qa/reports")
@require_auth
def api_qa_reports():
    """List available QA reports."""
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qa_reports')
    if not os.path.isdir(report_dir):
        return jsonify([])
    reports = []
    for fname in sorted(os.listdir(report_dir), reverse=True):
        if fname.endswith('_qa_report.md'):
            fpath = os.path.join(report_dir, fname)
            reports.append({
                'filename': fname,
                'size': os.path.getsize(fpath),
                'modified': os.path.getmtime(fpath),
            })
    return jsonify(reports[:50])


@app.route("/api/qa/report/<path:filename>")
@require_auth
def api_qa_report(filename):
    """Get a specific QA report content."""
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qa_reports')
    fpath = os.path.join(report_dir, filename)
    if not os.path.exists(fpath):
        return jsonify({"error": "Report not found"}), 404
    with open(fpath, 'r', encoding='utf-8') as f:
        content = f.read()
    return jsonify({"filename": filename, "content": content})


# --- Recovery action API ---

@app.route("/api/project/<project_id>/resume", methods=["POST"])
@require_auth
def api_resume(project_id):
    """Resume an escalated/paused project."""
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    # Reset state and try to restart
    models.update_project_state(project_id, "DEAD", None)
    models.log_event(project_id, "RESUMED", "Manually resumed by user")
    pid = cli_controller.start(project_config, oscar_config)
    return jsonify({"status": "resumed", "pid": pid})


@app.route("/api/project/<project_id>/abort", methods=["POST"])
@require_auth
def api_abort(project_id):
    """Abort a project (stop and mark as aborted)."""
    project_config, oscar_config = get_project_config(project_id)
    if not project_config:
        return jsonify({"error": "Project not found"}), 404
    cli_controller.stop(project_config, oscar_config)
    models.update_project_state(project_id, "DEAD", None)
    models.log_event(project_id, "ABORT", "Manually aborted by user")
    return jsonify({"status": "aborted"})


# --- Work hours data API ---

@app.route("/api/charts/work_hours")
@require_auth
def api_work_hours():
    """Get work hours per project (based on running time from events)."""
    days = request.args.get("days", 30, type=int)
    data = models.get_work_hours_by_project(days)
    return jsonify(data)


# --- Health check (no auth, used by Railway) ---

@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


# --- Heartbeat API (from oscar_core.py on local machine) ---

@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    """Receive heartbeat from local oscar_core.py monitoring loop."""
    data = request.get_json()
    if not data or not data.get("project_id"):
        return jsonify({"error": "project_id required"}), 400

    project_id = data["project_id"]
    status = data.get("status", "UNKNOWN")
    pid = data.get("pid")
    current_task = data.get("current_task")
    last_event = data.get("last_event")
    timestamp = data.get("timestamp", datetime.now().isoformat())

    # Update project state from heartbeat
    models.update_project_state(project_id, status, pid)
    if last_event:
        models.log_event(project_id, "HEARTBEAT", f"{status} | {last_event}")

    return jsonify({"status": "received", "timestamp": timestamp})


# --- Artifacts API (local mode only) ---

@app.route("/api/artifacts/<project_id>")
@require_auth
def api_artifacts_list(project_id):
    """List files in project's output/ directory."""
    if CLOUD_MODE:
        return jsonify({"error": "Artifacts only available on local dashboard"}), 400

    config = load_config()
    oscar_config = config.get("oscar", {})
    base = oscar_config.get("base_path", "")
    project_config = None
    for p in config.get("projects", []):
        if p["id"] == project_id:
            project_config = p
            break
    if not project_config:
        return jsonify({"error": "Project not found"}), 404

    output_dir = os.path.join(base, project_config["path"], "output")
    if not os.path.isdir(output_dir):
        return jsonify([])

    files = []
    for fname in os.listdir(output_dir):
        fpath = os.path.join(output_dir, fname)
        if os.path.isfile(fpath):
            ext = os.path.splitext(fname)[1].lower()
            ftype = "html" if ext == ".html" else "json" if ext == ".json" else "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp") else "text" if ext in (".txt", ".md", ".csv") else "other"
            files.append({
                "name": fname,
                "type": ftype,
                "size": os.path.getsize(fpath),
                "modified": datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat(),
            })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return jsonify(files)


@app.route("/api/artifacts/<project_id>/view/<path:filename>")
@require_auth
def api_artifacts_view(project_id, filename):
    """View file content (HTML rendered, text as plain text, JSON parsed)."""
    if CLOUD_MODE:
        return jsonify({"error": "Artifacts only available on local dashboard"}), 400

    config = load_config()
    base = config.get("oscar", {}).get("base_path", "")
    project_config = None
    for p in config.get("projects", []):
        if p["id"] == project_id:
            project_config = p
            break
    if not project_config:
        return jsonify({"error": "Project not found"}), 404

    fpath = os.path.join(base, project_config["path"], "output", filename)
    if not os.path.isfile(fpath):
        return jsonify({"error": "File not found"}), 404

    # Prevent directory traversal
    real = os.path.realpath(fpath)
    allowed = os.path.realpath(os.path.join(base, project_config["path"], "output"))
    if not real.startswith(allowed):
        return jsonify({"error": "Forbidden"}), 403

    ext = os.path.splitext(filename)[1].lower()
    if ext == ".html":
        with open(fpath, "r", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
    elif ext == ".json":
        with open(fpath, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return send_file(fpath)
    else:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/api/artifacts/<project_id>/download/<path:filename>")
@require_auth
def api_artifacts_download(project_id, filename):
    """Download a file."""
    if CLOUD_MODE:
        return jsonify({"error": "Artifacts only available on local dashboard"}), 400

    config = load_config()
    base = config.get("oscar", {}).get("base_path", "")
    project_config = None
    for p in config.get("projects", []):
        if p["id"] == project_id:
            project_config = p
            break
    if not project_config:
        return jsonify({"error": "Project not found"}), 404

    fpath = os.path.join(base, project_config["path"], "output", filename)
    real = os.path.realpath(fpath)
    allowed = os.path.realpath(os.path.join(base, project_config["path"], "output"))
    if not real.startswith(allowed) or not os.path.isfile(fpath):
        return jsonify({"error": "Not found"}), 404

    return send_file(fpath, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    models.init_db()
    config = load_config()
    port = int(os.environ.get("PORT", config.get("oscar", {}).get("dashboard_port", 5001)))
    print(f"OSCAR2 Dashboard running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
