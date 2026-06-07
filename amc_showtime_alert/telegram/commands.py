#!/usr/bin/env python3
"""Command handlers and argument parsing for the text-based bot commands.

Provided as a mixin consumed by TelegramBot; relies on the host providing
`_api`, `db_path`, `theaters`, `_slugs`, `_name_by_slug`, `_conversations`, and
the guided-flow entrypoint `_start_addalert_flow`.
"""

import shlex
from typing import List

from ..alert_manager import AlertManager
from ..alert_matcher import validate_pattern
from ..notification_state import UserNotificationState
from ..user_manager import UserManager
from ..movie_format_utils import KNOWN_FORMAT_TOKENS
from . import formatting, keyboards
from . import messages as msg

# Sentinel meaning "clear this filter" (e.g. theater:all) vs "not provided".
_CLEAR = object()

# Aliases accepted for format tokens, mapped to the canonical token.
_FORMAT_ALIASES = {
    "70": "70mm",
    "70 mm": "70mm",
    "dolbycinema": "dolby",
    "dolby cinema": "dolby",
    "reald": "3d",
    "reald 3d": "3d",
    "real d": "3d",
}


class CommandsMixin:
    """Subscription + custom-alert command handlers."""

    # -- subscription ----------------------------------------------------- #

    def _handle_cancel(self, chat_id: int):
        if self._conversations.pop(chat_id, None) is not None:
            self._api.send_message(chat_id, msg.CANCELLED_NOTHING)
        else:
            self._api.send_message(chat_id, msg.NOTHING_TO_CANCEL)

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
            self._api.send_message(chat_id, msg.WELCOME_MESSAGE.format(first_name=first_name))
        else:
            self._api.send_message(chat_id, msg.ALREADY_SUBSCRIBED_MESSAGE)

    def _handle_stop(self, chat_id: int, user: dict):
        um = UserManager(self.db_path)
        was_active = um.unsubscribe(chat_id)
        first_name = user.get("first_name") or "there"
        if was_active:
            self._api.send_message(
                chat_id, msg.UNSUBSCRIBE_MESSAGE.format(first_name=first_name)
            )
        else:
            self._api.send_message(chat_id, msg.NOT_SUBSCRIBED_MESSAGE)

    # -- argument parsing ------------------------------------------------- #

    def _tokenize(self, args: str) -> List[str]:
        """
        Split command args, honoring double-quoted phrases but NOT processing
        backslash escapes (so regex like \\bDune\\b survives intact).

        Raises ValueError on unbalanced quotes.
        """
        lex = shlex.shlex(args, posix=True)
        lex.whitespace_split = True
        lex.escape = ""
        lex.commenters = ""
        return list(lex)

    def _resolve_theater(self, value: str):
        """Resolve a theater: argument to a slug, _CLEAR, or (None, error)."""
        v = value.strip().lower()
        if v in ("", "all", "any", "*"):
            return _CLEAR, None
        if value in self._slugs:
            return value, None
        if v in self._slugs:
            return v, None
        candidates = [
            s
            for s in self._slugs
            if v in s.lower() or v in self._name_by_slug[s].lower()
        ]
        if len(candidates) == 1:
            return candidates[0], None
        if len(candidates) > 1:
            return None, msg.THEATER_AMBIGUOUS.format(
                value=value, matches=", ".join(sorted(candidates))
            )
        valid = ", ".join(sorted(self._slugs)) or "(none configured)"
        return None, msg.THEATER_UNKNOWN.format(value=value, slugs=valid)

    def _resolve_format(self, value: str):
        """Resolve a format: argument to a token, _CLEAR, or (None, error)."""
        v = value.strip().lower()
        if v in ("", "any", "all", "none"):
            return _CLEAR, None
        v = _FORMAT_ALIASES.get(v, v)
        if v in KNOWN_FORMAT_TOKENS:
            return v, None
        return None, msg.FORMAT_UNKNOWN.format(
            value=value, tokens=", ".join(KNOWN_FORMAT_TOKENS)
        )

    def _parse_alert_args(self, args: str):
        """
        Parse alert command args into a dict and a list of errors.

        Recognized: theater:<v>, format:<v> flags; bare regex / noregex toggles;
        everything else joins into the title pattern. Keys are present only when
        provided (theater_slug / format_filter may be _CLEAR to clear them).
        """
        try:
            tokens = self._tokenize(args)
        except ValueError:
            return {}, [msg.ARGS_PARSE_ERROR]

        result: dict = {}
        errors: List[str] = []
        pattern_parts: List[str] = []

        for tok in tokens:
            low = tok.lower()
            if low == "regex":
                result["is_regex"] = True
            elif low == "noregex":
                result["is_regex"] = False
            elif ":" in tok and tok.split(":", 1)[0].lower() in ("theater", "theatre"):
                res, err = self._resolve_theater(tok.split(":", 1)[1])
                if err:
                    errors.append(err)
                else:
                    result["theater_slug"] = res
            elif ":" in tok and tok.split(":", 1)[0].lower() in ("format", "fmt"):
                res, err = self._resolve_format(tok.split(":", 1)[1])
                if err:
                    errors.append(err)
                else:
                    result["format_filter"] = res
            else:
                pattern_parts.append(tok)

        if pattern_parts:
            result["pattern"] = " ".join(pattern_parts)
        return result, errors

    # -- alert commands --------------------------------------------------- #

    def _handle_addalert(self, chat_id: int, args: str):
        if not args.strip():
            # No args → start the guided, button-driven flow.
            self._start_addalert_flow(chat_id)
            return

        parsed, errors = self._parse_alert_args(args)
        if errors:
            self._api.send_message(chat_id, "⚠️ " + "\n".join(errors))
            return

        pattern = parsed.get("pattern")
        if not pattern:
            self._api.send_message(chat_id, msg.ADDALERT_NEED_PATTERN)
            return

        is_regex = parsed.get("is_regex", False)
        ok, err = validate_pattern(pattern, is_regex)
        if not ok:
            self._api.send_message(chat_id, f"❌ {err}")
            return

        theater_slug = parsed.get("theater_slug")
        theater_slug = None if theater_slug is _CLEAR else theater_slug
        fmt = parsed.get("format_filter")
        fmt = None if fmt is _CLEAR else fmt

        am = AlertManager(self.db_path)
        alert_id = am.add_alert(
            chat_id,
            pattern,
            is_regex=is_regex,
            theater_slug=theater_slug,
            format_filter=fmt,
        )
        if alert_id is None:
            self._api.send_message(chat_id, msg.ADDALERT_CREATE_FAILED)
            return

        alert = am.get_alert(chat_id, alert_id)
        text = msg.ALERT_CREATED_HEADER + "\n" + formatting.format_alert_card(
            alert, self._name_by_slug
        )
        # Remind the user that alerts only fire while subscribed.
        if chat_id not in set(UserManager(self.db_path).get_active_subscribers()):
            text += msg.NOT_SUBSCRIBED_SUFFIX
        self._api.send_message(chat_id, text, parse_mode="HTML")

    def _handle_listalerts(self, chat_id: int):
        am = AlertManager(self.db_path)
        alerts = am.list_alerts(chat_id)
        if not alerts:
            self._api.send_message(chat_id, msg.LISTALERTS_EMPTY)
            return
        text = (
            msg.LISTALERTS_HEADER + "\n"
            + formatting.format_alerts_table(alerts, self._name_by_slug)
            + msg.LISTALERTS_FOOTER
        )
        self._api.send_message(chat_id, text, parse_mode="HTML")

    def _handle_editalert(self, chat_id: int, args: str):
        parts = args.strip().split(maxsplit=1)
        if not parts or not parts[0].lstrip("#").isdigit():
            self._api.send_message(chat_id, msg.EDITALERT_USAGE)
            return

        alert_id = int(parts[0].lstrip("#"))
        rest = parts[1] if len(parts) > 1 else ""

        am = AlertManager(self.db_path)
        existing = am.get_alert(chat_id, alert_id)
        if existing is None:
            self._api.send_message(chat_id, msg.ALERT_NOT_FOUND.format(id=alert_id))
            return

        if not rest.strip():
            self._api.send_message(chat_id, msg.EDITALERT_NOTHING)
            return

        parsed, errors = self._parse_alert_args(rest)
        if errors:
            self._api.send_message(chat_id, "⚠️ " + "\n".join(errors))
            return

        kwargs: dict = {}
        if "pattern" in parsed:
            kwargs["pattern"] = parsed["pattern"]
        if "is_regex" in parsed:
            kwargs["is_regex"] = parsed["is_regex"]
        if "theater_slug" in parsed:
            kwargs["theater_slug"] = (
                None if parsed["theater_slug"] is _CLEAR else parsed["theater_slug"]
            )
        if "format_filter" in parsed:
            kwargs["format_filter"] = (
                None if parsed["format_filter"] is _CLEAR else parsed["format_filter"]
            )

        if not kwargs:
            self._api.send_message(chat_id, msg.EDITALERT_NOTHING_SHORT)
            return

        final_pattern = kwargs.get("pattern", existing.pattern)
        final_is_regex = kwargs.get("is_regex", existing.is_regex)
        ok, err = validate_pattern(final_pattern, final_is_regex)
        if not ok:
            self._api.send_message(chat_id, f"❌ {err}")
            return

        am.edit_alert(chat_id, alert_id, **kwargs)
        # Editing changes match semantics — wipe dedup so the alert re-arms.
        UserNotificationState(self.db_path).delete_by_alert(alert_id)

        updated = am.get_alert(chat_id, alert_id)
        self._api.send_message(
            chat_id,
            msg.ALERT_UPDATED_HEADER + "\n"
            + formatting.format_alert_card(updated, self._name_by_slug),
            parse_mode="HTML",
        )

    def _handle_delalert(self, chat_id: int, args: str):
        tok = args.strip().split()
        # With a valid id → delete directly (typed path).
        if tok and tok[0].lstrip("#").isdigit():
            self._handle_delalert_direct(chat_id, int(tok[0].lstrip("#")))
            return

        # No id → show a tappable picker of the user's alerts.
        alerts = AlertManager(self.db_path).list_alerts(chat_id)
        if not alerts:
            self._api.send_message(chat_id, msg.DELALERT_EMPTY)
            return
        self._api.send_message(
            chat_id, msg.DELALERT_PROMPT,
            reply_markup=keyboards.delete_keyboard(alerts),
        )

    def _delete_alert(self, chat_id: int, alert_id: int) -> bool:
        """Delete an alert and wipe its dedup state. Returns whether it existed."""
        am = AlertManager(self.db_path)
        if am.delete_alert(chat_id, alert_id):
            UserNotificationState(self.db_path).delete_by_alert(alert_id)
            return True
        return False

    def _handle_delalert_direct(self, chat_id: int, alert_id: int):
        if self._delete_alert(chat_id, alert_id):
            self._api.send_message(chat_id, msg.ALERT_DELETED.format(id=alert_id))
        else:
            self._api.send_message(chat_id, msg.ALERT_NOT_FOUND.format(id=alert_id))

    def _handle_delete_callback(self, chat_id: int, message_id: int, token: str):
        """Handle a tap on the /delalert picker (callback data after 'del:')."""
        if token == "x":
            self._api.edit_message(chat_id, message_id, msg.ALERT_DELETE_CANCELLED)
            return
        if not token.isdigit():
            return
        alert_id = int(token)
        if self._delete_alert(chat_id, alert_id):
            self._api.edit_message(
                chat_id, message_id, msg.ALERT_DELETED.format(id=alert_id)
            )
        else:
            self._api.edit_message(
                chat_id, message_id, msg.ALERT_ALREADY_GONE.format(id=alert_id)
            )
