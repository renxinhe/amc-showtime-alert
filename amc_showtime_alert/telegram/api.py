#!/usr/bin/env python3
"""Thin wrapper around the Telegram Bot HTTP API used by the command bot.

Centralizes every network call (getUpdates, sendMessage, edit*, callbacks,
setMyCommands) so the bot logic stays free of HTTP plumbing.
"""

import logging
import time
from typing import List, Optional

try:
    import requests
except ImportError:
    raise ImportError("requests library is required: pip install requests")

TELEGRAM_API_BASE = "https://api.telegram.org"

# Long-polling timeout (seconds). Telegram holds the request open until an
# update arrives or the timeout expires.
LONG_POLL_TIMEOUT = 30

# How long to wait after a network error before retrying.
ERROR_BACKOFF_SECONDS = 5


class TelegramAPI:
    """Stateless-ish HTTP client for one bot token."""

    def __init__(self, bot_token: str):
        self.logger = logging.getLogger("TelegramAPI")
        self._base = f"{TELEGRAM_API_BASE}/bot{bot_token}"

    # -- receiving -------------------------------------------------------- #

    def get_updates(self, offset: int, running: bool = True) -> list:
        """Long-poll for updates. Returns [] on error/timeout."""
        url = f"{self._base}/getUpdates"
        params = {
            "offset": offset,
            "timeout": LONG_POLL_TIMEOUT,
            "allowed_updates": ["message", "callback_query"],
        }
        try:
            response = requests.get(url, params=params, timeout=LONG_POLL_TIMEOUT + 10)
            if response.ok:
                return response.json().get("result", [])
            self.logger.warning(
                f"getUpdates returned {response.status_code}: {response.text[:200]}"
            )
        except requests.exceptions.RequestException as e:
            if running:
                self.logger.warning(f"Network error fetching updates: {e}")
            time.sleep(ERROR_BACKOFF_SECONDS)
        return []

    # -- sending ---------------------------------------------------------- #

    def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
    ) -> bool:
        url = f"{self._base}/sendMessage"
        payload: dict = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
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

    def send_picker(
        self, chat_id: int, text: str, reply_markup: dict
    ) -> Optional[int]:
        """Send an HTML message with an inline keyboard; return its message_id."""
        url = f"{self._base}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.ok:
                return response.json().get("result", {}).get("message_id")
            self.logger.error(
                f"Failed to send picker to {chat_id}: "
                f"{response.status_code} {response.text[:200]}"
            )
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error sending picker to {chat_id}: {e}")
        return None

    def edit_message(
        self,
        chat_id: int,
        message_id: Optional[int],
        text: str,
        reply_markup: Optional[dict] = None,
    ):
        """Replace a message's text (and keyboard). Removes the keyboard if none."""
        if message_id is None:
            self.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
            return
        url = f"{self._base}/editMessageText"
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._post_ignore_not_modified(url, payload)

    def edit_markup(self, chat_id: int, message_id: Optional[int], reply_markup: dict):
        """Update just the inline keyboard of an existing message."""
        if message_id is None:
            return
        url = f"{self._base}/editMessageReplyMarkup"
        self._post_ignore_not_modified(
            url,
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        )

    def _post_ignore_not_modified(self, url: str, payload: dict):
        try:
            response = requests.post(url, json=payload, timeout=10)
            # "message is not modified" (e.g. a redundant tap) is harmless.
            if not response.ok and "not modified" not in response.text:
                self.logger.error(
                    f"Edit failed: {response.status_code} {response.text[:200]}"
                )
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error editing message: {e}")

    def answer_callback(self, callback_id: Optional[str], text: str = ""):
        """Acknowledge a callback query so the client stops its loading spinner."""
        if not callback_id:
            return
        url = f"{self._base}/answerCallbackQuery"
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            requests.post(url, json=payload, timeout=10)
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error answering callback: {e}")

    def send_photo(
        self, chat_id: int, png_bytes: bytes, caption: Optional[str] = None
    ) -> bool:
        """Send a PNG photo (multipart) with an optional caption."""
        url = f"{self._base}/sendPhoto"
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        files = {"photo": ("seats.png", png_bytes, "image/png")}
        try:
            response = requests.post(url, data=data, files=files, timeout=30)
            if not response.ok:
                self.logger.error(
                    f"Failed to send photo to {chat_id}: "
                    f"{response.status_code} {response.text[:200]}"
                )
            return response.ok
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error sending photo to {chat_id}: {e}")
            return False

    def set_my_commands(self, commands: List[dict]) -> bool:
        url = f"{self._base}/setMyCommands"
        try:
            response = requests.post(url, json={"commands": commands}, timeout=10)
            if response.ok:
                self.logger.info("Bot commands registered with Telegram")
                return True
            self.logger.warning(f"Failed to register commands: {response.status_code}")
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Could not register bot commands: {e}")
        return False
