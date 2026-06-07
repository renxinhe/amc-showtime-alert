#!/usr/bin/env python3
"""
List a theater/date's showings together with their AMC showtime ids.

The main scraper keeps only the time text and discards the booking link; the
seat-alert picker needs the per-showtime id (the integer in /showtimes/<id>), so
this does a focused fetch+parse of one theater+date listing.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List

try:
    import requests
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError as e:
    raise ImportError("requests and beautifulsoup4 are required") from e

from ..movie_format_utils import detect_format_label, resolve_block_formats
from .seat_map import AMC_BASE_URL, RSC_TOKEN

logger = logging.getLogger("SeatAlertListings")

# The listing page is parsed as HTML (<section aria-label="Showtimes for ...">),
# so — unlike the seats endpoint — we do NOT send the RSC header (which would
# return a component payload). The _rsc query param still bypasses the queue.
LISTING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": f"{AMC_BASE_URL}/",
}

_THEATER_KEYWORDS = ("AMC", "Theatre", "Theater")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)", re.IGNORECASE)
_DISCOUNT_RE = re.compile(r"\s*(UP\s+TO\s+)?\d+%\s+OFF\s*", re.IGNORECASE)


@dataclass
class Showing:
    movie: str
    time: str            # "7:00 PM"
    showtime_id: str     # "143838750"
    formats: List[str] = field(default_factory=list)
    sold_out: bool = False


def fetch_showings(market: str, slug: str, date: str) -> List[Showing]:
    """
    Return all showings at a theater on a date, each with its showtime id.
    `date` is YYYY-MM-DD. Returns [] on fetch/parse failure.
    """
    url = (
        f"{AMC_BASE_URL}/movie-theatres/{market}/{slug}/showtimes"
        f"?date={date}&_rsc={RSC_TOKEN}"
    )
    try:
        resp = requests.get(url, headers=LISTING_HEADERS, timeout=30)
        if not resp.ok:
            logger.warning(f"Listing fetch {slug} {date} -> {resp.status_code}")
            return []
    except requests.exceptions.RequestException as e:
        logger.warning(f"Listing fetch {slug} {date} failed: {e}")
        return []
    return parse_showings(resp.text)


def parse_showings(html: str) -> List[Showing]:
    """Parse showings (with ids, formats, sold-out) from a listing page's HTML."""
    showings: List[Showing] = []
    soup = BeautifulSoup(html, "html.parser")
    sections = soup.find_all("section", attrs={"aria-label": re.compile(r"Showtimes for")})
    for section in sections:
        label = section.get("aria-label", "")
        m = re.match(r"Showtimes for (.+)", label)
        if not m:
            continue
        movie = m.group(1)
        if any(k.lower() in movie.lower() for k in _THEATER_KEYWORDS):
            continue  # this section is the theater header, not a movie

        # Flatten the section into an ordered event stream (format labels, the
        # ":" delimiter, and showtime items), then resolve each showtime's block
        # format with the shared resolver — so "IMAX 70mm" blocks correctly
        # classify as "imax" rather than the trailing "70mm" sub-label.
        #
        # Each showtime is a <div role="group">. Its id comes from the booking
        # link when bookable, or — for SOLD-OUT shows, which have no link — from
        # the sibling <div id="<showtime_id>-details">, so sold-out showings can
        # still be targeted for a seat alert.
        events = []
        for node in section.descendants:
            if isinstance(node, NavigableString):
                text = str(node).strip()
                if text == ":":
                    events.append(("colon", None))
                else:
                    fmt = detect_format_label(text)
                    if fmt:
                        events.append(("fmt", fmt))
                continue
            if not (isinstance(node, Tag) and node.get("role") == "group"):
                continue
            # AMC wraps all showtimes in an outer role="group" that contains the
            # per-showtime role="group" cells. Only parse the leaf cells, or the
            # wrapper would masquerade as a duplicate of the first showtime and
            # steal its id.
            if node.find("div", attrs={"role": "group"}) is not None:
                continue

            item = _parse_showtime_cell(node)
            if item:
                events.append(("item", item))

        # The DOM can repeat a cell (e.g. responsive layouts), and a duplicate
        # may sit outside its format block. Dedup by id, merging so the format
        # is taken from whichever copy carries it.
        by_id = {}
        order = []
        for (time_str, showtime_id, sold_out), fmt in resolve_block_formats(events):
            existing = by_id.get(showtime_id)
            if existing is None:
                by_id[showtime_id] = Showing(
                    movie=movie, time=time_str, showtime_id=showtime_id,
                    formats=[fmt] if fmt else [], sold_out=sold_out,
                )
                order.append(showtime_id)
            else:
                if fmt and not existing.formats:
                    existing.formats = [fmt]
                if sold_out:
                    existing.sold_out = True
        showings.extend(by_id[sid] for sid in order)
    return showings


def _parse_showtime_cell(cell):
    """Extract (time, showtime_id, sold_out) from one <div role="group">, or None."""
    link = cell.find("a", href=re.compile(r"/showtimes/\d+"))
    if link:
        showtime_id = re.search(r"/showtimes/(\d+)", link["href"]).group(1)
    else:
        details = cell.find("div", id=re.compile(r"^\d+-details$"))
        showtime_id = details["id"].split("-", 1)[0] if details else None
    if not showtime_id:
        return None

    raw = cell.get_text(" ", strip=True)
    tm = _TIME_RE.search(_DISCOUNT_RE.sub(" ", raw))
    if not tm:
        return None
    time_str = f"{tm.group(1)}:{tm.group(2)} {tm.group(3).upper()}"
    return time_str, showtime_id, ("sold out" in raw.lower())
