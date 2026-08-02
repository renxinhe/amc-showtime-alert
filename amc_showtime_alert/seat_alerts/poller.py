#!/usr/bin/env python3
"""
Poll watched showings and notify when *new* good seats become available.

For each active, non-expired seat alert: fetch the seat map, compute the
good+available seats, and notify the owner about any that weren't good+available
last time. The last-seen good set is stored per alert, so a seat that is taken
and later frees up notifies again, while a continuously-available seat doesn't
spam.

Fetches are spaced out (AMC rate-limits the seats endpoint with 429s) and any
unreachable showing is simply retried next cycle.
"""

import logging
import time
from typing import Callable, Dict, List, Optional

from .manager import SeatAlert, SeatAlertManager
from .seat_map import SeatLayout, fetch_seat_layout, good_available_seats

logger = logging.getLogger("SeatAlertPoller")

# Seconds between seat-map fetches, to stay under AMC's rate limit.
FETCH_DELAY_SECONDS = 1.5

# Cap how many seat names we list in one message.
MAX_SEATS_SHOWN = 10

FetchFn = Callable[[str], Optional[SeatLayout]]
SendFn = Callable[[int, str], bool]
SendPhotoFn = Callable[[int, bytes, str], bool]


def _format_message(
    alert: SeatAlert,
    new_seats: List[str],
    all_good: List[str],
    theater_name: str = "",
) -> str:
    shown = ", ".join(new_seats[:MAX_SEATS_SHOWN])
    if len(new_seats) > MAX_SEATS_SHOWN:
        shown += f" (+{len(new_seats) - MAX_SEATS_SHOWN} more)"
    header = alert.movie_name or "Your watched showing"
    label = alert.showtime_label or alert.showtime_date
    detail = " · ".join(p for p in (theater_name, label) if p)
    return (
        "🎟 Good seat opened up!\n"
        f"{header} — {detail}\n"
        f"New good seats: {shown}\n"
        f"({len(all_good)} good seat(s) available now)"
    )


def _notify(alert, message, new_seats, layout, send, send_photo) -> bool:
    """Deliver the alert as a seat-map photo when possible, else as text."""
    if send_photo is not None:
        try:
            from .render import render_seat_map

            png = render_seat_map(layout, highlight=new_seats)
            return send_photo(alert.chat_id, png, message)
        except Exception as e:  # rendering/Pillow issue — degrade to text
            logger.warning(f"Seat-map render/send failed, using text: {e}")
    return send(alert.chat_id, message)


def poll_seat_alerts(
    db_path: str,
    send: SendFn,
    fetch: Optional[FetchFn] = None,
    send_photo: Optional[SendPhotoFn] = None,
    sleep: Callable[[float], None] = time.sleep,
    delay: float = FETCH_DELAY_SECONDS,
    theater_names: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """
    Check every pollable seat alert once.

    `send(chat_id, text)` delivers a text notification; when `send_photo` is
    given, fires are delivered as a rendered seat-map image with the text as the
    caption (falling back to text on any render error). `fetch(showtime_id)` is
    injectable for testing. `theater_names` maps theater slug → display name, so
    alerts can name their theater; slugs missing from it are simply omitted.
    """
    theater_names = theater_names or {}
    fetch = fetch or fetch_seat_layout
    mgr = SeatAlertManager(db_path)
    alerts = mgr.get_pollable()
    stats = {"checked": 0, "notified": 0, "unreachable": 0}

    for i, alert in enumerate(alerts):
        layout = fetch(alert.showtime_id)
        stats["checked"] += 1
        if layout is None:
            stats["unreachable"] += 1
            continue  # transient (queue/429/format change) — retry next cycle

        good_names = sorted(s["name"] for s in good_available_seats(layout))
        previously = set(alert.last_good_seats)
        new_seats = [n for n in good_names if n not in previously]

        if new_seats:
            theater = theater_names.get(alert.theater_slug, "") if alert.theater_slug else ""
            message = _format_message(alert, new_seats, good_names, theater)
            if _notify(alert, message, new_seats, layout, send, send_photo):
                stats["notified"] += 1
        mgr.update_last_good_seats(alert.id, good_names)

        if delay and i < len(alerts) - 1:
            sleep(delay)

    return stats
