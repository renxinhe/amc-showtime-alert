#!/usr/bin/env python3
"""
Test suite for NotificationState manager.
Tests the core functionality of notification deduplication and state tracking.
"""

import sys
import unittest
import logging
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.schema import EventData, EventType
from amc_showtime_alert.notification_state import NotificationState


class TestNotificationState(unittest.TestCase):
    """Test cases for NotificationState manager"""

    @classmethod
    def setUpClass(cls):
        """Set up logging once for all tests"""
        logging.basicConfig(level=logging.ERROR)

    def setUp(self):
        """Set up test database before each test"""
        self.db_path = "test_notifications.db"
        self.state = NotificationState(self.db_path)
        self.test_event = EventData(
            movie_name='Test Movie Q&A',
            theater='AMC Lincoln Square 13',
            date='2025-11-06',
            slug='test-movie-q-a',
            event_type=EventType.QA,
            showtimes=['7:00 PM', '9:30 PM'],
            runtime=120,
            rating='R'
        )

    def tearDown(self):
        """Clean up test database after each test"""
        Path(self.db_path).unlink(missing_ok=True)

    def test_new_event_detection(self):
        """Test that new events are detected correctly"""
        should_notify, changes = self.state.should_notify(self.test_event)
        self.assertTrue(should_notify)
        self.assertIsNone(changes)

    def test_unchanged_event_detection(self):
        """Test that unchanged events are not re-notified"""
        self.state.mark_as_notified(self.test_event)
        should_notify, changes = self.state.should_notify(self.test_event)
        self.assertFalse(should_notify)
        self.assertIsNone(changes)

    def test_showtime_change_detection(self):
        """Test that showtime changes are detected correctly"""
        self.state.mark_as_notified(self.test_event)
        updated_event = replace(self.test_event, showtimes=['7:00 PM', '9:30 PM', '11:00 PM'])
        should_notify, changes = self.state.should_notify(updated_event)
        self.assertTrue(should_notify)
        self.assertIsNotNone(changes)
        self.assertEqual(changes.added, ['11:00 PM'])
        self.assertEqual(changes.removed, [])
        self.assertEqual(set(changes.unchanged), {'7:00 PM', '9:30 PM'})

    def test_statistics(self):
        """Test statistics collection"""
        self.state.mark_as_notified(self.test_event)
        stats = self.state.get_statistics()
        self.assertEqual(stats['total_records'], 1)
        self.assertEqual(stats['by_event_type'].get('Q&A', 0), 1)

    def test_event_history(self):
        """Test event history tracking"""
        self.state.mark_as_notified(self.test_event)
        updated_event = replace(self.test_event, showtimes=['7:00 PM', '9:30 PM', '11:00 PM'])
        self.state.mark_as_notified(updated_event, is_update=True)
        history = self.state.get_event_history(updated_event)
        self.assertIsNotNone(history)
        self.assertEqual(history['notification_count'], 2)

    def test_cleanup_old_entries(self):
        """Test cleanup of old entries"""
        self.state.mark_as_notified(self.test_event)
        deleted = self.state.cleanup_old_entries(days=30)
        self.assertEqual(deleted, 0)


if __name__ == "__main__":
    unittest.main()
