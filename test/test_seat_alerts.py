#!/usr/bin/env python3
"""
Tests for the seat-alert subsystem: SeatAlertManager (soft delete, day-after
expiry, pollable set) and the poller's new-good-seat diffing. No network — the
poller's fetch is injected.
"""

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.seat_alerts.manager import SeatAlertManager
from amc_showtime_alert.seat_alerts.poller import poll_seat_alerts
from amc_showtime_alert.seat_alerts.seat_map import SeatLayout
from amc_showtime_alert.user_manager import UserManager

CHAT = 555
TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _seat(row, column, available=True, type="CanReserve", name="X"):
    return {"available": available, "row": row, "column": column, "name": name,
            "type": type, "seatTier": "Regular", "shouldDisplay": True}


class TestSeatAlertManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        UserManager(self.db).subscribe(CHAT, first_name="Jim")
        self.mgr = SeatAlertManager(self.db)

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def test_create_and_list_active(self):
        aid = self.mgr.create(CHAT, "111", TOMORROW, movie_name="Dune")
        self.assertIsNotNone(aid)
        active = self.mgr.list_active(CHAT)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].showtime_id, "111")

    def test_soft_delete_keeps_row_but_hides_it(self):
        aid = self.mgr.create(CHAT, "111", TOMORROW)
        self.assertTrue(self.mgr.soft_delete(CHAT, aid))
        self.assertEqual(self.mgr.list_active(CHAT), [])
        # second delete is a no-op (already soft-deleted)
        self.assertFalse(self.mgr.soft_delete(CHAT, aid))
        # row still physically present with a deleted_at
        import sqlite3
        row = sqlite3.connect(self.db).execute(
            "SELECT deleted_at FROM seat_alerts WHERE id=?", (aid,)
        ).fetchone()
        self.assertIsNotNone(row[0])

    def test_soft_delete_requires_ownership(self):
        aid = self.mgr.create(CHAT, "111", TOMORROW)
        self.assertFalse(self.mgr.soft_delete(99999, aid))
        self.assertEqual(len(self.mgr.list_active(CHAT)), 1)

    def test_expire_past_is_day_after(self):
        past = self.mgr.create(CHAT, "p", YESTERDAY)
        today = self.mgr.create(CHAT, "t", TODAY)
        future = self.mgr.create(CHAT, "f", TOMORROW)
        n = self.mgr.expire_past()
        self.assertEqual(n, 1)  # only the past one
        remaining = {a.showtime_id for a in self.mgr.list_active(CHAT)}
        self.assertEqual(remaining, {"t", "f"})

    def test_pollable_excludes_inactive_user_and_past_and_deleted(self):
        keep = self.mgr.create(CHAT, "keep", TOMORROW)
        deleted = self.mgr.create(CHAT, "del", TOMORROW)
        self.mgr.soft_delete(CHAT, deleted)
        self.mgr.create(CHAT, "past", YESTERDAY)
        pollable = {a.showtime_id for a in self.mgr.get_pollable()}
        self.assertEqual(pollable, {"keep"})

        # /stop the user -> nothing pollable
        UserManager(self.db).unsubscribe(CHAT)
        self.assertEqual(self.mgr.get_pollable(), [])


class TestSeatPoller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        UserManager(self.db).subscribe(CHAT, first_name="Jim")
        self.mgr = SeatAlertManager(self.db)
        self.aid = self.mgr.create(CHAT, "111", TOMORROW, movie_name="Dune")
        self.sent = []

    def tearDown(self):
        Path(self.db).unlink(missing_ok=True)

    def _poll(self, seats, theater_names=None):
        layout = SeatLayout(rows=10, columns=10, seats=seats)
        return poll_seat_alerts(
            self.db,
            send=lambda cid, text: self.sent.append((cid, text)) or True,
            fetch=lambda sid: layout,
            sleep=lambda *_: None,
            delay=0,
            theater_names=theater_names,
        )

    def test_notifies_on_new_good_seat_then_dedups(self):
        good = _seat(7, 5, available=True, name="G5")  # back half + centre
        # run 1: new good seat -> notify
        stats = self._poll([good])
        self.assertEqual(stats["notified"], 1)
        self.assertIn("G5", self.sent[-1][1])
        # run 2: unchanged -> no notify
        self.sent.clear()
        stats = self._poll([good])
        self.assertEqual(stats["notified"], 0)
        self.assertEqual(self.sent, [])

    def test_excludes_front_and_offcentre_and_taken(self):
        seats = [
            _seat(2, 5, name="A5"),                 # too front
            _seat(8, 1, name="H1"),                 # too far left
            _seat(7, 5, available=False, name="G5"),# good spot but taken
        ]
        stats = self._poll(seats)
        self.assertEqual(stats["notified"], 0)

    def test_reopened_seat_renotifies(self):
        g = _seat(7, 5, name="G5")
        self._poll([g])                 # notify G5
        self.sent.clear()
        self._poll([_seat(7, 5, available=False, name="G5")])  # taken
        self.assertEqual(self.sent, [])
        # reopens -> notify again
        stats = self._poll([g])
        self.assertEqual(stats["notified"], 1)
        self.assertIn("G5", self.sent[-1][1])

    def test_message_names_the_theater(self):
        aid = self.mgr.create(CHAT, "222", TOMORROW, movie_name="Dune",
                              theater_slug="amc-empire-25",
                              showtime_label="Fri Jul 18 · 7:00 PM")
        self._poll([_seat(7, 5, name="G5")],
                   theater_names={"amc-empire-25": "AMC Empire 25"})
        texts = [t for _, t in self.sent]
        self.assertTrue(any("AMC Empire 25" in t for t in texts), texts)
        # the alert without a theater_slug still notifies, just unnamed
        self.assertEqual(len(texts), 2)

    def test_message_omits_unknown_theater_slug(self):
        self.mgr.create(CHAT, "222", TOMORROW, movie_name="Dune",
                        theater_slug="amc-not-in-config",
                        showtime_label="Fri Jul 18 · 7:00 PM")
        self._poll([_seat(7, 5, name="G5")], theater_names={})
        for _, text in self.sent:
            self.assertNotIn("amc-not-in-config", text)
            self.assertIn("G5", text)

    def test_fire_sends_photo_when_send_photo_given(self):
        layout = SeatLayout(rows=10, columns=10, seats=[_seat(7, 5, name="G5")])
        photos = []
        poll_seat_alerts(
            self.db,
            send=lambda cid, text: self.sent.append((cid, text)) or True,
            send_photo=lambda cid, png, cap: photos.append((cid, png, cap)) or True,
            fetch=lambda sid: layout,
            sleep=lambda *_: None, delay=0,
        )
        self.assertEqual(len(photos), 1)
        self.assertTrue(photos[0][1].startswith(b"\x89PNG"))  # real PNG bytes
        self.assertIn("G5", photos[0][2])                     # caption text
        self.assertEqual(self.sent, [])                       # text path not used

    def test_unreachable_layout_skipped(self):
        stats = poll_seat_alerts(
            self.db,
            send=lambda cid, text: self.sent.append((cid, text)) or True,
            fetch=lambda sid: None,  # transient failure
            sleep=lambda *_: None, delay=0,
        )
        self.assertEqual(stats, {"checked": 1, "notified": 0, "unreachable": 1})
        self.assertEqual(self.sent, [])


class TestSeatFlow(unittest.TestCase):
    """The guided /addseatalert flow: theater → month → day → movie → showtime."""

    THEATERS = [{"name": "AMC Lincoln Square 13",
                 "slug": "amc-lincoln-square-13", "market": "new-york-city"}]

    def setUp(self):
        import logging
        from types import SimpleNamespace
        from amc_showtime_alert.telegram import TelegramBot
        from amc_showtime_alert.telegram import seat_flow
        from amc_showtime_alert.seat_alerts.listings import Showing

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = self.tmp.name
        UserManager(self.db).subscribe(CHAT, first_name="Jim")

        # Stub the live listing fetch.
        self._orig_fetch = seat_flow.fetch_showings
        seat_flow.fetch_showings = lambda market, slug, date: [
            Showing(movie="Dune", time="7:00 PM", showtime_id="999", formats=["imax"]),
            Showing(movie="Dune", time="9:30 PM", showtime_id="1000", formats=["dolby"]),
        ]
        self._seat_flow = seat_flow

        b = TelegramBot.__new__(TelegramBot)
        b.db_path = self.db
        b.logger = logging.getLogger("test")
        b._conversations = {}
        b.theaters = self.THEATERS
        b._slugs = {t["slug"] for t in self.THEATERS}
        b._name_by_slug = {t["slug"]: t["name"] for t in self.THEATERS}
        b._api = SimpleNamespace(
            send_message=lambda *a, **k: True,
            send_picker=lambda *a, **k: 101,
            edit_message=lambda *a, **k: None,
            edit_markup=lambda *a, **k: None,
            answer_callback=lambda *a, **k: None,
        )
        self.bot = b

    def tearDown(self):
        self._seat_flow.fetch_showings = self._orig_fetch
        Path(self.db).unlink(missing_ok=True)

    def _cb(self, data):
        self.bot._handle_update({"callback_query": {"id": "c", "data": data,
                                 "message": {"chat": {"id": CHAT}, "message_id": 101}}})

    def test_full_flow_creates_alert_with_id_and_date(self):
        self.bot._handle_addseatalert(CHAT, "")        # start
        self._cb("sa:th:amc-lincoln-square-13")     # theater
        self._cb("sa:m:2026-07")                    # month
        self._cb("sa:d:2026-07-18")                 # day -> fetch movies
        self._cb("sa:mv:0")                         # movie "Dune"
        self._cb("sa:st:999")                       # the 7:00 PM IMAX showing

        alerts = SeatAlertManager(self.db).list_active(CHAT)
        self.assertEqual(len(alerts), 1)
        a = alerts[0]
        self.assertEqual(a.showtime_id, "999")
        self.assertEqual(a.showtime_date, "2026-07-18")
        self.assertEqual(a.movie_name, "Dune")
        self.assertEqual(self.bot._conversations, {})  # flow cleared

    def test_back_navigation_returns_to_prior_step(self):
        self.bot._handle_addseatalert(CHAT, "")
        self._cb("sa:th:amc-lincoln-square-13")
        self._cb("sa:m:2026-07")
        self.assertEqual(self.bot._conversations[CHAT]["step"], "day")
        self._cb("sa:back")
        self.assertEqual(self.bot._conversations[CHAT]["step"], "month")
        self._cb("sa:back")
        self.assertEqual(self.bot._conversations[CHAT]["step"], "theater")

    def test_cancel_clears_flow(self):
        self.bot._handle_addseatalert(CHAT, "")
        self._cb("sa:x")
        self.assertEqual(self.bot._conversations, {})

    def test_sold_out_showing_can_be_targeted(self):
        from amc_showtime_alert.seat_alerts.listings import Showing
        self._seat_flow.fetch_showings = lambda m, s, d: [
            Showing(movie="Dune", time="7:00 PM", showtime_id="777",
                    formats=["imax"], sold_out=True),
        ]
        self.bot._handle_addseatalert(CHAT, "")
        self._cb("sa:th:amc-lincoln-square-13")
        self._cb("sa:m:2026-07")
        self._cb("sa:d:2026-07-18")
        self._cb("sa:mv:0")
        self._cb("sa:st:777")  # the sold-out show
        alerts = SeatAlertManager(self.db).list_active(CHAT)
        self.assertEqual([a.showtime_id for a in alerts], ["777"])

    def test_listseatalerts(self):
        sent = []
        self.bot._api.send_message = lambda cid, text, **k: sent.append(text) or True
        self.bot._handle_listseatalerts(CHAT)
        self.assertIn("no seat alerts", sent[-1])
        mgr = SeatAlertManager(self.db)
        mgr.create(CHAT, "111", TOMORROW, movie_name="Dune", showtime_label="Fri · 7:00 PM")
        mgr.create(CHAT, "222", TOMORROW, movie_name="Wicked", showtime_label="Sat · 8:00 PM")
        self.bot._handle_listseatalerts(CHAT)
        self.assertIn("Dune", sent[-1])
        self.assertIn("Wicked", sent[-1])


class TestListingParse(unittest.TestCase):
    """Regression: AMC wraps showtimes in an outer role=group; the wrapper must
    not masquerade as a showtime and steal the first cell's id (which dropped the
    11 PM show and duplicated the 7 AM on 7/18)."""

    HTML = """
    <section aria-label="Showtimes for The Odyssey">
      <span>IMAX 70MM</span><span>:</span><span>EXTRAORDINARY AWAITS</span>
      <span>IMAX at AMC</span><span>70mm</span>
      <div role="group">
        <ul aria-label="Showtime Group Results">
          <li><div role="group">
            <button>7:00 am <span class="sr-only">Sold Out</span></button>
            <div id="144060333-details"><span>Sold Out</span></div>
          </div></li>
          <li><div role="group">
            <a href="/showtimes/143822250">11:00 pm</a>
            <div id="143822250-details">Almost Full</div>
          </div></li>
        </ul>
      </div>
    </section>
    """

    def test_wrapper_excluded_no_dup_no_collision(self):
        from amc_showtime_alert.seat_alerts.listings import parse_showings
        shows = parse_showings(self.HTML)
        by_time = {s.time: s for s in shows}
        self.assertEqual(len(shows), 2)                       # no wrapper ghost
        self.assertEqual(len({s.showtime_id for s in shows}), 2)  # no id collision
        self.assertEqual(by_time["7:00 AM"].showtime_id, "144060333")
        self.assertTrue(by_time["7:00 AM"].sold_out)
        self.assertIn("11:00 PM", by_time)                    # 11 PM not dropped
        self.assertEqual(by_time["11:00 PM"].showtime_id, "143822250")
        self.assertFalse(by_time["11:00 PM"].sold_out)
        # IMAX 70mm block resolves to imax for both
        self.assertEqual(by_time["7:00 AM"].formats, ["imax"])
        self.assertEqual(by_time["11:00 PM"].formats, ["imax"])


if __name__ == "__main__":
    unittest.main()
