#!/usr/bin/env python3
"""
Movie Format Utilities
Helpers for recognizing and normalizing AMC premium-format ("experience") labels.

AMC renders premium-format labels (e.g. "IMAX at AMC", "Dolby Cinema at AMC") as
headings above the showtimes shown in that format. We normalize the raw label
text to a short token so user alerts can filter by "imax", "dolby", etc.
"""

from typing import List, Optional


# Each entry maps a normalized token -> list of case-insensitive substrings that
# identify that format in the raw AMC label.
# Order matters: the first token whose substring is found wins.
# "standard" is the implicit fallback when no premium label applies.
FORMAT_TOKEN_PATTERNS: List[tuple] = [
    ("imax", ["imax"]),
    ("70mm", ["70mm", "70 mm"]),
    ("dolby", ["dolby"]),
    ("screenx", ["screenx", "screen x"]),
    ("3d", ["3d"]),
    ("standard", ["standard"]),
]

# Tokens a user is allowed to specify in a `format:` alert filter.
KNOWN_FORMAT_TOKENS: List[str] = [token for token, _ in FORMAT_TOKEN_PATTERNS]


def higher_priority_format(a: str, b: str) -> str:
    """
    Return the higher-priority of two format tokens (earlier in
    FORMAT_TOKEN_PATTERNS wins). Used when a single experience block carries
    several format sub-labels — e.g. "IMAX 70mm" then "70mm" — so the showtimes
    resolve to the dominant format ("imax") rather than the last one seen.
    """
    order = KNOWN_FORMAT_TOKENS
    ia = order.index(a) if a in order else len(order)
    ib = order.index(b) if b in order else len(order)
    return a if ia <= ib else b


def resolve_block_formats(events):
    """
    Resolve the premium format of each showtime from an ordered event stream.

    AMC renders each experience block as a primary heading followed by ":" and a
    tagline, then its showtimes — and a block may carry secondary format
    sub-labels before its showtimes (e.g. "IMAX 70mm" then "IMAX at AMC" then
    "70mm"). A format label followed by ":" starts a block; a label not followed
    by ":" either starts a new block (when the previous one already produced
    showtimes) or refines the current block, keeping the higher-priority format
    (so "IMAX 70mm" + "70mm" resolves to "imax", not "70mm").

    Args:
        events: ordered list of (kind, value) where kind is one of:
            "fmt"   -> value is a normalized format token
            "colon" -> the ":" delimiter (value ignored)
            "item"  -> a showtime; value is opaque (returned back to the caller)

    Returns:
        list of (item_value, format_token_or_None), one per "item" event, in order.
    """
    current = None
    emitted_since_label = False
    out = []
    n = len(events)
    for i, (kind, value) in enumerate(events):
        if kind == "fmt":
            followed_by_colon = i + 1 < n and events[i + 1][0] == "colon"
            if followed_by_colon:
                current, emitted_since_label = value, False
            elif current is None or emitted_since_label:
                current, emitted_since_label = value, False
            else:
                current = higher_priority_format(current, value)
        elif kind == "item":
            out.append((value, current))
            emitted_since_label = True
    return out


# Friendly labels for normalized tokens, used in messages and listings.
FORMAT_DISPLAY: dict = {
    "70mm": "Regular 70mm",
    "imax": "IMAX",
    "dolby": "Dolby Cinema",
    "screenx": "ScreenX",
    "3d": "3D",
    "standard": "Standard",
}


def format_display(token: Optional[str]) -> str:
    """Human-friendly name for a normalized format token."""
    if not token:
        return "Any format"
    return FORMAT_DISPLAY.get(token, token)


def normalize_format(raw_label: str) -> str:
    """
    Map a raw AMC format label to a normalized token.

    Args:
        raw_label: The format heading text from AMC (e.g. "IMAX with Laser at AMC")

    Returns:
        A normalized token (e.g. "imax"), or "standard" if nothing matches.
    """
    text = (raw_label or "").lower()
    for token, needles in FORMAT_TOKEN_PATTERNS:
        if any(needle in text for needle in needles):
            return token
    return "standard"


def detect_format_label(text: str) -> Optional[str]:
    """
    Return the normalized token if `text` looks like a premium-format heading,
    else None.

    Unlike normalize_format, this does NOT fall back to "standard" for arbitrary
    text — it only returns a token when a recognizable format keyword is present.
    This lets the scraper distinguish a format heading from other text nodes
    (showtimes, badges) while walking the HTML in document order.
    """
    if not text:
        return None
    low = text.strip().lower()
    for token, needles in FORMAT_TOKEN_PATTERNS:
        if token == "standard":
            if low in ("standard", "standard format"):
                return token
            continue
        if any(needle in low for needle in needles):
            return token
    return None
