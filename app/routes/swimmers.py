"""Blueprint handling swimmer CRUD views."""
from typing import Any, Dict

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import select

from ..db import db
from ..models import Event, PB, Swimmer
from ..services import swimrankings
from ..services.pb_utils import parse_time_to_seconds, format_seconds_to_time

bp = Blueprint("swimmers", __name__, url_prefix="/swimmers")


def _is_htmx(req: Any) -> bool:
    """Return True when the incoming request originated from HTMX."""
    return req.headers.get("HX-Request") == "true"


def _get_swimmer_or_404(swimmer_id: int) -> Swimmer:
    swimmer = db.session.get(Swimmer, swimmer_id)
    if swimmer is None:
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


@bp.get("/")
def index() -> str:
    """List swimmers grouped by gender."""
    female_stmt = (
        select(Swimmer)
        .where(Swimmer.gender == "f")
        .order_by(Swimmer.name.asc())
    )
    male_stmt = (
        select(Swimmer)
        .where(Swimmer.gender == "m")
        .order_by(Swimmer.name.asc())
    )
    female_swimmers = db.session.scalars(female_stmt).all()
    male_swimmers = db.session.scalars(male_stmt).all()
    return render_template(
        "swimmers/list.html",
        female_swimmers=female_swimmers,
        male_swimmers=male_swimmers,
    )


@bp.route("/new/<gender>", methods=["GET", "POST"])
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
    errors: list[str] = []
    messages: list[str] = []

    if request.method == "POST":
        action = request.form.get("action", "create")
        name_value = request.form.get("name", "").strip()
        swimrankings_identifier = request.form.get("swimrankings_identifier", "").strip()
        form_pbs = _extract_pb_inputs(events, request.form)

        if action == "import":
            if not swimrankings_identifier:
                errors.append("Provide a Swimrankings athlete URL or ID before importing.")
            else:
                try:
                    imported = swimrankings.fetch_personal_bests(swimrankings_identifier)
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

            pb_objects: list[PB] = []
            if not errors:
                swimmer = Swimmer(name=name_value, gender=gender_normalized)
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
        errors=errors,
        messages=messages,
    )


@bp.route("/<int:swimmer_id>/edit", methods=["GET", "POST"])
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
    errors: list[str] = []
    messages: list[str] = []

    if request.method == "POST":
        action = request.form.get("action", "save")
        form_name = request.form.get("name", form_name).strip()
        swimrankings_identifier = request.form.get("swimrankings_identifier", "").strip()
        form_pbs = _extract_pb_inputs(events, request.form)

        if action == "import":
            if not swimrankings_identifier:
                errors.append("Provide a Swimrankings athlete URL or ID before importing.")
            else:
                try:
                    imported = swimrankings.fetch_personal_bests(swimrankings_identifier)
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

                if existing is None:
                    db.session.add(
                        PB(
                            swimmer_id=swimmer.id,
                            event=event,
                            points=points_value if points_value is not None else 0,
                            time_seconds=time_value,
                        )
                    )
                else:
                    existing.points = points_value if points_value is not None else 0
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
        errors=errors,
        messages=messages,
    )


@bp.delete("/<int:swimmer_id>")
def delete(swimmer_id: int) -> Any:
    swimmer = _get_swimmer_or_404(swimmer_id)
    db.session.delete(swimmer)
    db.session.commit()

    if _is_htmx(request):
        return ("", 204)

    return redirect(url_for("swimmers.index"))


@bp.post("/<int:swimmer_id>/delete")
def delete_via_post(swimmer_id: int) -> Any:
    """Fallback for browsers without DELETE support."""
    return delete(swimmer_id)
