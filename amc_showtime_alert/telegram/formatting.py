#!/usr/bin/env python3
"""Render alerts into Telegram HTML (cards and aligned monospace tables)."""

from typing import Dict, List

from ..movie_format_utils import format_display


def html_escape(text: str) -> str:
    """Escape the three characters that matter for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def alert_theater(alert, name_by_slug: Dict[str, str]) -> str:
    """Human-readable theater for an alert ('All theaters' when unscoped)."""
    if alert.theater_slug:
        return name_by_slug.get(alert.theater_slug, alert.theater_slug)
    return "All theaters"


def format_alert_card(alert, name_by_slug: Dict[str, str]) -> str:
    """Render one alert as a tidy multi-line HTML card (for confirmations)."""
    theater = alert_theater(alert, name_by_slug)
    fmt = format_display(alert.format_filter) if alert.format_filter else "Any format"
    regex = " <i>(regex)</i>" if alert.is_regex else ""
    return (
        f"<b>#{alert.id}</b>  🎬 <b>{html_escape(alert.pattern)}</b>{regex}\n"
        f"     📍 {html_escape(theater)}\n"
        f"     🎟 {html_escape(fmt)}"
    )


def format_alerts_table(alerts: List, name_by_slug: Dict[str, str]) -> str:
    """Render alerts as an aligned monospace HTML table."""
    headers = ("#", "Movie", "Theater", "Format")
    rows = []
    for a in alerts:
        movie = a.pattern + ("*" if a.is_regex else "")
        theater = "All" if not a.theater_slug else alert_theater(a, name_by_slug)
        fmt = format_display(a.format_filter) if a.format_filter else "Any"
        rows.append((str(a.id), movie, theater, fmt))

    widths = [max(len(r[i]) for r in (headers, *rows)) for i in range(len(headers))]

    def row(cells):
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    sep = "  ".join("-" * w for w in widths)
    body = "\n".join([row(headers), sep, *(row(r) for r in rows)])
    table = "<pre>" + html_escape(body) + "</pre>"
    if any(a.is_regex for a in alerts):
        table += "\n<i>* = regex</i>"
    return table
