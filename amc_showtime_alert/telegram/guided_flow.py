#!/usr/bin/env python3
"""The guided, button-driven /addalert flow.

A small per-chat state machine: ask for the title, then collect theaters and
formats via inline-keyboard toggles, then fan the multi-select out into
individual single-select alert rows.

Provided as a mixin consumed by TelegramBot; relies on the host providing
`_api`, `db_path`, `theaters`, `_name_by_slug`, and `_conversations`.
"""

from ..alert_manager import AlertManager
from ..alert_matcher import validate_pattern
from ..movie_format_utils import KNOWN_FORMAT_TOKENS
from ..user_manager import UserManager
from . import formatting, keyboards


class GuidedFlowMixin:
    """Inline-keyboard /addalert wizard."""

    def _start_addalert_flow(self, chat_id: int):
        """Begin the button-driven /addalert flow by asking for the title."""
        self._conversations[chat_id] = {
            "step": "title",
            "pattern": None,
            "theaters": set(),  # slugs, or "*" for all
            "formats": set(),   # tokens, or "*" for any
            "message_id": None,
        }
        self._api.send_message(
            chat_id,
            "🎬 <b>New alert</b>\n\n"
            "Send me the movie title or keyword to watch for "
            "(e.g. <i>Dune</i>).\n\nSend /cancel anytime to stop.",
            parse_mode="HTML",
        )

    def _flow_set_title(self, chat_id: int, title: str):
        ok, err = validate_pattern(title, is_regex=False)
        if not ok:
            self._api.send_message(chat_id, f"❌ {err}\nTry again, or send /cancel.")
            return
        conv = self._conversations.get(chat_id)
        if not conv:
            return
        conv["pattern"] = title
        conv["step"] = "theaters"
        text = (
            f"🎬 <b>{formatting.html_escape(title)}</b>\n\n"
            "Which theaters? Tap to toggle, then <b>Next</b>.\n"
            "<i>(none selected = all theaters)</i>"
        )
        conv["message_id"] = self._api.send_picker(
            chat_id, text, keyboards.theater_keyboard(self.theaters, conv["theaters"])
        )

    def _handle_callback(self, callback: dict):
        data = callback.get("data", "")
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        # Always acknowledge so the client stops showing a spinner.
        self._api.answer_callback(callback.get("id"))

        # The /delalert picker is stateless (id encoded in the callback), so it
        # is handled before the guided-flow conversation check.
        if data.startswith("del:"):
            self._handle_delete_callback(chat_id, message_id, data[4:])
            return

        conv = self._conversations.get(chat_id)
        if not conv:
            return  # stale buttons from an old/finished flow

        if data == "x":
            self._conversations.pop(chat_id, None)
            self._api.edit_message(
                chat_id, message_id, "✖️ Cancelled. Nothing was created."
            )
            return

        if conv["step"] == "theaters":
            if data == "t:done":
                conv["step"] = "formats"
                text = (
                    f"🎬 <b>{formatting.html_escape(conv['pattern'])}</b>\n\n"
                    "Which formats? Tap to toggle, then <b>Create</b>.\n"
                    "<i>(none selected = any format)</i>"
                )
                self._api.edit_message(
                    chat_id, message_id, text,
                    keyboards.format_keyboard(conv["formats"]),
                )
            elif data.startswith("t:"):
                keyboards.toggle_selection(conv["theaters"], data[2:])
                self._api.edit_markup(
                    chat_id, message_id,
                    keyboards.theater_keyboard(self.theaters, conv["theaters"]),
                )
            return

        if conv["step"] == "formats":
            if data == "f:done":
                self._finish_addalert_flow(chat_id, message_id)
            elif data.startswith("f:"):
                keyboards.toggle_selection(conv["formats"], data[2:])
                self._api.edit_markup(
                    chat_id, message_id,
                    keyboards.format_keyboard(conv["formats"]),
                )
            return

    def _finish_addalert_flow(self, chat_id: int, message_id: int):
        conv = self._conversations.pop(chat_id, None)
        if not conv:
            return
        pattern = conv["pattern"]

        tsel = conv["theaters"]
        theaters = [None] if (not tsel or "*" in tsel) else sorted(tsel)
        fsel = conv["formats"]
        formats = (
            [None]
            if (not fsel or "*" in fsel)
            else [f for f in KNOWN_FORMAT_TOKENS if f in fsel]
        )

        am = AlertManager(self.db_path)
        created = []
        for theater_slug in theaters:
            for fmt in formats:
                aid = am.add_alert(
                    chat_id,
                    pattern,
                    is_regex=False,
                    theater_slug=theater_slug,
                    format_filter=fmt,
                )
                if aid is not None:
                    created.append(am.get_alert(chat_id, aid))

        if not created:
            self._api.edit_message(
                chat_id, message_id, "❌ Could not create the alert(s). Try again."
            )
            return

        if len(created) == 1:
            body = "✅ <b>Alert created</b>\n" + formatting.format_alert_card(
                created[0], self._name_by_slug
            )
        else:
            body = (
                f"✅ <b>{len(created)} alerts created</b>\n"
                + formatting.format_alerts_table(created, self._name_by_slug)
            )
        if chat_id not in set(UserManager(self.db_path).get_active_subscribers()):
            body += "\n\n⚠️ You're not subscribed — send /start to receive alerts."
        self._api.edit_message(chat_id, message_id, body)
