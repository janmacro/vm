"""Blueprint for running and viewing lineup optimizations."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from flask import Blueprint, render_template, request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import Event, Swimmer
from ..services import optimizer

bp = Blueprint("optimize", __name__, url_prefix="/optimize")

COMPETITION_OPTIONS: List[str] = ["Allgemeine Kategorie", "Nachwuchs"]


def _collect_active_swimmers(gender: str) -> List[Swimmer]:
    stmt = (
        select(Swimmer)
        .options(selectinload(Swimmer.pbs))
        .where(Swimmer.gender == gender, Swimmer.active.is_(True))
    )
    return list(db.session.scalars(stmt))


def _format_solution(
    assignment,
    segments,
    swimmer_lookup: Dict[int, Swimmer],
    selected_competition: str,
) -> Dict[str, Any]:
    segment_offsets = []
    running = 0
    for seg in segments:
        segment_offsets.append(running)
        running += len(seg)
    total_points = sum(item[4] for item in assignment)
    segment_rows: List[Dict[str, Any]] = []
    for seg_idx, seg_events in enumerate(segments):
        rows = []
        for slot, assigned_seg_idx, event, swimmer_id, pts in assignment:
            if assigned_seg_idx != seg_idx:
                continue
            local_slot = slot - segment_offsets[seg_idx] + 1
            rows.append(
                {
                    "slot": local_slot,
                    "event": event.value,
                    "swimmer": swimmer_lookup.get(swimmer_id).name if swimmer_id in swimmer_lookup else "—",
                    "points": pts,
                }
            )
        rows.sort(key=lambda item: item["slot"])
        if selected_competition == "Allgemeine Kategorie":
            day = seg_idx // 2 + 1
            seg = seg_idx % 2 + 1
            label = f"Day {day}, Segment {seg}"
        else:
            label = f"Segment {seg_idx + 1}"

        segment_rows.append(
            {
                "label": label,
                "entries": rows,
            }
        )

    return {
        "total_points": total_points,
        "segments": segment_rows,
    }


@bp.route("/", methods=["GET", "POST"])
def index():
    selected_gender = request.form.get("gender", "f")
    competition = request.form.get("competition", COMPETITION_OPTIONS[0])

    errors: List[str] = []
    solution: Dict[str, Any] = None
    enforce_rest = True if request.method == "GET" else bool(request.form.get("enforce_rest"))
    ran = request.method == "POST"

    try:
        segments = optimizer.get_segments(selected_gender, competition)
    except ValueError as exc:
        errors.append(str(exc))
        segments = []

    swimmers: List[Swimmer] = []
    if ran and not errors:
        swimmers = _collect_active_swimmers(selected_gender)
        if not swimmers:
            errors.append("No active swimmers available for the selected roster.")

    if ran and not errors:
        occurrences = Counter(ev for segment in segments for ev in segment)
        availability: Dict[Event, set[int]] = {event: set() for event in occurrences}

        for swimmer in swimmers:
            for pb in swimmer.pbs:
                if pb.event in availability and pb.points:
                    availability[pb.event].add(swimmer.id)

        missing = [
            f"{event.value} (need {required}, have {len(availability[event])})"
            for event, required in occurrences.items()
            if len(availability[event]) < required
        ]
        if missing:
            errors.append(
                f"Not enough swimmers with a personal best for: {', '.join(missing)}"
            )

    if ran and not errors:
        total_slots = sum(occurrences.values())
        max_races = optimizer.get_max_races_per_swimmer(competition)
        if max_races * len(swimmers) < total_slots:
            errors.append(
                "Roster too small for this competition: with each swimmer limited to "
                f"{max_races} races, you need {total_slots} starts but only have "
                f"{len(swimmers)} active swimmers."
            )

    if ran and not errors:
        swimmer_ids = [sw.id for sw in swimmers]
        points = {
            (sw.id, pb.event): pb.points
            for sw in swimmers
            for pb in sw.pbs
            if pb.points
        }

        try:
            penalty, lineup = optimizer.compute_best_lineup(
                swimmers=swimmer_ids,
                points=points,
                segments=segments,
                max_races_per_swimmer=max_races,
                enforce_adjacent_rest=enforce_rest
            )
        except ValueError as exc:
            errors.append(str(exc))
        except RuntimeError as exc:
            errors.append(f"Optimization failed: {exc}")
        else:
            swimmer_lookup = {sw.id: sw for sw in swimmers}
            solution = _format_solution(lineup, segments, swimmer_lookup, competition)

    return render_template(
        "optimize/index.html",
        competition_options=COMPETITION_OPTIONS,
        selected_gender=selected_gender,
        selected_competition=competition,
        enforce_rest=enforce_rest,
        errors=errors,
        solution=solution,
        ran=ran,
    )
