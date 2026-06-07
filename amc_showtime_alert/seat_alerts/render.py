#!/usr/bin/env python3
"""
Render a seat map to a PNG from the structured seatingLayout.

We draw the chart ourselves from the seat JSON (row/column/available/type +
grid size) rather than screenshotting AMC (their page is queue-walled and would
need a headless browser). Newly-opened good seats can be highlighted so the
"good seat opened up" alert shows exactly which seats are now free.

Colour key:
  highlighted -> gold (the seats this alert is about)
  good+free   -> green   (back-half rows, centre columns, available)
  free        -> blue    (available but outside the good zone)
  taken       -> dark gray
Gaps / non-seats are omitted, so the auditorium shape emerges naturally.
"""

import io
from typing import Iterable, Optional

from PIL import Image, ImageDraw

from .seat_map import SeatLayout, is_good_seat, is_real_seat

# Drawing geometry (pixels).
_CELL = 18
_GAP = 3
_MARGIN = 16
_SCREEN_H = 26
_LEGEND_H = 30

# Colours (RGB).
_BG = (17, 24, 39)
_SCREEN = (75, 85, 99)
_TEXT = (229, 231, 235)
_TAKEN = (55, 65, 81)
_FREE = (59, 130, 246)
_GOOD = (34, 197, 94)
_HILITE = (250, 204, 21)


def render_seat_map(
    layout: SeatLayout,
    highlight: Optional[Iterable[str]] = None,
    **thresholds,
) -> bytes:
    """Render the layout to PNG bytes, optionally highlighting seats by name."""
    highlight = set(highlight or ())
    rows, cols = max(layout.rows, 1), max(layout.columns, 1)

    grid_w = cols * (_CELL + _GAP) - _GAP
    grid_h = rows * (_CELL + _GAP) - _GAP
    width = grid_w + 2 * _MARGIN
    top = _MARGIN + _SCREEN_H + 10  # screen bar sits above the grid (front = row 1)
    height = top + grid_h + _LEGEND_H + _MARGIN

    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    # SCREEN bar at the front.
    draw.rectangle([_MARGIN, _MARGIN, _MARGIN + grid_w, _MARGIN + _SCREEN_H], fill=_SCREEN)
    draw.text((width // 2 - 24, _MARGIN + 7), "SCREEN", fill=_TEXT)

    for seat in layout.seats:
        if not is_real_seat(seat):
            continue  # omit gaps / wheelchair / hidden
        cx = _MARGIN + (seat["column"] - 1) * (_CELL + _GAP)
        cy = top + (seat["row"] - 1) * (_CELL + _GAP)
        box = [cx, cy, cx + _CELL, cy + _CELL]

        name = seat.get("name", "")
        if name and name in highlight:
            color, outline = _GOOD, _HILITE
        elif not seat.get("available"):
            color, outline = _TAKEN, None
        elif is_good_seat(seat, layout.rows, layout.columns, **thresholds):
            color, outline = _GOOD, None
        else:
            color, outline = _FREE, None

        draw.rounded_rectangle(box, radius=3, fill=color)
        if outline:
            draw.rounded_rectangle(box, radius=3, outline=outline, width=2)

    # Legend.
    ly = top + grid_h + 8
    swatches = [(_HILITE, "newly open"), (_GOOD, "good"), (_FREE, "available"), (_TAKEN, "taken")]
    x = _MARGIN
    for color, text in swatches:
        draw.rounded_rectangle([x, ly, x + 14, ly + 14], radius=3, fill=color)
        draw.text((x + 20, ly + 2), text, fill=_TEXT)
        x += 30 + 7 * len(text) + 16

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
