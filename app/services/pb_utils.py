"""Helper utilities for parsing and formatting swim times."""
from __future__ import annotations

from typing import Optional


def parse_time_to_seconds(value: str | None) -> Optional[float]:
    """Convert a time string like "1:05.32" to seconds.

    Accepts formats ss,ss.ss, m:ss, m:ss.ss, or h:mm:ss, ignoring whitespace.
    Returns None when the input is empty.
    """
    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    parts = raw.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0].replace(",", "."))
        seconds = float(parts[-1].replace(",", "."))
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) == 3 else 0
    except ValueError as exc:
        raise ValueError(f"Invalid time format: {value}") from exc

    total = seconds + minutes * 60 + hours * 3600
    return total


def format_seconds_to_time(seconds: Optional[float]) -> str:
    """Format seconds back to a string suitable for display."""
    if seconds is None:
        return ""

    remainder = float(seconds)
    hours = int(remainder // 3600)
    remainder -= hours * 3600
    minutes = int(remainder // 60)
    remainder -= minutes * 60

    if hours:
        return f"{hours}:{minutes:02d}:{remainder:05.2f}"
    if minutes:
        return f"{minutes}:{remainder:05.2f}"
    text = f"{remainder:.2f}"
    return text.rstrip("0").rstrip(".")
