#!/usr/bin/env python3
"""
Seat Alert Manager
Soft-deletable storage for per-user seat alerts.

A seat alert watches one specific showing (identified by its AMC showtime id)
and remembers the set of good+available seats last seen, so the poller can tell
when *new* good seats open up.

Alerts are never hard-deleted: deleting sets `deleted_at`. The service also
auto-soft-deletes alerts the day after their showtime (see expire_past).
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

CREATE_SEAT_ALERTS_TABLE = """
    CREATE TABLE IF NOT EXISTS seat_alerts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id         INTEGER NOT NULL,
        showtime_id     TEXT    NOT NULL,
        theater_slug    TEXT,
        movie_name      TEXT,
        showtime_date   TEXT    NOT NULL,   -- YYYY-MM-DD
        showtime_label  TEXT,               -- e.g. "Fri Jul 18 · 7:00 PM · IMAX"
        last_good_seats TEXT,               -- JSON array of seat names
        created_at      TIMESTAMP NOT NULL,
        deleted_at      TIMESTAMP           -- soft delete; NULL = active
    )
"""

CREATE_SEAT_ALERTS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_seat_alerts_chat ON seat_alerts(chat_id)"
)


@dataclass
class SeatAlert:
    id: int
    chat_id: int
    showtime_id: str
    theater_slug: Optional[str]
    movie_name: Optional[str]
    showtime_date: str
    showtime_label: Optional[str]
    last_good_seats: List[str]


class SeatAlertManager:
    """Manages soft-deletable seat alerts in the shared SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.logger = logging.getLogger("SeatAlertManager")
        self._init_database()

    def _init_database(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(CREATE_SEAT_ALERTS_TABLE)
                conn.execute(CREATE_SEAT_ALERTS_INDEX)
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error(f"Database initialization error: {e}")
            raise

    @staticmethod
    def _row_to_alert(row) -> SeatAlert:
        return SeatAlert(
            id=row[0],
            chat_id=row[1],
            showtime_id=row[2],
            theater_slug=row[3],
            movie_name=row[4],
            showtime_date=row[5],
            showtime_label=row[6],
            last_good_seats=json.loads(row[7]) if row[7] else [],
        )

    _COLS = (
        "id, chat_id, showtime_id, theater_slug, movie_name, "
        "showtime_date, showtime_label, last_good_seats"
    )

    def create(
        self,
        chat_id: int,
        showtime_id: str,
        showtime_date: str,
        theater_slug: Optional[str] = None,
        movie_name: Optional[str] = None,
        showtime_label: Optional[str] = None,
    ) -> Optional[int]:
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO seat_alerts
                        (chat_id, showtime_id, theater_slug, movie_name,
                         showtime_date, showtime_label, last_good_seats, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, '[]', ?)
                    """,
                    (chat_id, str(showtime_id), theater_slug, movie_name,
                     showtime_date, showtime_label, now),
                )
                conn.commit()
                return cur.lastrowid
        except sqlite3.Error as e:
            self.logger.error(f"Error creating seat alert for {chat_id}: {e}")
            return None

    def soft_delete(self, chat_id: int, alert_id: int) -> bool:
        """Mark an active alert deleted (owned by chat_id). Returns True if it was."""
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE seat_alerts SET deleted_at = ?
                    WHERE id = ? AND chat_id = ? AND deleted_at IS NULL
                    """,
                    (now, alert_id, chat_id),
                )
                conn.commit()
                return cur.rowcount > 0
        except sqlite3.Error as e:
            self.logger.error(f"Error soft-deleting seat alert {alert_id}: {e}")
            return False

    def list_active(self, chat_id: int) -> List[SeatAlert]:
        """Active (not soft-deleted) alerts for a user — used by the delete picker."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT {self._COLS} FROM seat_alerts "
                    "WHERE chat_id = ? AND deleted_at IS NULL ORDER BY showtime_date, id",
                    (chat_id,),
                )
                return [self._row_to_alert(r) for r in cur.fetchall()]
        except sqlite3.Error as e:
            self.logger.error(f"Error listing seat alerts for {chat_id}: {e}")
            return []

    def get_pollable(self) -> List[SeatAlert]:
        """
        Active, non-expired alerts whose owner is a subscribed user — the set the
        poller should check this cycle.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    SELECT {', '.join('sa.'+c for c in self._COLS.split(', '))}
                    FROM seat_alerts sa
                    JOIN users u ON u.chat_id = sa.chat_id
                    WHERE sa.deleted_at IS NULL
                      AND sa.showtime_date >= ?
                      AND u.is_active = 1
                    ORDER BY sa.id
                    """,
                    (today,),
                )
                return [self._row_to_alert(r) for r in cur.fetchall()]
        except sqlite3.Error as e:
            self.logger.error(f"Error getting pollable seat alerts: {e}")
            return []

    def update_last_good_seats(self, alert_id: int, seat_names: List[str]):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE seat_alerts SET last_good_seats = ? WHERE id = ?",
                    (json.dumps(seat_names), alert_id),
                )
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error(f"Error updating last_good_seats for {alert_id}: {e}")

    def expire_past(self, today: Optional[str] = None) -> int:
        """
        Soft-delete alerts whose showtime is in the past — i.e. the day after the
        show (showtime_date < today). Returns the number expired.
        """
        today = today or datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE seat_alerts SET deleted_at = ?
                    WHERE deleted_at IS NULL AND showtime_date < ?
                    """,
                    (now, today),
                )
                conn.commit()
                if cur.rowcount:
                    self.logger.info(f"Expired {cur.rowcount} past seat alert(s)")
                return cur.rowcount
        except sqlite3.Error as e:
            self.logger.error(f"Error expiring seat alerts: {e}")
            return 0
