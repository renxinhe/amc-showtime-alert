"""
AMC Showtime Alert Package

A comprehensive tool for scraping AMC theater showtimes, parsing special events,
and sending Telegram notifications — both the global Q&A feed and per-user
custom alerts.

Main pieces:
- amc_scraper: Scrapes movie showtimes (and premium formats) from AMC theaters
- special_events_parser: Parses special events from scraped data
- alert_manager / alert_matcher: Per-user custom alert storage and matching
- telegram/: The command bot (TelegramBot) and notification sender (TelegramNotifier)
"""
