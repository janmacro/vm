from enum import Enum
from ortools.sat.python import cp_model
import time
start_time = time.time()

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

swimmers = [f"S{i}" for i in range(1, 9)]

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

model = cp_model.CpModel()

# Decision vars: x[s, slot] in {0,1}
x = {}
for s in swimmers:
    for (slot_index, seg_idx, pos, event) in slots:
        x[(s, slot_index)] = model.NewBoolVar(f"x_{s}_{slot_index}")

# 1) One swimmer per slot (race occurrence)
for (slot_index, seg_idx, pos, event) in slots:
    model.AddExactlyOne([x[(s, slot_index)] for s in swimmers])

# 2) Per-swimmer max races across the whole meet (general <= K)
for s in swimmers:
    model.Add(sum(x[(s, slot_index)] for (slot_index, _, _, _) in slots) <= max_races)

# 3) Rest constraints inside each segment (sliding window of length space+1)
for seg_idx, seg in enumerate(segments):
    seg_len = len(seg)
    for start in range(seg_len):
        end = min(start + space + 1, seg_len)
        if end - start > 1:
            # Collect the slot_index on the fly (no persistent lists)
            # Add one AtMostOne per swimmer for this window.
            for s in swimmers:
                model.AddAtMostOne(
                    x[(s, slot_index)]
                    for (slot_index, si, pos, ev) in slots
                    if si == seg_idx and start <= pos < end
                )

# 4) No duplicate event per swimmer (each event at most once per swimmer)
for s in swimmers:
    for ev in Event:
        model.AddAtMostOne(
            x[(s, slot_index)]
            for (slot_index, _, _, e2) in slots
            if e2 == ev
        )

model.Maximize(
    sum(
        sample_points[(s, ev)] * x[(s, slot_index)]
        for s in swimmers
        for (slot_index, _, _, ev) in slots
    )
)

# ---- Solve (single optimal solution) ----
solver = cp_model.CpSolver()
# Optional: solver.parameters.max_time_in_seconds = 30
# Optional: solver.parameters.num_search_workers = 8
status = solver.Solve(model)



if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    best = solver.ObjectiveValue()
    print(f"Optimal total points: {best:.0f}\n")

#     current_seg = -1
#     grand_total = 0
#     for (slot_index, seg_idx, pos, ev) in slots:
#         if seg_idx != current_seg:
#             current_seg = seg_idx
#             print(f"=== Segment {seg_idx} ===")

#         chosen = None
#         for s in swimmers:
#             if solver.Value(x[(s, slot_index)]) == 1:
#                 chosen = s
#                 break

#         pts = sample_points[(chosen, ev)]
#         grand_total += pts
#         print(f"  Pos {pos:>2}: {ev.value:<12} -> {chosen}  ({pts} pts)")

#     print(f"\nCheck total points (recomputed): {int(grand_total)}")
# else:
#     print(f"Solve ended with status: {solver.StatusName(status)}")



# ---------- PASS 2: fix points, maximize rest ----------
# Tunables for density penalty
WINDOW_SIZES = [2, 3, 4]         # windows to penalize; 2=adjacent, 3=nearby
WINDOW_WEIGHTS = {2: 4, 3: 1, 4: 1} # larger weight for shorter windows (more severe)

model2 = cp_model.CpModel()

# Decision vars: x2[s, slot] in {0,1}
x2 = {}
for s in swimmers:
    for (slot_index, seg_idx, pos, ev) in slots:
        x2[(s, slot_index)] = model2.NewBoolVar(f"x_{s}_slot{slot_index}")

# 1) Exactly one swimmer per slot
for (slot_index, seg_idx, pos, ev) in slots:
    model2.AddExactlyOne([x2[(s, slot_index)] for s in swimmers])

# 2) Per-swimmer max races
for s in swimmers:
    model2.Add(sum(x2[(s, slot_index)] for (slot_index, _, _, _) in slots) <= max_races)

# 3) Hard rest inside each segment (keep your minimum rest as a hard rule)
for seg_idx, seg in enumerate(segments):
    seg_len = len(seg)
    for start in range(seg_len):
        end = min(start + space + 1, seg_len)
        if end - start > 1:
            for s in swimmers:
                model2.AddAtMostOne(
                    x2[(s, slot_index)]
                    for (slot_index, si, p, ev) in slots
                    if si == seg_idx and start <= p < end
                )

# 4) No duplicate event per swimmer
for s in swimmers:
    for ev in Event:
        model2.AddAtMostOne(
            x2[(s, slot_index)]
            for (slot_index, _, _, e2) in slots
            if e2 == ev
        )

# 5) Fix points to the optimal value from pass 1
total_points2 = sum(
    sample_points[(s, ev)] * x2[(s, slot_index)]
    for s in swimmers
    for (slot_index, _, _, ev) in slots
)
model2.Add(total_points2 == int(best))

# 6) Tie-break objective: minimize congestion penalties over sliding windows
penalty_terms = []

for seg_idx, seg in enumerate(segments):
    seg_len = len(seg)
    # For each window size (e.g., 2- and 3-slot windows)
    for L in WINDOW_SIZES:
        w = WINDOW_WEIGHTS[L]
        if L <= 1 or L > seg_len:
            continue
        # Slide window across this segment
        for start in range(seg_len - L + 1):
            window_slots = [
                slot for (slot, si, p, ev) in slots
                if si == seg_idx and start <= p < start + L
            ]
            for s in swimmers:
                # count = sum of x over the window (0..L)
                count = model2.NewIntVar(0, L, f"cnt_s{s}_seg{seg_idx}_start{start}_L{L}")
                model2.Add(count == sum(x2[(s, slot)] for slot in window_slots))

                # excess = max(0, count - 1)  (racing once in the window is fine)
                excess = model2.NewIntVar(0, L - 1, f"exc_s{s}_seg{seg_idx}_start{start}_L{L}")
                # ReLU via linear constraints
                # excess >= count - 1
                model2.Add(excess >= count - 1)
                # excess >= 0 is already enforced by domain
                penalty_terms.append(w * excess)

# Minimize total congestion penalty (subject to fixed max points)
if penalty_terms:
    model2.Minimize(sum(penalty_terms))
else:
    model2.Minimize(0)

# ---- Solve the tie-breaker ----
solver2 = cp_model.CpSolver()
# Optional tuning:
# solver2.parameters.num_search_workers = 8
# solver2.parameters.max_time_in_seconds = 30.0

status2 = solver2.Solve(model2)

if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    print(f"Min total congestion penalty: {int(solver2.ObjectiveValue())}\n")
    current_seg = -1
    total_pts = 0
    for (slot_index, seg_idx, pos, ev) in slots:
        if seg_idx != current_seg:
            current_seg = seg_idx
            print(f"=== Segment {seg_idx} ===")
        chosen = None
        for s in swimmers:
            if solver2.Value(x2[(s, slot_index)]) == 1:
                chosen = s
                break
        pts = sample_points[(chosen, ev)]
        total_pts += pts
        print(f"  Pos {pos:>2}: {ev.value:<12} -> {chosen} ({pts} pts)")
    print(f"\nPoints (fixed): {int(total_pts)}   Congestion penalty: {int(solver2.ObjectiveValue())}")
else:
    print(f"Tie-break solve ended with status: {solver2.StatusName(status2)}")


print("--- %s seconds ---" % (time.time() - start_time))