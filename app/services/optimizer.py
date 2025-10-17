from ortools.linear_solver import pywraplp
from typing import Dict, List, Tuple

from ..models import Event


SEGMENT_CATALOG: Dict[tuple[str, str], List[List[Event]]] = {
    ("m", "Allgemeine Kategorie"): [
        [Event.FL_50, Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_400],
        [Event.BR_200, Event.BK_100, Event.FL_200, Event.IM_400, Event.BK_50, Event.FR_1500, Event.FR_100],
        [Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_50, Event.BR_200],
        [Event.IM_100, Event.BK_100, Event.FL_200, Event.IM_400, Event.FR_400, Event.BR_50, Event.FR_100],
    ],
    ("f", "Allgemeine Kategorie"): [
        [Event.FL_50, Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_400],
        [Event.BR_200, Event.BK_100, Event.FL_200, Event.IM_400, Event.BK_50, Event.FR_800, Event.FR_100],
        [Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_50, Event.BR_200],
        [Event.IM_100, Event.BK_100, Event.FL_200, Event.IM_400, Event.FR_400, Event.BR_50, Event.FR_100],
    ],
    ("m", "Nachwuchs"): [
        [Event.IM_200, Event.FR_400, Event.BK_200, Event.IM_100, Event.FL_200, Event.BR_100, Event.FR_100, Event.IM_400],
        [Event.FR_200, Event.BK_100, Event.IM_200, Event.FL_100, Event.BR_200, Event.FR_1500, Event.IM_100, Event.FR_50],
    ],
    ("f", "Nachwuchs"): [
        [Event.IM_200, Event.FR_400, Event.BK_200, Event.IM_100, Event.FL_200, Event.BR_100, Event.FR_100, Event.IM_400],
        [Event.FR_200, Event.BK_100, Event.IM_200, Event.FL_100, Event.BR_200, Event.FR_800, Event.IM_100, Event.FR_50],
    ],
}

MAX_RACES_PER_SWIMMER: Dict[str, int] = {
    "Allgemeine Kategorie": 5,
    "Nachwuchs": 4,
}


def get_segments(gender: str, competition: str) -> List[List[Event]]:
    """Return the segment definition for the given roster/competition."""

    key = (gender.lower(), competition)
    try:
        return SEGMENT_CATALOG[key]
    except KeyError as exc:
        raise ValueError("Unsupported roster/competition combination.") from exc


def get_max_races_per_swimmer(competition: str) -> int:
    return MAX_RACES_PER_SWIMMER.get(competition)

def compute_best_lineup(
    swimmers: List[int],
    points: Dict[Tuple[int, Event], float],
    segments: List[List[Event]],
    max_races_per_swimmer: int,
    *,
    enforce_adjacent_rest: bool = True,
) -> List[Tuple[int, int, Event, int, float]]:
    # Precompute slot metadata
    slots: List[Tuple[int, int, Event]] = []
    segment_slot_indices: List[List[int]] = []
    slot_counter = 0
    for seg_idx, seg in enumerate(segments):
        indices = []
        for ev in seg:
            slots.append((slot_counter, seg_idx, ev))
            indices.append(slot_counter)
            slot_counter += 1
        segment_slot_indices.append(indices)

    seg_offsets: List[int] = [0]
    running = 0
    for seg in segments:
        seg_offsets.append(running)
        running += len(seg)
    seg_offsets = seg_offsets[1:]

    def _build_solver() -> Tuple[pywraplp.Solver, Dict[Tuple[int, int], pywraplp.Variable], pywraplp.LinearExpr]:
        solver = pywraplp.Solver.CreateSolver("CBC")
        if not solver:
            raise RuntimeError("OR-Tools CBC solver not available")
        x = {(s, slot_idx): solver.BoolVar(f"x_s{s}_{slot_idx}") for s in swimmers for (slot_idx, _, _) in slots}

        # Constraints
        for (slot_idx, seg_idx, _) in slots:
            solver.Add(sum(x[(s, slot_idx)] for s in swimmers) == 1)

        for s in swimmers:
            solver.Add(sum(x[(s, slot_idx)] for (slot_idx, _, _) in slots) <= max_races_per_swimmer)

        events_present = set(ev for *_, ev in slots)
        for s in swimmers:
            for ev in events_present:
                solver.Add(sum(x[(s, slot_idx)] for (slot_idx, _, ev2) in slots if ev2 == ev) <= 1)

        if enforce_adjacent_rest:
            for seg_idx, seg in enumerate(segments):
                indices = segment_slot_indices[seg_idx]
                for i in range(len(indices) - 1):
                    slot_a = indices[i]
                    slot_b = indices[i + 1]
                    for s in swimmers:
                        solver.Add(x[(s, slot_a)] + x[(s, slot_b)] <= 1)

        total_points_expr = solver.Sum(
            points.get((s, ev), 0) * x[(s, slot_idx)]
            for s in swimmers
            for (slot_idx, _, ev) in slots
        )

        return solver, x, total_points_expr

    # Pass 1: maximise points
    solver1, x1, total_points1 = _build_solver()
    solver1.Maximize(total_points1)
    if solver1.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("First optimisation pass failed")
    best_points = int(round(solver1.Objective().Value()))

    # Pass 2: minimise total races per swimmer
    solver2, x2, total_points2 = _build_solver()
    solver2.Add(total_points2 == best_points)
    y_vars = {}
    for s in swimmers:
        y = solver2.IntVar(0, len(slots), f"total_races_{s}")
        solver2.Add(y == solver2.Sum(x2[(s, slot_idx)] for (slot_idx, _, _) in slots))
        y_vars[s] = y
    solver2.Minimize(solver2.Sum(y_vars[s] for s in swimmers))
    if solver2.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("Second optimisation pass failed")
    min_total_races = int(round(solver2.Objective().Value()))

    # Pass 3: minimise overuse within segments
    solver3, x3, total_points3 = _build_solver()
    solver3.Add(total_points3 == best_points)
    y_total = {}
    for s in swimmers:
        y = solver3.IntVar(0, len(slots), f"total_races_{s}")
        solver3.Add(y == solver3.Sum(x3[(s, slot_idx)] for (slot_idx, _, _) in slots))
        y_total[s] = y
    solver3.Add(solver3.Sum(y_total.values()) == min_total_races)

    segment_penalty_vars = []
    for seg_idx, seg in enumerate(segments):
        seg_len = len(seg)
        for s in swimmers:
            y_seg = solver3.IntVar(0, seg_len, f"seg_races_{s}_{seg_idx}")
            solver3.Add(y_seg == solver3.Sum(x3[(s, slot_idx)] for slot_idx in segment_slot_indices[seg_idx]))
            overload = solver3.IntVar(0, seg_len, f"seg_over_{s}_{seg_idx}")
            solver3.Add(overload >= y_seg - 1)
            solver3.Add(overload >= 0)
            segment_penalty_vars.append(overload)

    solver3.Minimize(solver3.Sum(segment_penalty_vars))
    if solver3.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("Third optimisation pass failed")
    min_segment_penalty = int(round(solver3.Objective().Value()))

    # Pass 4: minimise congestion using padded windows
    solver4, x4, total_points4 = _build_solver()
    solver4.Add(total_points4 == best_points)

    y_total4 = {}
    for s in swimmers:
        y = solver4.IntVar(0, len(slots), f"total_races_{s}")
        solver4.Add(y == solver4.Sum(x4[(s, slot_idx)] for (slot_idx, _, _) in slots))
        y_total4[s] = y
    solver4.Add(solver4.Sum(y_total4.values()) == min_total_races)

    segment_overloads = []
    for seg_idx, seg in enumerate(segments):
        seg_len = len(seg)
        for s in swimmers:
            y_seg = solver4.IntVar(0, seg_len, f"seg_races_{s}_{seg_idx}")
            solver4.Add(y_seg == solver4.Sum(x4[(s, slot_idx)] for slot_idx in segment_slot_indices[seg_idx]))
            overload = solver4.IntVar(0, seg_len, f"seg_over_{s}_{seg_idx}")
            solver4.Add(overload >= y_seg - 1)
            solver4.Add(overload >= 0)
            segment_overloads.append(overload)
    solver4.Add(solver4.Sum(segment_overloads) == min_segment_penalty)

    penalty_terms = []
    for seg_idx, seg in enumerate(segments):
        seg_len = len(seg)
        if seg_len <= 1:
            continue
        pad = seg_len - 1
        indices = [None] * pad + segment_slot_indices[seg_idx] + [None] * pad
        for start in range(len(indices) - seg_len + 1):
            window = [idx for idx in indices[start:start + seg_len] if idx is not None]
            if len(window) <= 1:
                continue
            for s in swimmers:
                count = solver4.IntVar(0, len(window), f"cnt_{s}_{seg_idx}_{start}")
                solver4.Add(count == solver4.Sum(x4[(s, slot_idx)] for slot_idx in window))
                excess = solver4.IntVar(0, len(window) - 1, f"exc_{s}_{seg_idx}_{start}")
                solver4.Add(excess >= count - 1)
                solver4.Add(excess >= 0)
                penalty_terms.append(excess)

    solver4.Minimize(solver4.Sum(penalty_terms))
    if solver4.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("Final optimisation pass failed")

    assignment: List[Tuple[int, int, Event, int, float]] = []
    for (slot_idx, seg_idx, ev) in slots:
        chosen = None
        for s in swimmers:
            if x4[(s, slot_idx)].solution_value() > 0.5:
                chosen = s
                break
        pts = points.get((chosen, ev), 0) if chosen is not None else 0
        assignment.append((slot_idx, seg_idx, ev, chosen, pts))

    penalty_value = solver4.Objective().Value()
    return assignment
    solver2 = pywraplp.Solver.CreateSolver("CBC")
    if not solver2:
        raise RuntimeError("OR-Tools CBC solver not available (pass 2)")

    x2 = {(s, slot): solver2.BoolVar(f"x_s{s}_{slot}") for s in swimmers for (slot, _, _) in slots}

    # same constraints
    for (slot, seg_idx, ev) in slots:
        solver2.Add(sum(x2[(s, slot)] for s in swimmers) == 1)
    for s in swimmers:
        solver2.Add(sum(x2[(s, slot)] for (slot, _, _) in slots) <= max_races_per_swimmer)
    for s in swimmers:
        for ev in events_present:
            solver2.Add(sum(x2[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
    if enforce_adjacent_rest:
        for (slot, seg_idx, ev) in slots:
            seg_len = len(segments[seg_idx])
            base = seg_offsets[seg_idx]
            local_pos = slot - base
            if local_pos + 1 < seg_len:
                next_slot = slot + 1
                for s in swimmers:
                    solver2.Add(x2[(s, slot)] + x2[(s, next_slot)] <= 1)

    # Fix total points to optimum from pass 1
    total_points2 = solver2.Sum(points.get((s, ev), 0) * x2[(s, slot)]
                                for s in swimmers
                                for (slot, _, ev) in slots)
    solver2.Add(total_points2 == int(best_points))

    # Congestion objective
    penalty_terms = []
    for seg_idx, seg in enumerate(segments):
        seg_len = len(seg)
        base = seg_offsets[seg_idx]
        seg_slots = list(range(base, base + seg_len))
        for L in congestion_window_sizes:
            if L <= 1 or L > seg_len:
                continue
            w = congestion_weights.get(L, 1) if congestion_weights else 1
            for start in range(seg_len - L + 1):
                window_slots = seg_slots[start:start + L]
                for s in swimmers:
                    count = solver2.IntVar(0, L, f"cnt_s{s}_{seg_idx}_{start}_{L}")
                    solver2.Add(count == solver2.Sum(x2[(s, sl)] for sl in window_slots))
                    excess = solver2.IntVar(0, L - 1, f"exc_s{s}_{seg_idx}_{start}_{L}")
                    solver2.Add(excess >= count - 1)
                    penalty_terms.append(w * excess)

    # Objective: minimize congestion penalty
    total_penalty = solver2.Sum(penalty_terms)
    solver2.Minimize(total_penalty)

    status2 = solver2.Solve()
    if status2 != pywraplp.Solver.OPTIMAL:
        raise RuntimeError(f"Second pass failed (status={status2})")

    # Extract this solution
    assignment = []
    for (slot, seg_idx, ev) in slots:
        chosen = None
        for s in swimmers:
            if x2[(s, slot)].solution_value() > 0.5:
                chosen = s
                break
        pts = points.get((chosen, ev), 0) if chosen is not None else 0
        assignment.append((slot, seg_idx, ev, chosen, pts))

    pen_val = solver2.Objective().Value()

    return (pen_val, assignment)
