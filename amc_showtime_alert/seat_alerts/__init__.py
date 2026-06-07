"""Seat alerts: watch a specific AMC showing and notify when a good seat opens.

- seat_map:  fetch + evaluate a showtime's seat layout (good-seat geometry)
- listings:  list a theater/date's showings with their showtime ids (for the picker)
- manager:   SeatAlertManager — soft-deletable seat_alerts storage
- poller:    poll watched showings and notify on newly-available good seats
"""

from .manager import SeatAlert, SeatAlertManager

__all__ = ["SeatAlert", "SeatAlertManager"]
