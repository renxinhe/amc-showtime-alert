#!/usr/bin/env python3
"""
Unified AMC Alert Pipeline
Runs the complete pipeline: Scrape → Parse → Notify (with deduplication)
Designed to be run frequently without spamming users
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Setup path for imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "amc_showtime_alert"))

from amc_showtime_alert.amc_scraper import AMCShowtimeScraper
from amc_showtime_alert.special_events_parser import find_special_events
from amc_showtime_alert.telegram_notifier import TelegramNotifier
from amc_showtime_alert.schema import EventData, EventType

# Path constants
DEFAULT_CONFIG_PATH = "config.json"
DEFAULT_DB_PATH = "notifications.db"
OUTPUT_DIR = "output"

# Filename pattern constants
SCRAPED_DATA_FILENAME_PATTERN = "amc_showtimes_{}.json"
PARSED_EVENTS_FILENAME_PATTERN = "amc_showtimes_special_{}.json"

# Display constants
LOG_SEPARATOR_WIDTH = 60


class AlertPipeline:
    """Orchestrates the complete alert pipeline with deduplication"""

    def __init__(
        self, config_path: str = DEFAULT_CONFIG_PATH, db_path: str = DEFAULT_DB_PATH
    ):
        """
        Initialize the alert pipeline

        Args:
            config_path: Path to configuration file
            db_path: Path to notification state database
        """
        self.config_path = config_path
        self.db_path = db_path
        self.output_dir = Path(OUTPUT_DIR)
        self.output_dir.mkdir(exist_ok=True)

        # Setup logging
        self._setup_logging()

        self.logger.info("=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("AMC ALERT PIPELINE STARTING")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

    def _setup_logging(self):
        """Setup logging configuration"""
        self.logger = logging.getLogger("AlertPipeline")
        self.logger.setLevel(logging.INFO)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_format = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)

    def run_scraper(self) -> Optional[str]:
        """
        Run the AMC scraper

        Returns:
            Path to scraped data JSON file, or None if failed
        """
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("STEP 1: SCRAPING SHOWTIMES")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

        try:
            scraper = AMCShowtimeScraper(config_path=self.config_path)
            results = scraper.scrape_all_parallel()

            # Generate output filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = self.output_dir / SCRAPED_DATA_FILENAME_PATTERN.format(
                timestamp
            )

            # Save results
            scraper.save_results(results, filename=output_file.name)

            # Check if scraping was successful
            successful = sum(1 for r in results if r.success)
            total = len(results)

            self.logger.info(f"✅ Scraping completed: {successful}/{total} successful")

            if successful == 0:
                self.logger.error("❌ No successful scrapes, aborting pipeline")
                return None

            return str(output_file)

        except Exception as e:
            self.logger.error(f"❌ Scraping failed: {e}", exc_info=True)
            return None

    def run_parser(self, scraped_file: str) -> Optional[str]:
        """
        Parse special events from scraped data

        Args:
            scraped_file: Path to scraped data JSON file

        Returns:
            Path to parsed events JSON file, or None if failed
        """
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("STEP 2: PARSING SPECIAL EVENTS")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

        try:
            # Load scraped data
            with open(scraped_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Parse events
            events = find_special_events(data)

            # Generate output filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = self.output_dir / PARSED_EVENTS_FILENAME_PATTERN.format(
                timestamp
            )

            # Save parsed events
            output_data = {
                "timestamp": datetime.now().isoformat(),
                "source_file": scraped_file,
                "total_events": len(events),
                "events": events,
            }

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            self.logger.info(
                f"✅ Parsing completed: {len(events)} special events found"
            )
            self.logger.info(f"📁 Saved to: {output_file}")

            return str(output_file)

        except Exception as e:
            self.logger.error(f"❌ Parsing failed: {e}", exc_info=True)
            return None

    def run_notifier(self, parsed_file: str) -> Dict[str, int]:
        """
        Send notifications with deduplication

        Args:
            parsed_file: Path to parsed events JSON file

        Returns:
            Dictionary with notification statistics
        """
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("STEP 3: SENDING NOTIFICATIONS (WITH DEDUPLICATION)")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

        try:
            # Load environment variables
            self._load_env_file()

            # Get Telegram credentials
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_ids_str = os.getenv("TELEGRAM_CHAT_IDS")

            if not bot_token or not chat_ids_str:
                self.logger.error("❌ Missing Telegram credentials")
                self.logger.error(
                    "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS in .env file"
                )
                return {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}

            chat_ids = [cid.strip() for cid in chat_ids_str.split(",")]

            # Load parsed events
            with open(parsed_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            events_raw = data.get("events", [])

            if not events_raw:
                self.logger.info("📱 No special events found to process")
                return {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}

            # Convert dict events to EventData objects
            events = []
            for event_dict in events_raw:
                # Convert event_type string to EventType enum
                event_type_str = event_dict.get("event_type", "")
                try:
                    event_type = EventType(event_type_str)
                except ValueError:
                    self.logger.warning(
                        f"Unknown event type: {event_type_str}, skipping event"
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

            if not events:
                self.logger.info("📱 No valid events to process")
                return {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}

            # Initialize notifier
            notifier = TelegramNotifier()

            # Send notifications with deduplication
            stats = notifier.send_notifications_with_deduplication(
                events, chat_ids, db_path=self.db_path
            )

            self.logger.info("✅ Notification step completed")

            return stats

        except Exception as e:
            self.logger.error(f"❌ Notification failed: {e}", exc_info=True)
            return {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}

    def _load_env_file(self):
        """Load environment variables from .env file"""
        env_file = Path(".env")
        if env_file.exists():
            with open(env_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        value = value.strip("\"'")
                        os.environ[key] = value

    def run(self) -> bool:
        """
        Run the complete pipeline

        Returns:
            True if pipeline completed successfully, False otherwise
        """
        start_time = datetime.now()

        try:
            # Step 1: Scrape
            scraped_file = self.run_scraper()
            if not scraped_file:
                self.logger.error("Pipeline failed at scraping step")
                return False

            # Step 2: Parse
            parsed_file = self.run_parser(scraped_file)
            if not parsed_file:
                self.logger.error("Pipeline failed at parsing step")
                return False

            # Step 3: Notify (with deduplication)
            stats = self.run_notifier(parsed_file)

            # Print final summary
            elapsed = (datetime.now() - start_time).total_seconds()

            self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
            self.logger.info("PIPELINE COMPLETED SUCCESSFULLY")
            self.logger.info("=" * LOG_SEPARATOR_WIDTH)
            self.logger.info(f"⏱️  Total time: {elapsed:.1f}s")
            self.logger.info(f"📁 Scraped data: {scraped_file}")
            self.logger.info(f"📁 Parsed events: {parsed_file}")
            self.logger.info(f"📊 Notification Results:")
            self.logger.info(f"   🆕 New: {stats['sent'] - stats.get('updated', 0)}")
            self.logger.info(f"   🔄 Updated: {stats.get('updated', 0)}")
            self.logger.info(f"   ⏭️  Skipped: {stats.get('skipped', 0)}")
            self.logger.info(f"   ❌ Failed: {stats['failed']}")
            self.logger.info("=" * LOG_SEPARATOR_WIDTH)

            return True

        except KeyboardInterrupt:
            self.logger.info("\n\n⚠️  Pipeline interrupted by user")
            return False
        except Exception as e:
            self.logger.error(f"❌ Pipeline failed: {e}", exc_info=True)
            return False


def main():
    """Main entry point"""
    # Parse command line arguments
    import argparse

    parser = argparse.ArgumentParser(
        description="Run AMC Alert Pipeline with deduplication"
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to notification database (default: {DEFAULT_DB_PATH})",
    )

    args = parser.parse_args()

    # Run pipeline
    pipeline = AlertPipeline(config_path=args.config, db_path=args.db)
    success = pipeline.run()

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
