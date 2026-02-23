#!/usr/bin/env python3
"""
Telegram Bot Command Handler
Handles incoming bot commands via long-polling.

  /start  — Subscribe to AMC Q&A showtime alerts
  /stop   — Unsubscribe from alerts
  /help   — Show available commands
"""

import logging
import threading
import time
from typing import Optional

try:
    import requests
except ImportError:
    raise ImportError("requests library is required: pip install requests")

from .user_manager import UserManager

# Telegram API
TELEGRAM_API_BASE = "https://api.telegram.org"

# Long-polling timeout (seconds). Telegram holds the request open until
# an update arrives or the timeout expires.
LONG_POLL_TIMEOUT = 30

# How long to wait after a network error before retrying
ERROR_BACKOFF_SECONDS = 5

WELCOME_MESSAGE = (
    "👋 Hi {first_name}! You're now subscribed to AMC Q&A showtime alerts.\n\n"
    "You'll receive a message whenever new Q&A events are scheduled at AMC "
    "theaters in NYC.\n\n"
    "Send /stop to unsubscribe at any time."
)

ALREADY_SUBSCRIBED_MESSAGE = (
    "✅ You're already subscribed to AMC Q&A showtime alerts!\n\n"
    "Send /stop to unsubscribe."
)

UNSUBSCRIBE_MESSAGE = (
    "👋 Goodbye {first_name}! You've been unsubscribed from AMC Q&A showtime alerts.\n\n"
    "Send /start to subscribe again."
)

NOT_SUBSCRIBED_MESSAGE = (
    "You're not currently subscribed.\n\n"
    "Send /start to subscribe to AMC Q&A showtime alerts."
)

HELP_MESSAGE = (
    "🎬 *AMC Showtime Alert Bot*\n\n"
    "Available commands:\n"
    "/start — Subscribe to Q&A showtime alerts\n"
    "/stop — Unsubscribe from alerts\n"
    "/help — Show this message"
)

BOT_COMMANDS = [
    {"command": "start", "description": "Subscribe to Q&A showtime alerts"},
    {"command": "stop", "description": "Unsubscribe from alerts"},
    {"command": "help", "description": "Show available commands"},
]


class TelegramBot:
    """
    Telegram bot that listens for commands via long-polling and manages
    user subscriptions in the database.
    """

    def __init__(self, bot_token: str, db_path: str = "notifications.db"):
        self.bot_token = bot_token
        self.db_path = db_path
        self.logger = logging.getLogger("TelegramBot")
        self._api_base = f"{TELEGRAM_API_BASE}/bot{bot_token}"
        self._offset: int = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the bot polling loop in a background daemon thread"""
        if self._running:
            self.logger.warning("Bot polling is already running")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="TelegramBotPoller",
        )
        self._thread.start()
        self._register_commands()
        self.logger.info("Telegram bot polling started")

    def stop(self):
        """Signal the polling loop to stop and wait for the thread to exit"""
        self._running = False
        if self._thread and self._thread.is_alive():
            # The thread will exit after the current long-poll request finishes
            # (up to LONG_POLL_TIMEOUT seconds).
            self._thread.join(timeout=LONG_POLL_TIMEOUT + 5)
        self.logger.info("Telegram bot polling stopped")

    # ------------------------------------------------------------------ #
    # Polling loop                                                         #
    # ------------------------------------------------------------------ #

    def _poll_loop(self):
        """Main long-polling loop — runs in a background thread"""
        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    try:
                        self._handle_update(update)
                    except Exception as e:
                        self.logger.error(
                            f"Error handling update {update.get('update_id')}: {e}",
                            exc_info=True,
                        )
                    # Advance the offset so this update is acknowledged
                    self._offset = update["update_id"] + 1
            except Exception as e:
                self.logger.error(f"Error in polling loop: {e}", exc_info=True)
                time.sleep(ERROR_BACKOFF_SECONDS)

    def _get_updates(self) -> list:
        """
        Fetch pending updates from Telegram using long-polling.
        Blocks for up to LONG_POLL_TIMEOUT seconds.
        """
        url = f"{self._api_base}/getUpdates"
        params = {
            "offset": self._offset,
            "timeout": LONG_POLL_TIMEOUT,
            "allowed_updates": ["message"],
        }
        try:
            response = requests.get(
                url,
                params=params,
                timeout=LONG_POLL_TIMEOUT + 10,
            )
            if response.ok:
                return response.json().get("result", [])
            self.logger.warning(
                f"getUpdates returned {response.status_code}: {response.text[:200]}"
            )
        except requests.exceptions.RequestException as e:
            if self._running:
                self.logger.warning(f"Network error fetching updates: {e}")
            time.sleep(ERROR_BACKOFF_SECONDS)
        return []

    # ------------------------------------------------------------------ #
    # Update handling                                                      #
    # ------------------------------------------------------------------ #

    def _handle_update(self, update: dict):
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        text: str = message.get("text", "")
        if not text:
            return

        chat_id: int = message["chat"]["id"]
        from_user: dict = message.get("from", {})

        command = text.split()[0].lower().split("@")[0]  # strip @botname suffix

        if command == "/start":
            self._handle_start(chat_id, from_user)
        elif command == "/stop":
            self._handle_stop(chat_id, from_user)
        elif command == "/help":
            self._send_message(chat_id, HELP_MESSAGE, parse_mode="Markdown")
        else:
            self._send_message(
                chat_id,
                "Unknown command. Send /help to see available commands.",
            )

    def _handle_start(self, chat_id: int, user: dict):
        um = UserManager(self.db_path)
        is_new_or_resubscribed = um.subscribe(
            chat_id=chat_id,
            username=user.get("username"),
            first_name=user.get("first_name"),
            last_name=user.get("last_name"),
        )
        first_name = user.get("first_name") or "there"
        if is_new_or_resubscribed:
            self._send_message(
                chat_id, WELCOME_MESSAGE.format(first_name=first_name)
            )
        else:
            self._send_message(chat_id, ALREADY_SUBSCRIBED_MESSAGE)

    def _handle_stop(self, chat_id: int, user: dict):
        um = UserManager(self.db_path)
        was_active = um.unsubscribe(chat_id)
        first_name = user.get("first_name") or "there"
        if was_active:
            self._send_message(
                chat_id, UNSUBSCRIBE_MESSAGE.format(first_name=first_name)
            )
        else:
            self._send_message(chat_id, NOT_SUBSCRIBED_MESSAGE)

    # ------------------------------------------------------------------ #
    # Telegram API helpers                                                 #
    # ------------------------------------------------------------------ #

    def _send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> bool:
        url = f"{self._api_base}/sendMessage"
        payload: dict = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            response = requests.post(url, json=payload, timeout=10)
            if not response.ok:
                self.logger.error(
                    f"Failed to send message to {chat_id}: "
                    f"{response.status_code} {response.text[:200]}"
                )
            return response.ok
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error sending message to {chat_id}: {e}")
            return False

    def _register_commands(self):
        """Register bot commands so they appear in the Telegram UI"""
        url = f"{self._api_base}/setMyCommands"
        try:
            response = requests.post(
                url, json={"commands": BOT_COMMANDS}, timeout=10
            )
            if response.ok:
                self.logger.info("Bot commands registered with Telegram")
            else:
                self.logger.warning(
                    f"Failed to register commands: {response.status_code}"
                )
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Could not register bot commands: {e}")
