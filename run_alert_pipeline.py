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
import signal
import time
import glob as glob_module
import schedule
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

        # Load configuration
        self.config = self._load_config()

        # Setup logging
        self._setup_logging()

        self.logger.info("=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("AMC ALERT PIPELINE STARTING")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

    def _load_config(self) -> Dict:
        """Load configuration from JSON file"""
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")

    def _setup_logging(self):
        """Setup logging configuration"""
        self.logger = logging.getLogger("AlertPipeline")
        self.logger.setLevel(logging.DEBUG)

        # Remove existing handlers
        self.logger.handlers = []

        # Console handler
        console_handler = logging.StreamHandler()
        console_level = getattr(logging, self.config["logging"]["console_level"])
        console_handler.setLevel(console_level)
        console_format = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)

        # File handler (optional based on config)
        if self.config["logging"].get("enable_pipeline_file_logging", False):
            log_dir = Path(self.config["output"]["logs_dir"])
            log_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"pipeline_{timestamp}.log"

            file_handler = logging.FileHandler(log_file)
            file_level = getattr(logging, self.config["logging"]["file_level"])
            file_handler.setLevel(file_level)
            file_format = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - "
                "%(funcName)s:%(lineno)d - %(message)s"
            )
            file_handler.setFormatter(file_format)
            self.logger.addHandler(file_handler)
            self.logger.info(f"Pipeline logging to: {log_file}")

    def _write_status_log(
        self, status: str, duration: float, metrics: Dict, error_msg: str = "-"
    ):
        """
        Write simple status line to weekly log file

        Args:
            status: SUCCESS or FAILED
            duration: Elapsed time in seconds
            metrics: Dictionary with run metrics
            error_msg: Error message if failed, otherwise "-"
        """
        # Get current ISO week number for file naming
        now = datetime.now()
        year, week, _ = now.isocalendar()

        # Create logs directory if needed
        log_dir = Path(self.config["output"]["logs_dir"])
        log_dir.mkdir(exist_ok=True)

        # Status log file with weekly rotation
        status_file = log_dir / f"status_{year}-{week:02d}.log"

        # Format metrics
        theaters_str = f"Theaters:{metrics.get('theaters_success', 0)}/{metrics.get('theaters_total', 0)}"
        movies_str = f"Movies:{metrics.get('movies', 0)}"
        events_str = f"Events:{metrics.get('events', 0)}"
        sent_str = f"Sent:{metrics.get('sent', 0)}"
        updated_str = f"Updated:{metrics.get('updated', 0)}"
        skipped_str = f"Skipped:{metrics.get('skipped', 0)}"

        metrics_str = f"{theaters_str} {movies_str} {events_str} {sent_str} {updated_str} {skipped_str}"

        # Format: timestamp | status | duration | metrics | error
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"{timestamp_str} | {status:7s} | {duration:5.1f}s | {metrics_str} | {error_msg}\n"

        # Append to file
        with open(status_file, "a", encoding="utf-8") as f:
            f.write(log_line)

    def _cleanup_old_output_files(self):
        """
        Clean up old output files based on config threshold
        Removes files older than cleanup_interval_days
        """
        try:
            cleanup_days = self.config["server"].get("cleanup_interval_days", 7)
            cutoff_time = time.time() - (cleanup_days * 24 * 60 * 60)

            # Patterns for output files
            patterns = [
                str(self.output_dir / "amc_showtimes_*.json"),
                str(self.output_dir / "amc_showtimes_special_*.json"),
            ]

            deleted_count = 0
            for pattern in patterns:
                for filepath in glob_module.glob(pattern):
                    file_stat = os.stat(filepath)
                    if file_stat.st_mtime < cutoff_time:
                        os.remove(filepath)
                        deleted_count += 1
                        self.logger.debug(f"Deleted old file: {filepath}")

            if deleted_count > 0:
                self.logger.info(f"🧹 Cleaned up {deleted_count} old output files")

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}", exc_info=True)

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

    def run(self, write_status_log: bool = False) -> bool:
        """
        Run the complete pipeline

        Args:
            write_status_log: If True, write status to weekly log file (for server mode)

        Returns:
            True if pipeline completed successfully, False otherwise
        """
        start_time = datetime.now()
        metrics = {
            "theaters_success": 0,
            "theaters_total": 0,
            "movies": 0,
            "events": 0,
            "sent": 0,
            "updated": 0,
            "skipped": 0,
        }
        error_msg = "-"

        try:
            # Initialize metrics
            metrics["theaters_total"] = len(self.config.get("theaters", []))

            # Step 1: Scrape
            scraped_file = self.run_scraper()
            if not scraped_file:
                self.logger.error("Pipeline failed at scraping step")
                if write_status_log:
                    error_msg = "Scraping failed"
                    elapsed = (datetime.now() - start_time).total_seconds()
                    self._write_status_log("FAILED", elapsed, metrics, error_msg)
                return False

            # Count successful theaters and movies
            try:
                with open(scraped_file, "r", encoding="utf-8") as f:
                    scraped_data = json.load(f)
                    if isinstance(scraped_data, list):
                        metrics["theaters_success"] = sum(
                            1 for t in scraped_data if t.get("success", False)
                        )
                        for theater in scraped_data:
                            if theater.get("success") and "data" in theater:
                                for date_data in theater["data"]:
                                    metrics["movies"] += len(
                                        date_data.get("movies", [])
                                    )
            except Exception:
                pass

            # Step 2: Parse
            parsed_file = self.run_parser(scraped_file)
            if not parsed_file:
                self.logger.error("Pipeline failed at parsing step")
                if write_status_log:
                    error_msg = "Parsing failed"
                    elapsed = (datetime.now() - start_time).total_seconds()
                    self._write_status_log("FAILED", elapsed, metrics, error_msg)
                return False

            # Count events
            try:
                with open(parsed_file, "r", encoding="utf-8") as f:
                    parsed_data = json.load(f)
                    metrics["events"] = parsed_data.get("total_events", 0)
            except Exception:
                pass

            # Step 3: Notify (with deduplication)
            stats = self.run_notifier(parsed_file)
            metrics["sent"] = stats.get("sent", 0)
            metrics["updated"] = stats.get("updated", 0)
            metrics["skipped"] = stats.get("skipped", 0)

            # Calculate elapsed time
            elapsed = (datetime.now() - start_time).total_seconds()

            # Write status log if requested
            if write_status_log:
                self._write_status_log("SUCCESS", elapsed, metrics, error_msg)

            # Print final summary
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
            if write_status_log:
                error_msg = "Interrupted by user"
                elapsed = (datetime.now() - start_time).total_seconds()
                self._write_status_log("FAILED", elapsed, metrics, error_msg)
            return False
        except Exception as e:
            self.logger.error(f"❌ Pipeline failed: {e}", exc_info=True)
            if write_status_log:
                error_msg = str(e)[:50]  # Truncate long errors
                elapsed = (datetime.now() - start_time).total_seconds()
                self._write_status_log("FAILED", elapsed, metrics, error_msg)
            return False

    def run_server_mode(self):
        """
        Run pipeline in server mode with scheduled execution
        Uses schedule library to run pipeline at configured intervals
        """
        interval_minutes = self.config["server"].get("interval_minutes", 60)
        cleanup_days = self.config["server"].get("cleanup_interval_days", 7)

        # Track server state
        run_count = 0
        shutdown_requested = False

        def signal_handler(signum, frame):
            """Handle graceful shutdown on SIGINT/SIGTERM"""
            nonlocal shutdown_requested
            self.logger.info(
                "\n🛑 Shutdown signal received, stopping after current run..."
            )
            shutdown_requested = True

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Define scheduled job
        def run_job():
            nonlocal run_count
            run_count += 1
            self.logger.info(f"\n{'=' * LOG_SEPARATOR_WIDTH}")
            self.logger.info(f"🔄 Starting scheduled run #{run_count}")
            self.logger.info(f"{'=' * LOG_SEPARATOR_WIDTH}")
            # Use config to determine if status logs should be written
            write_status = self.config["logging"].get(
                "enable_status_file_logging", True
            )
            self.run(write_status_log=write_status)

        # Define cleanup job
        def cleanup_job():
            self.logger.info("\n🧹 Running scheduled cleanup...")
            self._cleanup_old_output_files()

        # Schedule the pipeline job
        schedule.every(interval_minutes).minutes.do(run_job)

        # Schedule cleanup job (runs weekly)
        schedule.every(cleanup_days).days.do(cleanup_job)

        # Log server startup
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("🚀 AMC ALERT PIPELINE - SERVER MODE")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)
        self.logger.info(f"⏰ Interval: Every {interval_minutes} minutes")
        self.logger.info(f"🧹 Cleanup: Every {cleanup_days} days")
        self.logger.info(
            f"📊 Status logs: {self.config['output']['logs_dir']}/status_YYYY-WW.log"
        )
        self.logger.info(f"🔌 Press Ctrl+C to stop gracefully")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

        # Run immediately on startup
        self.logger.info("\n🎬 Running initial pipeline execution...")
        run_job()

        # Main server loop
        self.logger.info(f"\n⏳ Next run scheduled in {interval_minutes} minutes...")

        while not shutdown_requested:
            schedule.run_pending()
            time.sleep(1)  # Sleep for 1 second to avoid busy-wait

            # Check if we just completed a run
            if run_count > 0 and schedule.idle_seconds() is not None:
                next_run = schedule.idle_seconds()
                if next_run > 0 and next_run < interval_minutes * 60:
                    # Only log next run time once after each completion
                    pass

        # Shutdown
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("👋 Server shutting down gracefully")
        self.logger.info(f"📊 Total runs completed: {run_count}")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)


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
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run in server mode with scheduled execution (interval from config)",
    )

    args = parser.parse_args()

    # Initialize pipeline
    pipeline = AlertPipeline(config_path=args.config, db_path=args.db)

    # Run in appropriate mode
    if args.server:
        # Server mode - runs continuously with scheduling
        pipeline.run_server_mode()
        sys.exit(0)
    else:
        # Single run mode
        success = pipeline.run()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
