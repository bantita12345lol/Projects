"""
solver.py
------------------------------------------------------------------
Time-indexed Binary Optimization Model solved by Google OR-Tools CP-SAT.

Decision variable
    x[i,j,t] = 1 if worker i starts task j at time t
    Cmax     = completion time of the last modeled activity

Key model choices
    - Same-zone cabin tasks use a follow-lag pipeline with adjustable k:
        S_k >= S_j + k
        E_k >= E_j + k
      This models workers following each other through the same cabin zone.
    - Cross-area precedence is strict finish-to-start:
        E_j <= S_k
    - If the same worker performs both GAL and LAV, GAL must finish before LAV starts.
    - In S3, DEI1 is linked by precedence after every cleaning task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import pandas as pd
from ortools.sat.python import cp_model

from aircraft_data import DEFAULT_FOLLOW_LAG, Task


@dataclass
class ProblemData:
    aircraft: str
    workers: List[str]
    tasks: List[Task]
    T: int
    a: Dict[Tuple[str, str], int]
    P: List[Tuple[str, str]] = field(default_factory=list)
    follow_lag: int = DEFAULT_FOLLOW_LAG
    hygiene_galley_first: bool = True
    enforce_time_limit: bool = True
    objective_mode: str = "Time + Workload"  # Time Only | Time + Workload | Workload Only
    scenario: str = "S1"
    # Secondary workload balancing can exclude special resources such as DEICE_TEAM.
    balance_workers: List[str] | None = None
    # Reporting metadata for S2/S3 outer search.
    service_worker_count: int | None = None


@dataclass
class SolveResult:
    status: str
    feasible: bool
    cmax: int | None
    buffer: int | None
    schedule: pd.DataFrame
    workload: pd.DataFrame
    message: str = ""
    solve_time: float = 0.0
    gap_pct: float | None = None
    max_balance_load: int | None = None


EMPTY_SCHEDULE = pd.DataFrame(
    columns=["Worker", "Task", "TaskName", "Zone", "Kind", "Start", "End", "Duration"]
)


def _same_cabin_zone(j: str, k: str, tasks: Dict[str, Task]) -> bool:
    return (
        j in tasks and k in tasks
        and tasks[j].zone == tasks[k].zone
        and tasks[j].zone.startswith("Z")
    )


def solve_model(data: ProblemData, max_seconds: float = 30.0) -> SolveResult:
    tasks = {t.id: t for t in data.tasks}
    J = list(tasks)
    I = list(data.workers)
    T = int(data.T)
    lag = max(0, int(data.follow_lag))

    if not J:
        return SolveResult("NO_TASK", False, None, None, EMPTY_SCHEDULE.copy(), pd.DataFrame(),
                           "ยังไม่มีงานในรายการ")
    if not I:
        return SolveResult("NO_WORKER", False, None, None, EMPTY_SCHEDULE.copy(), pd.DataFrame(),
                           "ยังไม่มีพนักงาน")

    total_duration = sum(t.duration for t in data.tasks)
    horizon = T if data.enforce_time_limit else max(T, total_duration)

    too_long = [j for j in J if tasks[j].duration > horizon]
    if too_long:
        return SolveResult(
            "INFEASIBLE", False, None, None, EMPTY_SCHEDULE.copy(), pd.DataFrame(),
            f"งาน {', '.join(too_long)} ใช้เวลานานกว่าขอบเขตเวลาที่กำหนด ({horizon} นาที)",
        )

    H = {j: list(range(0, horizon - tasks[j].duration + 1)) for j in J}
    model = cp_model.CpModel()

    # x[i,j,t]
    x: Dict[Tuple[str, str, int], cp_model.IntVar] = {}
    for i in I:
        for j in J:
            if data.a.get((i, j), 0) == 0:
                continue
            for t in H[j]:
                x[(i, j, t)] = model.NewBoolVar(f"x_{i}_{j}_{t}")

    # (1) Every task exactly once
    for j in J:
        lits = [x[(i, j, t)] for i in I for t in H[j] if (i, j, t) in x]
        if not lits:
            return SolveResult(
                "INFEASIBLE", False, None, None, EMPTY_SCHEDULE.copy(), pd.DataFrame(),
                f"ไม่มีพนักงานที่มีสิทธิ์ทำงาน {j} ตาม capability matrix",
            )
        model.AddExactlyOne(lits)

    # Derived expressions
    start_expr = {
        j: sum(t * x[(i, j, t)] for i in I for t in H[j] if (i, j, t) in x)
        for j in J
    }
    end_expr = {
        j: sum((t + tasks[j].duration) * x[(i, j, t)]
               for i in I for t in H[j] if (i, j, t) in x)
        for j in J
    }

    # (3) A worker cannot overlap tasks
    for i in I:
        for tau in range(horizon):
            active = [
                x[(i, j, t)]
                for j in J
                for t in H[j]
                if (i, j, t) in x and t <= tau < t + tasks[j].duration
            ]
            if len(active) > 1:
                model.AddAtMostOne(active)

    # (4) Precedence
    for j, k in data.P:
        if j not in tasks or k not in tasks:
            continue
        if _same_cabin_zone(j, k, tasks):
            # D1-B: following worker stays at least k minutes behind the previous task.
            # The end-lag condition prevents the follower from overtaking a longer task.
            model.Add(start_expr[k] >= start_expr[j] + lag)
            model.Add(end_expr[k] >= end_expr[j] + lag)
        else:
            model.Add(end_expr[j] <= start_expr[k])

    # (7) Hygiene rule: if the same worker performs GAL and LAV,
    # Galley must finish before Lavatory starts.
    if data.hygiene_galley_first:
        gal = [j for j in J if tasks[j].kind == "GAL"]
        lav = [j for j in J if tasks[j].kind == "LAV"]
        for i in I:
            for g in gal:
                for l in lav:
                    # Forbid any pair of starts on the same worker that would place
                    # LAV before GAL has finished. If tasks are on different workers,
                    # this condition has no effect.
                    for tg in H[g]:
                        if (i, g, tg) not in x:
                            continue
                        for tl in H[l]:
                            if (i, l, tl) not in x:
                                continue
                            if tl < tg + tasks[g].duration:
                                model.Add(x[(i, g, tg)] + x[(i, l, tl)] <= 1)

    # (5)(6) Cmax and turnaround limit
    cmax = model.NewIntVar(0, horizon, "Cmax")
    for j in J:
        model.Add(cmax >= end_expr[j])
    if data.enforce_time_limit:
        model.Add(cmax <= T)

    # Workload for tie-breaking / reporting
    load: Dict[str, cp_model.IntVar] = {}
    for i in I:
        load[i] = model.NewIntVar(0, total_duration, f"load_{i}")
        model.Add(load[i] == sum(
            tasks[j].duration * x[(i, j, t)]
            for j in J for t in H[j] if (i, j, t) in x
        ))
    balance_ids = [i for i in (data.balance_workers or I) if i in load]
    if not balance_ids:
        balance_ids = list(I)
    max_load = model.NewIntVar(0, total_duration, "max_load")
    model.AddMaxEquality(max_load, [load[i] for i in balance_ids])

    if data.objective_mode == "Workload Only":
        model.Minimize(max_load)
    elif data.objective_mode == "Time + Workload":
        # Two-level objective encoded by a dominating weight:
        # (1) minimize Cmax, then (2) among equal-Cmax schedules minimize
        # maximum workload of balance_workers.  total_duration+1 guarantees
        # a one-minute Cmax improvement dominates any workload difference.
        model.Minimize(cmax * (total_duration + 1) + max_load)
    else:
        model.Minimize(cmax)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_seconds)
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status_name == "INFEASIBLE":
            msg = (
                f"ไม่พบคำตอบภายใน T = {T} นาที ด้วยพนักงาน {len(I)} คน "
                "ภายใต้ Scenario และข้อจำกัดที่เลือก"
            )
        else:
            msg = f"Solver จบด้วยสถานะ {status_name}"
        return SolveResult(status_name, False, None, None, EMPTY_SCHEDULE.copy(),
                           pd.DataFrame(), msg, solver.WallTime(), None)

    rows = []
    for (i, j, t), var in x.items():
        if solver.Value(var) == 1:
            rows.append({
                "Worker": i,
                "Task": j,
                "TaskName": tasks[j].name,
                "Zone": tasks[j].zone,
                "Kind": tasks[j].kind,
                "Start": t,
                "End": t + tasks[j].duration,
                "Duration": tasks[j].duration,
            })
    schedule = pd.DataFrame(rows).sort_values(["Start", "Worker", "Task"]).reset_index(drop=True)

    cmax_value = int(solver.Value(cmax))
    workload = (
        schedule.groupby("Worker")
        .agg(Tasks=("Task", "count"), BusyMinutes=("Duration", "sum"))
        .reindex(I)
        .fillna(0)
        .astype(int)
        .reset_index()
    )
    workload["IdleMinutes"] = cmax_value - workload["BusyMinutes"]
    workload["Utilization %"] = (
        workload["BusyMinutes"] / cmax_value * 100
    ).round(1) if cmax_value > 0 else 0.0

    # The objective may be weighted, so report the gap on the actual CP-SAT
    # objective value rather than pretending it is a pure Cmax gap.
    obj = float(solver.ObjectiveValue())
    bound = float(solver.BestObjectiveBound())
    gap_pct = 0.0 if status_name == "OPTIMAL" else (
        abs(obj - bound) / max(1.0, abs(obj)) * 100.0
    )

    return SolveResult(
        status=status_name,
        feasible=True,
        cmax=cmax_value,
        buffer=T - cmax_value,
        schedule=schedule,
        workload=workload,
        message="",
        solve_time=solver.WallTime(),
        gap_pct=round(gap_pct, 3),
        max_balance_load=int(solver.Value(max_load)),
    )


def solve_best_variant(build_fn, variants, max_seconds: float = 20.0):
    """
    Solve a small family of policy variants and choose lexicographically by
    (Cmax, maximum balanced-worker load, variant value).

    Used for S2/S3 to avoid treating the Service-workload lower bound as a
    fixed team size.  Returns (best_variant, best_data, best_result, table).
    """
    rows = []
    best = None
    for v in variants:
        data = build_fn(v)
        res = solve_model(data, max_seconds=max_seconds)
        rows.append({
            "Variant": v,
            "Cmax": res.cmax,
            "Max Balanced Load": res.max_balance_load,
            "Status": res.status,
            "Feasible": res.feasible,
            "Solve Time (s)": round(res.solve_time, 3),
        })
        if not res.feasible:
            continue
        key = (res.cmax, res.max_balance_load if res.max_balance_load is not None else 10**9, v)
        if best is None or key < best[0]:
            best = (key, v, data, res)
    if best is None:
        return None, None, None, pd.DataFrame(rows)
    return best[1], best[2], best[3], pd.DataFrame(rows)


def find_min_workers(build_fn, m_min: int, m_max: int = 30,
                     max_seconds: float = 20.0):
    """
    Try workforce sizes in ascending order.

    The first feasible workforce is a PROVEN minimum only if every smaller
    tested workforce was proven INFEASIBLE.  UNKNOWN at a smaller workforce
    means the returned workforce is merely the smallest feasible one found
    within the time limit.

    build_fn(m) may return either ProblemData or a tuple (ProblemData, SolveResult).
    """
    rows = []
    best_m = None
    best_res = None
    uncertain_below = False
    for m in range(max(1, int(m_min)), int(m_max) + 1):
        built = build_fn(m)
        if isinstance(built, tuple) and len(built) == 2:
            data, res = built
        else:
            data = built
            res = solve_model(data, max_seconds=max_seconds)

        if res.status not in ("INFEASIBLE",) and not res.feasible:
            uncertain_below = True

        row = {
            "Workers": m,
            "Cmax": res.cmax,
            "Status": res.status,
            "Feasible": res.feasible,
            "Solve Time (s)": round(res.solve_time, 3),
            "Minimum Proven": False,
        }
        rows.append(row)
        if res.feasible:
            best_m, best_res = m, res
            row["Minimum Proven"] = not uncertain_below
            break
    return best_m, best_res, pd.DataFrame(rows)

