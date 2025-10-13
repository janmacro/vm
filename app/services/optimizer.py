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

def enumerate_top_k_by_congestion(
    swimmers: List[int],
    points: Dict[Tuple[int, Event], float],
    segments: List[List[Event]],
    max_races_per_swimmer: int,
    *,
    enforce_adjacent_rest: bool = True,
    congestion_window_sizes: Tuple[int, ...] = (3, 4),
    congestion_weights: Dict[int, int] = None,
    top_k: int = 100,
) -> List[Tuple[float, List[Tuple[int, int, Event, int, float]]]]:
    """
    Returns a list of up to top_k solutions, each as:
        (congestion_penalty, assignment)
    where assignment is [(slot, seg_idx, event, swimmer, pts), ...],
    ranked from lowest (best) congestion penalty upward.
    """
    if congestion_weights is None:
        congestion_weights = {3: 2, 4: 1}

    # Create flat list with all slots
    slots: List[Tuple[int, int, Event]] = []
    for seg_idx, seg in enumerate(segments):
        for ev in seg:
            slots.append((len(slots), seg_idx, ev))
    num_slots = len(slots)

    # Precompute segment base offsets once: seg_offsets[g] = first global slot index of segment g
    seg_offsets: List[int] = [0] * len(segments)
    running = 0
    for g, seg in enumerate(segments):
        seg_offsets[g] = running
        running += len(seg)

    # ---- PASS 1: maximize total points ----
    solver = pywraplp.Solver.CreateSolver("CBC")
    if not solver:
        raise RuntimeError("OR-Tools CBC solver not available (pass 1)")

    # Decision: x[(s, slot)] ∈ {0,1}
    x = {(s, slot): solver.BoolVar(f"x_s{s}_{slot}") for s in swimmers for (slot, _, _) in slots}

    # 1) Each slot covered by exactly one swimmer
    for (slot, seg_idx, ev) in slots:
        solver.Add(sum(x[(s, slot)] for s in swimmers) == 1)
    # 2) Per-swimmer max races
    for s in swimmers:
        solver.Add(sum(x[(s, slot)] for (slot, _, _) in slots) <= max_races_per_swimmer)
    # 3) No duplicate event per swimmer (across all segments)
    events_present = set(ev for *_, ev in slots)
    for s in swimmers:
        for ev in events_present:
            solver.Add(sum(x[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
    # 4) Hard rest: no back-to-back starts for same swimmer within a segment
    if enforce_adjacent_rest:
        for (slot, seg_idx, ev) in slots:
            seg_len = len(segments[seg_idx])
            base = seg_offsets[seg_idx]
            local_pos = slot - base
            if local_pos + 1 < seg_len:
                next_slot = slot + 1
                for s in swimmers:
                    solver.Add(x[(s, slot)] + x[(s, next_slot)] <= 1)

    # Objective: maximize total points
    total_points = solver.Sum(points.get((s, ev), 0) * x[(s, slot)]
                              for s in swimmers
                              for (slot, _, ev) in slots)
    solver.Maximize(total_points)

    status = solver.Solve()
    if status != pywraplp.Solver.OPTIMAL:
        raise RuntimeError(f"First pass failed (status={status})")
    best_points = solver.Objective().Value()

    # ---- PASS 2 base model: lock points, build congestion objective ----
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

    total_penalty = solver2.Sum(penalty_terms)

    # ---- Enumerate top_k by adding no-good cuts ----
    ranked: List[Tuple[float, List[Tuple[int, int, Event, int, float]]]] = []
    N = num_slots

    while len(ranked) < top_k:
        solver2.Minimize(total_penalty)
        if solver2.Solve() != pywraplp.Solver.OPTIMAL:
            break

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
        ranked.append((pen_val, assignment))

        # Add a no-good cut to forbid this exact assignment:
        # sum of chosen x2 == N for this solution, so we force ≤ N-1 next time.
        solver2.Add(
            solver2.Sum(
                x2[(s, slot)]
                for (slot, _, _, s, _) in assignment
            ) <= N - 1
        )

    return ranked

