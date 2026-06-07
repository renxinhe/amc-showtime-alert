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
    "/start — subscribe (also enables your custom alerts)\n"
    "/stop — unsubscribe (silences all your alerts)\n\n"
    "Custom alerts — get notified about any movie you choose:\n"
    "/addalert — guided setup: pick theaters & formats with buttons\n"
    "/listalerts — show your alerts and their IDs\n"
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

# ---------------------------------------------------------------------------- #
# Custom-alert copy (/addalert, /listalerts, /editalert, /delalert) + wizard
# ---------------------------------------------------------------------------- #

CANCELLED_NOTHING = "✖️ Cancelled. Nothing was created."
NOTHING_TO_CANCEL = "Nothing to cancel."
NOT_SUBSCRIBED_SUFFIX = "\n\n⚠️ You're not subscribed — send /start to receive alerts."

# Argument-parsing errors (returned, then shown). {value}/{matches}/{slugs}/{tokens}.
THEATER_AMBIGUOUS = "Ambiguous theater '{value}'. Matches: {matches}"
THEATER_UNKNOWN = "Unknown theater '{value}'. Valid slugs: {slugs}"
FORMAT_UNKNOWN = "Unknown format '{value}'. Valid: {tokens}"
ARGS_PARSE_ERROR = "Could not parse arguments — check your quotes."

ADDALERT_NEED_PATTERN = (
    "Please provide a keyword or phrase to match.\n"
    "Example: /addalert Oppenheimer format:imax"
)
ADDALERT_CREATE_FAILED = "❌ Could not create the alert. Try again."
ALERT_CREATED_HEADER = "✅ <b>Alert created</b>"

LISTALERTS_EMPTY = (
    "You have no custom alerts yet.\n"
    "Create one with /addalert — see /help for examples."
)
LISTALERTS_HEADER = "🔔 <b>Your alerts</b>"
LISTALERTS_FOOTER = (
    "\n\nEdit: <code>/editalert &lt;id&gt; …</code>"
    "   Delete: <code>/delalert &lt;id&gt;</code>"
)

EDITALERT_USAGE = (
    "Usage: /editalert <id> [keyword] [theater:…] [format:…] "
    "[regex|noregex]\nSee /listalerts for IDs."
)
EDITALERT_NOTHING = (
    "Nothing to change. Provide a new keyword and/or "
    "theater:… format:… regex|noregex."
)
EDITALERT_NOTHING_SHORT = "Nothing to change."
ALERT_UPDATED_HEADER = "✅ <b>Alert updated</b>"

ALERT_NOT_FOUND = "No alert #{id} found. Use /listalerts."           # .format(id=…)
DELALERT_EMPTY = "You have no alerts to delete."
DELALERT_PROMPT = "🗑 Which alert should I delete?"
ALERT_DELETED = "🗑 Deleted alert #{id}."                            # .format(id=…)
ALERT_DELETE_CANCELLED = "Okay — nothing deleted."
ALERT_ALREADY_GONE = "Alert #{id} was already gone."                 # .format(id=…)

# Guided /addalert wizard ({err}/{title}/{n} placeholders).
GUIDED_TITLE_PROMPT = (
    "🎬 <b>New alert</b>\n\n"
    "Send me the movie title or keyword to watch for "
    "(e.g. <i>Dune</i>).\n\nSend /cancel anytime to stop."
)
GUIDED_TITLE_INVALID = "❌ {err}\nTry again, or send /cancel."
GUIDED_THEATER_PROMPT = (
    "🎬 <b>{title}</b>\n\n"
    "Which theaters? Tap to toggle, then <b>Next</b>.\n"
    "<i>(none selected = all theaters)</i>"
)
GUIDED_FORMAT_PROMPT = (
    "🎬 <b>{title}</b>\n\n"
    "Which formats? Tap to toggle, then <b>Create</b>.\n"
    "<i>(none selected = any format)</i>"
)
GUIDED_CREATE_FAILED = "❌ Could not create the alert(s). Try again."
ALERTS_CREATED_HEADER = "✅ <b>{n} alerts created</b>"               # .format(n=…)

# ---------------------------------------------------------------------------- #
# Seat-alert flow copy (/addseatalert, /listseatalerts, /delseatalert)
# ---------------------------------------------------------------------------- #

SEAT_NO_THEATERS = "No theaters are configured."
SEAT_NEW_PROMPT = "🎟 <b>New seat alert</b>\n\nPick a theater:"

SEAT_ID_USAGE = (
    "Send just the numeric showtime id, e.g. "
    "<code>/addseatalert 143838750</code>, or use /addseatalert to "
    "pick from the menu."
)
SEAT_ID_UNREADABLE = (
    "Couldn't read that showtime's details. Use /addseatalert to "
    "pick it from the menu instead."
)

SEAT_LIST_EMPTY = "You have no seat alerts yet. Create one with /addseatalert."
SEAT_LIST_HEADER = "🎟 <b>Your seat alerts</b>"
SEAT_LIST_FOOTER = "\n\nDelete one with /delseatalert."

SEAT_DELETE_EMPTY = "You have no seat alerts to delete."
SEAT_DELETE_PROMPT = "🗑 Which seat alert should I delete?"
SEAT_DELETE_CANCELLED = "Okay — nothing deleted."
SEAT_DELETED = "🗑 Seat alert deleted."
SEAT_DELETE_GONE = "That seat alert was already gone."

SEAT_FLOW_CANCELLED = "✖️ Cancelled. Nothing was created."
SEAT_PICK_THEATER = "Pick a theater:"
SEAT_PICK_MONTH = "Pick a month:"
SEAT_PICK_DATE = "Pick a date:"
SEAT_PICK_MOVIE = "Pick a movie:"
SEAT_SHOWTIME_PROMPT = "Showtimes for <b>{movie}</b>:"          # .format(movie=…)
SEAT_NO_SHOWINGS = "No showings found at that theater on {date}."  # .format(date=…)

SEAT_CREATE_FAILED = "❌ Could not create the seat alert. Try again."
SEAT_CREATED_HEADER = "✅ <b>Seat alert created</b>"
SEAT_CREATED_FOOTER = "\n\nI'll message you when a good seat opens up."
