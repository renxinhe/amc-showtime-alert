# AMC Showtime Alert

[![CI](https://github.com/renxinhe/amc-showtime-alert/actions/workflows/ci.yml/badge.svg)](https://github.com/renxinhe/amc-showtime-alert/actions/workflows/ci.yml)
[![AMC Showtime Alert](https://healthchecks.io/b/2/cd80e297-2c8d-46ba-86f9-5469a94da7f5.svg)](https://healthchecks.io)

Monitors AMC theaters for special events (Q&As, special screenings) and sends Telegram notifications. Users can also create their own **custom alerts** for any movie, theater, and format (IMAX, Dolby, 70mm, …). Runs as a systemd service on a Raspberry Pi.

## Setup

```bash
pip install -e .
```

Create a `.env` file:

```bash
TELEGRAM_BOT_TOKEN="your_bot_token"
HEALTHCHECKS_PING_URL="https://hc-ping.com/your-uuid-here"  # optional
```

The bot delivers three kinds of alerts.

| Alert type | Opt in | Opt out | Audience |
|---|---|---|---|
| **Q&A showings** — screenings billed with a live Q&A (usually with the cast or crew) | `/startqnaalert` | `/stopqnaalert` | one shared feed, all subscribers |
| **Custom alerts** — any movie/theater/format you choose | `/addalert` | `/delalert` | per-alert, its creator only |
| **Seat alerts** — one watched showing, notified when a good seat frees up | `/addseatalert` | `/delseatalert` | per-alert, its creator only |

Only the Q&A feed uses the subscription in the `users` table; custom and seat alerts fire off their own rows, so `/stopqnaalert` does not silence them.

## Bot commands

| Command | Description |
|---------|-------------|
| `/startqnaalert` | Subscribe to special Q&A showings (Q&A feed only) |
| `/stopqnaalert` | Unsubscribe from special Q&A showings (leaves custom + seat alerts running) |
| `/addalert <keyword> [theater:<slug>] [format:<imax\|dolby\|70mm\|…>] [regex]` | Create a custom alert |
| `/listalerts` | List your alerts and their ids |
| `/editalert <id> [keyword] [theater:…] [format:…] [regex\|noregex]` | Edit an alert |
| `/delalert [id]` | Delete an alert (tap to pick if no id) |
| `/addseatalert` | Watch one showing for a good seat opening up |
| `/listseatalerts` | List your seat alerts |
| `/delseatalert` | Delete a seat alert (tap to pick) |
| `/help` | Show all commands |

**Custom alert examples:**

```
/addalert Oppenheimer format:imax
/addalert "Taylor Swift" theater:amc-lincoln-square-13
/addalert ^Dune.*Part regex format:imax
```

- The first non-flag word(s) are the title to match; quote a phrase to include spaces.
- Omit `theater:` to match **all** theaters; use `theater:all` to clear it on edit.
- `keyword` matches titles case-insensitively; add `regex` to treat it as a regular expression.
- A custom alert notifies **only its owner**, once per match, and again only when its showtimes change. This is independent of the global Q&A broadcast that every subscriber receives.

**Seat alerts** — watch one specific screening and get pinged when a *good* seat opens up:

- `/addseatalert` walks you through **theater → month → day → movie → showtime** (tap buttons; ◀️ Back at every step). Each showtime is identified by its AMC showtime id.
- A "good seat" is computed per auditorium: in the **back 60% of rows** (i.e. skipping the front 40%; row 1 = front) and the **centre 15–85% of columns**. Wheelchair spaces, gaps, and non-displayed seats are excluded.
- The poller checks each watched showing every cycle and messages you when a good seat becomes **newly available** (a seat that's taken then frees up alerts again; a continuously-open seat doesn't repeat).
- Alerts auto-expire (soft delete) the day after the showtime. Delete early with `/delseatalert`.

## Running

**Server mode**:
```bash
python run_alert_pipeline.py --server --db production.db
```

**Single run** (for testing or cron):
```bash
python run_alert_pipeline.py --db test.db
```

See [SERVICE_MANAGEMENT.md](SERVICE_MANAGEMENT.md) for systemd setup.

## Configuration

Edit `config.json` to configure theaters, scraping behavior, and intervals:

| Key | Default | Description |
|-----|---------|-------------|
| `scraping.days_ahead` | `30` | How many days ahead to scrape |
| `scraping.delay_between_requests` | `1.5` | Seconds between requests |
| `server.interval_minutes` | `20` | How often to run in server mode |
| `server.output_retention_days` | `7` | Days to keep output JSON files |
| `server.logs_retention_days` | `90` | Days to keep status logs |
| `telegram.retention_days` | `30` | Days to keep notification history |

## Status Logs

Each run appends a line to `logs/status_YYYY-WW.log`:

```
2025-11-02 10:30:15 | SUCCESS |  45.2s | Theaters:3/3 Movies:285 Events:5 Sent:2 Updated:1 Skipped:2 | -
2025-11-02 11:30:22 | FAILED  |  12.1s | Theaters:1/3 Movies:0 Events:0 Sent:0 Updated:0 Skipped:0 | Connection timeout
```

## Pipeline

Each run: **Scrape** → **Parse** → **Notify (global Q&A)** → **Match custom alerts** (deduplication via SQLite)

- New events → sends notification, records in DB
- Same event, same showtimes → skipped
- Same event, changed showtimes → sends update notification
