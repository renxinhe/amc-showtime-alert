#!/usr/bin/env python3
"""
Tests for the per-user custom alert feature:
  - AlertManager CRUD + active-user filtering
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

    def test_get_active_alerts_filters_inactive_users(self):
        self.um.subscribe(111, first_name="A")
        self.um.subscribe(222, first_name="B")
        self.am.add_alert(111, "Dune")
        self.am.add_alert(222, "Wicked")
        self.assertEqual(len(self.am.get_active_alerts()), 2)

        self.um.unsubscribe(222)
        active = self.am.get_active_alerts()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].chat_id, 111)

        # alert owned by a user not in the users table is excluded
        self.am.add_alert(333, "Avatar")
        self.assertEqual(len(self.am.get_active_alerts()), 1)


class TestAlertMatcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        self.am = AlertManager(self.db)
        self.um = UserManager(self.db)
        self.um.subscribe(111, first_name="A")

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


if __name__ == "__main__":
    unittest.main()
