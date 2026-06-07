#!/usr/bin/env python3
"""
Guided /seatalert flow + /delseatalert picker (create and delete only).

Create flow (all button-driven, with Back at every step):
    theater → month (next 3) → day → movie → showtime
The chosen showtime button carries the AMC showtime id, which uniquely
identifies the screening. The picked day gives the date used for expiry.

Provided as a mixin consumed by TelegramBot; relies on the host providing
`_api`, `db_path`, `theaters`, `_name_by_slug`, and `_conversations`.
"""

import calendar
import logging
from datetime import date

from ..movie_format_utils import format_display
from ..seat_alerts.listings import fetch_showings
from ..seat_alerts.manager import SeatAlertManager
from .formatting import html_escape

logger = logging.getLogger("SeatAlertFlow")

_MONTHS_AHEAD = 3
_DAYS_PER_ROW = 7


class SeatAlertFlowMixin:
    # ------------------------------------------------------------------ #
    # Entry points                                                         #
    # ------------------------------------------------------------------ #

    def _handle_addseatalert(self, chat_id: int, args: str):
        arg = args.strip()
        if arg:
            self._addseatalert_by_id(chat_id, arg)
            return
        # Guided flow: start at the theater picker.
        if not self.theaters:
            self._api.send_message(chat_id, "No theaters are configured.")
            return
        self._conversations[chat_id] = {"kind": "seat", "step": "theater"}
        mid = self._api.send_picker(
            chat_id, "🎟 <b>New seat alert</b>\n\nPick a theater:",
            self._seat_theater_kb(),
        )
        self._conversations[chat_id]["message_id"] = mid

    def _addseatalert_by_id(self, chat_id: int, arg: str):
        if not arg.isdigit():
            self._api.send_message(
                chat_id,
                "Send just the numeric showtime id, e.g. "
                "<code>/addseatalert 143838750</code>, or use /addseatalert to "
                "pick from the menu.",
                parse_mode="HTML",
            )
            return
        from ..seat_alerts.seat_map import fetch_showtime_meta

        meta = fetch_showtime_meta(arg)
        if not meta:
            self._api.send_message(
                chat_id,
                "Couldn't read that showtime's details. Use /addseatalert to "
                "pick it from the menu instead.",
            )
            return
        self._create_seat_alert(
            chat_id, showtime_id=arg, theater_slug=None,
            movie_name=meta.get("movie"), showtime_date=meta["date"],
            showtime_label=meta.get("label"),
        )

    def _handle_listseatalerts(self, chat_id: int):
        alerts = SeatAlertManager(self.db_path).list_active(chat_id)
        if not alerts:
            self._api.send_message(
                chat_id, "You have no seat alerts. Create one with /addseatalert."
            )
            return
        lines = ["🎟 <b>Your seat alerts</b>"]
        for a in alerts:
            theater = self._name_by_slug.get(a.theater_slug, "") if a.theater_slug else ""
            bits = [b for b in (a.movie_name, theater, a.showtime_label) if b]
            lines.append("• " + html_escape(" · ".join(bits)))
        lines.append("\nDelete one with /delseatalert.")
        self._api.send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    def _handle_delseatalert(self, chat_id: int):
        alerts = SeatAlertManager(self.db_path).list_active(chat_id)
        if not alerts:
            self._api.send_message(chat_id, "You have no seat alerts to delete.")
            return
        rows = []
        for a in alerts:
            label = a.movie_name or "showing"
            if a.showtime_label:
                label += f" · {a.showtime_label}"
            rows.append([{"text": f"🗑 {label}"[:60], "callback_data": f"sad:{a.id}"}])
        rows.append([{"text": "✖️ Cancel", "callback_data": "sad:x"}])
        self._api.send_message(
            chat_id, "🗑 Which seat alert should I delete?",
            reply_markup={"inline_keyboard": rows},
        )

    # ------------------------------------------------------------------ #
    # Callback handling                                                    #
    # ------------------------------------------------------------------ #

    def _handle_seat_delete_callback(self, chat_id, message_id, token):
        if token == "x":
            self._api.edit_message(chat_id, message_id, "Okay — nothing deleted.")
            return
        if not token.isdigit():
            return
        if SeatAlertManager(self.db_path).soft_delete(chat_id, int(token)):
            self._api.edit_message(chat_id, message_id, "🗑 Seat alert deleted.")
        else:
            self._api.edit_message(chat_id, message_id, "That seat alert was already gone.")

    def _handle_seat_callback(self, chat_id, message_id, data):
        """Handle sa:* callbacks for the create flow."""
        conv = self._conversations.get(chat_id)
        if not conv or conv.get("kind") != "seat":
            return
        action = data[3:]  # strip "sa:"

        if action == "x":
            self._conversations.pop(chat_id, None)
            self._api.edit_message(chat_id, message_id, "✖️ Cancelled.")
            return
        if action == "back":
            self._seat_back(chat_id, message_id, conv)
            return

        kind, _, value = action.partition(":")
        if kind == "th":
            conv.update(step="month", theater_slug=value)
            self._api.edit_message(chat_id, message_id, "Pick a month:", self._seat_month_kb())
        elif kind == "m":
            conv.update(step="day", year_month=value)
            self._api.edit_message(chat_id, message_id, "Pick a date:", self._seat_day_kb(value))
        elif kind == "d":
            conv.update(step="movie", date=value)
            self._render_movie_step(chat_id, message_id, conv)
        elif kind == "mv":
            conv.update(step="showtime", movie=conv["movies"][int(value)])
            self._api.edit_message(
                chat_id, message_id, f"Showtimes for <b>{conv['movie']}</b>:",
                self._seat_showtime_kb(conv),
            )
        elif kind == "st":
            self._finish_seat_flow(chat_id, message_id, conv, showtime_id=value)

    def _seat_back(self, chat_id, message_id, conv):
        step = conv.get("step")
        if step == "month":
            conv["step"] = "theater"
            self._api.edit_message(chat_id, message_id, "Pick a theater:", self._seat_theater_kb())
        elif step == "day":
            conv["step"] = "month"
            self._api.edit_message(chat_id, message_id, "Pick a month:", self._seat_month_kb())
        elif step == "movie":
            conv["step"] = "day"
            self._api.edit_message(
                chat_id, message_id, "Pick a date:", self._seat_day_kb(conv["year_month"])
            )
        elif step == "showtime":
            conv["step"] = "movie"
            self._render_movie_step(chat_id, message_id, conv)

    # ------------------------------------------------------------------ #
    # Steps / keyboards                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _nav_row(include_back=True):
        row = []
        if include_back:
            row.append({"text": "◀️ Back", "callback_data": "sa:back"})
        row.append({"text": "✖️ Cancel", "callback_data": "sa:x"})
        return row

    def _seat_theater_kb(self):
        rows = [[{"text": t.get("name", t["slug"]), "callback_data": f"sa:th:{t['slug']}"}]
                for t in self.theaters]
        rows.append(self._nav_row(include_back=False))
        return {"inline_keyboard": rows}

    def _seat_month_kb(self):
        today = date.today()
        rows = []
        y, m = today.year, today.month
        for _ in range(_MONTHS_AHEAD):
            rows.append([{"text": f"{calendar.month_name[m]} {y}", "callback_data": f"sa:m:{y:04d}-{m:02d}"}])
            m += 1
            if m > 12:
                m, y = 1, y + 1
        rows.append(self._nav_row())
        return {"inline_keyboard": rows}

    def _seat_day_kb(self, year_month: str):
        y, m = (int(x) for x in year_month.split("-"))
        today = date.today()
        first = today.day if (y, m) == (today.year, today.month) else 1
        last = calendar.monthrange(y, m)[1]
        buttons = [{"text": str(d), "callback_data": f"sa:d:{y:04d}-{m:02d}-{d:02d}"}
                   for d in range(first, last + 1)]
        rows = [buttons[i:i + _DAYS_PER_ROW] for i in range(0, len(buttons), _DAYS_PER_ROW)]
        rows.append(self._nav_row())
        return {"inline_keyboard": rows}

    def _theater_by_slug(self, slug):
        for t in self.theaters:
            if t.get("slug") == slug:
                return t
        return None

    def _render_movie_step(self, chat_id, message_id, conv):
        theater = self._theater_by_slug(conv["theater_slug"]) or {}
        showings = fetch_showings(theater.get("market", ""), conv["theater_slug"], conv["date"])
        conv["showings"] = showings
        movies = list(dict.fromkeys(s.movie for s in showings))  # unique, ordered
        conv["movies"] = movies
        if not movies:
            self._api.edit_message(
                chat_id, message_id,
                f"No showings found at that theater on {conv['date']}.",
                {"inline_keyboard": [self._nav_row()]},
            )
            return
        rows = [[{"text": mv[:55], "callback_data": f"sa:mv:{i}"}] for i, mv in enumerate(movies)]
        rows.append(self._nav_row())
        self._api.edit_message(chat_id, message_id, "Pick a movie:", {"inline_keyboard": rows})

    def _seat_showtime_kb(self, conv):
        rows = []
        for s in conv["showings"]:
            if s.movie != conv["movie"]:
                continue
            fmt = f" · {format_display(s.formats[0])}" if s.formats else ""
            sold = " · 🚫 Sold Out" if s.sold_out else ""
            rows.append([{"text": f"{s.time}{fmt}{sold}",
                          "callback_data": f"sa:st:{s.showtime_id}"}])
        rows.append(self._nav_row())
        return {"inline_keyboard": rows}

    def _finish_seat_flow(self, chat_id, message_id, conv, showtime_id):
        showing = next((s for s in conv["showings"] if s.showtime_id == showtime_id), None)
        d = date.fromisoformat(conv["date"])
        when = d.strftime("%a %b %d")
        time_part = showing.time if showing else ""
        fmt_part = f" · {format_display(showing.formats[0])}" if showing and showing.formats else ""
        label = f"{when} · {time_part}{fmt_part}".strip(" ·")
        self._conversations.pop(chat_id, None)
        self._create_seat_alert(
            chat_id, showtime_id=showtime_id, theater_slug=conv["theater_slug"],
            movie_name=conv.get("movie"), showtime_date=conv["date"],
            showtime_label=label, message_id=message_id,
        )

    def _create_seat_alert(self, chat_id, showtime_id, theater_slug, movie_name,
                           showtime_date, showtime_label, message_id=None):
        aid = SeatAlertManager(self.db_path).create(
            chat_id=chat_id, showtime_id=showtime_id, showtime_date=showtime_date,
            theater_slug=theater_slug, movie_name=movie_name, showtime_label=showtime_label,
        )
        if aid is None:
            text = "❌ Could not create the seat alert. Try again."
        else:
            theater = self._name_by_slug.get(theater_slug, "") if theater_slug else ""
            parts = [p for p in [movie_name, theater, showtime_label] if p]
            text = ("🎟 <b>Seat alert created</b>\n" + " · ".join(parts)
                    + "\n\nI'll message you when a good seat (centred, back half) opens up.")
        if message_id is not None:
            self._api.edit_message(chat_id, message_id, text)
        else:
            self._api.send_message(chat_id, text, parse_mode="HTML")
