"""
solver.py
------------------------------------------------------------------
Time-indexed Binary Optimization Model
แก้ด้วย Google OR-Tools CP-SAT

ตัวแปรตัดสินใจ
    x[i,j,t] = 1 ถ้าพนักงาน i เริ่มงาน j ที่เวลา t
    Cmax     = เวลาที่งานสุดท้ายเสร็จ

นิพจน์ที่คำนวณจากตัวแปร (ไม่ใช่ตัวแปรตัดสินใจ)
    S_j = sum_i sum_t  t * x[i,j,t]
    E_j = sum_i sum_t (t + d_j) * x[i,j,t]

Objective
    min Cmax

Constraints
    (1) ทุกงานถูกทำหนึ่งครั้ง
    (2) พนักงานต้องทำงานนั้นได้        x[i,j,t] <= a[i,j]
    (3) พนักงานหนึ่งคนทำงานซ้อนไม่ได้
    (4) ลำดับก่อน-หลัง                 E_j <= S_k   สำหรับ (j,k) in P
    (5) เชื่อมเวลาเสร็จกับ Cmax        Cmax >= E_j
    (6) ไม่เกิน Time Limit             Cmax <= T    (เปิด/ปิดได้)
    (7) งานที่ Block กันทำพร้อมกันไม่ได้
    (8) ภาระงาน LAV + GAL ต่อพนักงาน <= 25 นาที
    (9) S5: DEI1 เริ่มที่ t = 0
    (10) x in {0,1}, Cmax in Z>=0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import pandas as pd
from ortools.sat.python import cp_model

from aircraft_data import SERVICE_TASK_KINDS, SERVICE_WORKLOAD_LIMIT, Task


@dataclass
class ProblemData:
    aircraft: str
    workers: List[str]
    tasks: List[Task]
    T: int
    a: Dict[Tuple[str, str], int]
    P: List[Tuple[str, str]] = field(default_factory=list)
    B: List[Tuple[str, str]] = field(default_factory=list)
    enforce_time_limit: bool = True
    objective_mode: str = "Time Only"     # Time Only | Time + Workload | Workload Only
    scenario: str = "S1"


@dataclass
class SolveResult:
    status: str                # OPTIMAL / FEASIBLE / INFEASIBLE / ...
    feasible: bool
    cmax: int | None
    buffer: int | None
    schedule: pd.DataFrame
    workload: pd.DataFrame
    message: str = ""
    solve_time: float = 0.0


EMPTY_SCHEDULE = pd.DataFrame(
    columns=["Worker", "Task", "TaskName", "Zone", "Kind", "Start", "End", "Duration"]
)


def solve_model(data: ProblemData, max_seconds: float = 30.0) -> SolveResult:
    tasks = {t.id: t for t in data.tasks}
    J = list(tasks.keys())
    I = list(data.workers)
    T = int(data.T)

    if not J:
        return SolveResult("NO_TASK", False, None, None, EMPTY_SCHEDULE.copy(),
                           pd.DataFrame(), "ยังไม่มีงานในรายการ")
    if not I:
        return SolveResult("NO_WORKER", False, None, None, EMPTY_SCHEDULE.copy(),
                           pd.DataFrame(), "ยังไม่มีพนักงาน")

    total_duration = sum(t.duration for t in data.tasks)
    horizon = T if data.enforce_time_limit else max(T, total_duration)

    # งานที่ยาวเกินขอบเขตเวลา -> ตอบไม่ได้ตั้งแต่ต้น
    too_long = [j for j in J if tasks[j].duration > horizon]
    if too_long:
        return SolveResult(
            "INFEASIBLE", False, None, None, EMPTY_SCHEDULE.copy(), pd.DataFrame(),
            f"งาน {', '.join(too_long)} ใช้เวลานานกว่าเวลาจอดที่กำหนด ({horizon} นาที)",
        )

    # H_j = {0, 1, ..., horizon - d_j}
    H = {j: list(range(0, horizon - tasks[j].duration + 1)) for j in J}

    model = cp_model.CpModel()

    # ---- ตัวแปรตัดสินใจ x[i,j,t] -------------------------------------
    # Constraint (2) ถูกบังคับโดยไม่สร้างตัวแปรเมื่อ a[i,j] = 0
    x: Dict[Tuple[str, str, int], cp_model.IntVar] = {}
    for i in I:
        for j in J:
            if data.a.get((i, j), 1) == 0:
                continue
            for t in H[j]:
                x[(i, j, t)] = model.NewBoolVar(f"x_{i}_{j}_{t}")

    # ---- Constraint (1) ทุกงานถูกทำหนึ่งครั้ง -------------------------
    for j in J:
        lits = [x[(i, j, t)] for i in I for t in H[j] if (i, j, t) in x]
        if not lits:
            return SolveResult(
                "INFEASIBLE", False, None, None, EMPTY_SCHEDULE.copy(), pd.DataFrame(),
                f"ไม่มีพนักงานคนใดทำงาน {j} ได้ (a_ij = 0 ทั้งหมด) กรุณาแก้ตาราง Skill Matrix",
            )
        model.AddExactlyOne(lits)

    # ---- นิพจน์ S_j และ E_j -------------------------------------------
    start_expr = {
        j: sum(t * x[(i, j, t)] for i in I for t in H[j] if (i, j, t) in x) for j in J
    }
    end_expr = {
        j: sum((t + tasks[j].duration) * x[(i, j, t)]
               for i in I for t in H[j] if (i, j, t) in x)
        for j in J
    }

    # ---- Constraint (3) พนักงานคนเดียวทำงานซ้อนไม่ได้ -----------------
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

    # ---- Constraint (8) ภาระงานห้องน้ำ + ห้องครัวต่อคน <= 25 นาที ----
    # นับจากผลรวม duration ของงาน LAV/GAL ที่พนักงานคนนั้นได้รับมอบหมาย
    # หาก service workload รวมเกิน 25 นาที Solver จำเป็นต้องกระจายไปคนเพิ่ม
    for i in I:
        service_load_terms = [
            tasks[j].duration * x[(i, j, t)]
            for j in J
            if tasks[j].kind in SERVICE_TASK_KINDS
            for t in H[j]
            if (i, j, t) in x
        ]
        if service_load_terms:
            model.Add(sum(service_load_terms) <= SERVICE_WORKLOAD_LIMIT)

    # ---- Constraint (4) ลำดับก่อน-หลัง --------------------------------
    for (j, k) in data.P:
        if j in tasks and k in tasks:
            model.Add(end_expr[j] <= start_expr[k])

    # ---- Scenario S5: De-icing เริ่มทันทีที่นาที 0 ----------------------
    # DEICE1 เป็นพนักงานเฉพาะงาน DEI1 อยู่แล้วจาก capability matrix
    # จึงกำหนดเวลาเริ่มของ DEI1 = 0 เพื่อให้การฉีด De-icing ทำคู่ขนานกับ
    # งาน Cleaning/ground-service ตั้งแต่เริ่ม turnaround ได้โดยตรง
    if data.scenario == "S5" and "DEI1" in tasks:
        model.Add(start_expr["DEI1"] == 0)

    # ---- Constraint (7) งานที่ Block กัน ------------------------------
    for (j, k) in data.B:
        if j not in tasks or k not in tasks:
            continue
        for tau in range(horizon):
            active_j = [x[(i, j, t)] for i in I for t in H[j]
                        if (i, j, t) in x and t <= tau < t + tasks[j].duration]
            active_k = [x[(i, k, t)] for i in I for t in H[k]
                        if (i, k, t) in x and t <= tau < t + tasks[k].duration]
            if active_j and active_k:
                model.Add(sum(active_j) + sum(active_k) <= 1)

    # ---- Constraint (5)(6) และ Objective ------------------------------
    cmax = model.NewIntVar(0, horizon, "Cmax")
    for j in J:
        model.Add(cmax >= end_expr[j])
    if data.enforce_time_limit:
        model.Add(cmax <= T)

    load = {}
    for i in I:
        load[i] = model.NewIntVar(0, total_duration, f"load_{i}")
        model.Add(load[i] == sum(
            tasks[j].duration * x[(i, j, t)]
            for j in J for t in H[j] if (i, j, t) in x
        ))
    max_load = model.NewIntVar(0, total_duration, "max_load")
    model.AddMaxEquality(max_load, list(load.values()))

    if data.objective_mode == "Workload Only":
        model.Minimize(max_load)
    elif data.objective_mode == "Time + Workload":
        # ถ่วงน้ำหนักให้ Cmax เป็นเป้าหมายหลัก แล้วใช้ workload เป็นตัวตัดสินเมื่อเสมอกัน
        model.Minimize(cmax * (total_duration + 1) + max_load)
    else:
        model.Minimize(cmax)

    # ---- Solve --------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max_seconds)
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        msg = (
            f"ไม่พบคำตอบที่เป็นไปได้ภายในเวลาจอด {T} นาที "
            f"ด้วยพนักงาน {len(I)} คน — ลองเพิ่มพนักงาน เพิ่มเวลาจอด "
            f"หรือปิดข้อจำกัด Time Limit"
            if status_name == "INFEASIBLE" else
            f"Solver จบด้วยสถานะ {status_name}"
        )
        return SolveResult(status_name, False, None, None, EMPTY_SCHEDULE.copy(),
                           pd.DataFrame(), msg, solver.WallTime())

    # ---- แปลงคำตอบเป็นตาราง -------------------------------------------
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
    schedule = pd.DataFrame(rows).sort_values(["Start", "Worker"]).reset_index(drop=True)

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

    return SolveResult(
        status=status_name,
        feasible=True,
        cmax=cmax_value,
        buffer=T - cmax_value,
        schedule=schedule,
        workload=workload,
        message="",
        solve_time=solver.WallTime(),
    )


def compare_scenarios(build_fn, scenarios: List[str], max_seconds: float = 20.0) -> pd.DataFrame:
    """
    build_fn(scenario) -> ProblemData
    คืนตารางเปรียบเทียบผลของแต่ละ Scenario
    """
    rows = []
    for s in scenarios:
        data = build_fn(s)
        res = solve_model(data, max_seconds=max_seconds)
        rows.append({
            "Scenario": s,
            "Workers": len(data.workers),
            "Tasks": len(data.tasks),
            "T (min)": data.T,
            "Cmax": res.cmax if res.feasible else None,
            "Buffer": res.buffer if res.feasible else None,
            "Status": res.status,
            "Feasible": res.feasible,
            "Solve Time (s)": round(res.solve_time, 2),
        })
    return pd.DataFrame(rows)
