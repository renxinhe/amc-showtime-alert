#!/usr/bin/env python3
"""
Tests for premium-format resolution from a movie section's flattened token
sequence (AMCShowtimeScraper._resolve_showtime_formats), plus the
movie_format_utils helpers. These exercise the tricky block-boundary cases
(IMAX 70mm sub-labels, sold-out blocks) without needing the network.
"""

import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.amc_scraper import AMCShowtimeScraper
from amc_showtime_alert.movie_format_utils import (
    detect_format_label,
    higher_priority_format,
    normalize_format,
    resolve_block_formats,
)


def _resolver():
    """A scraper instance with just enough state to call the pure helper."""
    s = AMCShowtimeScraper.__new__(AMCShowtimeScraper)
    s.logger = logging.getLogger("test")
    return s


class TestFormatUtils(unittest.TestCase):
    def test_imax_70mm_normalizes_to_imax(self):
        self.assertEqual(normalize_format("IMAX 70MM"), "imax")
        self.assertEqual(detect_format_label("IMAX 70MM"), "imax")

    def test_bare_70mm_stays_70mm(self):
        self.assertEqual(normalize_format("70mm"), "70mm")
        self.assertEqual(detect_format_label("70mm"), "70mm")

    def test_reald_3d_normalizes_to_3d(self):
        self.assertEqual(normalize_format("RealD 3D"), "3d")
        self.assertEqual(detect_format_label("RealD 3D"), "3d")

    def test_higher_priority(self):
        self.assertEqual(higher_priority_format("imax", "70mm"), "imax")
        self.assertEqual(higher_priority_format("70mm", "imax"), "imax")
        self.assertEqual(higher_priority_format("dolby", "3d"), "dolby")


class TestResolveBlockFormats(unittest.TestCase):
    """The shared resolver used by both the scraper and the seat-alert picker."""

    def test_imax_70mm_block_classifies_as_imax(self):
        # AMC's "IMAX 70MM : tagline, IMAX at AMC, 70mm, <showtimes>" block.
        events = [
            ("fmt", "imax"), ("colon", None),   # IMAX 70MM : tagline
            ("fmt", "imax"),                     # IMAX at AMC
            ("fmt", "70mm"),                     # 70mm  (sub-label)
            ("item", "7:00 AM"),
            ("item", "11:00 AM"),
        ]
        self.assertEqual(
            resolve_block_formats(events),
            [("7:00 AM", "imax"), ("11:00 AM", "imax")],
        )

    def test_standalone_70mm_block_stays_70mm(self):
        events = [("fmt", "70mm"), ("colon", None), ("item", "8:30 AM")]
        self.assertEqual(resolve_block_formats(events), [("8:30 AM", "70mm")])

    def test_separate_blocks_keep_their_formats(self):
        events = [
            ("fmt", "imax"), ("colon", None), ("item", "7:00 AM"),
            ("fmt", "dolby"), ("colon", None), ("item", "10:00 AM"),
            ("fmt", "70mm"), ("colon", None), ("item", "8:30 AM"),
        ]
        self.assertEqual(
            resolve_block_formats(events),
            [("7:00 AM", "imax"), ("10:00 AM", "dolby"), ("8:30 AM", "70mm")],
        )

    def test_item_without_format(self):
        self.assertEqual(resolve_block_formats([("item", "1:00 PM")]), [("1:00 PM", None)])


class TestResolveShowtimeFormats(unittest.TestCase):
    def setUp(self):
        self.r = _resolver()

    def test_imax_70mm_block_resolves_to_imax(self):
        # AMC's "IMAX 70MM : tagline, IMAX at AMC, 70mm, <times>" structure.
        seq = [
            ("fmt", "imax"), ("colon", None),     # "IMAX 70MM" : tagline
            ("fmt", "imax"),                       # "IMAX at AMC" (sub-label)
            ("fmt", "70mm"),                       # "70mm" (sub-label)
            ("time", "7:00 PM"),
            ("time", "10:00 PM"),
        ]
        out = self.r._resolve_showtime_formats(seq)
        self.assertEqual(out["7:00 PM"], {"imax"})
        self.assertEqual(out["10:00 PM"], {"imax"})

    def test_sold_out_block_does_not_leak_into_next(self):
        # IMAX block has no bookable times (sold out), then a Dolby block.
        seq = [
            ("fmt", "imax"), ("colon", None),      # IMAX 70MM, sold out (no times)
            ("fmt", "imax"), ("fmt", "70mm"),
            ("fmt", "dolby"), ("colon", None),     # new Dolby block (colon-delimited)
            ("time", "3:00 PM"),
        ]
        out = self.r._resolve_showtime_formats(seq)
        self.assertEqual(out["3:00 PM"], {"dolby"})

    def test_consecutive_colon_blocks(self):
        seq = [
            ("fmt", "imax"), ("colon", None), ("time", "1:00 PM"),
            ("fmt", "dolby"), ("colon", None), ("time", "4:00 PM"),
            ("fmt", "70mm"), ("colon", None), ("time", "9:00 PM"),
        ]
        out = self.r._resolve_showtime_formats(seq)
        self.assertEqual(out["1:00 PM"], {"imax"})
        self.assertEqual(out["4:00 PM"], {"dolby"})
        self.assertEqual(out["9:00 PM"], {"70mm"})

    def test_lone_label_without_colon_then_times(self):
        # A single experience heading directly followed by its times.
        seq = [("fmt", "70mm"), ("time", "10:00 AM"), ("time", "1:00 PM")]
        out = self.r._resolve_showtime_formats(seq)
        self.assertEqual(out["10:00 AM"], {"70mm"})
        self.assertEqual(out["1:00 PM"], {"70mm"})

    def test_times_with_no_format_label(self):
        # Standard-only movie: times but no premium heading -> empty formats.
        seq = [("time", "10:00 AM"), ("time", "1:00 PM")]
        out = self.r._resolve_showtime_formats(seq)
        self.assertEqual(out["10:00 AM"], set())
        self.assertEqual(out["1:00 PM"], set())


if __name__ == "__main__":
    unittest.main()
