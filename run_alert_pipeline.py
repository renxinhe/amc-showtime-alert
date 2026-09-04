#!/usr/bin/env python3
"""
Unified AMC Alert Pipeline
Runs the complete pipeline: Scrape → Parse → Notify (with deduplication)
Designed to be run frequently without spamming users

Usage:
    # Server mode (runs continuously on a timer set in config.json):
    python run_alert_pipeline.py --server --db production.db

    # Test against a scratch database:
    python run_alert_pipeline.py --server --db test.db

    # Reuse the latest scraped JSON instead of scraping (parse/notify/match only):
    python run_alert_pipeline.py --db test.db --reuse

    # Use a custom config file:
    python run_alert_pipeline.py --db production.db --config /path/to/config.json
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
from amc_showtime_alert.telegram import TelegramNotifier, TelegramBot
from amc_showtime_alert.user_manager import UserManager
from amc_showtime_alert.alert_manager import AlertManager
from amc_showtime_alert.alert_matcher import find_alert_matches
from amc_showtime_alert.seat_alerts.manager import SeatAlertManager
from amc_showtime_alert.seat_alerts.poller import poll_seat_alerts
from amc_showtime_alert.telegram.api import TelegramAPI
from amc_showtime_alert.schema import EventData, EventType

# Path constants
DEFAULT_CONFIG_PATH = "config.json"
OUTPUT_DIR = "output"

# Filename pattern constants
SCRAPED_DATA_FILENAME_PATTERN = "amc_showtimes_{}.json"
PARSED_EVENTS_FILENAME_PATTERN = "amc_showtimes_special_{}.json"

# Display constants
LOG_SEPARATOR_WIDTH = 60

# Cleanup runs once per day regardless of other config
CLEANUP_INTERVAL_DAYS = 1


class AlertPipeline:
    """Orchestrates the complete alert pipeline with deduplication"""

    def __init__(
        self,
        db_path: str,
        config_path: str = DEFAULT_CONFIG_PATH,
        reuse_existing: bool = False,
    ):
        """
        Initialize the alert pipeline

        Args:
            config_path: Path to configuration file
            db_path: Path to notification state database
            reuse_existing: If True, reuse the most recent scraped JSON in the
                output dir instead of scraping AMC (useful for testing the
                parse/notify/match stages without hitting the network).
        """
        self.config_path = config_path
        self.db_path = db_path
        self.reuse_existing = reuse_existing
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
        """Setup logging configuration.

        Handlers are attached to the root logger so that all named loggers
        (TelegramBot, UserManager, NotificationState, etc.) inherit them via
        propagation and their output appears in stdout/the log file.
        """
        self.logger = logging.getLogger("AlertPipeline")

        # Configure the root logger — all other loggers propagate up to it.
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.handlers = []

        # Console handler
        console_handler = logging.StreamHandler()
        console_level = getattr(logging, self.config["logging"]["console_level"])
        console_handler.setLevel(console_level)
        console_format = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler.setFormatter(console_format)
        root_logger.addHandler(console_handler)

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
            root_logger.addHandler(file_handler)
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

    def _cleanup_old_files(self):
        """
        Clean up old output JSON files and old status log files based on
        configured retention periods.
        """
        server_cfg = self.config.get("server", {})
        output_retention_days = server_cfg.get("output_retention_days", 7)
        logs_retention_days = server_cfg.get("logs_retention_days", 90)

        now = time.time()

        # --- Output JSON files ---
        try:
            output_cutoff = now - (output_retention_days * 24 * 60 * 60)
            output_patterns = [
                str(self.output_dir / "amc_showtimes_*.json"),
                str(self.output_dir / "amc_showtimes_special_*.json"),
            ]

            deleted_output = 0
            for pattern in output_patterns:
                for filepath in glob_module.glob(pattern):
                    if os.stat(filepath).st_mtime < output_cutoff:
                        os.remove(filepath)
                        deleted_output += 1
                        self.logger.debug(f"Deleted old output file: {filepath}")

            if deleted_output > 0:
                self.logger.info(
                    f"🧹 Cleaned up {deleted_output} output files"
                    f" older than {output_retention_days} days"
                )
        except Exception as e:
            self.logger.error(f"Error during output file cleanup: {e}", exc_info=True)

        # --- Status log files ---
        try:
            log_dir = Path(self.config["output"]["logs_dir"])
            logs_cutoff = now - (logs_retention_days * 24 * 60 * 60)
            log_pattern = str(log_dir / "status_*.log")

            deleted_logs = 0
            for filepath in glob_module.glob(log_pattern):
                if os.stat(filepath).st_mtime < logs_cutoff:
                    os.remove(filepath)
                    deleted_logs += 1
                    self.logger.debug(f"Deleted old log file: {filepath}")

            if deleted_logs > 0:
                self.logger.info(
                    f"🧹 Cleaned up {deleted_logs} status log files"
                    f" older than {logs_retention_days} days"
                )
        except Exception as e:
            self.logger.error(f"Error during log file cleanup: {e}", exc_info=True)

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

    def _latest_scraped_file(self) -> Optional[str]:
        """
        Return the newest raw scrape JSON in the output dir, or None.

        Matches only timestamped scrape files (amc_showtimes_<digits>...json),
        so parsed/special and ad-hoc files are ignored.
        """
        pattern = str(self.output_dir / "amc_showtimes_[0-9]*.json")
        candidates = glob_module.glob(pattern)
        if not candidates:
            return None
        return max(candidates, key=os.path.getmtime)

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
            # Get Telegram credentials
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

            if not bot_token:
                self.logger.error("❌ Missing TELEGRAM_BOT_TOKEN in .env file")
                return {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}

            # Load Q&A broadcast subscribers from database
            user_mgr = UserManager(self.db_path)
            chat_ids = [str(cid) for cid in user_mgr.get_qna_subscribers()]

            if not chat_ids:
                self.logger.info(
                    "📱 No Q&A broadcast subscribers — skipping this step "
                    "(custom and seat alerts are unaffected). Users can "
                    "subscribe by sending /startqnaalert to the bot."
                )
                return {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}

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

    def run_alert_matcher(self, scraped_file: str) -> Dict[str, int]:
        """
        Match all scraped movies against users' custom alerts and notify each
        user about their own matches (independent of the global Q&A broadcast).

        Args:
            scraped_file: Path to the raw scraped showtimes JSON file

        Returns:
            Dictionary with notification statistics
        """
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("STEP 4: MATCHING CUSTOM USER ALERTS")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

        empty = {"sent": 0, "failed": 0, "skipped": 0, "updated": 0}
        try:
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            if not bot_token:
                self.logger.error("❌ Missing TELEGRAM_BOT_TOKEN in .env file")
                return empty

            alerts = AlertManager(self.db_path).get_active_alerts()
            if not alerts:
                self.logger.info("🔕 No active custom alerts — skipping")
                return empty

            with open(scraped_file, "r", encoding="utf-8") as f:
                scraped_data = json.load(f)

            theater_slug_by_name = {
                t["name"]: t["slug"]
                for t in self.config.get("theaters", [])
                if t.get("name") and t.get("slug")
            }

            matches = find_alert_matches(
                scraped_data, alerts, theater_slug_by_name
            )
            if not matches:
                self.logger.info("📱 No custom-alert matches this run")
                return empty

            notifier = TelegramNotifier()
            stats = notifier.send_alert_notifications(matches, db_path=self.db_path)
            self.logger.info("✅ Custom alert step completed")
            return stats

        except Exception as e:
            self.logger.error(f"❌ Custom alert matching failed: {e}", exc_info=True)
            return empty

    def run_seat_poller(self) -> Dict[str, int]:
        """
        Auto-expire past seat alerts (the day after the show), then poll every
        active watched showing and notify owners when a good seat opens up.
        """
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("STEP 5: SEAT ALERTS")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

        empty = {"checked": 0, "notified": 0, "unreachable": 0}
        try:
            # Soft-delete showings whose date has passed (day-after expiry).
            expired = SeatAlertManager(self.db_path).expire_past()
            if expired:
                self.logger.info(f"🧹 Expired {expired} past seat alert(s)")

            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            if not bot_token:
                self.logger.error("❌ Missing TELEGRAM_BOT_TOKEN — skipping seat poll")
                return empty

            api = TelegramAPI(bot_token)
            theater_names = {
                t["slug"]: t["name"]
                for t in self.config.get("theaters", [])
                if t.get("slug") and t.get("name")
            }
            stats = poll_seat_alerts(
                self.db_path,
                send=lambda chat_id, text: api.send_message(chat_id, text),
                send_photo=lambda chat_id, png, caption: api.send_photo(
                    chat_id, png, caption
                ),
                theater_names=theater_names,
            )
            self.logger.info(
                f"🎟 Seat alerts: checked={stats['checked']} "
                f"notified={stats['notified']} unreachable={stats['unreachable']}"
            )
            return stats
        except Exception as e:
            self.logger.error(f"❌ Seat alert step failed: {e}", exc_info=True)
            return empty

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

    def _ping_healthchecks(self, suffix: str = "") -> None:
        """
        Ping healthchecks.io to signal pipeline status.

        Args:
            suffix: URL suffix — "" for success, "/start" for run start,
                    "/fail" for failure
        """
        ping_url = os.getenv("HEALTHCHECKS_PING_URL", "").strip()
        if not ping_url:
            return
        try:
            import urllib.request
            url = ping_url.rstrip("/") + suffix
            urllib.request.urlopen(url, timeout=10)
            self.logger.debug(f"Healthchecks ping sent: {url}")
        except Exception as e:
            self.logger.warning(f"Healthchecks ping failed: {e}")

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

        # Load env early so HEALTHCHECKS_PING_URL and bot token are available
        self._load_env_file()
        self._ping_healthchecks("/start")

        try:
            # Initialize metrics
            metrics["theaters_total"] = len(self.config.get("theaters", []))

            # Step 1: Scrape (or reuse the latest existing scrape)
            if self.reuse_existing:
                scraped_file = self._latest_scraped_file()
                if scraped_file:
                    self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
                    self.logger.info(
                        f"♻️  STEP 1: REUSING EXISTING SCRAPE (skip scraping)\n"
                        f"    {scraped_file}"
                    )
                    self.logger.info("=" * LOG_SEPARATOR_WIDTH)
                else:
                    self.logger.warning(
                        "♻️  Reuse mode: no existing scrape found — scraping fresh"
                    )
                    scraped_file = self.run_scraper()
            else:
                scraped_file = self.run_scraper()

            if not scraped_file:
                self.logger.error("Pipeline failed at scraping step")
                error_msg = "Scraping failed"
                elapsed = (datetime.now() - start_time).total_seconds()
                if write_status_log:
                    self._write_status_log("FAILED", elapsed, metrics, error_msg)
                self._ping_healthchecks("/fail")
                return False

            # Count successful theaters and movies. The scrape file is
            # {"scraped_at", "stats", "results": [one entry per theater-day]}.
            try:
                with open(scraped_file, "r", encoding="utf-8") as f:
                    scraped_data = json.load(f)
                results = scraped_data.get("results", [])
                successful_theaters = set()
                for day in results:
                    if not day.get("success"):
                        continue
                    successful_theaters.add(day.get("theater"))
                    metrics["movies"] += len(day.get("movies", []))
                metrics["theaters_success"] = len(successful_theaters)
            except Exception:
                pass

            # Step 2: Parse
            parsed_file = self.run_parser(scraped_file)
            if not parsed_file:
                self.logger.error("Pipeline failed at parsing step")
                error_msg = "Parsing failed"
                elapsed = (datetime.now() - start_time).total_seconds()
                if write_status_log:
                    self._write_status_log("FAILED", elapsed, metrics, error_msg)
                self._ping_healthchecks("/fail")
                return False

            # Count events
            try:
                with open(parsed_file, "r", encoding="utf-8") as f:
                    parsed_data = json.load(f)
                    metrics["events"] = parsed_data.get("total_events", 0)
            except Exception:
                pass

            # Step 3: Notify (global Q&A broadcast, with deduplication)
            stats = self.run_notifier(parsed_file)
            metrics["sent"] = stats.get("sent", 0)
            metrics["updated"] = stats.get("updated", 0)
            metrics["skipped"] = stats.get("skipped", 0)

            # Step 4: Per-user custom alerts (additive to the global broadcast)
            alert_stats = self.run_alert_matcher(scraped_file)
            metrics["sent"] += alert_stats.get("sent", 0)
            metrics["updated"] += alert_stats.get("updated", 0)
            metrics["skipped"] += alert_stats.get("skipped", 0)

            # Step 5: Seat alerts (expire past ones, poll watched showings)
            seat_stats = self.run_seat_poller()
            metrics["sent"] += seat_stats.get("notified", 0)

            # Calculate elapsed time
            elapsed = (datetime.now() - start_time).total_seconds()

            if write_status_log:
                self._write_status_log("SUCCESS", elapsed, metrics, error_msg)
            self._ping_healthchecks()

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
            error_msg = "Interrupted by user"
            elapsed = (datetime.now() - start_time).total_seconds()
            if write_status_log:
                self._write_status_log("FAILED", elapsed, metrics, error_msg)
            self._ping_healthchecks("/fail")
            return False
        except Exception as e:
            self.logger.error(f"❌ Pipeline failed: {e}", exc_info=True)
            error_msg = str(e)[:50]
            elapsed = (datetime.now() - start_time).total_seconds()
            if write_status_log:
                self._write_status_log("FAILED", elapsed, metrics, error_msg)
            self._ping_healthchecks("/fail")
            return False

    def run_server_mode(self):
        """
        Run pipeline in server mode with scheduled execution
        Uses schedule library to run pipeline at configured intervals
        """
        server_cfg = self.config.get("server", {})
        interval_minutes = server_cfg.get("interval_minutes", 60)
        output_retention_days = server_cfg.get("output_retention_days", 7)
        logs_retention_days = server_cfg.get("logs_retention_days", 90)

        # Track server state
        run_count = 0
        shutdown_requested = False
        bot: Optional[TelegramBot] = None

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

        # Start the Telegram bot command listener
        self._load_env_file()
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if bot_token:
            bot = TelegramBot(
                bot_token=bot_token,
                db_path=self.db_path,
                theaters=self.config.get("theaters", []),
            )
            bot.start()
        else:
            self.logger.warning(
                "⚠️  TELEGRAM_BOT_TOKEN not set — bot command listener not started"
            )

        # Define scheduled job
        def run_job():
            nonlocal run_count
            run_count += 1
            self.logger.info(f"\n{'=' * LOG_SEPARATOR_WIDTH}")
            self.logger.info(f"🔄 Starting scheduled run #{run_count}")
            self.logger.info(f"{'=' * LOG_SEPARATOR_WIDTH}")
            write_status = self.config["logging"].get(
                "enable_status_file_logging", True
            )
            self.run(write_status_log=write_status)

        # Define cleanup job
        def cleanup_job():
            self.logger.info("\n🧹 Running scheduled cleanup...")
            self._cleanup_old_files()

        # Schedule the pipeline job
        schedule.every(interval_minutes).minutes.do(run_job)

        # Schedule cleanup job
        schedule.every(CLEANUP_INTERVAL_DAYS).days.do(cleanup_job)

        # Log server startup
        self.logger.info("\n" + "=" * LOG_SEPARATOR_WIDTH)
        self.logger.info("🚀 AMC ALERT PIPELINE - SERVER MODE")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)
        self.logger.info(f"⏰ Interval: Every {interval_minutes} minutes")
        self.logger.info(
            f"🧹 Cleanup: Daily"
            f" (output: {output_retention_days}d, logs: {logs_retention_days}d)"
        )
        self.logger.info(
            f"📊 Status logs: {self.config['output']['logs_dir']}/status_YYYY-WW.log"
        )
        self.logger.info(
            f"🤖 Bot listener: {'running' if bot else 'not started (missing token)'}"
        )
        self.logger.info(f"🔌 Press Ctrl+C to stop gracefully")
        self.logger.info("=" * LOG_SEPARATOR_WIDTH)

        # Run cleanup immediately on startup to handle any stale files from
        # previous runs, then run the pipeline.
        self.logger.info("\n🧹 Running startup cleanup...")
        self._cleanup_old_files()

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
        if bot:
            bot.stop()

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
        required=True,
        help="Path to the SQLite database (e.g. production.db or test.db)",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run in server mode with scheduled execution (interval from config)",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="Reuse the latest scraped JSON in the output dir instead of "
        "scraping AMC (parse/notify/match still run; useful for testing)",
    )

    args = parser.parse_args()

    # Initialize pipeline
    pipeline = AlertPipeline(
        config_path=args.config, db_path=args.db, reuse_existing=args.reuse
    )

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
