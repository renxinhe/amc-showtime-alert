#!/usr/bin/env python3
"""
Telegram bot — long-polling listener and update dispatcher.

Subscription and alert command handling live in CommandsMixin; the guided
inline-keyboard /addalert wizard lives in GuidedFlowMixin. This module owns the
polling thread, the update router, and bot-command registration.
"""

import logging
import threading
from typing import List, Optional

from .api import TelegramAPI, ERROR_BACKOFF_SECONDS, LONG_POLL_TIMEOUT
from .commands import CommandsMixin
from .guided_flow import GuidedFlowMixin
from .seat_flow import SeatAlertFlowMixin
from .messages import BOT_COMMANDS, HELP_MESSAGE

import time


class TelegramBot(CommandsMixin, GuidedFlowMixin, SeatAlertFlowMixin):
    """
    Listens for commands and inline-button presses via long-polling and manages
    user subscriptions and custom alerts in the database.
    """

    def __init__(
        self,
        bot_token: str,
        db_path: str,
        theaters: Optional[List[dict]] = None,
    ):
        self.db_path = db_path
        self.logger = logging.getLogger("TelegramBot")
        self._api = TelegramAPI(bot_token)
        self._offset: int = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # In-progress guided /addalert flows, keyed by chat_id. Mutated only by
        # the single poll thread, so no lock is needed. State is ephemeral —
        # a bot restart simply drops any half-finished flow.
        self._conversations: dict = {}

        # Theater config (list of {name, slug, market}) for validating/resolving
        # the theater: argument in alert commands.
        self.theaters = theaters or []
        self._slugs = {t["slug"] for t in self.theaters if t.get("slug")}
        self._name_by_slug = {
            t["slug"]: t.get("name", t["slug"])
            for t in self.theaters
            if t.get("slug")
        }

    # -- lifecycle -------------------------------------------------------- #

    def start(self):
        """Start the bot polling loop in a background daemon thread."""
        if self._running:
            self.logger.warning("Bot polling is already running")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="TelegramBotPoller"
        )
        self._thread.start()
        self._api.set_my_commands(BOT_COMMANDS)
        self.logger.info("Telegram bot polling started")

    def stop(self):
        """Signal the polling loop to stop and wait for the thread to exit."""
        self._running = False
        if self._thread and self._thread.is_alive():
            # The thread exits after the current long-poll finishes
            # (up to LONG_POLL_TIMEOUT seconds).
            self._thread.join(timeout=LONG_POLL_TIMEOUT + 5)
        self.logger.info("Telegram bot polling stopped")

    # -- polling ---------------------------------------------------------- #

    def _poll_loop(self):
        """Main long-polling loop — runs in a background thread."""
        while self._running:
            try:
                updates = self._api.get_updates(self._offset, running=self._running)
                for update in updates:
                    try:
                        self._handle_update(update)
                    except Exception as e:
                        self.logger.error(
                            f"Error handling update {update.get('update_id')}: {e}",
                            exc_info=True,
                        )
                    # Advance the offset so this update is acknowledged.
                    self._offset = update["update_id"] + 1
            except Exception as e:
                self.logger.error(f"Error in polling loop: {e}", exc_info=True)
                time.sleep(ERROR_BACKOFF_SECONDS)

    # -- dispatch --------------------------------------------------------- #

    def _handle_update(self, update: dict):
        # Inline-keyboard button presses arrive as callback_query updates.
        callback = update.get("callback_query")
        if callback:
            self._handle_callback(callback)
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        text: str = message.get("text", "")
        if not text:
            return

        chat_id: int = message["chat"]["id"]
        from_user: dict = message.get("from", {})
        stripped = text.strip()

        # If a guided /addalert flow is waiting for the movie title, consume any
        # non-command text as that title.
        conv = self._conversations.get(chat_id)
        if conv and conv.get("step") == "title" and not stripped.startswith("/"):
            self._flow_set_title(chat_id, stripped)
            return

        parts = text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]  # strip @botname suffix
        args = parts[1] if len(parts) > 1 else ""

        if command == "/cancel":
            self._handle_cancel(chat_id)
            return

        # Any other command interrupts an in-progress flow.
        if conv:
            if stripped.startswith("/"):
                self._conversations.pop(chat_id, None)
            else:
                self._api.send_message(
                    chat_id, "Please use the buttons above, or send /cancel."
                )
                return

        if command == "/startqnaalert":
            self._handle_start(chat_id, from_user)
        elif command == "/stopqnaalert":
            self._handle_stop(chat_id, from_user)
        # Telegram sends /start on its own when a user first taps Start (and for
        # t.me deep links), so it can't be an unknown command — show the help
        # text, which is the menu of what to opt into. Any deep-link payload in
        # `args` is ignored. Deliberately left out of BOT_COMMANDS: Telegram
        # surfaces the Start button itself.
        elif command in ("/help", "/start"):
            self._api.send_message(chat_id, HELP_MESSAGE)
        elif command == "/addalert":
            self._handle_addalert(chat_id, args)
        elif command == "/listalerts":
            self._handle_listalerts(chat_id)
        elif command == "/editalert":
            self._handle_editalert(chat_id, args)
        elif command == "/delalert":
            self._handle_delalert(chat_id, args)
        elif command == "/addseatalert":
            self._handle_addseatalert(chat_id, args)
        elif command == "/listseatalerts":
            self._handle_listseatalerts(chat_id)
        elif command == "/delseatalert":
            self._handle_delseatalert(chat_id)
        else:
            self._api.send_message(
                chat_id, "Unknown command. Send /help to see available commands."
            )
