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
    enforce_adjacent_rest: bool = False,
) -> List[Tuple[int, int, Event, int, float]]:
    """
    Optimizer:
      Pass 1: maximize total points.
      Pass 2: with points fixed, maximize # of distinct swimmers used.
      Pass 3: with (points, #used) fixed, minimize maximum total races per swimmer.
      Pass 4: with (points, #used, minimax) fixed, improve temporal smoothness per segment:
              A) avoid adjacency (gap=0),
              B) avoid gap=1 pairs (one-break),
              C) minimize max per-segment load,
              D) (only if 4 segments = 2 days) minimize max per-swimmer day imbalance.
    Returns:
        assignment: List[(slot, seg_idx, event, swimmer, pts)]
    """

    # ---- Build flat slot list and segment base offsets ----
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

    # Cumulative starting index for each segment in the flattened slot list
    seg_offsets: List[int] = []
    running = 0
    for seg in segments:
        seg_offsets.append(running)
        running += len(seg)

    events_present = set(ev for *_, ev in slots)
    S = len(swimmers)
    seg_lengths = [len(seg) for seg in segments]
    Nmax = max(seg_lengths) if seg_lengths else 0
    BIG = max_races_per_swimmer

    # ---- PASS 1: maximize total points ----
    solver1 = pywraplp.Solver.CreateSolver("CBC")
    if not solver1:
        raise RuntimeError("OR-Tools CBC solver not available (pass 1)")

    x1 = {(s, slot): solver1.BoolVar(f"x1_s{s}_{slot}")
          for s in swimmers for (slot, _, _) in slots}

    # constraints
    for (slot, _, _) in slots:
        solver1.Add(solver1.Sum(x1[(s, slot)] for s in swimmers) == 1)
    for s in swimmers:
        solver1.Add(solver1.Sum(x1[(s, slot)] for (slot, _, _) in slots) <= max_races_per_swimmer)
    for s in swimmers:
        for ev in events_present:
            solver1.Add(solver1.Sum(x1[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
    if enforce_adjacent_rest:
        for (slot, seg_idx, _) in slots:
            base = seg_offsets[seg_idx]
            seg_len = len(segments[seg_idx])
            local = slot - base
            if local + 1 < seg_len:
                for s in swimmers:
                    solver1.Add(x1[(s, slot)] + x1[(s, slot + 1)] <= 1)

    total_points1 = solver1.Sum(points.get((s, ev), 0.0) * x1[(s, slot)]
                                for s in swimmers for (slot, _, ev) in slots)
    solver1.Maximize(total_points1)
    if solver1.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("First pass failed")
    best_points = int(round(total_points1.solution_value()))

    # ---- PASS 2: maximize number of swimmers used (points fixed) ----
    solver2 = pywraplp.Solver.CreateSolver("CBC")
    if not solver2:
        raise RuntimeError("OR-Tools CBC solver not available (pass 2)")

    x2 = {(s, slot): solver2.BoolVar(f"x2_s{s}_{slot}")
          for s in swimmers for (slot, _, _) in slots}

    for (slot, _, _) in slots:
        solver2.Add(solver2.Sum(x2[(s, slot)] for s in swimmers) == 1)
    for s in swimmers:
        solver2.Add(solver2.Sum(x2[(s, slot)] for (slot, _, _) in slots) <= max_races_per_swimmer)
    for s in swimmers:
        for ev in events_present:
            solver2.Add(solver2.Sum(x2[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
    if enforce_adjacent_rest:
        for (slot, seg_idx, _) in slots:
            base = seg_offsets[seg_idx]
            seg_len = len(segments[seg_idx])
            local = slot - base
            if local + 1 < seg_len:
                for s in swimmers:
                    solver2.Add(x2[(s, slot)] + x2[(s, slot + 1)] <= 1)

    tot2 = solver2.Sum(points.get((s, ev), 0.0) * x2[(s, slot)]
                       for s in swimmers for (slot, _, ev) in slots)
    solver2.Add(tot2 == best_points)

    used2 = {s: solver2.BoolVar(f"used2_s{s}") for s in swimmers}
    for s in swimmers:
        races_s = solver2.Sum(x2[(s, slot)] for (slot, _, _) in slots)
        solver2.Add(races_s <= BIG * used2[s])
        solver2.Add(races_s >= used2[s])

    solver2.Maximize(solver2.Sum(used2[s] for s in swimmers))
    if solver2.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("Second pass failed")
    max_used = int(round(sum(used2[s].solution_value() for s in swimmers)))

    # ---- PASS 3: minimize max total races per swimmer (with points & #used fixed) ----
    solver3 = pywraplp.Solver.CreateSolver("CBC")
    if not solver3:
        raise RuntimeError("OR-Tools CBC solver not available (pass 3)")

    x3 = {(s, slot): solver3.BoolVar(f"x3_s{s}_{slot}")
          for s in swimmers for (slot, _, _) in slots}

    for (slot, _, _) in slots:
        solver3.Add(solver3.Sum(x3[(s, slot)] for s in swimmers) == 1)
    for s in swimmers:
        solver3.Add(solver3.Sum(x3[(s, slot)] for (slot, _, _) in slots) <= max_races_per_swimmer)
    for s in swimmers:
        for ev in events_present:
            solver3.Add(solver3.Sum(x3[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
    if enforce_adjacent_rest:
        for (slot, seg_idx, _) in slots:
            base = seg_offsets[seg_idx]
            seg_len = len(segments[seg_idx])
            local = slot - base
            if local + 1 < seg_len:
                for s in swimmers:
                    solver3.Add(x3[(s, slot)] + x3[(s, slot + 1)] <= 1)

    tot3 = solver3.Sum(points.get((s, ev), 0.0) * x3[(s, slot)]
                       for s in swimmers for (slot, _, ev) in slots)
    solver3.Add(tot3 == best_points)

    used3 = {s: solver3.BoolVar(f"used3_s{s}") for s in swimmers}
    for s in swimmers:
        y = solver3.Sum(x3[(s, slot)] for (slot, _, _) in slots)
        solver3.Add(y <= BIG * used3[s])
        solver3.Add(y >= used3[s])
    solver3.Add(solver3.Sum(used3[s] for s in swimmers) == max_used)

    y3 = {s: solver3.IntVar(0, max_races_per_swimmer, f"y3_s{s}") for s in swimmers}
    for s in swimmers:
        solver3.Add(y3[s] == solver3.Sum(x3[(s, slot)] for (slot, _, _) in slots))

    Mtot = solver3.IntVar(0, max_races_per_swimmer, "Mtot")
    for s in swimmers:
        solver3.Add(y3[s] <= Mtot)

    solver3.Minimize(Mtot)
    if solver3.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("Third pass failed")
    min_max_total = int(round(Mtot.solution_value()))

    # ---- PASS 4: spacing (adjacency > one-break > per-seg max [> per-day balance if 4 segs]) ----
    solver4 = pywraplp.Solver.CreateSolver("CBC")
    if not solver4:
        raise RuntimeError("OR-Tools CBC solver not available (pass 4)")

    x4 = {(s, slot): solver4.BoolVar(f"x4_s{s}_{slot}")
          for s in swimmers for (slot, _, _) in slots}

    # hard constraints
    for (slot, _, _) in slots:
        solver4.Add(solver4.Sum(x4[(s, slot)] for s in swimmers) == 1)
    for s in swimmers:
        solver4.Add(solver4.Sum(x4[(s, slot)] for (slot, _, _) in slots) <= max_races_per_swimmer)
    for s in swimmers:
        for ev in events_present:
            solver4.Add(solver4.Sum(x4[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
    if enforce_adjacent_rest:
        for (slot, seg_idx, _) in slots:
            base = seg_offsets[seg_idx]
            seg_len = len(segments[seg_idx])
            local = slot - base
            if local + 1 < seg_len:
                for s in swimmers:
                    solver4.Add(x4[(s, slot)] + x4[(s, slot + 1)] <= 1)

    # lock points, #used, and global minimax
    tot4 = solver4.Sum(points.get((s, ev), 0.0) * x4[(s, slot)]
                       for s in swimmers for (slot, _, ev) in slots)
    solver4.Add(tot4 == best_points)

    used4 = {s: solver4.BoolVar(f"used4_s{s}") for s in swimmers}
    for s in swimmers:
        y = solver4.Sum(x4[(s, slot)] for (slot, _, _) in slots)
        solver4.Add(y <= BIG * used4[s])
        solver4.Add(y >= used4[s])
        solver4.Add(y <= min_max_total)  # preserve global minimax
    solver4.Add(solver4.Sum(used4[s] for s in swimmers) == max_used)

    # A) adjacency (gap=0) penalties
    z0_list = []
    for g, seg in enumerate(segments):
        base = seg_offsets[g]; N = len(seg)
        for s in swimmers:
            for i in range(N - 1):
                a = x4[(s, base + i)]
                b = x4[(s, base + i + 1)]
                z = solver4.BoolVar(f"adj_{s}_{g}_{i}")
                solver4.Add(z >= a + b - 1)
                solver4.Add(z <= a)
                solver4.Add(z <= b)
                z0_list.append(z)
    V_adj = solver4.Sum(z0_list)

    # B) one-break (gap=1) penalties via length-3 windows: excess >= count - 1
    z1_list = []
    for g, seg in enumerate(segments):
        base = seg_offsets[g]; N = len(seg)
        if N >= 3:
            for s in swimmers:
                for i in range(N - 2):
                    count = solver4.IntVar(0, 3, f"cnt3_{s}_{g}_{i}")
                    solver4.Add(count == x4[(s, base + i)] +
                                         x4[(s, base + i + 1)] +
                                         x4[(s, base + i + 2)])
                    exc = solver4.IntVar(0, 2, f"exc3_{s}_{g}_{i}")
                    solver4.Add(exc >= count - 1)
                    z1_list.append(exc)
    V_gap1 = solver4.Sum(z1_list)

    # C) per-segment load balance: minimize Mseg = max_{s,g} races in segment g for swimmer s
    y_seg = {(s, g): solver4.IntVar(0, len(seg), f"yseg_{s}_{g}")
             for g, seg in enumerate(segments) for s in swimmers}
    for g, seg in enumerate(segments):
        base = seg_offsets[g]; N = len(seg)
        for s in swimmers:
            solver4.Add(y_seg[(s, g)] == solver4.Sum(x4[(s, base + i)] for i in range(N)))

    Mseg = solver4.IntVar(0, Nmax, "Mseg")
    for (s, g), ysg in y_seg.items():
        solver4.Add(ysg <= Mseg)

    # D) (only if 4 segments) per-day balance: minimize max per-swimmer day imbalance
    has_two_days = (len(segments) == 4)
    if has_two_days:
        # Day 1: segments 0 & 1, Day 2: segments 2 & 3
        day1_slots = segment_slot_indices[0] + segment_slot_indices[1]
        day2_slots = segment_slot_indices[2] + segment_slot_indices[3]

        d1 = {s: solver4.IntVar(0, len(day1_slots), f"d1_{s}") for s in swimmers}
        d2 = {s: solver4.IntVar(0, len(day2_slots), f"d2_{s}") for s in swimmers}
        for s in swimmers:
            solver4.Add(d1[s] == solver4.Sum(x4[(s, t)] for t in day1_slots))
            solver4.Add(d2[s] == solver4.Sum(x4[(s, t)] for t in day2_slots))

        # delta_s >= |d1 - d2|
        delta = {s: solver4.IntVar(0, min_max_total, f"ddiff_{s}") for s in swimmers}
        for s in swimmers:
            solver4.Add(delta[s] >= d1[s] - d2[s])
            solver4.Add(delta[s] >= d2[s] - d1[s])

        # D = max_s delta_s
        D = solver4.IntVar(0, min_max_total, "D_day_imbalance")
        for s in swimmers:
            solver4.Add(delta[s] <= D)
    else:
        D = None  # not used

    # ---- Lexicographic weights ----
    # Upper bounds
    UB_adj = sum(max(0, N - 1) for N in seg_lengths) * S
    UB_gap1 = sum(max(0, N - 2) * 2 for N in seg_lengths) * S
    UB_Mseg = Nmax if Nmax > 0 else 1
    UB_D = min_max_total if has_two_days else 0

    if has_two_days:
        # Ensure: W0 >> (W1, W2, D), W1 >> (W2, D), W2 >> D
        W2 = UB_D + 1
        W1 = UB_Mseg * W2 + UB_D + 1
        W0 = UB_gap1 * W1 + UB_Mseg * W2 + UB_D + 1
        solver4.Minimize(W0 * V_adj + W1 * V_gap1 + W2 * Mseg + D)
    else:
        W1 = UB_Mseg + 1
        W0 = UB_gap1 * W1 + UB_Mseg + 1
        solver4.Minimize(W0 * V_adj + W1 * V_gap1 + Mseg)

    if solver4.Solve() != pywraplp.Solver.OPTIMAL:
        raise RuntimeError("Fourth pass failed")

    # ---- Extract final assignment from pass 4 ----
    assignment: List[Tuple[int, int, Event, int, float]] = []
    for (slot, seg_idx, ev) in slots:
        chosen = None
        for s in swimmers:
            if x4[(s, slot)].solution_value() > 0.5:
                chosen = s
                break
        pts = points.get((chosen, ev), 0.0) if chosen is not None else 0.0
        assignment.append((slot, seg_idx, ev, chosen, pts))

    return assignment
