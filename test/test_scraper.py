#!/usr/bin/env python3
"""
Test suite for AMC Scraper.
Tests scraping functionality for a single date from one theater.
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.amc_scraper import AMCShowtimeScraper


class TestAMCScraper(unittest.TestCase):
    """Test cases for AMC Scraper"""

    def setUp(self):
        """Set up scraper before each test"""
        config_path = Path("config.json")
        if not config_path.exists():
            self.skipTest("config.json not found")

        self.scraper = AMCShowtimeScraper(config_path="config.json")
        self.tomorrow = (datetime.now() + timedelta(days=1)).strftime(
            '%Y-%m-%d'
        )
        self.theater = self.scraper.config['theaters'][0]

    def test_scraper_initialization(self):
        """Test that scraper initializes correctly"""
        self.assertIsNotNone(self.scraper)
        self.assertIsNotNone(self.scraper.config)
        self.assertGreater(len(self.scraper.config['theaters']), 0)

    def test_single_date_scrape(self):
        """Test scraping a single date from one theater"""
        result = self.scraper.scrape_date(self.tomorrow, self.theater)
        self.assertIsNotNone(result)
        if result.success:
            self.assertIsNotNone(result.movies)
            self.assertIsInstance(result.movies, list)
        else:
            self.assertIsNotNone(result.error_message)

    def test_scraper_stats_tracking(self):
        """Test that scraper tracks statistics correctly"""
        initial_requests = self.scraper.stats['total_requests']
        self.scraper.scrape_date(self.tomorrow, self.theater)
        self.assertGreater(
            self.scraper.stats['total_requests'],
            initial_requests
        )

    def test_movie_data_structure(self):
        """Test that scraped movie data has correct structure"""
        result = self.scraper.scrape_date(self.tomorrow, self.theater)
        if result.success and len(result.movies) > 0:
            movie = result.movies[0]
            self.assertIsNotNone(movie.name)
            self.assertIsNotNone(movie.showtimes)
            self.assertIsInstance(movie.showtimes, list)


if __name__ == "__main__":
    unittest.main()
