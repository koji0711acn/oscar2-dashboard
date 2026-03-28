"""Notification system for OSCAR2 with base class design for future extensions.

Current: Windows desktop (plyer) + SQLite history
Future:  LINE, Slack, Email via subclasses of NotifierBase
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("oscar2.notifier")

# Events that trigger desktop notifications
NOTIFY_EVENTS = {"ESCALATE", "ESCALATE_TO_HUMAN", "COMPLETED", "ABORT", "PAUSE", "ERROR"}


class NotifierBase(ABC):
    """Base class for notification backends.

    Subclass this to add LINE, Slack, Email, etc.
    Each subclass implements send() for its channel.
    """

    @abstractmethod
    def send(self, title, message, event_type=None, project_id=None):
        """Send a notification via this backend."""
        pass

    def should_notify(self, event_type):
        """Check if this event type should trigger a notification."""
        if event_type is None:
            return True
        return event_type in NOTIFY_EVENTS


class DesktopNotifier(NotifierBase):
    """Windows desktop notification via plyer."""

    def send(self, title, message, event_type=None, project_id=None):
        if not self.should_notify(event_type):
            return False
        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message,
                app_name="OSCAR2",
                timeout=10,
            )
            logger.info(f"Desktop notification sent: {title}")
            return True
        except ImportError:
            logger.warning("plyer not installed, falling back to print")
            print(f"[NOTIFICATION] {title}: {message}")
            return False
        except Exception as e:
            logger.warning(f"Desktop notification failed: {e}")
            print(f"[NOTIFICATION] {title}: {message}")
            return False


class SlackNotifier(NotifierBase):
    """Slack notification (placeholder for future implementation)."""

    def __init__(self, webhook_url=None):
        self.webhook_url = webhook_url

    def send(self, title, message, event_type=None, project_id=None):
        if not self.webhook_url:
            logger.debug("Slack webhook not configured, skipping")
            return False
        # Future: POST to webhook_url
        logger.info(f"Slack notification would be sent: {title}")
        return False


class LineNotifier(NotifierBase):
    """LINE Notify (placeholder for future implementation)."""

    def __init__(self, token=None):
        self.token = token

    def send(self, title, message, event_type=None, project_id=None):
        if not self.token:
            logger.debug("LINE token not configured, skipping")
            return False
        # Future: POST to LINE Notify API
        logger.info(f"LINE notification would be sent: {title}")
        return False


# Active notifier backends
_backends = [DesktopNotifier()]


def add_backend(backend):
    """Add a notification backend."""
    _backends.append(backend)


def notify(title, message, event_type=None, project_id=None):
    """Send notification via all backends and record in DB.

    Args:
        title: Notification title
        message: Notification body
        event_type: Optional event type (ESCALATE, COMPLETED, ABORT, PAUSE, etc.)
        project_id: Optional project ID for tracking
    """
    # Record to database always
    try:
        import models
        models.log_notification(title, message, event_type=event_type, project_id=project_id)
    except Exception as e:
        logger.warning(f"Failed to log notification to DB: {e}")

    # Skip desktop notification for non-important events
    if event_type and event_type not in NOTIFY_EVENTS:
        logger.info(f"Notification logged (no popup): {title}: {message}")
        return

    # Send via all backends
    for backend in _backends:
        try:
            backend.send(title, message, event_type=event_type, project_id=project_id)
        except Exception as e:
            logger.warning(f"Backend {type(backend).__name__} failed: {e}")
