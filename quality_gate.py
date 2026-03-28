"""Quality Gate: Mechanical Judge + Strategic Judge for project health assessment."""

import os
import json
import glob
import logging
from datetime import datetime, timedelta
from process_monitor import find_claude_process
import models

logger = logging.getLogger("oscar2.quality_gate")


# ============================================================
# Mechanical Judge: process survival and file checks
# ============================================================

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


# ============================================================
# Strategic Judge: debug packet analysis and quality assessment
# ============================================================

def strategic_judge(project_config, oscar_config):
    """Analyze project output quality via debug packets in output/ directory.

    Returns dict with:
        verdict: 'publishable' | 'needs_revision' | 'no_data'
        action: 'CONTINUE' | 'ESCALATE_TO_HUMAN' | 'PAUSE' | None
        detail: str
    """
    project_id = project_config["id"]
    project_path = os.path.join(
        oscar_config["base_path"], project_config["path"]
    )

    result = {
        "project_id": project_id,
        "verdict": "no_data",
        "action": None,
        "detail": "",
        "timestamp": datetime.now().isoformat(),
    }

    # --- Check 1: Cost limit ---
    today = datetime.now().strftime("%Y-%m-%d")
    daily_cost = models.get_daily_cost(project_id, today)
    max_cost = project_config.get("max_cost_per_day_usd", 10)

    if daily_cost >= max_cost:
        result["verdict"] = "cost_exceeded"
        result["action"] = "PAUSE"
        result["detail"] = f"Daily cost ${daily_cost:.2f} >= limit ${max_cost:.2f}"
        models.record_quality(project_id, "cost_exceeded", result["detail"])
        logger.warning(f"[{project_id}] Cost limit exceeded: {result['detail']}")
        return result

    # --- Check 2: Analyze debug packets in output/ ---
    output_dir = os.path.join(project_path, "output")
    if os.path.isdir(output_dir):
        verdict = _analyze_debug_packets(output_dir)
        result["verdict"] = verdict
        result["detail"] = f"Debug packet analysis: {verdict}"
    else:
        result["verdict"] = "no_data"
        result["detail"] = "No output/ directory found"

    # Record quality verdict
    if result["verdict"] in ("publishable", "needs_revision"):
        models.record_quality(project_id, result["verdict"], result["detail"])

    # --- Check 3: Consecutive needs_revision → ESCALATE ---
    consecutive = models.get_consecutive_needs_revision(project_id)
    if consecutive >= 3:
        result["action"] = "ESCALATE_TO_HUMAN"
        result["detail"] += f" | {consecutive} consecutive needs_revision"
        logger.warning(f"[{project_id}] Escalating: {consecutive} consecutive needs_revision")

    return result


def _analyze_debug_packets(output_dir):
    """Analyze the most recent debug packet files in output/ directory.

    Looks for JSON files with quality/verdict fields, or text files with
    PASS/FAIL indicators.

    Returns: 'publishable' | 'needs_revision' | 'no_data'
    """
    # Find most recent files
    patterns = [
        os.path.join(output_dir, "*.json"),
        os.path.join(output_dir, "**", "*.json"),
        os.path.join(output_dir, "*.txt"),
    ]

    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern, recursive=True))

    if not files:
        return "no_data"

    # Sort by modification time, most recent first
    files.sort(key=lambda f: os.path.getmtime(f), reverse=True)

    # Analyze the most recent file
    latest = files[0]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            content = f.read()

        # Try JSON with verdict/quality fields
        if latest.endswith(".json"):
            try:
                data = json.loads(content)
                # Check common verdict field names
                for key in ("verdict", "quality", "status", "result"):
                    val = data.get(key, "")
                    if isinstance(val, str):
                        val_lower = val.lower()
                        if "publish" in val_lower or "pass" in val_lower or "good" in val_lower:
                            return "publishable"
                        if "revision" in val_lower or "fail" in val_lower or "bad" in val_lower:
                            return "needs_revision"
            except json.JSONDecodeError:
                pass

        # Fallback: text content analysis
        upper = content.upper()
        if "PUBLISHABLE" in upper or "PASS" in upper or "SUCCESS" in upper:
            return "publishable"
        if "NEEDS_REVISION" in upper or "FAIL" in upper or "ERROR" in upper:
            return "needs_revision"

    except OSError:
        pass

    return "no_data"


# ============================================================
# QA Checker integration: auto-check HTML articles in output/
# ============================================================

def run_qa_check_on_latest(project_config, oscar_config):
    """Run qa_checker on the most recent HTML file in project output/.

    Returns dict with qa_result or None if no files found.
    """
    project_id = project_config["id"]
    project_path = os.path.join(oscar_config["base_path"], project_config["path"])
    output_dir = os.path.join(project_path, "output")

    if not os.path.isdir(output_dir):
        return None

    # Find most recent HTML file
    html_files = []
    for fname in os.listdir(output_dir):
        if fname.endswith('.html'):
            fpath = os.path.join(output_dir, fname)
            html_files.append((os.path.getmtime(fpath), fpath, fname))

    if not html_files:
        return None

    html_files.sort(reverse=True)
    latest_time, latest_path, latest_name = html_files[0]

    # Extract keywords from filename
    keywords = []
    parts = latest_name.replace('.html', '').split('_')
    for part in parts:
        if part in ('NEW',) or part.isdigit():
            continue
        if len(part) >= 2:
            keywords.append(part)

    try:
        import qa_checker
        with open(latest_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        result = qa_checker.check_article(html_content, keywords)
        result['filename'] = latest_name
        result['filepath'] = latest_path
        result['keywords'] = keywords

        verdict = 'publishable' if result['passed'] else 'needs_revision'
        detail = f"QA: {result['summary']['critical']} critical, {result['summary']['warning']} warning"
        models.record_quality(project_id, verdict, f"{latest_name}: {detail}")
        logger.info(f"[{project_id}] QA check on {latest_name}: {verdict} ({detail})")

        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qa_reports')
        os.makedirs(report_dir, exist_ok=True)
        report = qa_checker.generate_report_md(result, latest_path, keywords)
        report_path = os.path.join(report_dir, f"{latest_name.replace('.html', '')}_qa_report.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)

        return result
    except Exception as e:
        logger.error(f"[{project_id}] QA check failed: {e}")
        return None
