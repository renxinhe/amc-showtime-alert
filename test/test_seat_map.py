#!/usr/bin/env python3
"""
Tests for seat_map: eligibility filtering, the size-relative good-seat geometry,
and seatingLayout extraction from an RSC-style payload. No network.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.seat_alerts.seat_map import (
    SeatLayout,
    good_available_seats,
    is_good_seat,
    is_real_seat,
    parse_seating_layout,
)


def seat(row, column, available=True, type="CanReserve", shouldDisplay=True, name="X"):
    return {
        "available": available,
        "row": row,
        "column": column,
        "name": name,
        "type": type,
        "seatTier": "Regular",
        "shouldDisplay": shouldDisplay,
    }


class TestEligibility(unittest.TestCase):
    def test_real_seat_types_kept(self):
        for t in ("CanReserve", "LoveSeatLeft", "LoveSeatRight", "Companion"):
            self.assertTrue(is_real_seat(seat(5, 5, type=t)), t)

    def test_excluded_types(self):
        self.assertFalse(is_real_seat(seat(5, 5, type="NotASeat")))
        self.assertFalse(is_real_seat(seat(5, 5, type="Wheelchair")))

    def test_should_display_false_excluded(self):
        self.assertFalse(is_real_seat(seat(5, 5, shouldDisplay=False)))


class TestGoodSeatGeometry(unittest.TestCase):
    def test_back_60pct_and_centre_10x10(self):
        rows = cols = 10
        # Good band for 10x10: rows 5-10 (back 60%), cols 2-9 (centre 15-85%).
        self.assertTrue(is_good_seat(seat(5, 2), rows, cols))
        self.assertTrue(is_good_seat(seat(10, 9), rows, cols))
        self.assertFalse(is_good_seat(seat(4, 5), rows, cols))   # row 4 = front 40%
        self.assertFalse(is_good_seat(seat(8, 1), rows, cols))   # too far left
        self.assertFalse(is_good_seat(seat(8, 10), rows, cols))  # too far right

    def test_scales_to_theatre_size_16x28(self):
        # Real-auditorium dimensions: back rows 7-16, centre cols 5-24.
        self.assertTrue(is_good_seat(seat(7, 5), 16, 28))
        self.assertTrue(is_good_seat(seat(16, 24), 16, 28))
        self.assertFalse(is_good_seat(seat(6, 14), 16, 28))   # row 6 = front 40%
        self.assertFalse(is_good_seat(seat(12, 4), 16, 28))   # col 4 = left of centre
        self.assertFalse(is_good_seat(seat(12, 25), 16, 28))  # col 25 = right of centre

    def test_degenerate_dimensions(self):
        self.assertFalse(is_good_seat(seat(1, 1), 0, 0))


class TestGoodAvailableSeats(unittest.TestCase):
    def test_only_available_real_good_seats(self):
        layout = SeatLayout(
            rows=10,
            columns=10,
            seats=[
                seat(7, 5, available=True),                     # good + free  ✓
                seat(7, 6, available=False),                    # good but taken
                seat(7, 5, available=True, type="Wheelchair"),  # excluded type
                seat(7, 5, available=True, shouldDisplay=False),# not displayed
                seat(2, 5, available=True),                     # too front
                seat(8, 1, available=True),                     # too far left
            ],
        )
        good = good_available_seats(layout)
        self.assertEqual(len(good), 1)
        self.assertEqual((good[0]["row"], good[0]["column"]), (7, 5))


class TestParsing(unittest.TestCase):
    def test_extract_from_rsc_like_payload(self):
        layout_obj = {
            "columns": 3,
            "rows": 2,
            "error": None,
            "seats": [seat(1, 1), seat(2, 3)],
        }
        # Embed it the way the RSC stream does — surrounded by other junk.
        payload = '5:["x",{"foo":1}] 6:{"seatingLayout":' + json.dumps(layout_obj) + ',"more":true}'
        parsed = parse_seating_layout(payload)
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.rows, parsed.columns), (2, 3))
        self.assertEqual(len(parsed.seats), 2)

    def test_missing_layout_returns_none(self):
        self.assertIsNone(parse_seating_layout('{"something":"else"}'))


if __name__ == "__main__":
    unittest.main()
