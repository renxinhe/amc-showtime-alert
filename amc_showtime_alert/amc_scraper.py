#!/usr/bin/env python3
"""
Robust AMC Theatres Showtime Scraper
Scrapes movie showtimes from AMC theatres in NYC using HTML parsing
with comprehensive error handling, retry logic, and validation.
"""

import requests
import re
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import asdict
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from .schema import DailyShowtimes, Movie


class AMCShowtimeScraper:
    """
    Robust scraper for AMC theatre showtimes with:
    - Retry logic with exponential backoff
    - Multiple regex pattern fallbacks
    - Comprehensive error handling
    - Data validation
    - Detailed logging
    """

    # Known theater names to exclude from movie matches
    THEATER_KEYWORDS = ["AMC", "IMAX", "Dolby", "Prime", "Empire", "Lincoln", "Square"]

    # Validation constants
    MIN_MOVIES_PER_DAY = 1
    WARN_IF_NO_SHOWTIMES = True
    VALIDATE_TIME_FORMAT = True

    def __init__(self, config_path: str = "config.json"):
        """Initialize scraper with configuration"""
        self.config = self._load_config(config_path)
        self._setup_logging()
        self.base_url = "https://www.amctheatres.com"
        self.graph_url = "https://graph.amctheatres.com/v1/graphql"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " "AppleWebKit/537.36"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": "https://www.amctheatres.com/",
        }
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_movies_found": 0,
            "parsing_errors": 0,
        }
        # Thread-safe lock for statistics updates
        self._stats_lock = threading.Lock()

        # Create output directories
        self._create_directories()

        self.logger.info("AMC Scraper initialized successfully")

    def _update_stats(self, **kwargs):
        """Thread-safe method to update statistics"""
        with self._stats_lock:
            for key, value in kwargs.items():
                if key in self.stats:
                    self.stats[key] += value

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from JSON file"""
        try:
            with open(config_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")

    def _setup_logging(self):
        """Setup logging with both file and console handlers"""
        # Create logger
        self.logger = logging.getLogger("AMC_Scraper")
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
        if self.config["logging"].get("enable_scraper_file_logging", False):
            log_dir = Path(self.config["output"]["logs_dir"])
            log_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"scraper_{timestamp}.log"

            file_handler = logging.FileHandler(log_file)
            file_level = getattr(logging, self.config["logging"]["file_level"])
            file_handler.setLevel(file_level)
            file_format = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - "
                "%(funcName)s:%(lineno)d - %(message)s"
            )
            file_handler.setFormatter(file_format)
            self.logger.addHandler(file_handler)

            self.logger.info(f"Scraper logging to: {log_file}")

    def _create_directories(self):
        """Create necessary output directories"""
        dirs = [
            self.config["output"]["output_dir"],
            self.config["output"]["logs_dir"],
            Path(self.config["output"]["logs_dir"]) / "raw_responses",
        ]
        for directory in dirs:
            Path(directory).mkdir(parents=True, exist_ok=True)

    def _fetch_with_retry(
        self, url: str, date: str, theater_slug: str
    ) -> Optional[str]:
        """
        Fetch URL with retry logic and exponential backoff

        Args:
            url: URL to fetch
            date: Date being fetched (for logging)
            theater_slug: Theater slug for organizing responses

        Returns:
            Response text or None if all retries failed
        """
        max_retries = self.config["scraping"]["max_retries"]
        retry_delays = self.config["scraping"]["retry_delays"]
        timeout = self.config["scraping"]["request_timeout"]

        for attempt in range(max_retries):
            try:
                self.logger.debug(
                    f"Fetching {url} (attempt {attempt + 1}/{max_retries})"
                )
                self._update_stats(total_requests=1)

                response = requests.get(url, headers=self.headers, timeout=timeout)
                response.raise_for_status()

                self._update_stats(successful_requests=1)
                self.logger.debug(f"Successfully fetched data for {date}")

                # Save raw response if configured
                if self.config["output"]["save_raw_responses"]:
                    self._save_raw_response(response.text, date, theater_slug)

                return response.text

            except requests.exceptions.Timeout:
                self.logger.warning(f"Timeout fetching {date} (attempt {attempt + 1})")
            except requests.exceptions.HTTPError as e:
                self.logger.error(f"HTTP error for {date}: {e.response.status_code}")
                if e.response.status_code == 429:  # Rate limited
                    self.logger.warning("Rate limited, waiting longer...")
                    time.sleep(retry_delays[-1] * 2)
            except requests.exceptions.RequestException as e:
                self.logger.error(f"Request error for {date}: {str(e)}")

            # Wait before retry (except on last attempt)
            if attempt < max_retries - 1:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                self.logger.info(f"Waiting {delay}s before retry...")
                time.sleep(delay)

        # All retries failed
        self._update_stats(failed_requests=1)
        self.logger.error(
            f"Failed to fetch data for {date} after {max_retries} attempts"
        )
        return None

    def _save_raw_response(self, response: str, date: str, theater_slug: str):
        """Save raw response for debugging in theater-specific directory"""
        try:
            raw_dir = (
                Path(self.config["output"]["logs_dir"]) / "raw_responses" / theater_slug
            )
            raw_dir.mkdir(parents=True, exist_ok=True)
            filename = raw_dir / f"response_{date}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(response)
            self.logger.debug(f"Saved raw response to {filename}")
        except Exception as e:
            self.logger.warning(f"Failed to save raw response: {e}")

    def _parse_movies(
        self, html_data: str
    ) -> List[Tuple[str, str, Optional[int], str, List[str]]]:
        """
        Parse movie information from HTML using BeautifulSoup

        Returns:
            List of tuples: (name, slug, runtime, rating, showtimes)
        """
        movies = []

        try:
            soup = BeautifulSoup(html_data, "html.parser")

            # Find all movie sections with aria-label="Showtimes for ..."
            movie_sections = soup.find_all(
                "section", attrs={"aria-label": re.compile(r"Showtimes for")}
            )

            self.logger.debug(f"Found {len(movie_sections)} movie sections")

            for section in movie_sections:
                try:
                    # Extract movie name from aria-label
                    aria_label = section.get("aria-label", "")
                    match = re.match(r"Showtimes for (.+)", aria_label)
                    if not match:
                        continue

                    movie_name = match.group(1)

                    # Skip theater name entries
                    if self._is_theater_name(movie_name):
                        continue

                    # Extract slug from section ID
                    section_id = section.get("id", "")
                    # ID format is usually "movie-slug-12345", extract just the slug part
                    slug_match = re.match(r"(.+)-\d+$", section_id)
                    slug = slug_match.group(1) if slug_match else section_id

                    # Extract runtime and rating from header
                    header = section.find("header")
                    runtime = None
                    rating = ""

                    if header:
                        header_text = header.get_text(separator=" ")

                        # Look for runtime (e.g., "2 HR 0 MIN")
                        runtime_match = re.search(
                            r"(\d+)\s*HR\s*(\d+)\s*MIN", header_text, re.IGNORECASE
                        )
                        if runtime_match:
                            hours = int(runtime_match.group(1))
                            minutes = int(runtime_match.group(2))
                            runtime = hours * 60 + minutes

                        # Look for rating
                        rating_match = re.search(
                            r"\b(G|PG|PG13|PG-13|R|NC17|NC-17|NR|Not Rated)\b",
                            header_text,
                            re.IGNORECASE,
                        )
                        if rating_match:
                            rating = rating_match.group(1)

                    # Extract showtimes from links
                    showtime_links = section.find_all(
                        "a", href=re.compile(r"/showtimes/")
                    )
                    showtimes = []

                    for link in showtime_links:
                        time_text = link.get_text(strip=True)

                        # Remove discount labels like "20% OFF", "UP TO 15% OFF"
                        time_text = re.sub(
                            r"\s*(UP\s+TO\s+)?\d+%\s+OFF\s*",
                            "",
                            time_text,
                            flags=re.IGNORECASE,
                        )

                        # Parse time (e.g., "1:00 pm", "11:30 am")
                        time_match = re.match(
                            r"(\d{1,2}):(\d{2})\s*(am|pm)", time_text, re.IGNORECASE
                        )
                        if time_match:
                            hour = time_match.group(1)
                            minute = time_match.group(2)
                            period = time_match.group(3).upper()
                            formatted_time = f"{hour}:{minute} {period}"
                            showtimes.append(formatted_time)

                    # Remove duplicates and sort
                    showtimes = sorted(
                        list(set(showtimes)), key=lambda t: self._time_to_minutes(t)
                    )

                    if showtimes:  # Only add movies with showtimes
                        movies.append((movie_name, slug, runtime, rating, showtimes))

                except Exception as e:
                    self.logger.warning(f"Error parsing movie section: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"Error parsing HTML: {e}", exc_info=True)
            self._update_stats(parsing_errors=1)

        self.logger.info(f"Parsed {len(movies)} movies with showtimes")
        return movies

    def _is_theater_name(self, name: str) -> bool:
        """Check if a name is likely a theater name rather than a movie"""
        return any(keyword.lower() in name.lower() for keyword in self.THEATER_KEYWORDS)

    def _time_to_minutes(self, time_str: str) -> int:
        """Convert time string to minutes since midnight for sorting"""
        try:
            match = re.match(r"(\d+):(\d+)\s*(AM|PM)", time_str)
            if match:
                hour, minute, period = match.groups()
                hour = int(hour)
                minute = int(minute)

                # Convert to 24-hour format
                if period == "PM" and hour != 12:
                    hour += 12
                elif period == "AM" and hour == 12:
                    hour = 0

                return hour * 60 + minute
        except Exception:
            pass
        return 0

    def _validate_movie(self, movie: Movie) -> bool:
        """
        Validate movie data with configurable checks

        Args:
            movie: Movie object to validate

        Returns:
            True if valid, False otherwise
        """
        if not movie.is_valid():
            self.logger.warning(f"Invalid movie data: {movie.name}")
            return False

        if self.VALIDATE_TIME_FORMAT:
            invalid_times = [t for t in movie.showtimes if not Movie._is_valid_time(t)]
            if invalid_times:
                self.logger.warning(
                    f"Invalid time formats for {movie.name}: {invalid_times}"
                )
                return False

        return True

    def scrape_date(self, date: str, theater: Dict) -> DailyShowtimes:
        """
        Scrape showtimes for a specific date and theater

        Args:
            date: Date in YYYY-MM-DD format
            theater: Theater dictionary with 'slug' and 'market' keys

        Returns:
            DailyShowtimes object with results
        """
        theater_name = theater["name"]
        self.logger.info(f"Scraping {theater_name} for {date}")

        # Build URL
        url = (
            f"{self.base_url}/movie-theatres/{theater['market']}/"
            f"{theater['slug']}/showtimes?date={date}&_rsc=yfjqh"
        )

        # Fetch data with retries
        rsc_data = self._fetch_with_retry(url, date, theater["slug"])

        if not rsc_data:
            return DailyShowtimes(
                date=date,
                theater=theater_name,
                movies=[],
                fetch_time=datetime.now().isoformat(),
                success=False,
                error_message="Failed to fetch data after all retries",
            )

        # Parse movies with showtimes
        try:
            movie_data = self._parse_movies(rsc_data)

            if not movie_data:
                self.logger.warning(f"No movies found for {date}")
                return DailyShowtimes(
                    date=date,
                    theater=theater_name,
                    movies=[],
                    fetch_time=datetime.now().isoformat(),
                    success=False,
                    error_message="No movies found in response",
                )

            # Create Movie objects from parsed data
            movies = []
            for name, slug, runtime, rating, showtimes in movie_data:
                movie = Movie(
                    name=name,
                    slug=slug,
                    runtime=runtime,
                    rating=rating,
                    showtimes=showtimes,
                )

                if self._validate_movie(movie):
                    movies.append(movie)
                    self._update_stats(total_movies_found=1)
                else:
                    self.logger.debug(f"Skipping invalid movie: {name}")

            # Validate results
            success = len(movies) >= self.MIN_MOVIES_PER_DAY
            error_msg = None if success else "Fewer movies than expected"

            if not success and self.WARN_IF_NO_SHOWTIMES:
                self.logger.warning(
                    f"Only {len(movies)} movies found for {date} (expected >= {self.MIN_MOVIES_PER_DAY})"
                )

            return DailyShowtimes(
                date=date,
                theater=theater_name,
                movies=movies,
                fetch_time=datetime.now().isoformat(),
                success=success,
                error_message=error_msg,
            )

        except Exception as e:
            self.logger.error(f"Error parsing data for {date}: {str(e)}", exc_info=True)
            return DailyShowtimes(
                date=date,
                theater=theater_name,
                movies=[],
                fetch_time=datetime.now().isoformat(),
                success=False,
                error_message=f"Parsing error: {str(e)}",
            )

    def scrape_all(self) -> List[DailyShowtimes]:
        """
        Scrape showtimes for all configured theaters and dates

        Returns:
            List of DailyShowtimes objects
        """
        all_results = []
        theaters = self.config["theaters"]
        days_ahead = self.config["scraping"]["days_ahead"]
        delay = self.config["scraping"]["delay_between_requests"]

        # Generate dates
        dates = [
            (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(days_ahead)
        ]

        self.logger.info(
            f"Starting scrape: {len(theaters)} theaters × {len(dates)} days = {len(theaters) * len(dates)} requests"
        )

        start_time = time.time()

        # Scrape each theater for each date
        for theater in theaters:
            for date in dates:
                result = self.scrape_date(date, theater)
                all_results.append(result)

                # Delay between requests
                if delay > 0:
                    time.sleep(delay)

        elapsed = time.time() - start_time
        self.logger.info(f"Scraping completed in {elapsed:.1f}s")

        # Print stats
        self._print_stats(all_results)

        return all_results

    def scrape_all_parallel(
        self, max_workers: Optional[int] = None
    ) -> List[DailyShowtimes]:
        """
        Scrape showtimes for all configured theaters and dates using parallel processing

        Args:
            max_workers: Maximum number of worker threads (default: from config or auto)

        Returns:
            List of DailyShowtimes objects
        """
        all_results = []
        theaters = self.config["theaters"]
        days_ahead = self.config["scraping"]["days_ahead"]

        # Get concurrency settings from config
        if max_workers is None:
            max_workers = self.config.get("scraping", {}).get("max_workers", None)

        # Generate all theater-date combinations
        dates = [
            (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(days_ahead)
        ]

        # Create list of all tasks
        tasks = []
        for theater in theaters:
            for date in dates:
                tasks.append((date, theater))

        self.logger.info(
            f"Starting parallel scrape: {len(theaters)} theaters × "
            f"{len(dates)} days = {len(tasks)} requests"
        )
        if max_workers:
            self.logger.info(f"Using {max_workers} worker threads")
        else:
            self.logger.info("Using auto-detected number of worker threads")

        start_time = time.time()

        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_task = {
                executor.submit(self.scrape_date, date, theater): (date, theater)
                for date, theater in tasks
            }

            # Collect results as they complete
            for future in as_completed(future_to_task):
                date, theater = future_to_task[future]
                try:
                    result = future.result()
                    all_results.append(result)
                    self.logger.debug(f"Completed {theater['name']} for {date}")
                except Exception as e:
                    self.logger.error(
                        f"Error processing {theater['name']} for {date}: {e}"
                    )
                    # Create error result
                    error_result = DailyShowtimes(
                        date=date,
                        theater=theater["name"],
                        movies=[],
                        fetch_time=datetime.now().isoformat(),
                        success=False,
                        error_message=f"Execution error: {str(e)}",
                    )
                    all_results.append(error_result)

        # Sort results to maintain consistent order (theater, then date)
        all_results.sort(key=lambda x: (x.theater, x.date))

        elapsed = time.time() - start_time
        self.logger.info(f"Parallel scraping completed in {elapsed:.1f}s")

        # Print stats
        self._print_stats(all_results)

        return all_results

    def _print_stats(self, results: List[DailyShowtimes]):
        """Print scraping statistics"""
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful
        total_movies = sum(len(r.movies) for r in results)

        self.logger.info("=" * 60)
        self.logger.info("SCRAPING STATISTICS")
        self.logger.info("=" * 60)
        self.logger.info(f"Total requests: {self.stats['total_requests']}")
        self.logger.info(f"Successful requests: {self.stats['successful_requests']}")
        self.logger.info(f"Failed requests: {self.stats['failed_requests']}")
        self.logger.info(f"Days scraped successfully: {successful}/{len(results)}")
        self.logger.info(f"Days with errors: {failed}")
        self.logger.info(f"Total movies found: {total_movies}")
        self.logger.info(f"Parsing errors: {self.stats['parsing_errors']}")
        self.logger.info("=" * 60)

    def save_results(
        self, results: List[DailyShowtimes], filename: Optional[str] = None
    ):
        """
        Save results to JSON file

        Args:
            results: List of DailyShowtimes objects
            filename: Output filename (auto-generated if None)
        """
        if not self.config["output"]["save_to_json"]:
            self.logger.info("JSON output disabled in config")
            return

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"amc_showtimes_{timestamp}.json"

        output_path = Path(self.config["output"]["output_dir"]) / filename

        try:
            # Convert to dict
            data = {
                "scraped_at": datetime.now().isoformat(),
                "stats": self.stats,
                "results": [
                    {
                        "date": r.date,
                        "theater": r.theater,
                        "success": r.success,
                        "error_message": r.error_message,
                        "fetch_time": r.fetch_time,
                        "movies": [asdict(m) for m in r.movies],
                    }
                    for r in results
                ],
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            self.logger.info(f"Results saved to: {output_path}")

        except Exception as e:
            self.logger.error(f"Failed to save results: {e}", exc_info=True)

    def print_summary(self, results: List[DailyShowtimes]):
        """Print a human-readable summary of results"""
        print("\n" + "=" * 80)
        print("AMC SHOWTIMES SUMMARY")
        print("=" * 80)

        for result in results:
            print(f"\n📅 {result.date} - {result.theater}")
            print("-" * 80)

            if not result.success:
                print(f"❌ Error: {result.error_message}")
                continue

            if not result.movies:
                print("⚠️  No movies found")
                continue

            for movie in result.movies:
                runtime_str = f" ({movie.runtime}min)" if movie.runtime else ""
                rating_str = f" [{movie.rating}]" if movie.rating else ""
                print(f"\n🎬 {movie.name}{runtime_str}{rating_str}")
                print(f"   Times: {', '.join(movie.showtimes)}")


def main():
    """Main entry point"""
    try:
        # Create scraper
        scraper = AMCShowtimeScraper(config_path="config.json")

        # Check if parallel processing is enabled
        use_parallel = scraper.config.get("scraping", {}).get("use_parallel", True)

        if use_parallel:
            scraper.logger.info("Using parallel scraping")
            results = scraper.scrape_all_parallel()
        else:
            scraper.logger.info("Using sequential scraping")
            results = scraper.scrape_all()

        # Save results
        scraper.save_results(results)

        # Print summary
        scraper.print_summary(results)

    except KeyboardInterrupt:
        print("\n\nScraping interrupted by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
