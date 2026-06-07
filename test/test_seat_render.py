#!/usr/bin/env python3
"""Tests for the seat-map PNG renderer."""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.seat_alerts.render import render_seat_map
from amc_showtime_alert.seat_alerts.seat_map import SeatLayout

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _seat(row, column, available=True, type="CanReserve", name="X"):
    return {"available": available, "row": row, "column": column, "name": name,
            "type": type, "seatTier": "Regular", "shouldDisplay": True}


class TestRenderSeatMap(unittest.TestCase):
    def _layout(self):
        return SeatLayout(rows=10, columns=10, seats=[
            _seat(7, 5, available=True, name="G5"),    # good + free
            _seat(2, 5, available=True, name="A5"),    # free, not good
            _seat(8, 6, available=False, name="H6"),   # taken
            _seat(1, 1, type="NotASeat", name=""),     # gap (omitted)
        ])

    def test_returns_valid_png(self):
        png = render_seat_map(self._layout(), highlight=["G5"])
        self.assertTrue(png.startswith(PNG_MAGIC))
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        self.assertEqual(im.format, "PNG")
        self.assertGreater(im.width, 0)
        self.assertGreater(im.height, 0)

    def test_handles_empty_and_no_highlight(self):
        self.assertTrue(render_seat_map(self._layout()).startswith(PNG_MAGIC))
        empty = SeatLayout(rows=0, columns=0, seats=[])
        self.assertTrue(render_seat_map(empty).startswith(PNG_MAGIC))


if __name__ == "__main__":
    unittest.main()
