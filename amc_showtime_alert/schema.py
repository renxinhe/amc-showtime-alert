#!/usr/bin/env python3
"""
Schema Definitions
Centralized dataclasses and enums used across the AMC Showtime Alert system.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import List, Optional


# Enums
class EventType(StrEnum):
    """String enum for special event types"""

    ADVANCE_SCREENING = "Advance Screening"
    EARLY_ACCESS = "Early Access"
    FAN_EVENT = "Fan Event"
    ONE_NIGHT_ONLY = "One Night Only"
    PANEL_DISCUSSION = "Panel Discussion"
    PREMIERE_EVENT = "Premiere Event"
    QA = "Q&A"
    SNEAK_PEEK = "Sneak Peek"
    SPECIAL_EVENT = "Special Event"
    TALKBACK = "Talkback"


# Notification-related dataclasses
@dataclass
class ShowtimeChange:
    """Represents changes in showtimes for a movie"""
    added: List[str]
    removed: List[str]
    unchanged: List[str]


@dataclass
class EventData:
    """Represents a movie event with all required information"""
    movie_name: str
    theater: str
    date: str
    slug: str
    event_type: EventType
    showtimes: List[str]
    runtime: Optional[int] = None
    rating: str = ''


# Scraper-related dataclasses
@dataclass
class Movie:
    """Represents a movie with its details"""

    name: str
    slug: str
    runtime: Optional[int]
    rating: str
    showtimes: List[str]

    def is_valid(self) -> bool:
        """Validate movie data"""
        return (
            bool(self.name)
            and bool(self.slug)
            and len(self.showtimes) > 0
            and all(self._is_valid_time(t) for t in self.showtimes)
        )

    @staticmethod
    def _is_valid_time(time_str: str) -> bool:
        """Check if time string is in valid format (e.g., '7:30 PM')"""
        return bool(re.match(r"^\d{1,2}:\d{2}\s*(AM|PM)$", time_str))


@dataclass
class DailyShowtimes:
    """Represents showtimes for a specific date at a theater"""

    date: str
    theater: str
    movies: List[Movie]
    fetch_time: str
    success: bool
    error_message: Optional[str] = None

    def is_valid(self) -> bool:
        """Validate daily showtimes data"""
        return self.success and len(self.movies) > 0
