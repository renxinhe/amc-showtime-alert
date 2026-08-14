#!/usr/bin/env python3
"""
Tests for the per-user custom alert feature:
  - AlertManager CRUD + independence from the Q&A subscription
  - alert_matcher.find_alert_matches (theater / keyword / regex / format)
  - UserNotificationState per-user dedup + change detection
  - validate_pattern safety checks
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.alert_manager import AlertManager
from amc_showtime_alert.alert_matcher import (
    AlertMatch,
    find_alert_matches,
    validate_pattern,
)
from amc_showtime_alert.notification_state import UserNotificationState
from amc_showtime_alert.user_manager import UserManager


def _scraped(theater, date, movies):
    return {"results": [{"theater": theater, "date": date, "success": True, "movies": movies}]}


def _movie(name, slug, showtimes, details=None):
    m = {"name": name, "slug": slug, "showtimes": showtimes, "runtime": 120, "rating": "PG"}
    if details is not None:
        m["showtime_details"] = details
    return m


THEATER_NAME = "AMC Lincoln Square 13"
THEATER_SLUG = "amc-lincoln-square-13"
SLUG_BY_NAME = {THEATER_NAME: THEATER_SLUG}


class TestAlertManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        self.am = AlertManager(self.db)
        self.um = UserManager(self.db)

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def test_add_list_get_edit_delete(self):
        aid = self.am.add_alert(111, "Dune", is_regex=False, format_filter="imax")
        self.assertIsNotNone(aid)
        alerts = self.am.list_alerts(111)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].pattern, "Dune")
        self.assertEqual(alerts[0].format_filter, "imax")
        self.assertIsNone(alerts[0].theater_slug)  # default = all theaters

        # edit: set theater, clear format
        self.assertTrue(
            self.am.edit_alert(111, aid, theater_slug=THEATER_SLUG, format_filter=None)
        )
        a = self.am.get_alert(111, aid)
        self.assertEqual(a.theater_slug, THEATER_SLUG)
        self.assertIsNone(a.format_filter)

        # ownership enforced
        self.assertIsNone(self.am.get_alert(999, aid))
        self.assertFalse(self.am.delete_alert(999, aid))
        self.assertTrue(self.am.delete_alert(111, aid))
        self.assertEqual(self.am.list_alerts(111), [])

    def test_get_active_alerts_independent_of_qna_subscription(self):
        """Custom alerts are their own alert type — /stopqnaalert must not stop them."""
        self.um.subscribe_qna(111, first_name="A")
        self.um.subscribe_qna(222, first_name="B")
        self.am.add_alert(111, "Dune")
        self.am.add_alert(222, "Wicked")
        self.assertEqual(len(self.am.get_active_alerts()), 2)

        self.um.unsubscribe_qna(222)
        self.assertEqual(len(self.am.get_active_alerts()), 2)

        # an alert from a user with no users row at all still fires
        self.am.add_alert(333, "Avatar")
        self.assertEqual(
            {a.chat_id for a in self.am.get_active_alerts()}, {111, 222, 333}
        )

    def test_delete_is_soft(self):
        import sqlite3
        aid = self.am.add_alert(111, "Dune")
        self.assertTrue(self.am.delete_alert(111, aid))
        self.assertEqual(self.am.list_alerts(111), [])     # hidden from reads
        self.assertIsNone(self.am.get_alert(111, aid))
        self.assertFalse(self.am.delete_alert(111, aid))   # already soft-deleted
        self.assertFalse(self.am.edit_alert(111, aid, pattern="x"))  # can't edit
        row = sqlite3.connect(self.db).execute(
            "SELECT deleted_at FROM alerts WHERE id=?", (aid,)
        ).fetchone()
        self.assertIsNotNone(row[0])                       # row kept, marked deleted

    def test_soft_deleted_excluded_from_active(self):
        self.um.subscribe_qna(111, first_name="A")
        a1 = self.am.add_alert(111, "Dune")
        self.am.add_alert(111, "Wicked")
        self.am.delete_alert(111, a1)
        self.assertEqual([a.pattern for a in self.am.get_active_alerts()], ["Wicked"])


class TestAlertMatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        self.am = AlertManager(self.db)
        self.um = UserManager(self.db)
        self.um.subscribe_qna(111, first_name="A")

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def test_keyword_match_all_theaters(self):
        self.am.add_alert(111, "Dune")
        data = _scraped(THEATER_NAME, "2026-07-01", [_movie("Dune Part Two", "dune-2", ["7:00 PM"])])
        matches = find_alert_matches(data, self.am.get_active_alerts(), SLUG_BY_NAME)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].matched_showtimes, ["7:00 PM"])

    def test_theater_filter_excludes_other_theater(self):
        self.am.add_alert(111, "Dune", theater_slug="amc-empire-25")
        data = _scraped(THEATER_NAME, "2026-07-01", [_movie("Dune", "dune", ["7:00 PM"])])
        self.assertEqual(find_alert_matches(data, self.am.get_active_alerts(), SLUG_BY_NAME), [])

    def test_regex_match(self):
        self.am.add_alert(111, r"^Dune.*Part", is_regex=True)
        data = _scraped(THEATER_NAME, "2026-07-01", [
            _movie("Dune Part Two", "dune-2", ["7:00 PM"]),
            _movie("Sand Dune", "sand", ["8:00 PM"]),
        ])
        matches = find_alert_matches(data, self.am.get_active_alerts(), SLUG_BY_NAME)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].movie_name, "Dune Part Two")

    def test_format_filter_keeps_only_matching_showtimes(self):
        self.am.add_alert(111, "Wicked", format_filter="imax")
        details = [
            {"time": "7:00 PM", "formats": ["imax"]},
            {"time": "9:00 PM", "formats": ["dolby"]},
        ]
        data = _scraped(THEATER_NAME, "2026-07-01",
                        [_movie("Wicked", "wicked", ["7:00 PM", "9:00 PM"], details)])
        matches = find_alert_matches(data, self.am.get_active_alerts(), SLUG_BY_NAME)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].matched_showtimes, ["7:00 PM"])

    def test_format_filter_no_details_skips(self):
        self.am.add_alert(111, "Wicked", format_filter="imax")
        data = _scraped(THEATER_NAME, "2026-07-01",
                        [_movie("Wicked", "wicked", ["7:00 PM"])])  # no showtime_details
        self.assertEqual(find_alert_matches(data, self.am.get_active_alerts(), SLUG_BY_NAME), [])

    def test_unknown_theater_name_skipped(self):
        self.am.add_alert(111, "Dune")
        data = _scraped("Unknown Theater", "2026-07-01", [_movie("Dune", "dune", ["7:00 PM"])])
        self.assertEqual(find_alert_matches(data, self.am.get_active_alerts(), SLUG_BY_NAME), [])


class TestValidatePattern(unittest.TestCase):
    def test_empty_rejected(self):
        self.assertFalse(validate_pattern("   ", is_regex=False)[0])

    def test_keyword_ok(self):
        self.assertTrue(validate_pattern("Dune", is_regex=False)[0])

    def test_invalid_regex_rejected(self):
        self.assertFalse(validate_pattern("(unclosed", is_regex=True)[0])

    def test_catastrophic_regex_rejected(self):
        self.assertFalse(validate_pattern("(a+)+", is_regex=True)[0])

    def test_valid_regex_ok(self):
        self.assertTrue(validate_pattern(r"^Dune.*Part", is_regex=True)[0])


class TestUserNotificationState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        self.state = UserNotificationState(self.db)

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def _match(self, showtimes):
        return AlertMatch(
            chat_id=111, alert_id=1, theater="T", theater_slug=THEATER_SLUG,
            date="2026-07-01", movie_name="Dune", slug="dune", runtime=120,
            rating="PG", format_filter=None, matched_showtimes=showtimes,
        )

    def test_new_then_unchanged_then_changed(self):
        m = self._match(["7:00 PM"])
        should, changes = self.state.should_notify(m)
        self.assertTrue(should)
        self.assertIsNone(changes)
        self.state.mark_as_notified(m)

        # unchanged -> skip
        should, changes = self.state.should_notify(self._match(["7:00 PM"]))
        self.assertFalse(should)

        # changed -> update with diff
        should, changes = self.state.should_notify(self._match(["7:00 PM", "9:00 PM"]))
        self.assertTrue(should)
        self.assertEqual(changes.added, ["9:00 PM"])

    def test_delete_by_alert_rearms(self):
        m = self._match(["7:00 PM"])
        self.state.should_notify(m)
        self.state.mark_as_notified(m)
        self.assertFalse(self.state.should_notify(self._match(["7:00 PM"]))[0])
        self.state.delete_by_alert(1)
        # after wipe, treated as new again
        self.assertTrue(self.state.should_notify(self._match(["7:00 PM"]))[0])

    def test_dedup_is_per_alert_and_per_user(self):
        m = self._match(["7:00 PM"])
        self.state.mark_as_notified(m)
        # same movie, different alert id -> not deduped
        other_alert = AlertMatch(
            chat_id=111, alert_id=2, theater="T", theater_slug=THEATER_SLUG,
            date="2026-07-01", movie_name="Dune", slug="dune", runtime=120,
            rating="PG", format_filter=None, matched_showtimes=["7:00 PM"],
        )
        self.assertTrue(self.state.should_notify(other_alert)[0])


class TestGuidedAddAlertFlow(unittest.TestCase):
    """The button-driven /addalert flow: title -> theaters -> formats -> fan-out."""

    THEATERS = [
        {"name": "AMC Empire 25", "slug": "amc-empire-25"},
        {"name": "AMC Lincoln Square 13", "slug": "amc-lincoln-square-13"},
    ]

    def setUp(self):
        import logging
        from types import SimpleNamespace
        from amc_showtime_alert.telegram import TelegramBot

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        UserManager(self.db).subscribe_qna(999, first_name="Jim")

        b = TelegramBot.__new__(TelegramBot)
        b.db_path = self.db
        b.logger = logging.getLogger("test")
        b._conversations = {}
        b.theaters = self.THEATERS
        b._slugs = {t["slug"] for t in self.THEATERS}
        b._name_by_slug = {t["slug"]: t["name"] for t in self.THEATERS}
        # Stub the Telegram API client so no network I/O happens.
        b._api = SimpleNamespace(
            send_message=lambda *a, **k: True,
            send_picker=lambda *a, **k: 101,
            edit_message=lambda *a, **k: None,
            edit_markup=lambda *a, **k: None,
            answer_callback=lambda *a, **k: None,
        )
        self.bot = b

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def _text(self, t):
        self.bot._handle_update(
            {"message": {"chat": {"id": 999}, "from": {"first_name": "Jim"}, "text": t}}
        )

    def _cb(self, data):
        self.bot._handle_update(
            {"callback_query": {"id": "c", "data": data,
                                "message": {"chat": {"id": 999}, "message_id": 101}}}
        )

    def _alerts(self):
        return AlertManager(self.db).list_alerts(999)

    def test_fan_out_multi_theater_multi_format(self):
        self._text("/addalert")
        self._text("The Odyssey")
        self._cb("t:amc-empire-25")
        self._cb("t:amc-lincoln-square-13")
        self._cb("t:done")
        self._cb("f:imax")
        self._cb("f:70mm")
        self._cb("f:done")
        rows = {(a.theater_slug, a.format_filter) for a in self._alerts()}
        self.assertEqual(rows, {
            ("amc-empire-25", "imax"), ("amc-empire-25", "70mm"),
            ("amc-lincoln-square-13", "imax"), ("amc-lincoln-square-13", "70mm"),
        })

    def test_defaults_create_single_all_any_alert(self):
        self._text("/addalert")
        self._text("Dune")
        self._cb("t:done")  # nothing selected -> all theaters
        self._cb("f:done")  # nothing selected -> any format
        alerts = self._alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIsNone(alerts[0].theater_slug)
        self.assertIsNone(alerts[0].format_filter)

    def test_all_toggle_is_exclusive(self):
        self._text("/addalert")
        self._text("Wicked")
        self._cb("t:amc-empire-25")
        self._cb("t:*")  # selecting "all" clears the specific pick
        self.assertEqual(self.bot._conversations[999]["theaters"], {"*"})

    def test_cancel_creates_nothing(self):
        self._text("/addalert")
        self._text("Wicked")
        self._cb("x")
        self.assertEqual(self.bot._conversations, {})
        self.assertEqual(self._alerts(), [])

    def test_command_midflow_interrupts(self):
        self._text("/addalert")
        self._text("Wicked")
        self._cb("t:done")
        self._text("/listalerts")  # a command aborts the in-progress flow
        self.assertEqual(self.bot._conversations, {})

    def test_delalert_no_id_then_tap_deletes(self):
        aid = AlertManager(self.db).add_alert(999, "Dune", format_filter="imax")
        self._text("/delalert")  # no id -> picker; nothing deleted yet
        self.assertEqual(len(self._alerts()), 1)
        self._cb(f"del:{aid}")  # tap the alert's button
        self.assertEqual(self._alerts(), [])

    def test_delalert_picker_cancel_keeps_alert(self):
        AlertManager(self.db).add_alert(999, "Dune")
        self._text("/delalert")
        self._cb("del:x")  # cancel
        self.assertEqual(len(self._alerts()), 1)

    def test_delalert_with_id_deletes_directly(self):
        aid = AlertManager(self.db).add_alert(999, "Dune")
        self._text(f"/delalert {aid}")
        self.assertEqual(self._alerts(), [])


if __name__ == "__main__":
    unittest.main()
