#!/usr/bin/env python3
"""
Notification State Manager
Tracks which movie events have been notified via Telegram to prevent duplicates.
Uses SQLite for persistent storage with 30-day retention.
"""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .schema import EventData, ShowtimeChange
from .alert_matcher import AlertMatch


# Database configuration
DEFAULT_RETENTION_DAYS = 30

# Database schema
CREATE_NOTIFICATIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS notifications (
        notification_id TEXT PRIMARY KEY,
        theater TEXT NOT NULL,
        date TEXT NOT NULL,
        movie_name TEXT NOT NULL,
        movie_slug TEXT NOT NULL,
        event_type TEXT NOT NULL,
        showtimes TEXT NOT NULL,
        runtime INTEGER,
        rating TEXT,
        first_notified_at TIMESTAMP NOT NULL,
        last_updated_at TIMESTAMP NOT NULL,
        notification_count INTEGER DEFAULT 1
    )
"""

CREATE_INDEXES = (
    """CREATE INDEX IF NOT EXISTS idx_date
       ON notifications(date)""",
    """CREATE INDEX IF NOT EXISTS idx_theater_date
       ON notifications(theater, date)""",
)


class NotificationState:
    """Manages notification state in SQLite database"""

    def __init__(self, db_path: str):
        """
        Initialize notification state manager

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.logger = logging.getLogger("NotificationState")
        self._init_database()

    def _init_database(self):
        """Initialize database schema if not exists"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Create notifications table
                cursor.execute(CREATE_NOTIFICATIONS_TABLE)

                # Create indexes for faster queries
                for index_sql in CREATE_INDEXES:
                    cursor.execute(index_sql)

                conn.commit()
                self.logger.info(f"Database initialized at {self.db_path}")

        except sqlite3.Error as e:
            self.logger.error(f"Database initialization error: {e}")
            raise

    def _generate_notification_id(self, event: EventData) -> str:
        """
        Generate unique notification ID for an event

        Args:
            event: Event data with theater, date, and slug

        Returns:
            Unique notification ID string
        """
        theater = event.theater.replace(" ", "_")
        date = event.date
        slug = event.slug
        return f"{theater}_{date}_{slug}"

    def should_notify(self, event: EventData) -> Tuple[bool, Optional[ShowtimeChange]]:
        """
        Check if an event should trigger a notification

        Args:
            event: Event data with all details

        Returns:
            Tuple of (should_notify: bool, changes: Optional[ShowtimeChange])
            - (True, None): New event, should notify
            - (True, ShowtimeChange): Existing event with changes, should notify
            - (False, None): Existing event unchanged, skip notification
        """

        notification_id = self._generate_notification_id(event)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if event exists
                cursor.execute(
                    """
                    SELECT showtimes FROM notifications
                    WHERE notification_id = ?
                """,
                    (notification_id,),
                )

                result = cursor.fetchone()

                if result is None:
                    # New event - should notify
                    self.logger.info(f"New event: {event.movie_name} on {event.date}")
                    return True, None

                # Event exists - check for showtime changes
                existing_showtimes = set(json.loads(result[0]))
                new_showtimes = set(event.showtimes)

                if existing_showtimes == new_showtimes:
                    # No changes - skip notification
                    self.logger.debug(
                        f"No changes for: {event.movie_name} on {event.date}"
                    )
                    return False, None

                # Showtimes changed - should notify with details
                added = sorted(list(new_showtimes - existing_showtimes))
                removed = sorted(list(existing_showtimes - new_showtimes))
                unchanged = sorted(list(existing_showtimes & new_showtimes))

                changes = ShowtimeChange(
                    added=added, removed=removed, unchanged=unchanged
                )

                self.logger.info(
                    f"Showtime changes for {event.movie_name}: "
                    f"+{len(added)} -{len(removed)} ={len(unchanged)}"
                )

                return True, changes

        except sqlite3.Error as e:
            self.logger.error(f"Database error checking notification: {e}")
            # On error, default to notifying (better to duplicate than miss)
            return True, None
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error: {e}")
            return True, None

    def mark_as_notified(self, event: EventData, is_update: bool = False):
        """
        Mark an event as notified in the database

        Args:
            event: Event data with all details
            is_update: Whether this is an update to existing event
        """

        notification_id = self._generate_notification_id(event)
        now = datetime.now().isoformat()

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                if is_update:
                    # Update existing record
                    cursor.execute(
                        """
                        UPDATE notifications
                        SET showtimes = ?,
                            last_updated_at = ?,
                            notification_count = notification_count + 1
                        WHERE notification_id = ?
                    """,
                        (json.dumps(event.showtimes), now, notification_id),
                    )
                    self.logger.info(f"Updated notification record: {notification_id}")

                else:
                    # Insert new record
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO notifications
                        (notification_id, theater, date, movie_name, movie_slug,
                         event_type, showtimes, runtime, rating,
                         first_notified_at, last_updated_at, notification_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                        (
                            notification_id,
                            event.theater,
                            event.date,
                            event.movie_name,
                            event.slug,
                            event.event_type,
                            json.dumps(event.showtimes),
                            event.runtime,
                            event.rating,
                            now,
                            now,
                        ),
                    )
                    self.logger.info(f"Created notification record: {notification_id}")

                conn.commit()

        except sqlite3.Error as e:
            self.logger.error(f"Database error marking as notified: {e}")
            raise

    def cleanup_old_entries(self, days: int = DEFAULT_RETENTION_DAYS):
        """
        Remove notification records older than specified days

        Args:
            days: Number of days to retain (default: DEFAULT_RETENTION_DAYS)

        Returns:
            Number of records deleted
        """
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Delete old records
                cursor.execute(
                    """
                    DELETE FROM notifications
                    WHERE date < ?
                """,
                    (cutoff_date,),
                )

                deleted_count = cursor.rowcount
                conn.commit()

                if deleted_count > 0:
                    self.logger.info(
                        f"Cleaned up {deleted_count} old notification records"
                    )

                return deleted_count

        except sqlite3.Error as e:
            self.logger.error(f"Database error during cleanup: {e}")
            return 0

    def get_statistics(self) -> Dict:
        """
        Get statistics about notification state

        Returns:
            Dictionary with statistics
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Total notifications
                cursor.execute("SELECT COUNT(*) FROM notifications")
                total = cursor.fetchone()[0]

                # By event type
                cursor.execute(
                    """
                    SELECT event_type, COUNT(*)
                    FROM notifications
                    GROUP BY event_type
                """
                )
                by_type = dict(cursor.fetchall())

                # Upcoming events (future dates)
                today = datetime.now().strftime("%Y-%m-%d")
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM notifications
                    WHERE date >= ?
                """,
                    (today,),
                )
                upcoming = cursor.fetchone()[0]

                return {
                    "total_records": total,
                    "by_event_type": by_type,
                    "upcoming_events": upcoming,
                }

        except sqlite3.Error as e:
            self.logger.error(f"Database error getting statistics: {e}")
            return {}

    def get_event_history(self, event: EventData) -> Optional[Dict]:
        """
        Get notification history for a specific event

        Args:
            event: Event data

        Returns:
            Dictionary with notification history or None if not found
        """
        notification_id = self._generate_notification_id(event)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute(
                    """
                    SELECT * FROM notifications
                    WHERE notification_id = ?
                """,
                    (notification_id,),
                )

                result = cursor.fetchone()

                if result:
                    return dict(result)
                return None

        except sqlite3.Error as e:
            self.logger.error(f"Database error getting event history: {e}")
            return None


CREATE_USER_NOTIFICATIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS user_notifications (
        chat_id           INTEGER NOT NULL,
        alert_id          INTEGER NOT NULL,
        theater_slug      TEXT    NOT NULL,
        date              TEXT    NOT NULL,
        movie_slug        TEXT    NOT NULL,
        movie_name        TEXT    NOT NULL,
        matched_showtimes TEXT    NOT NULL,
        format_filter     TEXT,
        first_notified_at TIMESTAMP NOT NULL,
        last_updated_at   TIMESTAMP NOT NULL,
        notification_count INTEGER DEFAULT 1,
        PRIMARY KEY (chat_id, alert_id, theater_slug, date, movie_slug)
    )
"""

CREATE_USER_NOTIFICATIONS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_un_date ON user_notifications(date)",
    "CREATE INDEX IF NOT EXISTS idx_un_alert ON user_notifications(alert_id)",
)


class UserNotificationState:
    """
    Per-user notification dedup for custom alerts.

    Mirrors NotificationState's change-detection logic, but keyed per
    (chat_id, alert_id, theater, date, movie) so the same movie can notify
    multiple users — and multiple alerts of one user — independently. Comparison
    is on the alert's matched (format-filtered) showtimes, so a change to times
    in the chosen format is detected while unrelated times are ignored.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.logger = logging.getLogger("UserNotificationState")
        self._init_database()

    def _init_database(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(CREATE_USER_NOTIFICATIONS_TABLE)
                for index_sql in CREATE_USER_NOTIFICATIONS_INDEXES:
                    cursor.execute(index_sql)
                conn.commit()
            self.logger.debug(f"user_notifications table ready at {self.db_path}")
        except sqlite3.Error as e:
            self.logger.error(f"Database initialization error: {e}")
            raise

    @staticmethod
    def _key(match: AlertMatch) -> Tuple[int, int, str, str, str]:
        return (
            match.chat_id,
            match.alert_id,
            match.theater_slug,
            match.date,
            match.slug,
        )

    def should_notify(
        self, match: AlertMatch
    ) -> Tuple[bool, Optional[ShowtimeChange]]:
        """
        Decide whether a match warrants a notification.

        Returns:
            (True, None)            -> new match, notify
            (True, ShowtimeChange)  -> matched showtimes changed, notify
            (False, None)           -> already notified, unchanged, skip
        """
        chat_id, alert_id, theater_slug, date, slug = self._key(match)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT matched_showtimes FROM user_notifications
                    WHERE chat_id = ? AND alert_id = ? AND theater_slug = ?
                      AND date = ? AND movie_slug = ?
                    """,
                    (chat_id, alert_id, theater_slug, date, slug),
                )
                result = cursor.fetchone()

                if result is None:
                    return True, None

                existing = set(json.loads(result[0]))
                new = set(match.matched_showtimes)
                if existing == new:
                    return False, None

                changes = ShowtimeChange(
                    added=sorted(new - existing),
                    removed=sorted(existing - new),
                    unchanged=sorted(existing & new),
                )
                return True, changes

        except sqlite3.Error as e:
            self.logger.error(f"Database error checking user notification: {e}")
            # Prefer a duplicate over a miss.
            return True, None
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error: {e}")
            return True, None

    def mark_as_notified(self, match: AlertMatch, is_update: bool = False):
        """Insert or update the dedup record for a notified match"""
        chat_id, alert_id, theater_slug, date, slug = self._key(match)
        now = datetime.now().isoformat()
        showtimes_json = json.dumps(match.matched_showtimes)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if is_update:
                    cursor.execute(
                        """
                        UPDATE user_notifications
                        SET matched_showtimes = ?,
                            movie_name = ?,
                            format_filter = ?,
                            last_updated_at = ?,
                            notification_count = notification_count + 1
                        WHERE chat_id = ? AND alert_id = ? AND theater_slug = ?
                          AND date = ? AND movie_slug = ?
                        """,
                        (
                            showtimes_json,
                            match.movie_name,
                            match.format_filter,
                            now,
                            chat_id,
                            alert_id,
                            theater_slug,
                            date,
                            slug,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO user_notifications
                            (chat_id, alert_id, theater_slug, date, movie_slug,
                             movie_name, matched_showtimes, format_filter,
                             first_notified_at, last_updated_at, notification_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            chat_id,
                            alert_id,
                            theater_slug,
                            date,
                            slug,
                            match.movie_name,
                            showtimes_json,
                            match.format_filter,
                            now,
                            now,
                        ),
                    )
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error(f"Database error marking user notification: {e}")
            raise

    def delete_by_alert(self, alert_id: int) -> int:
        """
        Remove all dedup rows for an alert (called when it is edited or deleted)
        so a recreated/changed alert re-arms its notifications.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM user_notifications WHERE alert_id = ?",
                    (alert_id,),
                )
                conn.commit()
                return cursor.rowcount
        except sqlite3.Error as e:
            self.logger.error(f"Database error deleting alert state {alert_id}: {e}")
            return 0

    def cleanup_old_entries(self, days: int = DEFAULT_RETENTION_DAYS) -> int:
        """Remove dedup rows for past dates older than the retention window"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM user_notifications WHERE date < ?", (cutoff,)
                )
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    self.logger.info(
                        f"Cleaned up {deleted} old user_notification records"
                    )
                return deleted
        except sqlite3.Error as e:
            self.logger.error(f"Database error during cleanup: {e}")
            return 0
