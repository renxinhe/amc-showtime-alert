#!/usr/bin/env python3
"""
Telegram Bot Command Handler
Handles incoming bot commands via long-polling.

  /start  — Subscribe to AMC Q&A showtime alerts
  /stop   — Unsubscribe from alerts
  /help   — Show available commands
"""

import logging
import shlex
import threading
import time
from typing import List, Optional

try:
    import requests
except ImportError:
    raise ImportError("requests library is required: pip install requests")

from .user_manager import UserManager
from .alert_manager import AlertManager
from .alert_matcher import validate_pattern
from .notification_state import UserNotificationState
from .movie_format_utils import KNOWN_FORMAT_TOKENS, format_display

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
    "🎬 AMC Showtime Alert Bot\n\n"
    "Subscription:\n"
    "/start — Subscribe (also enables your custom alerts)\n"
    "/stop — Unsubscribe (silences all your alerts)\n\n"
    "Custom alerts — get notified about any movie you choose:\n"
    "/addalert <keyword> [theater:<slug>] [format:<imax|dolby|70mm|…>] [regex]\n"
    "/listalerts — show your alerts and their ids\n"
    "/editalert <id> [keyword] [theater:…] [format:…] [regex|noregex]\n"
    "/delalert <id> — delete an alert\n\n"
    "Examples:\n"
    '  /addalert Oppenheimer format:imax\n'
    '  /addalert "Taylor Swift" theater:amc-lincoln-square-13\n'
    "  /addalert ^Dune.*Part regex format:imax\n\n"
    "Omit theater: to match all theaters. Use theater:all to clear it on edit.\n"
    "/theaters — list theater slugs you can use\n"
    "/help — show this message"
)

BOT_COMMANDS = [
    {"command": "start", "description": "Subscribe to alerts"},
    {"command": "stop", "description": "Unsubscribe from alerts"},
    {"command": "addalert", "description": "Create a custom showtime alert"},
    {"command": "listalerts", "description": "List your custom alerts"},
    {"command": "editalert", "description": "Edit an existing alert"},
    {"command": "delalert", "description": "Delete an alert"},
    {"command": "theaters", "description": "List available theater slugs"},
    {"command": "help", "description": "Show available commands"},
]


class TelegramBot:
    """
    Telegram bot that listens for commands via long-polling and manages
    user subscriptions in the database.
    """

    def __init__(
        self,
        bot_token: str,
        db_path: str,
        theaters: Optional[List[dict]] = None,
    ):
        self.bot_token = bot_token
        self.db_path = db_path
        self.logger = logging.getLogger("TelegramBot")
        self._api_base = f"{TELEGRAM_API_BASE}/bot{bot_token}"
        self._offset: int = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Theater config (list of {name, slug, market}) for validating/resolving
        # the theater: argument in alert commands.
        self.theaters = theaters or []
        self._slugs = {t["slug"] for t in self.theaters if t.get("slug")}
        self._name_by_slug = {
            t["slug"]: t.get("name", t["slug"])
            for t in self.theaters
            if t.get("slug")
        }

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

        parts = text.split(maxsplit=1)
        command = parts[0].lower().split("@")[0]  # strip @botname suffix
        args = parts[1] if len(parts) > 1 else ""

        if command == "/start":
            self._handle_start(chat_id, from_user)
        elif command == "/stop":
            self._handle_stop(chat_id, from_user)
        elif command == "/help":
            self._send_message(chat_id, HELP_MESSAGE)
        elif command == "/addalert":
            self._handle_addalert(chat_id, args)
        elif command == "/listalerts":
            self._handle_listalerts(chat_id)
        elif command == "/editalert":
            self._handle_editalert(chat_id, args)
        elif command == "/delalert":
            self._handle_delalert(chat_id, args)
        elif command == "/theaters":
            self._handle_theaters(chat_id)
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
    # Custom alert commands                                                #
    # ------------------------------------------------------------------ #

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
            return None, (
                f"Ambiguous theater '{value}'. Matches: "
                f"{', '.join(sorted(candidates))}"
            )
        return None, (
            f"Unknown theater '{value}'. Use /theaters to list valid slugs."
        )

    def _resolve_format(self, value: str):
        """Resolve a format: argument to a token, _CLEAR, or (None, error)."""
        v = value.strip().lower()
        if v in ("", "any", "all", "none"):
            return _CLEAR, None
        v = _FORMAT_ALIASES.get(v, v)
        if v in KNOWN_FORMAT_TOKENS:
            return v, None
        return None, (
            f"Unknown format '{value}'. Valid: {', '.join(KNOWN_FORMAT_TOKENS)}"
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
            return {}, ["Could not parse arguments — check your quotes."]

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

    @staticmethod
    def _html_escape(text: str) -> str:
        """Escape the three characters that matter for Telegram HTML parse mode."""
        return (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    def _alert_theater(self, alert) -> str:
        if alert.theater_slug:
            return self._name_by_slug.get(alert.theater_slug, alert.theater_slug)
        return "All theaters"

    def _format_alert_card(self, alert) -> str:
        """Render one alert as a tidy multi-line HTML card (for confirmations)."""
        theater = self._alert_theater(alert)
        fmt = format_display(alert.format_filter) if alert.format_filter else "Any format"
        regex = " <i>(regex)</i>" if alert.is_regex else ""
        return (
            f"<b>#{alert.id}</b>  🎬 <b>{self._html_escape(alert.pattern)}</b>{regex}\n"
            f"     📍 {self._html_escape(theater)}\n"
            f"     🎟 {self._html_escape(fmt)}"
        )

    def _format_alerts_table(self, alerts) -> str:
        """Render alerts as an aligned monospace HTML table."""
        headers = ("#", "Movie", "Theater", "Format")
        rows = []
        for a in alerts:
            movie = a.pattern + ("*" if a.is_regex else "")
            theater = "All" if not a.theater_slug else self._alert_theater(a)
            fmt = format_display(a.format_filter) if a.format_filter else "Any"
            rows.append((str(a.id), movie, theater, fmt))

        widths = [
            max(len(r[i]) for r in (headers, *rows)) for i in range(len(headers))
        ]

        def row(cells):
            return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

        sep = "  ".join("-" * w for w in widths)
        body = "\n".join([row(headers), sep, *(row(r) for r in rows)])
        table = "<pre>" + self._html_escape(body) + "</pre>"
        if any(a.is_regex for a in alerts):
            table += "\n<i>* = regex</i>"
        return table

    def _handle_addalert(self, chat_id: int, args: str):
        if not args.strip():
            self._send_message(
                chat_id,
                "Usage: /addalert <keyword> [theater:<slug>] "
                "[format:<imax|dolby|70mm|…>] [regex]\n"
                "Example: /addalert Dune format:imax",
            )
            return

        parsed, errors = self._parse_alert_args(args)
        if errors:
            self._send_message(chat_id, "⚠️ " + "\n".join(errors))
            return

        pattern = parsed.get("pattern")
        if not pattern:
            self._send_message(
                chat_id,
                "Please provide a keyword or phrase to match.\n"
                "Example: /addalert Oppenheimer format:imax",
            )
            return

        is_regex = parsed.get("is_regex", False)
        ok, err = validate_pattern(pattern, is_regex)
        if not ok:
            self._send_message(chat_id, f"❌ {err}")
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
            self._send_message(chat_id, "❌ Could not create alert. Try again.")
            return

        alert = am.get_alert(chat_id, alert_id)
        msg = "✅ <b>Alert created</b>\n" + self._format_alert_card(alert)
        # Remind the user that alerts only fire while subscribed.
        if chat_id not in set(UserManager(self.db_path).get_active_subscribers()):
            msg += "\n\n⚠️ You're not subscribed — send /start to receive alerts."
        self._send_message(chat_id, msg, parse_mode="HTML")

    def _handle_listalerts(self, chat_id: int):
        am = AlertManager(self.db_path)
        alerts = am.list_alerts(chat_id)
        if not alerts:
            self._send_message(
                chat_id,
                "You have no custom alerts yet.\n"
                "Create one with /addalert — see /help for examples.",
            )
            return
        msg = (
            "🔔 <b>Your alerts</b>\n"
            + self._format_alerts_table(alerts)
            + "\n\nEdit: <code>/editalert &lt;id&gt; …</code>"
            + "   Delete: <code>/delalert &lt;id&gt;</code>"
        )
        self._send_message(chat_id, msg, parse_mode="HTML")

    def _handle_editalert(self, chat_id: int, args: str):
        parts = args.strip().split(maxsplit=1)
        if not parts or not parts[0].lstrip("#").isdigit():
            self._send_message(
                chat_id,
                "Usage: /editalert <id> [keyword] [theater:…] [format:…] "
                "[regex|noregex]\nSee /listalerts for ids.",
            )
            return

        alert_id = int(parts[0].lstrip("#"))
        rest = parts[1] if len(parts) > 1 else ""

        am = AlertManager(self.db_path)
        existing = am.get_alert(chat_id, alert_id)
        if existing is None:
            self._send_message(
                chat_id, f"No alert #{alert_id} found. Use /listalerts."
            )
            return

        if not rest.strip():
            self._send_message(
                chat_id,
                "Nothing to change. Provide a new keyword and/or "
                "theater:… format:… regex|noregex.",
            )
            return

        parsed, errors = self._parse_alert_args(rest)
        if errors:
            self._send_message(chat_id, "⚠️ " + "\n".join(errors))
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
            self._send_message(chat_id, "Nothing to change.")
            return

        final_pattern = kwargs.get("pattern", existing.pattern)
        final_is_regex = kwargs.get("is_regex", existing.is_regex)
        ok, err = validate_pattern(final_pattern, final_is_regex)
        if not ok:
            self._send_message(chat_id, f"❌ {err}")
            return

        am.edit_alert(chat_id, alert_id, **kwargs)
        # Editing changes match semantics — wipe dedup so the alert re-arms.
        UserNotificationState(self.db_path).delete_by_alert(alert_id)

        updated = am.get_alert(chat_id, alert_id)
        self._send_message(
            chat_id,
            "✅ <b>Alert updated</b>\n" + self._format_alert_card(updated),
            parse_mode="HTML",
        )

    def _handle_delalert(self, chat_id: int, args: str):
        tok = args.strip().split()
        if not tok or not tok[0].lstrip("#").isdigit():
            self._send_message(
                chat_id, "Usage: /delalert <id> (see /listalerts)"
            )
            return
        alert_id = int(tok[0].lstrip("#"))
        am = AlertManager(self.db_path)
        if am.delete_alert(chat_id, alert_id):
            UserNotificationState(self.db_path).delete_by_alert(alert_id)
            self._send_message(chat_id, f"🗑 Deleted alert #{alert_id}.")
        else:
            self._send_message(
                chat_id, f"No alert #{alert_id} found. Use /listalerts."
            )

    def _handle_theaters(self, chat_id: int):
        if not self.theaters:
            self._send_message(chat_id, "No theaters are configured.")
            return
        lines = ["🎟 Available theaters (use the slug in theater:<slug>):"]
        for t in self.theaters:
            lines.append(f'  {t.get("slug")} — {t.get("name", "")}')
        lines.append("\nOmit theater: to match all theaters.")
        self._send_message(chat_id, "\n".join(lines))

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
