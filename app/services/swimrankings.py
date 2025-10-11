"""Utilities for importing personal bests from swimrankings.net."""
from __future__ import annotations

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
        # "Medley" already matches Swimrankings values; no extra aliases needed.
    return lookup


_EVENT_LOOKUP = _build_event_lookup()


def _map_event(label: str) -> Event | None:
    normalized = _normalize_label(label)
    return _EVENT_LOOKUP.get(normalized)


def _extract_athlete_id(url: str) -> str:
    candidate = url.strip()
    if not candidate:
        raise SwimrankingsError("Swimrankings URL is required.")

    parsed = urlparse(candidate)
    if parsed.netloc != "www.swimrankings.net" or parsed.path != "/index.php":
        raise SwimrankingsError("Only swimrankings athlete detail URLs are supported.")

    qs = parse_qs(parsed.query)
    if qs.get("page", [None])[0] != "athleteDetail":
        raise SwimrankingsError("URL must include page=athleteDetail.")

    athlete_ids = qs.get("athleteId")
    if not athlete_ids or not athlete_ids[0].isdigit():
        raise SwimrankingsError("URL must include a numeric athleteId parameter.")

    return athlete_ids[0]


def _extract_gender(soup) -> str:
    icon = soup.find("img", src=lambda s: s and "images/gender" in s)
    if not icon or not icon.get("src"):
        raise SwimrankingsError("Unable to determine swimmer gender from Swimrankings page.")
    src = icon["src"].lower()
    if "gender1" in src:
        return "m"
    if "gender" in src:
        return "f"
    raise SwimrankingsError("Unknown gender icon on Swimrankings page.")


def _select_pb_table(soup) -> tuple:
    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        if not header_cells:
            continue
        headers = [cell.get_text(strip=True) for cell in header_cells]
        if any("Pts" in h for h in headers) and any("Event" in h for h in headers):
            return table, headers
    raise SwimrankingsError("Personal bests table missing from Swimrankings page.")


_ALLOWED_PBEST_SEASONS = {"2025", "2026"}


def fetch_personal_bests(
    identifier: str,
    expected_gender: str,
    season: str | None = None,
) -> Dict[Event, Dict[str, str]]:
    """Fetch personal bests from swimrankings.net.

    Returns a mapping of Event -> {"points": str, "time": str, "course": str}.
    """
    athlete_id = _extract_athlete_id(identifier)
    if season is not None and season not in _ALLOWED_PBEST_SEASONS:
        raise SwimrankingsError("Unsupported Swimrankings season filter.")

    url = (
        "https://www.swimrankings.net/index.php?page=athleteDetail"
        f"&athleteId={athlete_id}&language=us"
    )
    if season:
        url += f"&pbest={season}"

    try:
        response = httpx.get(url, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SwimrankingsError(f"Unable to fetch Swimrankings data: {exc}") from exc

    soup = BeautifulSoup(response.text, "html.parser")

    page_gender = _extract_gender(soup)
    if page_gender != expected_gender:
        raise SwimrankingsError("Swimmer gender on Swimrankings page does not match the roster entry.")

    heading = soup.find(lambda tag: tag.name in {"h2", "b"} and "Personal bests" in tag.get_text())
    if heading is None:
        raise SwimrankingsError("Personal bests section not found on Swimrankings page.")

    table, headers = _select_pb_table(soup)

    header_map = {header: idx for idx, header in enumerate(headers)}

    def _find_index(name: str) -> int | None:
        for header, idx in header_map.items():
            normalized = header.lower()
            if name in normalized:
                return idx
        return None

    event_idx = _find_index("event")
    points_idx = _find_index("pts")
    time_idx = _find_index("time")
    course_idx = _find_index("course")

    if event_idx is None or points_idx is None or time_idx is None:
        raise SwimrankingsError("Personal bests table is missing required columns.")

    results: Dict[Event, Dict[str, str]] = {}
    preferred_course = "25m"

    for row in table.find_all("tr"):
        # Skip header rows
        if row.find("th"):
            continue

        cells = row.find_all("td")
        if not cells or len(cells) <= max(event_idx, points_idx, time_idx):
            continue

        event_label = cells[event_idx].get_text(strip=True)
        mapped_event = _map_event(event_label)
        if not mapped_event:
            continue

        course = ""
        if course_idx is not None and len(cells) > course_idx:
            course = cells[course_idx].get_text(strip=True)

        time_cell = cells[time_idx]
        time_anchor = time_cell.find("a", class_="time")
        raw_time = time_anchor.get_text(strip=True) if time_anchor else time_cell.get_text(strip=True)
        if raw_time and raw_time.endswith("M"):
            raw_time = raw_time[:-1]
        raw_time = raw_time.strip() if raw_time else ""

        points_text = cells[points_idx].get_text(strip=True)
        if not points_text:
            continue

        payload = {"points": points_text, "time": raw_time, "course": course}
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
