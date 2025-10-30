#!/usr/bin/env python3
"""Telegram Bot Notifier for AMC Q&A Events"""

from datetime import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("❌ Error: requests library not installed")
    print("Install with: pip install requests")
    sys.exit(1)


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

    def _test_bot_connection(self) -> bool:
        """Test if bot token is valid and bot is accessible"""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
            response = requests.get(url, timeout=10)
            return bool(response.ok)
        except Exception:
            return False

    def send_message(
        self,
        message: str,
        chat_id: str,
    ) -> bool:
        """Send message via Telegram Bot API"""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        # Escape markdown special characters
        message = self._escape_markdown_v2(message)

        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=30)
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

    def format_all_events_summary(self, events: list[dict[str, Any]]) -> str:
        """Format all Q&A events into a single comprehensive Telegram message"""
        if not events:
            return "📱 No Q&A events found"

        # Group events by type for better organization
        events_by_type: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            event_type = event["event_type"]
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
                movie_name = event["movie_name"]
                theater = event["theater"]
                date = event["date"]
                showtimes = ", ".join(event["showtimes"])
                runtime = event.get("runtime")
                rating = event.get("rating", "")

                # Format runtime and rating
                runtime_str = f"_{runtime}min_" if runtime else ""
                rating_str = f"[{rating}]" if rating else ""

                message += (
                    f"• *{movie_name}*\n"
                    f"  📍 {theater} - {date}\n"
                    f"  ⏳ {runtime_str} {rating_str}\n"
                    f"  ⏰ {showtimes}\n\n"
                )

        # Check if message is too long (Telegram limit is 4096 characters)
        if len(message) > 4096:
            truncated_message = "... (message truncated)"
            message = message[: 4096 - len(truncated_message) - 1]
            message += f"\n{truncated_message}"

        return message.strip()

    def filter_qa_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter events to only include Q&A type events"""
        qa_events = [event for event in events if event.get("event_type") == "Q&A"]
        print(f"🔍 Filtered {len(events)} events to {len(qa_events)} Q&A events")
        return qa_events

    def send_special_events_notifications(
        self,
        events: list[dict[str, Any]],
        chat_ids: list[str],
    ) -> dict[str, int]:
        """Send Telegram notifications for Q&A events only"""
        qa_events = self.filter_qa_events(events)

        if not qa_events:
            print("📱 No Q&A events to notify about")
            return {"sent": 0, "failed": 0}

        print(
            f"📱 Sending notifications for {len(qa_events)} Q&A events "
            f"to {len(chat_ids)} chat(s)..."
        )

        # Create comprehensive summary message for Q&A events only
        summary_message = self.format_all_events_summary(qa_events)

        sent_count = 0
        failed_count = 0

        # Send one message to each chat
        for chat_id in chat_ids:
            if self.send_message(summary_message, chat_id):
                sent_count += 1
            else:
                failed_count += 1

            # Rate limiting - wait between messages
            time.sleep(0.5)

        return {"sent": sent_count, "failed": failed_count}


def load_special_events(json_file: str) -> list[dict[str, Any]]:
    """Load special events from JSON file"""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        events = data.get("events", [])
        print(f"📁 Loaded {len(events)} special events from {json_file}")
        return events if isinstance(events, list) else []
    except FileNotFoundError:
        print(f"❌ Error: File not found: {json_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in file {json_file}: {e}")
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

        qa_events = [event for event in events if event.get("event_type") == "Q&A"]
        if not qa_events:
            print("📱 No Q&A events found to notify about")
            sys.exit(0)

        # Initialize Telegram notifier
        notifier = TelegramNotifier()

        # Send notifications
        print("📱 Sending Telegram notifications for Q&A events...")
        results = notifier.send_special_events_notifications(qa_events, chat_ids)

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
