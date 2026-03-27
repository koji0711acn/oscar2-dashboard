"""Windows desktop notifications for OSCAR2 with SQLite history."""

import logging

logger = logging.getLogger("oscar2.notifier")

# Events that trigger desktop notifications
NOTIFY_EVENTS = {"ESCALATE", "ESCALATE_TO_HUMAN", "COMPLETED", "ABORT", "PAUSE", "ERROR"}


def notify(title, message, event_type=None, project_id=None):
    """Send a Windows desktop notification and record in DB.

    Args:
        title: Notification title
        message: Notification body
        event_type: Optional event type (ESCALATE, COMPLETED, ABORT, PAUSE, etc.)
        project_id: Optional project ID for tracking
    """
    # Record to database
    try:
        import models
        models.log_notification(title, message, event_type=event_type, project_id=project_id)
    except Exception as e:
        logger.warning(f"Failed to log notification to DB: {e}")

    # Only show desktop notification for important events
    if event_type and event_type not in NOTIFY_EVENTS:
        logger.info(f"Notification logged (no popup): {title}: {message}")
        return

    # Send desktop notification
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="OSCAR2",
            timeout=10,
        )
        logger.info(f"Desktop notification sent: {title}")
    except ImportError:
        logger.warning("plyer not installed, falling back to print")
        print(f"[NOTIFICATION] {title}: {message}")
    except Exception as e:
        logger.warning(f"Desktop notification failed: {e}")
        print(f"[NOTIFICATION] {title}: {message}")
