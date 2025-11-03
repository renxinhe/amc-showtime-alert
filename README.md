# AMC Showtime Alert

A Python toolkit for scraping AMC Theatres showtimes, detecting special events, and sending Telegram notifications.

## Installation

```bash
pip install -e .
```

## Components

### 1. Scraper (`amc_scraper.py`)

Scrapes movie showtimes from AMC theatres.

#### Input
- **Config file**: `config.json` (theater list and scraping settings)
- **Command line**: `amc-scraper` or `python amc_scraper.py`

#### Output
- **JSON file**: `output/amc_showtimes_TIMESTAMP.json`
- **Logs**: `logs/scraper_TIMESTAMP.log`

#### Configuration (`config.json`)
```json
{
  "theaters": [
    {
      "name": "AMC Empire 25",
      "slug": "amc-empire-25",
      "market": "new-york-city"
    }
  ],
  "scraping": {
    "days_ahead": 21,
    "delay_between_requests": 1.5,
    "max_retries": 3,
    "retry_delays": [2, 4, 8],
    "request_timeout": 30,
    "use_parallel": true,
    "max_workers": 5
  },
  "output": {
    "save_raw_responses": true,
    "save_to_json": true,
    "output_dir": "output",
    "logs_dir": "logs"
  },
  "logging": {
    "console_level": "INFO",
    "file_level": "DEBUG",
    "enable_scraper_file_logging": false,
    "enable_pipeline_file_logging": false,
    "enable_status_file_logging": true
  },
  "telegram": {
    "retention_days": 30
  },
  "server": {
    "interval_minutes": 60,
    "cleanup_interval_days": 7
  }
}
```

#### Parallel Processing
The scraper now supports parallel processing for significantly improved performance:

- **`use_parallel`**: Enable/disable parallel processing (default: `true`)
- **`max_workers`**: Maximum number of concurrent threads (default: `5`)

**Performance Benefits:**
- 3-5x faster execution for typical workloads
- Concurrent HTTP requests reduce total scraping time
- Thread-safe statistics tracking
- Maintains same output format and error handling

**Usage:**
```bash
# Parallel scraping (default)
amc-scraper

# Sequential scraping (for debugging)
# Set "use_parallel": false in config.json
```

#### Output Format
```json
{
  "scraped_at": "2025-10-29T10:30:00",
  "stats": {
    "total_requests": 42,
    "successful_requests": 40,
    "failed_requests": 2,
    "total_movies_found": 285
  },
  "results": [
    {
      "date": "2025-10-30",
      "theater": "AMC Empire 25",
      "success": true,
      "movies": [
        {
          "name": "Movie Title",
          "slug": "movie-title",
          "runtime": 120,
          "rating": "PG-13",
          "showtimes": ["7:30 PM", "10:00 PM"]
        }
      ]
    }
  ]
}
```

---

### 2. Parser (`special_events_parser.py`)

Detects special events (Q&A sessions, early access, special screenings) from scraped showtimes.

#### Input
- **JSON file**: `output/amc_showtimes_TIMESTAMP.json` (from scraper)
- **Command line**: `amc-parser <json_file>` or `python special_events_parser.py <json_file>`

#### Output
- **JSON file**: `output/amc_showtimes_special_TIMESTAMP.json`

#### Output Format
```json
{
  "parsed_at": "2025-10-29T18:11:43.915005",
  "total_events": 5,
  "events_by_type": {
    "Q&A": 2,
    "Early Access": 1,
    "Special Screening": 2
  },
  "events": [
    {
      "date": "2025-10-30",
      "theater": "AMC Empire 25",
      "movie_name": "Movie Title",
      "showtime": "7:30 PM",
      "event_type": "Q&A",
      "description": "Q&A with director after screening"
    }
  ]
}
```

#### Detection Patterns
- **Q&A**: Contains "Q&A", "Q & A", "question", "answer"
- **Early Access**: Contains "early access", "advance screening", "preview"
- **Special Screening**: Contains "special screening", "exclusive", "limited"

---

### 3. Telegram Notifier (`telegram_notifier.py`)

Sends formatted notifications to Telegram for special events with smart deduplication.

#### Input
- **JSON file**: `output/amc_showtimes_special_TIMESTAMP.json` (from parser)
- **Environment variables**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Command line**: `amc-telegram <special_events_json>` or `python telegram_notifier.py <special_events_json>`

#### Output
- **Telegram messages**: Formatted notifications sent to configured chat
- **Console output**: Success/failure status

#### Setup
1. Message @BotFather on Telegram
2. Send `/newbot` and follow instructions
3. Get your bot token and chat ID
4. Set environment variables:
   ```bash
   export TELEGRAM_BOT_TOKEN="your_bot_token"
   export TELEGRAM_CHAT_IDS="your_chat_id1,your_chat_id2"
   ```

#### Message Formats

**New Event:**
```
🎬 New Q&A Event!

Movie Title
📍 AMC Empire 25
📅 2025-10-30
⏳ 120min [PG-13]
⏰ 7:30 PM, 9:00 PM
```

**Updated Event (Showtime Changes):**
```
🔔 Updated Q&A Event

🎬 Movie Title
📍 AMC Empire 25
📅 2025-10-30
⏳ 120min [PG-13]

✅ New showtimes:
  ⏰ 11:00 PM

❌ Removed showtimes:
  ⏰ 7:30 PM

📌 Still available:
  ⏰ 9:00 PM
```

---

### 4. Notification State Manager (`notification_state.py`)

Tracks notification history in SQLite to prevent duplicate alerts and enable frequent polling without spam.

#### Features
- **Smart Deduplication**: Tracks which events have been notified
- **Showtime Change Detection**: Re-notifies only when showtimes are added/removed
- **30-Day Retention**: Automatically cleans up old notification records
- **SQLite Storage**: Persistent state across pipeline runs

#### Database Schema
```sql
CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    theater TEXT,
    date TEXT,
    movie_name TEXT,
    event_type TEXT,
    showtimes TEXT,  -- JSON array
    first_notified_at TIMESTAMP,
    last_updated_at TIMESTAMP,
    notification_count INTEGER
);
```

#### How It Works
1. **First time**: Event is new → Send notification and record in database
2. **Second time**: Same event with same showtimes → Skip notification
3. **Showtimes change**: Same event but different times → Send update notification
4. **Past events**: Automatically removed after configured retention period (default: 30 days)

#### Configuration
Set `retention_days` in `config.json` under the `telegram` section to control how long notification records are kept.

---

### 5. Unified Pipeline (`run_alert_pipeline.py`)

Runs the complete pipeline with deduplication in a single command or as a long-running server.

#### Usage

**Single Run Mode (Default):**
```bash
# Run with defaults
python run_alert_pipeline.py

# Specify custom paths
python run_alert_pipeline.py --config config.json --db notifications.db
```

**Server Mode (Continuous):**
```bash
# Run in server mode with scheduled execution
python run_alert_pipeline.py --server

# With custom config
python run_alert_pipeline.py --server --config config.json --db notifications.db
```

#### What It Does
1. **Scrapes** showtimes from all configured theaters
2. **Parses** special events from scraped data
3. **Checks** notification state for duplicates
4. **Sends** only new or updated events via Telegram
5. **Updates** notification state database
6. **Cleans up** old notification records (in server mode)

#### Server Mode Features
- **Scheduled Execution**: Runs pipeline automatically at configured intervals (default: every 60 minutes)
- **Weekly Status Logs**: Appends simple status lines to `logs/status_YYYY-WW.log`
- **Automatic Cleanup**: Removes old output files weekly to save disk space
- **Graceful Shutdown**: Handles Ctrl+C to stop cleanly after current run
- **Minimal Logging**: Disables verbose file logs by default, keeping only essential status logs
- **Persistent State**: SQLite database maintains notification history across runs

#### Status Log Format
```
2025-11-02 10:30:15 | SUCCESS |  45.2s | Theaters:3/3 Movies:285 Events:5 Sent:2 Updated:1 Skipped:2 | -
2025-11-02 11:30:22 | SUCCESS |  42.8s | Theaters:3/3 Movies:287 Events:5 Sent:0 Updated:0 Skipped:5 | -
2025-11-02 12:30:18 | FAILED  |  12.1s | Theaters:1/3 Movies:0 Events:0 Sent:0 Updated:0 Skipped:0 | Connection timeout
```

#### Configuration Options

**Logging Control:**
- `enable_scraper_file_logging`: Detailed scraper logs (default: `false`)
- `enable_pipeline_file_logging`: Detailed pipeline logs (default: `false`)
- `enable_status_file_logging`: Weekly status logs (default: `true`)

**Server Settings:**
- `interval_minutes`: How often to run pipeline in server mode (default: `60`)
- `cleanup_interval_days`: How often to clean old output files (default: `7`)

**Telegram Settings:**
- `retention_days`: How long to keep notification history (default: `30`)

#### Benefits
- Can run continuously without user intervention
- Detects and notifies about showtime changes
- Prevents notification spam through smart deduplication
- Automatic cleanup preserves disk space
- Simple status logs for monitoring
- Graceful interrupt handling (Ctrl+C)

---

## Workflows

### Option 1: Server Mode (Recommended for Production)

Long-running server with automatic scheduling - set it and forget it:

```bash
python run_alert_pipeline.py --server
```

**Best for:**
- Running on a VPS or home server
- Continuous monitoring without manual intervention
- Automatic disk space management
- Simple status logging for monitoring

### Option 2: Unified Pipeline (Recommended for Cron Jobs)

Single command with deduplication - safe to run frequently:

```bash
python run_alert_pipeline.py
```

**Best for:**
- Cron jobs or GitHub Actions
- Systems where you want external scheduling
- One-off manual runs

### Option 3: Step-by-Step (Manual)

For testing or custom workflows:

```bash
# 1. Scrape showtimes
amc-scraper

# 2. Find special events
amc-parser output/amc_showtimes_*.json

# 3. Send notifications (legacy - no deduplication)
amc-telegram output/amc_showtimes_special_*.json
```

## Programmatic Usage

```python
# Scrape showtimes
from amc_showtime_alert.amc_scraper import AMCShowtimeScraper
scraper = AMCShowtimeScraper(config_path="config.json")
results = scraper.scrape_all()
scraper.save_results(results, "my_showtimes.json")

# Parse special events
from amc_showtime_alert.special_events_parser import SpecialEventsParser
parser = SpecialEventsParser()
events = parser.parse_file("my_showtimes.json")
parser.save_events(events, "my_events.json")

# Send notifications
from amc_showtime_alert.telegram_notifier import TelegramNotifier
notifier = TelegramNotifier()
notifier.send_events("my_events.json")
```
