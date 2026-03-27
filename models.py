"""SQLite models for OSCAR2: project state, event logs, cost records."""

import sqlite3
import os
import json
from datetime import datetime, date

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


def get_project_state(project_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM project_state WHERE project_id=?", (project_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# Initialize on import
init_db()
