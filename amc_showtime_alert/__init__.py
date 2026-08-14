"""
AMC Showtime Alert Package

A comprehensive tool for scraping AMC theater showtimes, parsing special events,
and sending Telegram notifications.

- Q&A showings: the global broadcast, subscribed to via /startqnaalert
  (users table) — see user_manager
- Custom alerts: per-user movie/theater/format watches, /addalert
  (alerts table) — see alert_manager / alert_matcher
- Seat alerts: per-user watches on one showing, /addseatalert
  (seat_alerts table) — see seat_alerts/

Main pieces:
- amc_scraper: Scrapes movie showtimes (and premium formats) from AMC theaters
- special_events_parser: Parses special events from scraped data
- alert_manager / alert_matcher: Per-user custom alert storage and matching
- telegram/: The command bot (TelegramBot) and notification sender (TelegramNotifier)
"""
