#!/usr/bin/env python3
"""Inline-keyboard builders for the guided /addalert flow.

Callback data scheme (kept well under Telegram's 64-byte limit):
  t:<slug>  toggle a theater      t:*  toggle "all theaters"   t:done  next step
  f:<token> toggle a format       f:*  toggle "any format"     f:done  create
  x         cancel
"""

from typing import List

from ..movie_format_utils import KNOWN_FORMAT_TOKENS, format_display


def _mark(key, selected: set) -> str:
    return "✅ " if key in selected else "▫️ "


def theater_keyboard(theaters: List[dict], selected: set) -> dict:
    rows = [[{"text": _mark("*", selected) + "🌐 All theaters", "callback_data": "t:*"}]]
    for t in theaters:
        slug = t.get("slug")
        rows.append(
            [{"text": _mark(slug, selected) + t.get("name", slug),
              "callback_data": f"t:{slug}"}]
        )
    rows.append(
        [
            {"text": "➡️ Next", "callback_data": "t:done"},
            {"text": "✖️ Cancel", "callback_data": "x"},
        ]
    )
    return {"inline_keyboard": rows}


def format_keyboard(selected: set) -> dict:
    rows = [[{"text": _mark("*", selected) + "✨ Any format", "callback_data": "f:*"}]]
    row = []
    for tok in KNOWN_FORMAT_TOKENS:
        row.append(
            {"text": _mark(tok, selected) + format_display(tok),
             "callback_data": f"f:{tok}"}
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            {"text": "✅ Create", "callback_data": "f:done"},
            {"text": "✖️ Cancel", "callback_data": "x"},
        ]
    )
    return {"inline_keyboard": rows}


def delete_keyboard(alerts: List) -> dict:
    """One button per alert (tap to delete), plus Cancel.

    Callback data: del:<id> to delete, del:x to cancel.
    """
    rows = []
    for a in alerts:
        label = f"🗑 #{a.id} {a.pattern}"
        if a.format_filter:
            label += f" · {format_display(a.format_filter)}"
        rows.append([{"text": label[:60], "callback_data": f"del:{a.id}"}])
    rows.append([{"text": "✖️ Cancel", "callback_data": "del:x"}])
    return {"inline_keyboard": rows}


def toggle_selection(selected: set, value: str):
    """Toggle a value, keeping '*' (all/any) mutually exclusive with others."""
    if value in selected:
        selected.discard(value)
        return
    if value == "*":
        selected.clear()
    else:
        selected.discard("*")
    selected.add(value)
