"""SQLite models for OSCAR2: project state, event logs, cost records, notifications, quality."""

import sqlite3
import os
from datetime import datetime, date, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oscar2.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS project_state (
            project_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'DEAD',
            pid INTEGER,
            last_check TEXT,
            last_restart TEXT,
            restart_count INTEGER DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS cost_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            date TEXT NOT NULL,
            cost_usd REAL DEFAULT 0,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT,
            event_type TEXT,
            project_id TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS quality_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
    """)
    conn.commit()
    conn.close()


def update_project_state(project_id, status, pid=None):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO project_state (project_id, status, pid, last_check, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            status=excluded.status,
            pid=COALESCE(excluded.pid, pid),
            last_check=excluded.last_check,
            updated_at=excluded.updated_at
    """, (project_id, status, pid, now, now))
    conn.commit()
    conn.close()


def record_restart(project_id):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE project_state SET last_restart=?, restart_count=restart_count+1, updated_at=?
        WHERE project_id=?
    """, (now, now, project_id))
    conn.commit()
    conn.close()


def log_event(project_id, event_type, message=""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO event_log (project_id, event_type, message) VALUES (?, ?, ?)",
        (project_id, event_type, message),
    )
    conn.commit()
    conn.close()


def get_all_project_states():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM project_state ORDER BY project_id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_events(limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM event_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_filtered_events(limit=100, project_id=None, event_type=None):
    """Get events with optional project and event_type filters."""
    conn = get_connection()
    query = "SELECT * FROM event_log WHERE 1=1"
    params = []
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_project_state(project_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM project_state WHERE project_id=?", (project_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Cost records ---

def get_daily_cost(project_id, date_str):
    """Get total cost for a project on a given date."""
    conn = get_connection()
    row = conn.execute(
        "SELECT SUM(cost_usd) as total FROM cost_record WHERE project_id=? AND date=?",
        (project_id, date_str)
    ).fetchone()
    conn.close()
    return row["total"] if row and row["total"] else 0.0


def record_cost(project_id, cost_usd, date_str=None):
    """Record cost for a project."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO cost_record (project_id, date, cost_usd, updated_at) VALUES (?, ?, ?, ?)",
        (project_id, date_str, cost_usd, now)
    )
    conn.commit()
    conn.close()


def get_total_daily_cost(date_str=None):
    """Get total cost across all projects for a date."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    row = conn.execute(
        "SELECT SUM(cost_usd) as total FROM cost_record WHERE date=?", (date_str,)
    ).fetchone()
    conn.close()
    return row["total"] if row and row["total"] else 0.0


def get_daily_cost_history(days=30):
    """Get daily cost aggregated by date for the last N days."""
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT date, SUM(cost_usd) as total_cost
        FROM cost_record
        WHERE date >= ?
        GROUP BY date
        ORDER BY date
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Notification log ---

def log_notification(title, message, event_type=None, project_id=None):
    """Record a notification in the database."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO notification_log (title, message, event_type, project_id) VALUES (?, ?, ?, ?)",
        (title, message, event_type, project_id)
    )
    conn.commit()
    conn.close()


def get_recent_notifications(limit=50):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM notification_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Quality records ---

def record_quality(project_id, verdict, detail=""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO quality_record (project_id, verdict, detail) VALUES (?, ?, ?)",
        (project_id, verdict, detail)
    )
    conn.commit()
    conn.close()


def get_recent_quality(project_id, limit=10):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM quality_record WHERE project_id=? ORDER BY id DESC LIMIT ?",
        (project_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_consecutive_needs_revision(project_id):
    """Count consecutive 'needs_revision' verdicts (most recent first)."""
    records = get_recent_quality(project_id, limit=10)
    count = 0
    for r in records:
        if r["verdict"] == "needs_revision":
            count += 1
        else:
            break
    return count


def get_publishable_rate_history(days=30):
    """Get publishable rate trend grouped by date."""
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT DATE(created_at) as date,
               COUNT(CASE WHEN verdict='publishable' THEN 1 END) as publishable,
               COUNT(*) as total
        FROM quality_record
        WHERE DATE(created_at) >= ?
        GROUP BY DATE(created_at)
        ORDER BY date
    """, (cutoff,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        rate = (r["publishable"] / r["total"] * 100) if r["total"] > 0 else 0
        result.append({"date": r["date"], "rate": round(rate, 1), "total": r["total"]})
    return result


def get_event_type_breakdown():
    """Get event type counts for pie chart."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT event_type, COUNT(*) as count
        FROM event_log
        GROUP BY event_type
        ORDER BY count DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_work_hours_by_project(days=30):
    """Estimate work hours per project per day based on STARTED/RUNNING events."""
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT DATE(created_at) as date, project_id,
               COUNT(*) as check_count
        FROM event_log
        WHERE DATE(created_at) >= ?
          AND event_type IN ('CHECK_OK', 'CONTINUE', 'RUNNING', 'STARTED', 'BATCH_STARTED')
        GROUP BY DATE(created_at), project_id
        ORDER BY date
    """, (cutoff,)).fetchall()
    conn.close()
    # Each check ~= 1 minute of run time (check_interval_seconds=60)
    result = []
    for r in rows:
        hours = round(r["check_count"] / 60.0, 2)
        result.append({"date": r["date"], "project_id": r["project_id"], "hours": hours})
    return result


def get_filtered_notifications(limit=50, event_type=None):
    """Get notifications filtered by event_type."""
    conn = get_connection()
    if event_type:
        rows = conn.execute(
            "SELECT * FROM notification_log WHERE event_type = ? ORDER BY id DESC LIMIT ?",
            (event_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM notification_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Initialize on import
init_db()
