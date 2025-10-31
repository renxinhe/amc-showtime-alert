#!/usr/bin/env python3
"""
Simplified Special Events Parser for AMC Showtime Data
Parses scraped JSON data to find special events using regex pattern matching.

USAGE GUIDE:
============

Basic Usage:
    python special_events_parser.py <json_file>

Examples:
    # Parse latest showtimes data
    python special_events_parser.py output/amc_showtimes_20251029_180649.json

    # Parse any showtimes JSON file
    python special_events_parser.py path/to/your/showtimes.json

Input:
    - JSON file from AMC scraper (amc_scraper.py output)
    - Must contain 'results' array with theater and movie data

Output:
    - Creates timestamped file:
      output/amc_showtimes_special_YYYYMMDD_HHMMSS.json
    - Contains all special events found in the input data
    - Includes metadata: parse time, source file, event counts

Special Events Detected:
    - Q&A sessions (Live Q&A, Q&A, Q & A, etc.)
    - Early Access screenings
    - Advance screenings
    - Special events and fan events
    - One night only screenings
    - Sneak peeks and premiere events
    - Talkbacks and panel discussions

Output Format:
    {
        "parsed_at": "2025-10-29T18:11:43.915005",
        "source_file": "output/amc_showtimes_20251029_180649.json",
        "total_events_found": 11,
        "events": [
            {
                "movie_name": "Die My Love Live Q&A with Jennifer Lawrence",
                "event_type": "Q&A",
                "theater": "AMC Lincoln Square 13",
                "date": "2025-11-06",
                "showtimes": ["7:00 PM"],
                "runtime": 130,
                "rating": "R",
                "matched_pattern": "Live Q&A",
                "slug": "die-my-love-live-q-a-with-jennifer-lawrence"
            }
        ]
    }

Error Handling:
    - File not found: Clear error message
    - Invalid JSON: Parse error details
    - Missing data: Warning messages for missing keys

Requirements:
    - Python 3.6+
    - Input JSON file from AMC scraper
    - 'output' directory (created automatically)
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .schema import EventType


def find_special_events(json_data: Dict) -> List[Dict]:
    """
    Find special events in the loaded JSON data

    Args:
        json_data: Parsed JSON data from showtimes file

    Returns:
        List of special event dictionaries
    """
    # Regex pattern for detecting special events in movie titles
    SPECIAL_EVENTS_PATTERN = re.compile(
        r"""(?ix)
        \b(
            live(?:\s*[- ]?stream(?:ed|ing)?)?\s*q\W*a      # "Live Q&A", "Livestream Q&A", etc.
          | q\s*(?:&|&amp;|and|\+|\/)\s*a                   # "Q&A", "Q & A", "Q and A", "Q+A", "Q/A"
          | q\W*a                                           # fallback: "Q A", "Q—A", etc.
          | early\s*access
          | advance(?:d)?\s*screening
          | special\s*(?:screening|event)
          | fan\s*event
          | one\s*night\s*only
          | sneak\s*peek
          | premiere\s*event
          | talkback
          | panel\s+discussion
        )\b
    """,
        re.VERBOSE,
    )

    special_events: List[Dict] = []

    if "results" not in json_data:
        print("Warning: No 'results' key found in JSON data")
        return special_events

    for result in json_data["results"]:
        if not result.get("success", False):
            continue

        theater = result.get("theater", "Unknown Theater")
        date = result.get("date", "Unknown Date")

        for movie in result.get("movies", []):
            movie_name = movie.get("name", "")
            if not movie_name:
                continue

            # Check if movie title contains special event patterns
            matches = SPECIAL_EVENTS_PATTERN.findall(movie_name)

            if matches:
                # Determine event type based on the matched pattern
                event_type = classify_event_type(matches[0])

                special_event = {
                    "movie_name": movie_name,
                    "event_type": event_type,
                    "theater": theater,
                    "date": date,
                    "showtimes": movie.get("showtimes", []),
                    "runtime": movie.get("runtime"),
                    "rating": movie.get("rating", ""),
                    "matched_pattern": matches[0],
                    "slug": movie.get("slug", ""),
                }

                special_events.append(special_event)
                print(f"Found special event: {event_type} - {movie_name} at {theater}")

    print(f"Found {len(special_events)} special events total")
    return special_events


def classify_event_type(matched_pattern: str) -> EventType:
    """
    Classify the type of special event based on the matched pattern

    Args:
        matched_pattern: The regex pattern that was matched

    Returns:
        EventType enum value
    """
    pattern_lower = matched_pattern.lower().strip()

    if ("live" in pattern_lower and "q" in pattern_lower and "a" in pattern_lower) or (
        "q" in pattern_lower and "a" in pattern_lower
    ):
        return EventType.QA
    elif "early access" in pattern_lower:
        return EventType.EARLY_ACCESS
    elif "advance" in pattern_lower and "screening" in pattern_lower:
        return EventType.ADVANCE_SCREENING
    elif "special" in pattern_lower and (
        "screening" in pattern_lower or "event" in pattern_lower
    ):
        return EventType.SPECIAL_EVENT
    elif "fan event" in pattern_lower:
        return EventType.FAN_EVENT
    elif "one night only" in pattern_lower:
        return EventType.ONE_NIGHT_ONLY
    elif "sneak peek" in pattern_lower:
        return EventType.SNEAK_PEEK
    elif "premiere" in pattern_lower and "event" in pattern_lower:
        return EventType.PREMIERE_EVENT
    elif "talkback" in pattern_lower:
        return EventType.TALKBACK
    elif "panel" in pattern_lower and "discussion" in pattern_lower:
        return EventType.PANEL_DISCUSSION
    else:
        return EventType.SPECIAL_EVENT


def main():
    """Main entry point - takes JSON file and outputs special events"""
    if len(sys.argv) != 2:
        print("Usage: python special_events_parser.py <json_file>")
        sys.exit(1)

    json_file = sys.argv[1]

    try:
        # Load JSON data
        with open(json_file, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        print(f"Loaded JSON data from {json_file}")

        # Find special events
        special_events = find_special_events(json_data)

        # Create output directory if it doesn't exist
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)

        # Extract timestamp from input filename
        input_filename = Path(json_file).name
        # Look for pattern: amc_showtimes_YYYYMMDD_HHMMSS.json
        import re

        timestamp_match = re.search(
            r"amc_showtimes_(\d{8}_\d{6})\.json", input_filename
        )

        if timestamp_match:
            timestamp = timestamp_match.group(1)
        else:
            # Fallback to current timestamp if pattern not found
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            print(
                f"Warning: Could not extract timestamp from filename '{input_filename}', using current time"
            )

        output_file = output_dir / f"amc_showtimes_special_{timestamp}.json"

        # Save results
        output_data = {
            "parsed_at": datetime.now().isoformat(),
            "source_file": json_file,
            "total_events_found": len(special_events),
            "events": special_events,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"Special events saved to: {output_file}")

        # Print summary
        if special_events:
            print(f"\n📊 Found {len(special_events)} special events:")
            by_type = {}
            for event in special_events:
                event_type = event["event_type"]
                if event_type not in by_type:
                    by_type[event_type] = 0
                by_type[event_type] += 1

            for event_type, count in by_type.items():
                print(f"   {event_type}: {count}")
        else:
            print("\n🎬 No special events found!")

    except FileNotFoundError:
        print(f"❌ Error: File not found: {json_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in file {json_file}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
