from ortools.linear_solver import pywraplp
from enum import Enum
from collections import defaultdict

space = 1 # e.g. 1 means at least 1 race gap before swimming again

# --- Event enum (types of races) ---
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

# Competition schedule (segments = parts of days separated by breaks)
segments = [
    [Event.FL_50, Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_400],
    [Event.BR_200, Event.BK_100, Event.FL_200, Event.IM_400, Event.BK_50, Event.FR_1500, Event.FR_100],
    [Event.FR_200, Event.BR_100, Event.BK_200, Event.FL_100, Event.IM_200, Event.FR_50, Event.BR_200],
    [Event.IM_100, Event.BK_100, Event.FL_200, Event.IM_400, Event.FR_400, Event.BR_50, Event.FR_100],
]

max_races = 5

swimmers = [f"S{i}" for i in range(1, 10)]

# Random points per swimmer × event
import random
random.seed(0)
sample_points = {(s, e): random.randint(700, 950) for s in swimmers for e in Event}

# -----------------------
# Flatten into slots (each occurrence of an event in the competition)
slots = []
for seg_idx, seg in enumerate(segments):
    for pos, event in enumerate(seg):
        slot_index = len(slots)
        slots.append((slot_index, seg_idx, pos, event))


# Create solver
solver = pywraplp.Solver.CreateSolver('CBC')
if not solver:
    raise RuntimeError("CBC solver not available")

# Decision variables: x[(swimmer, slot)] = 1 if swimmer swims in that slot
x = {}
for s in swimmers:
    for slot_index, seg_idx, pos, event in slots:
        x[(s, slot_index)] = solver.BoolVar(f"x_{s}_{slot_index}")

# Constraint: each slot (occurrence) must have exactly one swimmer
for slot_index, seg_idx, pos, event in slots:
    solver.Add(solver.Sum(x[(s, slot_index)] for s in swimmers) == 1)


# Constraint: each swimmer can only swim up to max_races slots
for s in swimmers:
    solver.Add(solver.Sum(x[(s, slot_index)] for slot_index, seg_idx, pos, event in slots) <= max_races)


# Required rest slots between two races inside the same segment
for seg_idx, seg in enumerate(segments):
    seg_slot_indices = [slot_index for (slot_index, segi, pos, event) in slots if segi == seg_idx]
    seg_count = len(seg_slot_indices)

    # Precompute windows (each is a list of slot indices)
    windows = []
    for start in range(seg_count):
        end = min(start + space + 1, seg_count)
        if end - start > 1:  # only meaningful if window has >1 slot
            windows.append(seg_slot_indices[start:end])

    # Add constraints for each swimmer
    for s in swimmers:
        for window in windows:
            solver.Add(solver.Sum(x[(s, slot)] for slot in window) <= 1)

# Group slot indices by event type
event_to_slots = defaultdict(list)
for slot_index, segi, pos, event in slots:
    event_to_slots[event].append(slot_index)

# For each swimmer and each event type, add at-most-one constraint
for s in swimmers:
    for event, slot_indices in event_to_slots.items():
        solver.Add(solver.Sum(x[(s, slot)] for slot in slot_indices) <= 1)

# ---- Objective: maximize total points ----
total_points = solver.Sum(
    sample_points.get((s, event), 0) * x[(s, slot_index)]
    for s in swimmers
    for (slot_index, seg_idx, pos, event) in slots
)

solver.Maximize(total_points)

import time
start_time = time.time()
status = solver.Solve()
print("--- %s seconds ---" % (time.time() - start_time))


if status == pywraplp.Solver.OPTIMAL:
    obj_val = solver.Objective().Value()
    print(f"Optimal total points: {obj_val:.2f}\n")

    grand_total = 0.0
    current_seg = -1

    for slot_index, seg_idx, pos, event in slots:
        # Print a header when entering a new segment
        if seg_idx != current_seg:
            current_seg = seg_idx
            print(f"=== Segment {seg_idx} ===")

        # Find chosen swimmer for this slot
        chosen_swimmer = None
        for s in swimmers:
            if x[(s, slot_index)].solution_value() > 0.5:
                chosen_swimmer = s
                break

        pts = float(sample_points.get((chosen_swimmer, event), 0))
        grand_total += pts
        print(f"  Pos {pos:>2}: {event.value:<12} -> {chosen_swimmer}  ({pts:.0f} pts)")

    print(f"\nCheck total points (recomputed): {grand_total:.2f}")

elif status == pywraplp.Solver.FEASIBLE:
    print("A feasible solution was found, but optimality was not proven.")
elif status == pywraplp.Solver.INFEASIBLE:
    print("Model is infeasible. Check constraints (e.g., max_races too small).")
elif status == pywraplp.Solver.UNBOUNDED:
    print("Model is unbounded (should not happen with these constraints).")
else:
    print(f"Solver ended with status code: {status}")