#!/usr/bin/env python3
"""
Alert Matcher
Matches scraped showtimes against per-user custom alerts.

Given the raw scraper output (all movies at all theaters) and the list of active
alerts, this produces one AlertMatch per (alert, movie/date) that satisfies the
alert's theater, title and format criteria. The notifier then dedups and sends
each match to its owning user.

This is independent of the legacy special-events parser, which still drives the
global Q&A broadcast.
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("AlertMatcher")

# --- Regex safety -----------------------------------------------------------
# User-supplied regex is not fully trusted. We cap length and reject a few
# obviously catastrophic constructs (nested quantifiers) to limit ReDoS risk;
# Python's re has no execution timeout. Keyword alerts skip regex entirely.
MAX_PATTERN_LENGTH = 200

# Heuristic: a quantifier applied to a group that itself contains a quantifier,
# e.g. (a+)+, (a*)*, (.*)+, (a+)* — the classic ReDoS shapes.
_CATASTROPHIC_RE = re.compile(r"\([^)]*[+*][^)]*\)\s*[+*]")


def validate_pattern(
    pattern: str, is_regex: bool
) -> Tuple[bool, Optional[str]]:
    """
    Validate a user-supplied alert pattern.

    Returns (ok, error_message). For keyword patterns we only check emptiness and
    length. For regex we additionally compile it and reject catastrophic shapes.
    """
    if not pattern or not pattern.strip():
        return False, "Pattern is empty."
    if len(pattern) > MAX_PATTERN_LENGTH:
        return False, f"Pattern too long (max {MAX_PATTERN_LENGTH} characters)."

    if is_regex:
        if _CATASTROPHIC_RE.search(pattern):
            return False, "Pattern looks unsafe (nested quantifiers)."
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return False, f"Invalid regex: {e}"

    return True, None


@dataclass
class AlertMatch:
    """A movie/date that satisfied a user's alert"""

    chat_id: int
    alert_id: int
    theater: str  # theater display name (as in scraped output)
    theater_slug: str
    date: str
    movie_name: str
    slug: str
    runtime: Optional[int]
    rating: str
    format_filter: Optional[str]
    matched_showtimes: List[str]


def _compile_alert(alert) -> Tuple[bool, Optional[re.Pattern]]:
    """
    Prepare an alert's title matcher once.

    Returns (usable, compiled_regex). For keyword alerts compiled_regex is None
    and matching uses a case-insensitive substring test. An invalid/unsafe regex
    yields (False, None) so the caller skips the alert for this run.
    """
    if not alert.is_regex:
        return True, None
    ok, _ = validate_pattern(alert.pattern, is_regex=True)
    if not ok:
        logger.warning(f"Skipping alert #{alert.id}: unusable regex {alert.pattern!r}")
        return False, None
    try:
        return True, re.compile(alert.pattern, re.IGNORECASE)
    except re.error as e:
        logger.warning(f"Skipping alert #{alert.id}: regex error {e}")
        return False, None


def _title_matches(alert, compiled: Optional[re.Pattern], title: str) -> bool:
    if compiled is not None:
        try:
            return compiled.search(title) is not None
        except re.error:
            return False
    return alert.pattern.lower() in title.lower()


def _matched_showtimes(alert, movie: Dict) -> Optional[List[str]]:
    """
    Determine which of a movie's showtimes satisfy the alert's format filter.

    Returns the list of matching time strings, or None if the alert does not
    match this movie at all (no qualifying showtimes / format unconfirmable).
    """
    showtimes: List[str] = movie.get("showtimes", []) or []

    if not alert.format_filter:
        return showtimes if showtimes else None

    details = movie.get("showtime_details") or []
    if not details:
        # No structured format info (e.g. legacy data) — cannot confirm format.
        return None

    matched = [
        d.get("time")
        for d in details
        if alert.format_filter in (d.get("formats") or [])
    ]
    matched = [t for t in matched if t]
    return matched if matched else None


def find_alert_matches(
    scraped_data: Dict,
    alerts: List,
    theater_slug_by_name: Dict[str, str],
) -> List[AlertMatch]:
    """
    Match all scraped movies against all alerts.

    Args:
        scraped_data: parsed scraper JSON (contains a "results" list)
        alerts: active Alert rows (see alert_manager.Alert)
        theater_slug_by_name: maps a theater's display name to its slug

    Returns:
        A flat list of AlertMatch, to be grouped per-user by the notifier.
    """
    matches: List[AlertMatch] = []

    results = scraped_data.get("results")
    if not results:
        logger.warning("No 'results' in scraped data; no alerts matched")
        return matches

    if not alerts:
        return matches

    # Compile each alert's title matcher once.
    prepared = []
    for alert in alerts:
        usable, compiled = _compile_alert(alert)
        if usable:
            prepared.append((alert, compiled))

    for result in results:
        if not result.get("success", False):
            continue

        theater_name = result.get("theater", "")
        theater_slug = theater_slug_by_name.get(theater_name)
        if theater_slug is None:
            logger.warning(
                f"Theater name {theater_name!r} not found in config; "
                "skipping its showtimes for alert matching"
            )
            continue
        date = result.get("date", "")

        for movie in result.get("movies", []):
            title = movie.get("name", "")
            if not title:
                continue

            for alert, compiled in prepared:
                # Theater filter: None means all theaters.
                if alert.theater_slug and alert.theater_slug != theater_slug:
                    continue

                if not _title_matches(alert, compiled, title):
                    continue

                matched_times = _matched_showtimes(alert, movie)
                if not matched_times:
                    continue

                matches.append(
                    AlertMatch(
                        chat_id=alert.chat_id,
                        alert_id=alert.id,
                        theater=theater_name,
                        theater_slug=theater_slug,
                        date=date,
                        movie_name=title,
                        slug=movie.get("slug", ""),
                        runtime=movie.get("runtime"),
                        rating=movie.get("rating", ""),
                        format_filter=alert.format_filter,
                        matched_showtimes=matched_times,
                    )
                )

    logger.info(f"Alert matching produced {len(matches)} match(es)")
    return matches
