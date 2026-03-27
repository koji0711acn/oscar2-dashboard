"""Task Backlog: SQLite-based task queue for Agent Teams batch management."""

import logging
from datetime import datetime
from models import get_connection

logger = logging.getLogger("oscar2.task_backlog")


def init_task_queue():
    """Create the task_queue table if it doesn't exist."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            batch_name TEXT NOT NULL,
            description TEXT,
            tasks_text TEXT NOT NULL,
            priority INTEGER DEFAULT 3,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            started_at TEXT,
            completed_at TEXT
        )
    """)
    conn.commit()
    conn.close()


# Initialize on import
init_task_queue()


# --- CRUD Operations ---

def add_batch(project_id, batch_name, tasks_text, description="", priority=3):
    """Add a new task batch to the queue. Returns the new row id."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO task_queue (project_id, batch_name, description, tasks_text, priority, status)
           VALUES (?, ?, ?, ?, ?, 'pending')""",
        (project_id, batch_name, description, tasks_text, priority),
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    logger.info(f"Added batch '{batch_name}' for {project_id} (id={row_id}, priority={priority})")
    return row_id


def get_next_pending(project_id):
    """Get the next pending batch for a project (lowest priority number = highest priority, then oldest).
    Returns dict or None."""
    conn = get_connection()
    row = conn.execute(
        """SELECT * FROM task_queue
           WHERE project_id = ? AND status = 'pending'
           ORDER BY priority ASC, id ASC
           LIMIT 1""",
        (project_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_running(batch_id):
    """Mark a batch as running."""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE task_queue SET status = 'running', started_at = ? WHERE id = ?",
        (now, batch_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"Batch {batch_id} marked as running")


def mark_completed(batch_id):
    """Mark a batch as completed."""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE task_queue SET status = 'completed', completed_at = ? WHERE id = ?",
        (now, batch_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"Batch {batch_id} marked as completed")


def mark_failed(batch_id):
    """Mark a batch as failed."""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE task_queue SET status = 'failed', completed_at = ? WHERE id = ?",
        (now, batch_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"Batch {batch_id} marked as failed")


def cancel_batch(batch_id):
    """Cancel a running or pending batch. Returns True if cancelled."""
    conn = get_connection()
    row = conn.execute("SELECT status FROM task_queue WHERE id = ?", (batch_id,)).fetchone()
    if not row:
        conn.close()
        return False
    if row["status"] not in ("pending", "running"):
        conn.close()
        return False
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE task_queue SET status = 'failed', completed_at = ? WHERE id = ?",
        (now, batch_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"Batch {batch_id} cancelled")
    return True


def update_priority(batch_id, new_priority):
    """Update the priority of a batch. Returns True if updated."""
    conn = get_connection()
    row = conn.execute("SELECT id FROM task_queue WHERE id = ?", (batch_id,)).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute(
        "UPDATE task_queue SET priority = ? WHERE id = ?",
        (new_priority, batch_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"Batch {batch_id} priority updated to {new_priority}")
    return True


def delete_batch(batch_id):
    """Delete a batch from the queue. Returns True if deleted."""
    conn = get_connection()
    cursor = conn.execute("DELETE FROM task_queue WHERE id = ?", (batch_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    if deleted:
        logger.info(f"Batch {batch_id} deleted")
    return deleted


def list_all(project_id=None):
    """List all batches, optionally filtered by project_id."""
    conn = get_connection()
    if project_id:
        rows = conn.execute(
            "SELECT * FROM task_queue WHERE project_id = ? ORDER BY priority ASC, id ASC",
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM task_queue ORDER BY project_id, priority ASC, id ASC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_batch(batch_id):
    """Get a single batch by id. Returns dict or None."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM task_queue WHERE id = ?", (batch_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_running_batch(project_id):
    """Get the currently running batch for a project. Returns dict or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM task_queue WHERE project_id = ? AND status = 'running' LIMIT 1",
        (project_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def count_by_status(project_id):
    """Count batches by status for a project. Returns dict like {'pending': 2, 'running': 1, ...}."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM task_queue WHERE project_id = ? GROUP BY status",
        (project_id,),
    ).fetchall()
    conn.close()
    return {r["status"]: r["cnt"] for r in rows}
