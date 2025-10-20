"""Blueprint handling swimmer CRUD views."""
from typing import Any, Dict

from flask import (
    Blueprint,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from flask_login import login_required, current_user

from ..db import db
from ..models import Event, PB, Swimmer
from ..services import optimizer
from ..services import swimrankings
import re

bp = Blueprint("swimmers", __name__, url_prefix="")

COMPETITION_OPTIONS: list[str] = ["Allgemeine Kategorie", "Nachwuchs"]


def _is_htmx(req: Any) -> bool:
    """Return True when the incoming request originated from HTMX."""
    return req.headers.get("HX-Request") == "true"


def _get_swimmer_or_404(swimmer_id: int) -> Swimmer:
    swimmer = db.session.get(Swimmer, swimmer_id)
    if swimmer is None:
        abort(404)
    if swimmer.owner_id != getattr(current_user, "id", None):
        abort(404)
    return swimmer


def _empty_pb_form(events: list[Event]) -> Dict[str, Dict[str, str]]:
    return {event.name: {"points": "", "time": ""} for event in events}


def _extract_pb_inputs(events: list[Event], form: Any) -> Dict[str, Dict[str, str]]:
    data: Dict[str, Dict[str, str]] = {}
    for event in events:
        data[event.name] = {
            "points": form.get(f"points_{event.name}", "").strip(),
            "time": form.get(f"time_{event.name}", "").strip(),
        }
    return data


def _coerce_int(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid points value: {value}") from exc


_MINUTE_PATTERN = re.compile(r"^(\d+):([0-5]\d)\.(\d{2})$")
_SECOND_PATTERN = re.compile(r"^(\d+)\.(\d{2})$")


def parse_time_to_seconds(value: str | None) -> float | None:
    """Convert a time string into seconds or return None for blank input.

    Accepted formats: ``ss.ss`` or ``m:ss.ss`` (seconds always two digits).
    """

    if not value:
        return None

    raw = value.strip()
    if not raw:
        return None

    match = _MINUTE_PATTERN.match(raw)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        hundredths = int(match.group(3))
        if seconds >= 60:
            raise ValueError(f"Invalid time format: {value}")
        return minutes * 60 + seconds + hundredths / 100

    match = _SECOND_PATTERN.match(raw)
    if match:
        seconds = int(match.group(1))
        hundredths = int(match.group(2))
        return seconds + hundredths / 100

    raise ValueError(f"Invalid time format: {value}")


def format_seconds_to_time(seconds: float | None) -> str:
    """Return a canonical time string with hundredths (``m:ss.ss`` or ``ss.ss``)."""

    if seconds is None:
        return ""

    total = max(0.0, float(seconds))
    total = round(total, 2)
    minutes = int(total // 60)
    remainder = round(total - minutes * 60, 2)

    # Handle rounding that bumps remainder to 60.00
    if remainder >= 60:
        minutes += 1
        remainder -= 60

    if minutes:
        return f"{minutes}:{remainder:05.2f}"
    return f"{remainder:.2f}"


def _build_form_from_swimmer(swimmer: Swimmer, events: list[Event]) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    pb_map = {pb.event: pb for pb in swimmer.pbs}
    for event in events:
        pb = pb_map.get(event)
        rows[event.name] = {
            "points": str(pb.points) if pb else "",
            "time": format_seconds_to_time(pb.time_seconds) if pb else "",
        }
    return rows


@bp.route("/", methods=["GET", "POST"])
@login_required
def index() -> str:
    """List swimmers grouped by gender."""
    female_stmt = (
        select(Swimmer)
        .where(Swimmer.gender == "f", Swimmer.owner_id == current_user.id)
        .order_by(Swimmer.name.asc())
    )
    male_stmt = (
        select(Swimmer)
        .where(Swimmer.gender == "m", Swimmer.owner_id == current_user.id)
        .order_by(Swimmer.name.asc())
    )
    female_swimmers = db.session.scalars(female_stmt).all()
    male_swimmers = db.session.scalars(male_stmt).all()
    selected_gender = request.form.get("gender", "f")
    competition = request.form.get("competition", COMPETITION_OPTIONS[0])
    enforce_rest = True if request.method == "GET" else bool(request.form.get("enforce_rest"))
    ran = request.method == "POST"

    errors: list[str] = []
    solution: dict | None = None

    try:
        segments = optimizer.get_segments(selected_gender, competition)
    except ValueError as exc:
        errors.append(str(exc))
        segments = []

    swimmers_for_gender: list[Swimmer] = []
    if ran and not errors:
        stmt = (
            select(Swimmer)
            .options(selectinload(Swimmer.pbs))
            .where(Swimmer.gender == selected_gender, Swimmer.active.is_(True), Swimmer.owner_id == current_user.id)
        )
        swimmers_for_gender = list(db.session.scalars(stmt))
        if not swimmers_for_gender:
            errors.append("No active swimmers available for the selected roster.")

    if ran and not errors:
        from collections import Counter
        occurrences = Counter(ev for segment in segments for ev in segment)
        availability: dict[Event, set[int]] = {event: set() for event in occurrences}
        for sw in swimmers_for_gender:
            for pb in sw.pbs:
                if pb.event in availability and pb.points:
                    availability[pb.event].add(sw.id)
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
        total_slots = sum(len(seg) for seg in segments)
        max_races = optimizer.get_max_races_per_swimmer(competition)
        if max_races * len(swimmers_for_gender) < total_slots:
            errors.append(
                "Roster too small for this competition: with each swimmer limited to "
                f"{max_races} races, you need {total_slots} starts but only have "
                f"{len(swimmers_for_gender)} active swimmers."
            )

    if ran and not errors:
        swimmer_ids = [sw.id for sw in swimmers_for_gender]
        points = {
            (sw.id, pb.event): pb.points
            for sw in swimmers_for_gender
            for pb in sw.pbs
            if pb.points
        }
        try:
            lineup = optimizer.compute_best_lineup(
                swimmers=swimmer_ids,
                points=points,
                segments=segments,
                max_races_per_swimmer=max_races,
                enforce_adjacent_rest=enforce_rest,
            )
        except (ValueError, RuntimeError) as exc:
            errors.append(f"Optimization failed: {exc}")
        else:
            # format solution similar to optimize._format_solution
            segment_offsets = []
            running = 0
            for seg in segments:
                segment_offsets.append(running)
                running += len(seg)
            total_points = int(sum(item[4] for item in lineup))
            segment_rows: list[dict] = []
            lookup = {sw.id: sw for sw in swimmers_for_gender}
            for seg_idx, seg_events in enumerate(segments):
                rows = []
                for slot, assigned_seg_idx, event, swimmer_id, pts in lineup:
                    if assigned_seg_idx != seg_idx:
                        continue
                    local_slot = slot - segment_offsets[seg_idx] + 1
                    rows.append({
                        "slot": local_slot,
                        "event": event.value,
                        "swimmer": lookup.get(swimmer_id).name if swimmer_id in lookup else "—",
                        "points": pts,
                    })
                rows.sort(key=lambda item: item["slot"])
                if competition == "Allgemeine Kategorie":
                    day = seg_idx // 2 + 1
                    seg_label = seg_idx % 2 + 1
                    label = f"Day {day}, Segment {seg_label}"
                else:
                    label = f"Segment {seg_idx + 1}"
                segment_rows.append({"label": label, "entries": rows})
            solution = {"total_points": total_points, "segments": segment_rows}

    return render_template(
        "swimmers/list.html",
        female_swimmers=female_swimmers,
        male_swimmers=male_swimmers,
        competition_options=COMPETITION_OPTIONS,
        selected_gender=selected_gender,
        selected_competition=competition,
        enforce_rest=enforce_rest,
        errors=errors,
        solution=solution,
        ran=ran,
    )


@bp.route("/new/<gender>", methods=["GET", "POST"])
@login_required
def new(gender: str):
    gender_normalized = gender.lower()
    if gender_normalized not in {"m", "f"}:
        abort(404)

    events = [
        event
        for event in Event
        if not (gender_normalized == "f" and event == Event.FR_1500)
        and not (gender_normalized == "m" and event == Event.FR_800)
    ]
    allowed_events = {event for event in events}
    form_pbs = _empty_pb_form(events)
    name_value = ""
    swimrankings_identifier = ""
    pbest_season = "all"
    errors: list[str] = []
    messages: list[str] = []

    if request.method == "POST":
        action = request.form.get("action", "create")
        name_value = request.form.get("name", "").strip()
        swimrankings_identifier = request.form.get("swimrankings_identifier", "").strip()
        pbest_season = request.form.get("pbest_season", "all").strip()
        form_pbs = _extract_pb_inputs(events, request.form)

        if action == "import":
            if not swimrankings_identifier:
                errors.append("Provide a Swimrankings athlete URL before importing.")
            else:
                try:
                    imported = swimrankings.fetch_personal_bests(
                        swimrankings_identifier,
                        gender_normalized,
                        None if pbest_season == "all" else pbest_season,
                    )
                except swimrankings.SwimrankingsError as exc:
                    errors.append(str(exc))
                else:
                    for event, payload in imported.items():
                        if event not in allowed_events:
                            continue
                        form_pbs[event.name]["points"] = payload.get("points", "")
                        form_pbs[event.name]["time"] = payload.get("time", "")
                    messages.append("Personal bests imported from Swimrankings. Review and save to create the swimmer.")
        else:
            if not name_value:
                errors.append("Name is required.")
            if not errors:
                pb_objects: list[PB] = []
                swimmer = Swimmer(name=name_value, gender=gender_normalized, owner_id=current_user.id)
                db.session.add(swimmer)
                db.session.flush()

                for event in events:
                    values = form_pbs[event.name]
                    try:
                        points_value = _coerce_int(values["points"])
                    except ValueError as exc:
                        errors.append(str(exc))
                        break
                    try:
                        time_value = parse_time_to_seconds(values["time"])
                    except ValueError as exc:
                        errors.append(str(exc))
                        break

                    if points_value is None and time_value is None:
                        continue

                    pb_objects.append(
                        PB(
                            swimmer_id=swimmer.id,
                            event=event,
                            points=points_value if points_value is not None else 0,
                            time_seconds=time_value,
                        )
                    )

                if errors:
                    db.session.rollback()
                else:
                    db.session.add_all(pb_objects)
                    db.session.commit()
                    return redirect(url_for("swimmers.edit", swimmer_id=swimmer.id))

    gender_label = "Female" if gender_normalized == "f" else "Male"
    return render_template(
        "swimmers/new.html",
        gender=gender_normalized,
        gender_label=gender_label,
        form_name=name_value,
        form_pbs=form_pbs,
        events=events,
        swimrankings_identifier=swimrankings_identifier,
        pbest_season=pbest_season,
        errors=errors,
        messages=messages,
    )


@bp.route("/<int:swimmer_id>/edit", methods=["GET", "POST"])
@login_required
def edit(swimmer_id: int):
    swimmer = _get_swimmer_or_404(swimmer_id)
    events = [
        event
        for event in Event
        if not (swimmer.gender == "f" and event == Event.FR_1500)
        and not (swimmer.gender == "m" and event == Event.FR_800)
    ]
    allowed_events = {event for event in events}

    form_pbs = _build_form_from_swimmer(swimmer, events)
    form_name = swimmer.name
    swimrankings_identifier = ""
    pbest_season = "all"
    errors: list[str] = []
    messages: list[str] = []

    if request.method == "POST":
        action = request.form.get("action", "save")
        form_name = request.form.get("name", form_name).strip()
        swimrankings_identifier = request.form.get("swimrankings_identifier", "").strip()
        pbest_season = request.form.get("pbest_season", "all").strip()
        form_pbs = _extract_pb_inputs(events, request.form)

        if action == "import":
            if not swimrankings_identifier:
                errors.append("Provide a Swimrankings athlete URL or ID before importing.")
            else:
                try:
                    imported = swimrankings.fetch_personal_bests(
                        swimrankings_identifier,
                        swimmer.gender,
                        None if pbest_season == "all" else pbest_season,
                    )
                except swimrankings.SwimrankingsError as exc:
                    errors.append(str(exc))
                else:
                    for event, payload in imported.items():
                        if event not in allowed_events:
                            continue
                        form_pbs[event.name]["points"] = payload.get("points", "")
                        form_pbs[event.name]["time"] = payload.get("time", "")
                    if not errors:
                        messages.append("Imported personal bests from Swimrankings. Review and save to apply them.")
        else:
            if not form_name:
                errors.append("Name is required.")

            pb_map = {pb.event: pb for pb in swimmer.pbs}

            for event in events:
                values = form_pbs[event.name]
                try:
                    points_value = _coerce_int(values["points"])
                except ValueError as exc:
                    errors.append(str(exc))
                    break
                try:
                    time_value = parse_time_to_seconds(values["time"])
                except ValueError as exc:
                    errors.append(str(exc))
                    break

                existing = pb_map.get(event)

                if points_value is None and time_value is None:
                    if existing:
                        db.session.delete(existing)
                    continue
                
                if points_value is not None:
                    if existing is None and points_value is not None:
                        db.session.add(
                            PB(
                                swimmer_id=swimmer.id,
                                event=event,
                                points=points_value,
                                time_seconds=time_value,
                            )
                        )
                    else:
                        existing.points = points_value
                        existing.time_seconds = time_value

            if not errors:
                swimmer.name = form_name
                db.session.commit()
                messages.append("Swimmer updated.")
                form_pbs = _build_form_from_swimmer(swimmer, events)
                form_name = swimmer.name

    gender_label = "Female" if swimmer.gender == "f" else "Male"
    return render_template(
        "swimmers/edit.html",
        swimmer=swimmer,
        gender_label=gender_label,
        events=events,
        form_pbs=form_pbs,
        form_name=form_name,
        swimrankings_identifier=swimrankings_identifier,
        pbest_season=pbest_season,
        errors=errors,
        messages=messages,
    )


@bp.patch("/<int:swimmer_id>/active")
@login_required
def toggle_active(swimmer_id: int):
    swimmer = _get_swimmer_or_404(swimmer_id)
    swimmer.active = not swimmer.active
    db.session.commit()
    return render_template("swimmers/_row.html", swimmer=swimmer)


@bp.delete("/<int:swimmer_id>")
@login_required
def delete(swimmer_id: int) -> Any:
    swimmer = _get_swimmer_or_404(swimmer_id)
    db.session.delete(swimmer)
    db.session.commit()

    if _is_htmx(request):
        response = make_response("", 204)
        response.headers["HX-Redirect"] = url_for("swimmers.index")
        return response

    return redirect(url_for("swimmers.index"))
