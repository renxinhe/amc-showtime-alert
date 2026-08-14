#!/usr/bin/env python3
"""
Alert Manager
Manages per-user custom showtime alerts in the SQLite database.

Each alert belongs to a Telegram user (chat_id) and describes what that user
wants to be notified about:
  - theater_slug : a specific AMC theater slug, or NULL to match all theaters
  - pattern      : a keyword (substring) or regex matched against movie titles
  - is_regex     : whether `pattern` is a regular expression
  - format_filter: a normalized format token (e.g. "imax"), or NULL for any
"""

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Sentinel for edit_alert so callers can distinguish "leave unchanged" from
# "set to NULL" (theater_slug / format_filter legitimately accept None).
_UNSET = object()

CREATE_ALERTS_TABLE = """
    CREATE TABLE IF NOT EXISTS alerts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id       INTEGER NOT NULL,
        theater_slug  TEXT,
        pattern       TEXT    NOT NULL,
        is_regex      INTEGER NOT NULL DEFAULT 0,
        format_filter TEXT,
        created_at    TIMESTAMP NOT NULL,
        deleted_at    TIMESTAMP             -- soft delete; NULL = active
    )
"""

CREATE_ALERTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_alerts_chat ON alerts(chat_id)"
)


@dataclass
class Alert:
    """A single user-defined alert (one row of the alerts table)"""

    id: int
    chat_id: int
    theater_slug: Optional[str]  # specific slug, or None = all theaters
    pattern: str
    is_regex: bool
    format_filter: Optional[str]  # normalized token, or None = any format


class AlertManager:
    """Manages per-user custom alerts in the shared SQLite database"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.logger = logging.getLogger("AlertManager")
        self._init_database()

    def _init_database(self):
        """Create the alerts table if it doesn't exist, and migrate older DBs."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(CREATE_ALERTS_TABLE)
                conn.execute(CREATE_ALERTS_INDEX)
                conn.commit()
            self.logger.debug(f"Alerts table ready at {self.db_path}")
        except sqlite3.Error as e:
            self.logger.error(f"Database initialization error: {e}")
            raise

    @staticmethod
    def _row_to_alert(row) -> Alert:
        return Alert(
            id=row[0],
            chat_id=row[1],
            theater_slug=row[2],
            pattern=row[3],
            is_regex=bool(row[4]),
            format_filter=row[5],
        )

    def add_alert(
        self,
        chat_id: int,
        pattern: str,
        is_regex: bool = False,
        theater_slug: Optional[str] = None,
        format_filter: Optional[str] = None,
    ) -> Optional[int]:
        """Create a new alert and return its id (or None on error)"""
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO alerts
                        (chat_id, theater_slug, pattern, is_regex,
                         format_filter, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        theater_slug,
                        pattern,
                        1 if is_regex else 0,
                        format_filter,
                        now,
                    ),
                )
                conn.commit()
                alert_id = cursor.lastrowid
                self.logger.info(f"Added alert #{alert_id} for chat_id={chat_id}")
                return alert_id
        except sqlite3.Error as e:
            self.logger.error(f"Database error adding alert for {chat_id}: {e}")
            return None

    def list_alerts(self, chat_id: int) -> List[Alert]:
        """Return all alerts owned by a user, ordered by created_at"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, chat_id, theater_slug, pattern, is_regex, format_filter
                    FROM alerts WHERE chat_id = ? AND deleted_at IS NULL
                    ORDER BY created_at
                    """,
                    (chat_id,),
                )
                return [self._row_to_alert(r) for r in cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error(f"Database error listing alerts for {chat_id}: {e}")
            return []

    def get_alert(self, chat_id: int, alert_id: int) -> Optional[Alert]:
        """Return one alert if it exists AND is owned by chat_id, else None"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, chat_id, theater_slug, pattern, is_regex, format_filter
                    FROM alerts WHERE id = ? AND chat_id = ? AND deleted_at IS NULL
                    """,
                    (alert_id, chat_id),
                )
                row = cursor.fetchone()
                return self._row_to_alert(row) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting alert {alert_id}: {e}")
            return None

    def edit_alert(
        self,
        chat_id: int,
        alert_id: int,
        pattern=_UNSET,
        is_regex=_UNSET,
        theater_slug=_UNSET,
        format_filter=_UNSET,
    ) -> bool:
        """
        Update the given fields of an alert owned by chat_id.

        Only fields that are passed (i.e. not the _UNSET sentinel) are changed,
        so None can be used to clear theater_slug / format_filter.

        Returns True if a row was updated, False otherwise.
        """
        sets = []
        params: list = []
        if pattern is not _UNSET:
            sets.append("pattern = ?")
            params.append(pattern)
        if is_regex is not _UNSET:
            sets.append("is_regex = ?")
            params.append(1 if is_regex else 0)
        if theater_slug is not _UNSET:
            sets.append("theater_slug = ?")
            params.append(theater_slug)
        if format_filter is not _UNSET:
            sets.append("format_filter = ?")
            params.append(format_filter)

        if not sets:
            return False

        params.extend([alert_id, chat_id])
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE alerts SET {', '.join(sets)} "
                    "WHERE id = ? AND chat_id = ? AND deleted_at IS NULL",
                    params,
                )
                conn.commit()
                updated = cursor.rowcount > 0
                if updated:
                    self.logger.info(f"Edited alert #{alert_id} for {chat_id}")
                return updated
        except sqlite3.Error as e:
            self.logger.error(f"Database error editing alert {alert_id}: {e}")
            return False

    def delete_alert(self, chat_id: int, alert_id: int) -> bool:
        """
        Soft-delete an active alert owned by chat_id (sets deleted_at), matching
        seat_alerts. Returns True if an active row was deleted.
        """
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE alerts SET deleted_at = ? "
                    "WHERE id = ? AND chat_id = ? AND deleted_at IS NULL",
                    (now, alert_id, chat_id),
                )
                conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    self.logger.info(f"Soft-deleted alert #{alert_id} for {chat_id}")
                return deleted
        except sqlite3.Error as e:
            self.logger.error(f"Database error deleting alert {alert_id}: {e}")
            return False

    def get_active_alerts(self) -> List[Alert]:
        """
        Return every alert that has not been soft-deleted.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, chat_id, theater_slug, pattern,
                           is_regex, format_filter
                    FROM alerts
                    WHERE deleted_at IS NULL
                    ORDER BY chat_id, id
                    """
                )
                return [self._row_to_alert(r) for r in cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting active alerts: {e}")
            return []
