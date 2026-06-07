#!/usr/bin/env python3
"""
User Manager
Manages Telegram subscriber users in the SQLite database.
Users subscribe via /start and unsubscribe via /stop with the bot.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

CREATE_USERS_TABLE = """
    CREATE TABLE IF NOT EXISTS users (
        chat_id     INTEGER PRIMARY KEY,
        username    TEXT,
        first_name  TEXT,
        last_name   TEXT,
        is_active   INTEGER NOT NULL DEFAULT 1,
        subscribed_at   TIMESTAMP NOT NULL,
        unsubscribed_at TIMESTAMP,
        last_seen_at    TIMESTAMP NOT NULL
    )
"""


class UserManager:
    """Manages Telegram subscribers in the shared SQLite database"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.logger = logging.getLogger("UserManager")
        self._init_database()

    def _init_database(self):
        """Create the users table if it doesn't exist"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(CREATE_USERS_TABLE)
                conn.commit()
            self.logger.debug(f"Users table ready at {self.db_path}")
        except sqlite3.Error as e:
            self.logger.error(f"Database initialization error: {e}")
            raise

    def subscribe(
        self,
        chat_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> bool:
        """
        Subscribe a user to alerts.

        Returns:
            True if newly subscribed or re-subscribed, False if already active.
        """
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT is_active FROM users WHERE chat_id = ?", (chat_id,)
                )
                row = cursor.fetchone()

                if row is None:
                    cursor.execute(
                        """
                        INSERT INTO users
                            (chat_id, username, first_name, last_name,
                             is_active, subscribed_at, last_seen_at)
                        VALUES (?, ?, ?, ?, 1, ?, ?)
                        """,
                        (chat_id, username, first_name, last_name, now, now),
                    )
                    conn.commit()
                    self.logger.info(f"New subscriber: chat_id={chat_id} @{username}")
                    return True

                if row[0] == 0:
                    cursor.execute(
                        """
                        UPDATE users
                        SET is_active = 1,
                            username = ?,
                            first_name = ?,
                            last_name = ?,
                            subscribed_at = ?,
                            unsubscribed_at = NULL,
                            last_seen_at = ?
                        WHERE chat_id = ?
                        """,
                        (username, first_name, last_name, now, now, chat_id),
                    )
                    conn.commit()
                    self.logger.info(f"Re-subscribed: chat_id={chat_id} @{username}")
                    return True

                # Already active — update last_seen and profile
                cursor.execute(
                    """
                    UPDATE users
                    SET last_seen_at = ?,
                        username = ?,
                        first_name = ?,
                        last_name = ?
                    WHERE chat_id = ?
                    """,
                    (now, username, first_name, last_name, chat_id),
                )
                conn.commit()
                return False

        except sqlite3.Error as e:
            self.logger.error(f"Database error subscribing user {chat_id}: {e}")
            return False

    def unsubscribe(self, chat_id: int) -> bool:
        """
        Unsubscribe a user from alerts.

        Returns:
            True if successfully unsubscribed, False if not found or already inactive.
        """
        now = datetime.now().isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT is_active FROM users WHERE chat_id = ?", (chat_id,)
                )
                row = cursor.fetchone()

                if row is None or row[0] == 0:
                    return False

                cursor.execute(
                    """
                    UPDATE users
                    SET is_active = 0, unsubscribed_at = ?
                    WHERE chat_id = ?
                    """,
                    (now, chat_id),
                )
                conn.commit()
                self.logger.info(f"Unsubscribed: chat_id={chat_id}")
                return True

        except sqlite3.Error as e:
            self.logger.error(f"Database error unsubscribing user {chat_id}: {e}")
            return False

    def get_active_subscribers(self) -> List[int]:
        """Return chat IDs of all active subscribers"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT chat_id FROM users WHERE is_active = 1")
                return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting subscribers: {e}")
            return []

    def get_statistics(self) -> Dict:
        """Return subscriber statistics"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
                active = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM users")
                total = cursor.fetchone()[0]
                return {"active_subscribers": active, "total_users": total}
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting statistics: {e}")
            return {"active_subscribers": 0, "total_users": 0}
