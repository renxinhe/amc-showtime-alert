#!/usr/bin/env python3
"""
Seat Map
Fetches and evaluates AMC seat availability for a single showtime.

AMC embeds the seat map as JSON in the React Server Component (RSC) payload of
the seat-selection route, which — unlike the normal booking page — is not put
behind the virtual-queue/bot wall:

    GET https://www.amctheatres.com/showtimes/<showtime_id>/seats?_rsc=<token>
        headers: RSC: 1

The payload contains a `seatingLayout` object:

    {"columns": 28, "rows": 16, "error": null,
     "seats": [{"available": bool, "row": int, "column": int, "name": str,
                "type": str, "seatTier": str, "shouldDisplay": bool}, ...]}

Every grid cell is present; aisles/gaps appear as type "NotASeat".
Row 1 is the front (screen side); higher row numbers are further back.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

try:
    import requests
except ImportError:
    raise ImportError("requests library is required: pip install requests")

logger = logging.getLogger("SeatMap")

AMC_BASE_URL = "https://www.amctheatres.com"
# The ?_rsc=<value> query param is what makes AMC's edge serve the React Server
# Component payload directly instead of redirecting to the virtual queue. Only
# its PRESENCE matters — the value is not a build hash and is not validated.
RSC_TOKEN = "1"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    # With the RSC header we get the compact component payload (~350 KB) rather
    # than the full HTML document (~2.8 MB); both contain the seat map.
    "RSC": "1",
    "Accept": "text/x-component",
}

# Seat types that never count as a bookable seat for alerts.
EXCLUDED_SEAT_TYPES = {"NotASeat", "Wheelchair"}

# "Good seat" geometry, as fractions of the auditorium's own dimensions.
BACK_ROW_FRACTION = 0.60   # consider seats in the back 60% of rows (skip front 40%)
COL_CENTER_LOW = 0.15      # ... and within the centre 15–85% of columns
COL_CENTER_HIGH = 0.85


@dataclass
class SeatLayout:
    """Parsed seating layout for one showtime."""

    rows: int
    columns: int
    seats: List[dict]


def parse_seating_layout(payload: str) -> Optional[SeatLayout]:
    """
    Extract the `seatingLayout` object from a seats RSC payload.

    Returns None if it can't be found/parsed (caller logs with context).
    """
    if '"seatingLayout"' not in payload:
        return None
    try:
        start = payload.index("{", payload.index('"seatingLayout"'))
        depth = 0
        for k in range(start, len(payload)):
            ch = payload[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj = json.loads(payload[start : k + 1])
                    return SeatLayout(
                        rows=obj.get("rows", 0),
                        columns=obj.get("columns", 0),
                        seats=obj.get("seats", []) or [],
                    )
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def fetch_seat_layout(showtime_id: str, timeout: int = 30) -> Optional[SeatLayout]:
    """Fetch and parse the seat layout for a showtime id, or None on failure."""
    url = f"{AMC_BASE_URL}/showtimes/{showtime_id}/seats?_rsc={RSC_TOKEN}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.exceptions.RequestException as e:
        logger.warning(f"Seat fetch for {showtime_id} failed: {e}")
        return None

    if not response.ok:
        logger.warning(f"Seat fetch for {showtime_id} returned {response.status_code}")
        return None

    layout = parse_seating_layout(response.text)
    if layout is None:
        # A 2xx with no seatingLayout. The common, EXPECTED case is a fully
        # sold-out showtime (AMC shows a "sold out" notice instead of the map) —
        # that's normal for a watched show, so it's debug, not a warning. A
        # virtual-queue/anti-bot page or anything else is genuinely noteworthy.
        text = response.text
        low = text.lower()
        if "showtime is sold out" in low or "sold out, please choose" in low:
            logger.debug(f"Seat map for {showtime_id}: sold out — no seats to show yet")
        elif "queue.amctheatres" in text or "/queue" in text:
            logger.warning(
                f"Seat map for {showtime_id} unavailable — virtual queue (rate-limited?)"
            )
        else:
            logger.warning(
                f"Seat map for {showtime_id} unavailable — "
                f"no seatingLayout in {len(text)} byte response"
            )
    return layout


def fetch_showtime_meta(showtime_id: str, timeout: int = 30) -> Optional[dict]:
    """
    Best-effort metadata for a showtime id (used by the `/addseatalert <id>`
    shortcut): {"date": "YYYY-MM-DD", "movie": str|None, "label": str}.

    Returns None if a plausible showtime date can't be determined — callers
    should then fall back to the guided picker, which gets the date directly.
    The guided flow does NOT depend on this.
    """
    from datetime import date as _date, datetime as _dt

    url = f"{AMC_BASE_URL}/showtimes/{showtime_id}?_rsc={RSC_TOKEN}"
    try:
        # No RSC header → the full rendered document, which carries readable
        # schedule info. The _rsc param still bypasses the virtual queue.
        resp = requests.get(
            url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=timeout
        )
        if not resp.ok:
            return None
        text = resp.text
    except requests.exceptions.RequestException as e:
        logger.warning(f"Showtime meta fetch for {showtime_id} failed: {e}")
        return None

    # Pick the earliest upcoming ISO datetime as the show time (past and
    # sale-window datetimes are filtered out by requiring date >= today).
    today = _date.today().isoformat()
    upcoming = sorted(
        {m for m in re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text) if m[:10] >= today}
    )
    if not upcoming:
        return None
    show_dt = upcoming[0]
    movie = None
    title = re.search(r"<title>([^<]+)</title>", text)
    if title:
        movie = title.group(1).split("|")[0].strip() or None
    try:
        label = _dt.fromisoformat(show_dt).strftime("%a %b %d · %I:%M %p")
    except ValueError:
        label = show_dt[:10]
    return {"date": show_dt[:10], "movie": movie, "label": label}


def is_real_seat(seat: dict) -> bool:
    """
    A cell that represents an actual, alertable seat: it must be displayed and
    must not be an aisle/gap (NotASeat) or a wheelchair space.
    """
    return bool(seat.get("shouldDisplay")) and seat.get("type") not in EXCLUDED_SEAT_TYPES


def is_good_seat(
    seat: dict,
    rows: int,
    columns: int,
    back_row_fraction: float = BACK_ROW_FRACTION,
    col_low: float = COL_CENTER_LOW,
    col_high: float = COL_CENTER_HIGH,
) -> bool:
    """
    Whether a seat is "good": in the back `back_row_fraction` of rows and within
    the centre [col_low, col_high] band of columns, scaled to this auditorium.
    Row 1 is the front, so the back band starts at (1 - back_row_fraction).
    """
    if rows <= 0 or columns <= 0:
        return False
    row_pos = (seat["row"] - 0.5) / rows       # 0 = front, 1 = back
    col_pos = (seat["column"] - 0.5) / columns  # 0 = left, 1 = right
    in_back = row_pos >= (1.0 - back_row_fraction)
    in_centre = col_low <= col_pos <= col_high
    return in_back and in_centre


def good_available_seats(layout: SeatLayout, **thresholds) -> List[dict]:
    """Real, currently-available seats that satisfy the good-seat geometry."""
    return [
        s
        for s in layout.seats
        if is_real_seat(s)
        and s.get("available")
        and is_good_seat(s, layout.rows, layout.columns, **thresholds)
    ]
