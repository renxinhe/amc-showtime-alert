#!/usr/bin/env python3
"""
Test suite for Notification Deduplication System
Tests the NotificationState manager and end-to-end deduplication logic
"""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from amc_showtime_alert.schema import EventData, EventType
from amc_showtime_alert.notification_state import NotificationState
from amc_showtime_alert.telegram_notifier import TelegramNotifier


class TestDeduplicationLogic(unittest.TestCase):
    """Test cases for notification deduplication logic"""

    def setUp(self):
        """Set up test database before each test"""
        self.db_path = "test_dedup.db"
        Path(self.db_path).unlink(missing_ok=True)
        self.state = NotificationState(self.db_path)
        self.test_event = EventData(
            movie_name='Test Movie Live Q&A with Cast',
            theater='AMC Lincoln Square 13',
            date='2025-11-06',
            slug='test-movie-live-q-a-with-cast',
            event_type=EventType.QA,
            showtimes=['7:00 PM', '9:30 PM'],
            runtime=120,
            rating='R'
        )

    def tearDown(self):
        """Clean up test database after each test"""
        Path(self.db_path).unlink(missing_ok=True)

    def test_new_event_should_notify(self):
        """Test that new events trigger notifications"""
        should_notify, changes = self.state.should_notify(self.test_event)
        self.assertTrue(should_notify)
        self.assertIsNone(changes)

    def test_unchanged_event_should_not_notify(self):
        """Test that unchanged events do not trigger notifications"""
        self.state.mark_as_notified(self.test_event)
        should_notify, changes = self.state.should_notify(self.test_event)
        self.assertFalse(should_notify)
        self.assertIsNone(changes)

    def test_added_showtimes_detected(self):
        """Test that added showtimes are detected correctly"""
        self.state.mark_as_notified(self.test_event)
        updated_event = replace(self.test_event, showtimes=['7:00 PM', '9:30 PM', '11:00 PM'])
        should_notify, changes = self.state.should_notify(updated_event)
        self.assertTrue(should_notify)
        self.assertIsNotNone(changes)
        self.assertEqual(len(changes.added), 1)
        self.assertIn('11:00 PM', changes.added)
        self.assertEqual(len(changes.removed), 0)
        self.assertEqual(len(changes.unchanged), 2)

    def test_removed_showtimes_detected(self):
        """Test that removed showtimes are detected correctly"""
        event_with_three = replace(self.test_event, showtimes=['7:00 PM', '9:30 PM', '11:00 PM'])
        self.state.mark_as_notified(event_with_three)
        updated_event = replace(event_with_three, showtimes=['7:00 PM'])
        should_notify, changes = self.state.should_notify(updated_event)
        self.assertTrue(should_notify)
        self.assertIsNotNone(changes)
        self.assertEqual(len(changes.removed), 2)
        self.assertIn('9:30 PM', changes.removed)
        self.assertIn('11:00 PM', changes.removed)
        self.assertEqual(len(changes.unchanged), 1)

    def test_database_statistics(self):
        """Test that database statistics are tracked correctly"""
        self.state.mark_as_notified(self.test_event)
        stats = self.state.get_statistics()
        self.assertGreaterEqual(stats['total_records'], 1)

    def test_cleanup_future_events(self):
        """Test that future events are not deleted during cleanup"""
        self.state.mark_as_notified(self.test_event)
        deleted = self.state.cleanup_old_entries(days=30)
        self.assertEqual(deleted, 0)


class MockTelegramNotifier(TelegramNotifier):
    """Mock TelegramNotifier for testing without actual API calls"""

    def __init__(self):
        self.messages_sent = []
        self.bot_token = "test_token"
        self.chat_ids = "test_chat"

    def _test_bot_connection(self):
        return True

    def send_message(self, message, chat_id):
        self.messages_sent.append((message, chat_id))
        return True


class TestEndToEndDeduplication(unittest.TestCase):
    """Test cases for end-to-end notification deduplication"""

    def setUp(self):
        """Set up test database and mock events before each test"""
        self.db_path = "test_e2e_dedup.db"
        Path(self.db_path).unlink(missing_ok=True)
        self.events = [
            EventData(
                movie_name='Die My Love Live Q&A',
                theater='AMC Lincoln Square 13',
                date='2025-11-06',
                slug='die-my-love-live-q-a',
                event_type=EventType.QA,
                showtimes=['7:00 PM'],
                runtime=130,
                rating='R'
            ),
            EventData(
                movie_name='Another Movie Q&A',
                theater='AMC Empire 25',
                date='2025-11-07',
                slug='another-movie-q-a',
                event_type=EventType.QA,
                showtimes=['8:00 PM', '10:00 PM'],
                runtime=120,
                rating='PG-13'
            )
        ]

    def tearDown(self):
        """Clean up test database after each test"""
        Path(self.db_path).unlink(missing_ok=True)

    def test_first_run_sends_all_new_events(self):
        """Test that first run sends all new events"""
        notifier = MockTelegramNotifier()
        stats = notifier.send_notifications_with_deduplication(
            self.events, ['test_chat'], db_path=self.db_path
        )
        self.assertEqual(stats['sent'], 2)
        self.assertEqual(stats['skipped'], 0)
        self.assertEqual(stats['updated'], 0)

    def test_second_run_skips_unchanged_events(self):
        """Test that second run with unchanged events skips all"""
        notifier1 = MockTelegramNotifier()
        notifier1.send_notifications_with_deduplication(
            self.events, ['test_chat'], db_path=self.db_path
        )
        notifier2 = MockTelegramNotifier()
        stats = notifier2.send_notifications_with_deduplication(
            self.events, ['test_chat'], db_path=self.db_path
        )
        self.assertEqual(stats['sent'], 0)
        self.assertEqual(stats['skipped'], 2)
        self.assertEqual(stats['updated'], 0)

    def test_updated_event_sends_notification(self):
        """Test that updated events are detected and sent"""
        notifier1 = MockTelegramNotifier()
        notifier1.send_notifications_with_deduplication(
            self.events, ['test_chat'], db_path=self.db_path
        )
        # Update first event with new showtimes
        updated_events = [
            replace(self.events[0], showtimes=['7:00 PM', '9:30 PM']),
            self.events[1]
        ]
        notifier2 = MockTelegramNotifier()
        stats = notifier2.send_notifications_with_deduplication(
            updated_events, ['test_chat'], db_path=self.db_path
        )
        self.assertEqual(stats['sent'], 1)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(stats['updated'], 1)
        self.assertEqual(len(notifier2.messages_sent), 1)


if __name__ == "__main__":
    unittest.main()
