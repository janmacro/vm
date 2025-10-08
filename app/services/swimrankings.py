"""Utilities for importing personal bests from swimrankings.net."""
from __future__ import annotations

import re
from typing import Dict
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import Event


class SwimrankingsError(RuntimeError):
    """Raised when swimrankings data cannot be retrieved or parsed."""


def _normalize_label(label: str) -> str:
    return " ".join(label.lower().split())


def _build_event_lookup() -> Dict[str, Event]:
    lookup: Dict[str, Event] = {}
    for event in Event:
        base = _normalize_label(event.value)
        lookup[base] = event

        # common aliases per stroke
        if "free" in base:
            lookup[_normalize_label(base.replace("free", "freestyle"))] = event
        if "back" in base:
            lookup[_normalize_label(base.replace("back", "backstroke"))] = event
        if "breast" in base:
            lookup[_normalize_label(base.replace("breast", "breaststroke"))] = event
        if "fly" in base:
            lookup[_normalize_label(base.replace("fly", "butterfly"))] = event
        if "medley" in base:
            lookup[_normalize_label(base.replace("medley", "individual medley"))] = event
            lookup[_normalize_label(base.replace("medley", "im"))] = event
            lookup[_normalize_label(base.replace("medley", "i.m."))] = event
        # Allow shorthand like "50m IM"
        if " medley" in base:
            lookup[_normalize_label(base.replace(" medley", " im"))] = event
    return lookup


_EVENT_LOOKUP = _build_event_lookup()


def _map_event(label: str) -> Event | None:
    normalized = _normalize_label(label)
    return _EVENT_LOOKUP.get(normalized)


def _extract_athlete_id(identifier: str) -> str:
    candidate = identifier.strip()
    if not candidate:
        raise SwimrankingsError("Swimrankings identifier is required.")

    # Direct numeric ID
    if candidate.isdigit():
        return candidate

    # Try to parse from URL query string
    parsed = urlparse(candidate)
    if parsed.query:
        qs = parse_qs(parsed.query)
        athlete_ids = qs.get("athleteId")
        if athlete_ids:
            return athlete_ids[0]

    # Last attempt: search for numbers in the string
    match = re.search(r"(\d{4,})", candidate)
    if match:
        return match.group(1)

    raise SwimrankingsError("Could not determine athlete ID from input.")


def fetch_personal_bests(identifier: str) -> Dict[Event, Dict[str, str]]:
    """Fetch personal bests from swimrankings.net.

    Returns a mapping of Event -> {"points": str, "time": str, "course": str}.
    Prefers 50m course when multiple options exist.
    """
    athlete_id = _extract_athlete_id(identifier)
    url = (
        "https://www.swimrankings.net/index.php?page=athleteDetail"
        f"&athleteId={athlete_id}&language=us"
    )

    try:
        response = httpx.get(url, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SwimrankingsError(f"Unable to fetch Swimrankings data: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")

    heading = soup.find(lambda tag: tag.name in {"h2", "b"} and "Personal bests" in tag.get_text())
    if heading is None:
        raise SwimrankingsError("Personal bests section not found on Swimrankings page.")

    table = heading.find_next("table")
    if table is None:
        raise SwimrankingsError("Personal bests table missing from Swimrankings page.")

    results: Dict[Event, Dict[str, str]] = {}
    preferred_course = "50m"

    for row in table.find_all("tr"):
        # Skip header rows
        if row.find("th"):
            continue

        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        event_label = cells[0].get_text(strip=True)
        mapped_event = _map_event(event_label)
        if not mapped_event:
            continue

        course_cell = row.select_one("td.course")
        course = course_cell.get_text(strip=True) if course_cell else ""

        time_anchor = row.select_one("a.time")
        time_text = time_anchor.get_text(strip=True) if time_anchor else cells[2].get_text(strip=True)

        points_cell = row.select_one("td.code")
        points_text = points_cell.get_text(strip=True) if points_cell else cells[-1].get_text(strip=True)

        payload = {"points": points_text, "time": time_text, "course": course}
        existing = results.get(mapped_event)
        if existing is None:
            results[mapped_event] = payload
        else:
            existing_course = existing.get("course", "")
            if existing_course != preferred_course and course == preferred_course:
                results[mapped_event] = payload

    if not results:
        raise SwimrankingsError("No personal bests were parsed from Swimrankings.")

    return results
