#!/usr/bin/env python3
"""Static bot copy: greetings, help text, and the registered command list."""

WELCOME_MESSAGE = (
    "👋 Hi {first_name}! You're now subscribed to AMC Q&A showtime alerts.\n\n"
    "You'll receive a message whenever new Q&A events are scheduled at AMC "
    "theaters in NYC.\n\n"
    "Send /stop to unsubscribe at any time."
)

ALREADY_SUBSCRIBED_MESSAGE = (
    "✅ You're already subscribed to AMC Q&A showtime alerts!\n\n"
    "Send /stop to unsubscribe."
)

UNSUBSCRIBE_MESSAGE = (
    "👋 Goodbye {first_name}! You've been unsubscribed from AMC Q&A showtime alerts.\n\n"
    "Send /start to subscribe again."
)

NOT_SUBSCRIBED_MESSAGE = (
    "You're not currently subscribed.\n\n"
    "Send /start to subscribe to AMC Q&A showtime alerts."
)

HELP_MESSAGE = (
    "🎬 AMC Showtime Alert Bot\n\n"
    "Subscription:\n"
    "/start — Subscribe (also enables your custom alerts)\n"
    "/stop — Unsubscribe (silences all your alerts)\n\n"
    "Custom alerts — get notified about any movie you choose:\n"
    "/addalert — guided setup: pick theaters & formats with buttons\n"
    "/listalerts — show your alerts and their ids\n"
    "/editalert <id> [keyword] [theater:…] [format:…] [regex|noregex]\n"
    "/delalert [id] — delete an alert (tap to pick if no id given)\n\n"
    "Seat alerts — watch one specific showing for a good seat opening up:\n"
    "/addseatalert — pick theater → date → movie → showtime\n"
    "/listseatalerts — show your seat alerts\n"
    "/delseatalert — delete a seat alert (tap to pick)\n"
    "/cancel — stop the guided setup\n\n"
    "Prefer typing? You can still pass everything inline:\n"
    '  /addalert Oppenheimer format:imax\n'
    '  /addalert "Taylor Swift" theater:amc-lincoln-square-13\n'
    "  /addalert ^Dune.*Part regex format:imax\n\n"
    "Omit theater: to match all theaters. Use theater:all to clear it on edit.\n"
    "/help — show this message"
)

BOT_COMMANDS = [
    {"command": "start", "description": "Subscribe to alerts"},
    {"command": "stop", "description": "Unsubscribe from alerts"},
    {"command": "addalert", "description": "Create a custom showtime alert"},
    {"command": "listalerts", "description": "List your custom alerts"},
    {"command": "editalert", "description": "Edit an existing alert"},
    {"command": "delalert", "description": "Delete an alert"},
    {"command": "addseatalert", "description": "Watch a showing for a good seat"},
    {"command": "listseatalerts", "description": "List your seat alerts"},
    {"command": "delseatalert", "description": "Delete a seat alert"},
    {"command": "cancel", "description": "Cancel the guided alert setup"},
    {"command": "help", "description": "Show available commands"},
]
