#!/usr/bin/env python3
"""Telegram Bot Notifier for AMC Q&A Events"""

from datetime import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Tuple

try:
    import requests
except ImportError:
    print("❌ Error: requests library not installed")
    print("Install with: pip install requests")
    sys.exit(1)

# Import schema and notification state manager
from .schema import EventData, EventType, ShowtimeChange
from .notification_state import NotificationState

# Telegram API constants
TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_PARSE_MODE = "MarkdownV2"
TELEGRAM_MESSAGE_CHAR_LIMIT = 4096
MESSAGE_TRUNCATION_SUFFIX = "... (message truncated)"

# Timeout constants (seconds)
BOT_CONNECTION_TIMEOUT_SECONDS = 10
SEND_MESSAGE_TIMEOUT_SECONDS = 30
RATE_LIMIT_DELAY_SECONDS = 0.5

# Retention constants
DEFAULT_RETENTION_DAYS = 30


class TelegramNotifier:
    """Telegram notification service using Telegram Bot API"""

    def __init__(self) -> None:
        """Initialize Telegram notifier with bot credentials"""
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_ids = os.getenv("TELEGRAM_CHAT_IDS")

        # Validate credentials
        if not all([self.bot_token, self.chat_ids]):
            print("❌ Error: Missing Telegram credentials")
            print("Set environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS")
            sys.exit(1)

        # Test bot connection
        if not self._test_bot_connection():
            print("❌ Error: Could not connect to Telegram bot")
            print("Check your bot token and internet connection")
            sys.exit(1)

        print("✅ Telegram bot initialized successfully")

    def send_notifications(
        self,
        events: list[EventData],
        chat_ids: list[str],
    ) -> dict[str, int]:
        """Send Telegram notifications for Q&A events only"""
        qa_events = self._filter_qa_events(events)

        if not qa_events:
            print("📱 No Q&A events to notify about")
            return {"sent": 0, "failed": 0}

        print(
            f"📱 Sending notifications for {len(qa_events)} Q&A events "
            f"to {len(chat_ids)} chat(s)..."
        )

        # Create comprehensive summary message for Q&A events only
        summary_message = self._format_all_events_summary(qa_events)

        sent_count = 0
        failed_count = 0

        # Send one message to each chat
        for chat_id in chat_ids:
            if self._send_message(summary_message, chat_id):
                sent_count += 1
            else:
                failed_count += 1

            # Rate limiting - wait between messages
            time.sleep(RATE_LIMIT_DELAY_SECONDS)

        return {"sent": sent_count, "failed": failed_count}

    def send_notifications_with_deduplication(
        self,
        events: list[EventData],
        chat_ids: list[str],
        db_path: str = "notifications.db",
    ) -> dict[str, int]:
        """
        Send notifications with smart deduplication using NotificationState

        Args:
            events: List of all special events
            chat_ids: List of Telegram chat IDs
            db_path: Path to notification state database

        Returns:
            Dictionary with statistics: sent, failed, skipped, updated
        """
        # Filter to Q&A events only
        qa_events = self._filter_qa_events(events)

        if not qa_events:
            print("📱 No Q&A events to process")
            return {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}

        print(f"🔍 Checking {len(qa_events)} Q&A events for notifications...")

        # Initialize notification state
        state = NotificationState(db_path)

        # Track statistics
        stats = {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}
        events_to_notify = []

        # Check each event against notification state
        for event in qa_events:
            should_notify, changes = state.should_notify(event)

            if not should_notify:
                stats["skipped"] += 1
                print(f"⏭️  Skipping (already notified): {event.movie_name}")
                continue

            if changes:
                # This is an update
                stats["updated"] += 1
                message = self._format_update_message(event, changes)
                print(f"🔄 Update detected: {event.movie_name}")
            else:
                # This is a new event
                message = self._format_new_event_message(event)
                print(f"🆕 New event: {event.movie_name}")

            events_to_notify.append((event, message, changes is not None))

        # If no events to notify, cleanup and return
        if not events_to_notify:
            print("📱 No new or updated events to notify about")
            deleted = state.cleanup_old_entries(days=DEFAULT_RETENTION_DAYS)
            if deleted > 0:
                print(f"🧹 Cleaned up {deleted} old notification records")
            return stats

        print(
            f"\n📱 Sending {len(events_to_notify)} notifications to {len(chat_ids)} chat(s)..."
        )

        # Send notifications
        for event, message, is_update in events_to_notify:
            success = True

            # Send to all chat IDs
            for chat_id in chat_ids:
                if self._send_message(message, chat_id):
                    # Only count as sent once per event (not per chat)
                    pass
                else:
                    success = False
                    stats["failed"] += 1

                # Rate limiting
                time.sleep(RATE_LIMIT_DELAY_SECONDS)

            # Mark as notified if sent successfully to at least one chat
            if success:
                state.mark_as_notified(event, is_update=is_update)
                stats["sent"] += 1

        # Cleanup old entries
        deleted = state.cleanup_old_entries(days=DEFAULT_RETENTION_DAYS)
        if deleted > 0:
            print(f"🧹 Cleaned up {deleted} old notification records")

        # Print statistics
        db_stats = state.get_statistics()
        print(f"\n📊 Notification Statistics:")
        print(f"   🆕 New events sent: {stats['sent'] - stats['updated']}")
        print(f"   🔄 Updated events sent: {stats['updated']}")
        print(f"   ⏭️  Events skipped: {stats['skipped']}")
        print(f"   ❌ Failures: {stats['failed']}")
        print(f"\n📚 Database Statistics:")
        print(f"   Total tracked: {db_stats.get('total_records', 0)}")
        print(f"   Upcoming events: {db_stats.get('upcoming_events', 0)}")

        return stats

    def _test_bot_connection(self) -> bool:
        """Test if bot token is valid and bot is accessible"""
        try:
            url = f"{TELEGRAM_API_BASE_URL}/bot{self.bot_token}/getMe"
            response = requests.get(url, timeout=BOT_CONNECTION_TIMEOUT_SECONDS)
            return bool(response.ok)
        except Exception:
            return False

    def _send_message(
        self,
        message: str,
        chat_id: str,
    ) -> bool:
        """Send message via Telegram Bot API"""
        url = f"{TELEGRAM_API_BASE_URL}/bot{self.bot_token}/sendMessage"

        # Escape markdown special characters
        message = self._escape_markdown_v2(message)

        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": TELEGRAM_PARSE_MODE,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(
                url, json=payload, timeout=SEND_MESSAGE_TIMEOUT_SECONDS
            )
            if response.status_code == 200:
                print("✅ Telegram message sent successfully")
                return True
            else:
                print(
                    f"❌ Error sending Telegram message: "
                    f"{response.status_code} - {response.text}"
                )
                return False
        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending Telegram message: {e}")
            return False

    def _escape_markdown_v2(self, text: str) -> str:
        """Escape special characters for MarkdownV2 parsing"""
        # Characters that need escaping in MarkdownV2
        # According to https://core.telegram.org/bots/api#formatting-options
        escape_chars = [
            "[",
            "]",
            "(",
            ")",
            "~",
            "`",
            ">",
            "#",
            "+",
            "-",
            "=",
            "|",
            "{",
            "}",
            ".",
            "!",
        ]
        for char in escape_chars:
            text = text.replace(char, f"\\{char}")
        return text

    def _filter_qa_events(self, events: list[EventData]) -> list[EventData]:
        """Filter events to only include Q&A type events"""
        qa_events = [event for event in events if event.event_type == EventType.QA]
        print(f"🔍 Filtered {len(events)} events to {len(qa_events)} Q&A events")
        return qa_events

    def _format_all_events_summary(self, events: list[EventData]) -> str:
        """Format all Q&A events into a single comprehensive Telegram message"""
        if not events:
            return "📱 No Q&A events found"

        # Group events by type for better organization
        events_by_type: dict[str, list[EventData]] = {}
        for event in events:
            event_type = event.event_type
            if event_type not in events_by_type:
                events_by_type[event_type] = []
            events_by_type[event_type].append(event)

        # Create summary header
        total_events = len(events)
        now = datetime.now().astimezone()
        message = (
            f"🎬 *AMC Q&A Events Summary*\n"
            f"*{now.strftime('%Y-%m-%d %H:%M:%S %Z')}*\n"
            f"_{total_events} Q&A events found_\n\n"
        )

        # Add each event type section
        for event_type, type_events in events_by_type.items():
            message += f"🎭 *{event_type}* _{len(type_events)} events_:\n"
            for event in type_events:
                movie_name = event.movie_name
                theater = event.theater
                date = event.date
                showtimes = ", ".join(event.showtimes)
                runtime = event.runtime
                rating = event.rating

                # Format runtime and rating
                runtime_str = f"_{runtime}min_" if runtime else ""
                rating_str = f"[{rating}]" if rating else ""

                message += (
                    f"• *{movie_name}*\n"
                    f"  📍 {theater} - {date}\n"
                    f"  ⏳ {runtime_str} {rating_str}\n"
                    f"  ⏰ {showtimes}\n\n"
                )

        # Check if message is too long (Telegram character limit)
        if len(message) > TELEGRAM_MESSAGE_CHAR_LIMIT:
            message = message[
                : TELEGRAM_MESSAGE_CHAR_LIMIT - len(MESSAGE_TRUNCATION_SUFFIX) - 1
            ]
            message += f"\n{MESSAGE_TRUNCATION_SUFFIX}"

        return message.strip()

    def _format_update_message(self, event: EventData, changes: ShowtimeChange) -> str:
        """Format an update notification message with showtime changes"""

        movie_name = event.movie_name
        theater = event.theater
        date = event.date
        runtime = event.runtime
        rating = event.rating

        # Format runtime and rating
        runtime_str = f"_{runtime}min_" if runtime else ""
        rating_str = f"[{rating}]" if rating else ""

        now = datetime.now().astimezone()

        message = (
            f"🔔 *Updated Q&A Event*\n"
            f"*{now.strftime('%Y-%m-%d %H:%M:%S %Z')}*\n\n"
            f"🎬 *{movie_name}*\n"
            f"📍 {theater}\n"
            f"📅 {date}\n"
            f"⏳ {runtime_str} {rating_str}\n\n"
        )

        # Show added showtimes
        if changes.added:
            message += "✅ *New showtimes:*\n"
            for time in changes.added:
                message += f"  ⏰ {time}\n"

        # Show removed showtimes
        if changes.removed:
            message += "\n❌ *Removed showtimes:*\n"
            for time in changes.removed:
                message += f"  ⏰ {time}\n"

        # Show unchanged showtimes
        if changes.unchanged:
            message += "\n📌 *Still available:*\n"
            for time in changes.unchanged:
                message += f"  ⏰ {time}\n"

        return message.strip()

    def _format_new_event_message(self, event: EventData) -> str:
        """Format a new event notification message"""

        movie_name = event.movie_name
        theater = event.theater
        date = event.date
        showtimes = ", ".join(event.showtimes)
        runtime = event.runtime
        rating = event.rating

        # Format runtime and rating
        runtime_str = f"_{runtime}min_" if runtime else ""
        rating_str = f"[{rating}]" if rating else ""

        now = datetime.now().astimezone()

        message = (
            f"🎬 *New Q&A Event!*\n"
            f"*{now.strftime('%Y-%m-%d %H:%M:%S %Z')}*\n\n"
            f"*{movie_name}*\n"
            f"📍 {theater}\n"
            f"📅 {date}\n"
            f"⏳ {runtime_str} {rating_str}\n"
            f"⏰ {showtimes}\n"
        )

        return message.strip()


def load_special_events(json_file: str) -> list[EventData]:
    """Load special events from JSON file and parse into EventData objects"""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        events_raw = data.get("events", [])
        if not isinstance(events_raw, list):
            events_raw = []

        # Parse raw dicts into EventData objects
        events = []
        for event_dict in events_raw:
            # Convert event_type string to EventType enum
            event_type_str = event_dict.get("event_type", "")
            try:
                event_type = EventType(event_type_str)
            except ValueError:
                print(
                    f"⚠️  Warning: Unknown event type '{event_type_str}', skipping event"
                )
                continue

            events.append(
                EventData(
                    movie_name=event_dict["movie_name"],
                    theater=event_dict["theater"],
                    date=event_dict["date"],
                    slug=event_dict["slug"],
                    event_type=event_type,
                    showtimes=event_dict["showtimes"],
                    runtime=event_dict.get("runtime"),
                    rating=event_dict.get("rating", ""),
                )
            )

        print(f"📁 Loaded {len(events)} special events from {json_file}")
        return events
    except FileNotFoundError:
        print(f"❌ Error: File not found: {json_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in file {json_file}: {e}")
        sys.exit(1)
    except TypeError as e:
        print(f"❌ Error: Invalid event data format in {json_file}: {e}")
        sys.exit(1)


def load_env_file() -> None:
    """Load environment variables from .env file if it exists"""
    env_file = Path(".env")
    if env_file.exists():
        try:
            with open(env_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        value = value.strip("\"'")
                        os.environ[key] = value
        except Exception as e:
            print(f"⚠️  Warning: Could not load .env file: {e}")
    else:
        print("📁 No .env file found, using system environment variables")


def main() -> None:
    """Main entry point for Telegram notifications"""
    if len(sys.argv) != 2:
        print("Usage: python telegram_notifier.py <special_events_json_file>")
        sys.exit(1)

    json_file = sys.argv[1]
    load_env_file()

    try:
        # Load special events
        events = load_special_events(json_file)

        if not events:
            print("📱 No special events found to notify about")
            sys.exit(0)

        chat_ids = os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
        if not chat_ids:
            print("❌ Error: TELEGRAM_CHAT_IDS not found")
            print("Please set your chat IDs in the .env file")
            sys.exit(1)

        qa_events = [event for event in events if event.event_type == EventType.QA]
        if not qa_events:
            print("📱 No Q&A events found to notify about")
            sys.exit(0)

        # Initialize Telegram notifier
        notifier = TelegramNotifier()

        # Send notifications
        print("📱 Sending Telegram notifications for Q&A events...")
        results = notifier.send_notifications(qa_events, chat_ids)

        # Print summary
        print("\n📊 Notification Summary:")
        print(f"   ✅ Messages sent: {results['sent']}")
        print(f"   ❌ Messages failed: {results['failed']}")

        if results["failed"] > 0:
            print(
                "\n⚠️  Some messages failed. Check your bot token, chat ID, "
                "and internet connection."
            )

    except KeyboardInterrupt:
        print("\n\n📱 Telegram notifications cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
