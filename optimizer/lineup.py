from ortools.linear_solver import pywraplp
from typing import Dict, List, Tuple
from enum import Enum

import time
start_time = time.time()

class Event(Enum):
    FR_50 = "50m Free"
    FR_100 = "100m Free"
    FR_200 = "200m Free"
    FR_400 = "400m Free"
    FR_800 = "800m Free"
    FR_1500 = "1500m Free"
    BK_50 = "50m Back"
    BK_100 = "100m Back"
    BK_200 = "200m Back"
    BR_50 = "50m Breast"
    BR_100 = "100m Breast"
    BR_200 = "200m Breast"
    FL_50 = "50m Fly"
    FL_100 = "100m Fly"
    FL_200 = "200m Fly"
    IM_50 = "50m Medley"
    IM_100 = "100m Medley"
    IM_200 = "200m Medley"
    IM_400 = "400m Medley"

# 4 segments (two days, two blocks/day). Each item is a race *occurrence*.
segments_vm_m: List[List[Event]] = [
    [Event.FL_50, Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_400],
    [Event.BR_200, Event.BK_100, Event.FL_200, Event.IM_400, Event.BK_50, Event.FR_1500, Event.FR_100],
    [Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_50, Event.BR_200],
    [Event.IM_100, Event.BK_100, Event.FL_200, Event.IM_400, Event.FR_400, Event.BR_50, Event.FR_100],
]

def enumerate_top_k_by_congestion(
    swimmers: List[str],
    points: Dict[Tuple[str, Event], float],
    segments: List[List[Event]],
    max_races_per_swimmer: int,
    congestion_window_sizes: Tuple[int, ...] = (3, 4),
    congestion_weights: Dict[int, int] = None,
    top_k: int = 100,
) -> List[Tuple[float, List[Tuple[int, int, Event, str, float]]]]:
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

    # Quick feasibility check
    if max_races_per_swimmer * len(swimmers) < num_slots:
        raise ValueError(
            f"Infeasible: total slots={num_slots} exceed swimmers*max_races="
            f"{len(swimmers)}*{max_races_per_swimmer}={len(swimmers)*max_races_per_swimmer}"
        )

    # ---- PASS 1: maximize total points ----
    solver = pywraplp.Solver.CreateSolver("CBC")
    if not solver:
        raise RuntimeError("OR-Tools CBC solver not available (pass 1)")

    # Decision: x[(s, slot)] ∈ {0,1}
    x = {(s, slot): solver.BoolVar(f"x_{s}_{slot}") for s in swimmers for (slot, _, _) in slots}

    # 1) Each slot covered by exactly one swimmer
    for (slot, seg_idx, ev) in slots:
        solver.Add(sum(x[(s, slot)] for s in swimmers) == 1)
    # 2) Per-swimmer max races
    for s in swimmers:
        solver.Add(sum(x[(s, slot)] for (slot,_,_) in slots) <= max_races_per_swimmer)
    # 3) No duplicate event per swimmer (across all segments)
    events_present = set(ev for *_, ev in slots)
    for s in swimmers:
        for ev in events_present:
            solver.Add(sum(x[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
    # 4) Hard rest: no back-to-back starts for same swimmer within a segment
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

    x2 = {(s, slot): solver2.BoolVar(f"x_{s}_{slot}") for s in swimmers for (slot, _, _) in slots}

    # same constraints
    for (slot, seg_idx, ev) in slots:
        solver2.Add(sum(x2[(s, slot)] for s in swimmers) == 1)
    for s in swimmers:
        solver2.Add(sum(x2[(s, slot)] for (slot, _, _) in slots) <= max_races_per_swimmer)
    for s in swimmers:
        for ev in events_present:
            solver2.Add(sum(x2[(s, slot)] for (slot, _, ev2) in slots if ev2 == ev) <= 1)
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
                    count = solver2.IntVar(0, L, f"cnt_{s}_{seg_idx}_{start}_{L}")
                    solver2.Add(count == solver2.Sum(x2[(s, sl)] for sl in window_slots))
                    excess = solver2.IntVar(0, L - 1, f"exc_{s}_{seg_idx}_{start}_{L}")
                    solver2.Add(excess >= count - 1)
                    penalty_terms.append(w * excess)

    total_penalty = solver2.Sum(penalty_terms)

    # ---- Enumerate top_k by adding no-good cuts ----
    ranked: List[Tuple[float, List[Tuple[int, int, Event, str, float]]]] = []
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


# -----------------------------
# Example usage / demo
# -----------------------------
if __name__ == "__main__":
    swimmers = [f"S{i}" for i in range(1, 20)]
    # Toy points
    import random
    random.seed(0)
    sample_points: Dict[Tuple[str, Event], float] = {}
    for s in swimmers:
        for ev in Event:
            sample_points[(s, ev)] = random.randint(700, 950)

    ranked = enumerate_top_k_by_congestion(
        swimmers=swimmers,
        points=sample_points,
        segments=segments_vm_m,
        max_races_per_swimmer=5,
    )

    print(f"Found {len(ranked)} optimal-points solutions (ranked by congestion).")
    for idx, (pen, lineup) in enumerate(ranked, 1):
        print(f"\n=== Solution #{idx} — Congestion penalty: {pen:.0f} ===")
        current_seg = -1
        check_points = 0
        for (slot, seg_idx, ev, s, pts) in lineup:
            if seg_idx != current_seg:
                current_seg = seg_idx
                print(f"--- Segment {seg_idx} ---")
            print(f"  Slot {slot:>2}: {ev.value:<12} -> {s} ({pts} pts)")
            check_points += pts
        print(f"Total points (fixed): {check_points:.0f}")


print("--- %s seconds ---" % (time.time() - start_time))