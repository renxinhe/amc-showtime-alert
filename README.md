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
    "days_ahead": 14,
    "delay_between_requests": 1.5,
    "max_retries": 3,
    "retry_delays": [2, 4, 8]
  },
  "validation": {
    "min_movies_per_day": 1,
    "warn_if_no_showtimes": true
  },
  "logging": {
    "console_level": "INFO",
    "file_level": "DEBUG"
  }
}
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

Sends formatted notifications to Telegram for special events.

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

#### Message Format
```
🎬 Special Events Found!

📅 2025-10-30 - AMC Empire 25
🎭 Movie Title (7:30 PM)
   Q&A with director after screening

📅 2025-10-31 - AMC Empire 25  
🎭 Another Movie (8:00 PM)
   Early Access Screening
```

---

## Workflow

```bash
# 1. Scrape showtimes
amc-scraper

# 2. Find special events
amc-parser output/amc_showtimes_*.json

# 3. Send notifications
amc-telegram output/amc_showtimes_special_*.json
```

## Programmatic Usage

```python
from amc_scraper import AMCShowtimeScraper

# Scrape showtimes
scraper = AMCShowtimeScraper(config_path="config.json")
results = scraper.scrape_all()
scraper.save_results(results, "my_showtimes.json")

# Parse special events
from special_events_parser import SpecialEventsParser
parser = SpecialEventsParser()
events = parser.parse_file("my_showtimes.json")
parser.save_events(events, "my_events.json")

# Send notifications
from telegram_notifier import TelegramNotifier
notifier = TelegramNotifier()
notifier.send_events("my_events.json")
```

## Error Handling

- **Network errors**: Automatic retry with exponential backoff
- **Rate limiting**: Configurable delays between requests
- **Parsing errors**: Multiple regex pattern fallbacks
- **Invalid data**: Validation and filtering
- **Missing showtimes**: Warnings logged, scraping continues

## Troubleshooting

### No movies found
- Check logs in `logs/` directory
- Verify theater slugs in `config.json`
- Check for rate limiting (increase delays)

### Parsing errors
- Check raw responses in `logs/raw_responses/`
- Scraper tries multiple patterns automatically

### Telegram issues
- Verify bot token and chat ID
- Check environment variables are set
- Ensure bot is added to chat