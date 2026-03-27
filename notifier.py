"""Windows desktop notifications for OSCAR2."""

import logging

logger = logging.getLogger("oscar2.notifier")


def notify(title, message):
    """Send a Windows desktop notification."""
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="OSCAR2",
            timeout=10,
        )
        logger.info(f"Notification sent: {title}")
    except ImportError:
        logger.warning("plyer not installed, falling back to print")
        print(f"[NOTIFICATION] {title}: {message}")
    except Exception as e:
        logger.warning(f"Notification failed: {e}")
        print(f"[NOTIFICATION] {title}: {message}")
