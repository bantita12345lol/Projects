"""
app.py
------------------------------------------------------------------
Aircraft Cleaning Optimization — research dashboard

Design intent
- S1/S2 are the normal-condition cleaning scenarios.
- S3 is a separate Winter De-icing Extension.
- Main model uses Normal/Clear weather and baseline task times only.
- Task times are literature-calibrated assumptions when field data are unavailable.
"""

from __future__ import annotations

import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from aircraft_data import (
    AIRCRAFT_LIBRARY,
    CLEANING_TYPES,
    DEFAULT_AIRCRAFT,
    DEFAULT_CLEANING_TYPE,
    DEFAULT_DURATION_FACTOR,
    DEFAULT_FOLLOW_LAG,
    MODEL_ASSUMPTIONS,
    QUICK_TRANSIT,
    QUICK_TRANSIT_OPTIONAL,
    SCENARIOS,
    TASK_KIND_LABEL,
    Task,
    build_capability,
    build_precedence,
    build_scenario_workers,
    build_tasks,
    cleaning_kinds,
    cleaning_time_window,
    minimum_total_workers_for_policy,
    required_service_workers,
    service_worker_count_options,
    service_workload_minutes,
    scenario_settings,
)
from solver import ProblemData, find_min_workers, solve_best_variant, solve_model

st.set_page_config(page_title="Aircraft Cleaning Optimization", page_icon="✈️", layout="wide")

KIND_COLORS = {
    "C1": "#4E79A7", "C2": "#59A14F", "C3": "#F28E2B", "D": "#E15759",
    "E": "#76B7B2", "F": "#EDC948", "LAV": "#B07AA1", "GAL": "#FF9DA7",
    "OVH": "#9C755F", "FD": "#BAB0AC", "CR": "#2F4B7C", "RC": "#7A5195",
    "DEI": "#00A6A6",
}
MODE_TH = {
    "schedule": "หาตารางงานจากจำนวนพนักงานที่กำหนด",
    "min_workers": "หาจำนวนพนักงานน้อยที่สุดที่เสร็จภายใน T",
}


def worker_name(w: str) -> str:
    if w == "DEICE_TEAM":
        return "DEICE_TEAM — ทีม De-icing"
    if w.startswith("M") and w[1:].isdigit():
        return f"{w} — พนักงานคนที่ {int(w[1:])}"
    return w


def zone_name(z: str, aircraft: str) -> str:
    for zid, name, seats in AIRCRAFT_LIBRARY[aircraft]["zones"]:
        if z == zid:
            return f"{name} ({seats} ที่นั่ง)"
    return {
        "LAV": "Lavatory", "GAL": "Galley", "CREW": "Crew Area",
        "CHECK": "Final Check", "DEICE": "De-icing Area",
    }.get(z, z)


def tasks_to_df(tasks: list[Task], aircraft: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "เลือก": True,
        "Task ID": t.id,
        "ประเภท": t.kind,
        "ประเภทงานจริง": TASK_KIND_LABEL.get(t.kind, t.kind),
        "Zone": t.zone,
        "Work Unit / Zone จริง": zone_name(t.zone, aircraft),
        "ชื่องาน": t.name,
        "d_j (นาที)": t.duration,
    } for t in tasks])


def df_to_tasks(df: pd.DataFrame) -> list[Task]:
    out: list[Task] = []
    for _, r in df.iterrows():
        if not bool(r.get("เลือก", True)):
            continue
        try:
            duration = int(r["d_j (นาที)"])
        except Exception:
            continue
        if duration <= 0:
            continue
        out.append(Task(
            str(r["Task ID"]), str(r["ประเภท"]), str(r["Zone"]),
            str(r["ชื่องาน"]), duration,
        ))
    return out


def skill_matrix_df(workers: list[str], tasks: list[Task], zone_based: bool,
                    dedicated: str | None, service_count: int = 0) -> pd.DataFrame:
    a = build_capability(
        workers, tasks, zone_based=zone_based,
        service_worker_count=service_count,
        dedicated_deicing_worker=dedicated,
    )
    data = {"พนักงาน": workers}
    for t in tasks:
        data[t.id] = [bool(a.get((w, t.id), 0)) for w in workers]
    return pd.DataFrame(data)


def df_to_capability(df: pd.DataFrame, tasks: list[Task]) -> dict:
    out = {}
    for _, r in df.iterrows():
        w = str(r["พนักงาน"])
        for t in tasks:
            out[(w, t.id)] = 1 if bool(r.get(t.id, False)) else 0
    return out


def capability_to_df(data: ProblemData) -> pd.DataFrame:
    rows = []
    for w in data.workers:
        row = {"Worker": w}
        for t in data.tasks:
            row[t.id] = data.a.get((w, t.id), 0)
        rows.append(row)
    return pd.DataFrame(rows)


def schedule_display_df(schedule: pd.DataFrame, aircraft: str) -> pd.DataFrame:
    d = schedule.copy()
    d["พนักงาน"] = d["Worker"].map(worker_name)
    d["ประเภทงาน"] = d["Kind"].map(lambda k: TASK_KIND_LABEL.get(k, k))
    d["พื้นที่"] = d["Zone"].map(lambda z: zone_name(z, aircraft))
    return d[["พนักงาน", "TaskName", "ประเภทงาน", "พื้นที่", "Task", "Start", "End", "Duration"]].rename(
        columns={"TaskName": "ชื่องาน", "Task": "Task ID", "Start": "เริ่ม (นาที)",
                 "End": "เสร็จ (นาที)", "Duration": "เวลา (นาที)"}
    )


def workload_display_df(workload: pd.DataFrame) -> pd.DataFrame:
    d = workload.copy()
    d["Worker"] = d["Worker"].map(worker_name)
    return d.rename(columns={
        "Worker": "พนักงาน", "Tasks": "จำนวนงาน", "BusyMinutes": "เวลาทำงาน",
        "IdleMinutes": "เวลาว่าง", "Utilization %": "Utilization (%)",
    })


def gantt_chart(schedule: pd.DataFrame, workers: list[str], T: int, cmax: int) -> go.Figure:
    fig = go.Figure()
    plot = schedule.copy()
    plot["WorkerDisplay"] = plot.Worker.map(worker_name)
    for kind in plot.Kind.unique():
        sub = plot[plot.Kind == kind]
        fig.add_trace(go.Bar(
            y=sub.WorkerDisplay, x=sub.Duration, base=sub.Start, orientation="h",
            name=TASK_KIND_LABEL.get(kind, kind), marker_color=KIND_COLORS.get(kind, "#64748B"),
            text=sub.TaskName, textposition="inside",
            customdata=sub[["Task", "Start", "End"]],
            hovertemplate=(
                "<b>%{text}</b><br>Task %{customdata[0]}"
                "<br>Start %{customdata[1]}<br>End %{customdata[2]}<extra></extra>"
            ),
        ))
    fig.add_vline(x=cmax, line_dash="dash", annotation_text=f"Cmax={cmax}")
    fig.add_vline(x=T, line_dash="dot", annotation_text=f"T={T}")
    fig.update_layout(
        barmode="overlay", height=max(420, 70 * len(workers) + 140),
        xaxis_title="เวลา (นาที)", yaxis_title="ทรัพยากร/พนักงาน",
        legend_title="ประเภทงาน", margin=dict(l=10, r=10, t=60, b=10),
    )
    return fig


def workload_chart(workload: pd.DataFrame) -> go.Figure:
    d = workload.copy()
    d["WorkerDisplay"] = d.Worker.map(worker_name)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=d.WorkerDisplay, y=d.BusyMinutes, name="Busy"))
    fig.add_trace(go.Bar(x=d.WorkerDisplay, y=d.IdleMinutes, name="Idle"))
    fig.update_layout(barmode="stack", height=350, yaxis_title="นาที")
    return fig


def compare_chart(table: pd.DataFrame, T: int) -> go.Figure:
    fig = go.Figure()
    for s in table.Scenario.unique():
        d = table[table.Scenario == s]
        fig.add_trace(go.Scatter(x=d.Workers, y=d.Cmax, mode="lines+markers", name=s))
    fig.add_hline(y=T, line_dash="dot", annotation_text=f"T={T}")
    fig.update_layout(xaxis_title="จำนวนทรัพยากรรวม", yaxis_title="Cmax (นาที)", height=420)
    return fig


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name[:31], index=False)
    return buf.getvalue()


def status_th(status: str) -> str:
    return {
        "OPTIMAL": "คำตอบเหมาะที่สุด",
        "FEASIBLE": "พบคำตอบที่เป็นไปได้",
        "INFEASIBLE": "พิสูจน์ว่าไม่มีคำตอบที่เป็นไปได้",
        "UNKNOWN": "ยังสรุปไม่ได้ภายในเวลาคำนวณ",
    }.get(status, status)


# ==================================================================
# Sidebar
# ==================================================================
st.sidebar.title("⚙️ ตั้งค่าการทดลอง")

st.sidebar.subheader("1) อากาศยานและรูปแบบงาน")
aircraft_names = list(AIRCRAFT_LIBRARY)
aircraft = st.sidebar.selectbox(
    "ประเภทอากาศยาน", aircraft_names,
    index=aircraft_names.index(DEFAULT_AIRCRAFT),
)
spec = AIRCRAFT_LIBRARY[aircraft]
st.sidebar.caption(
    f"{spec['seats']} ที่นั่ง · {len(spec['zones'])} โซน · "
    f"ห้องน้ำ {spec['n_lav']} · ครัว {spec['n_gal']} · "
    f"{spec['seats_abreast']} ที่นั่ง/แถว (สมมติฐาน)"
)
st.sidebar.caption("Aircraft configuration เป็นค่าที่กำหนดสำหรับการศึกษา ไม่ใช่ layout เฉพาะสายการบิน")

cleaning_type = st.sidebar.selectbox(
    "รูปแบบการทำความสะอาด", list(CLEANING_TYPES),
    index=list(CLEANING_TYPES).index(DEFAULT_CLEANING_TYPE),
)
if cleaning_type == QUICK_TRANSIT:
    opt_surface = st.sidebar.checkbox(
        f"เพิ่มงานพื้นผิว ({QUICK_TRANSIT_OPTIONAL['E']})", False
    )
    opt_amenity = st.sidebar.checkbox(
        f"เพิ่มงาน Amenity ({QUICK_TRANSIT_OPTIONAL['F']})", False
    )
else:
    opt_surface = opt_amenity = False
active_kinds = cleaning_kinds(cleaning_type, opt_surface, opt_amenity)

duration_factor = DEFAULT_DURATION_FACTOR
st.sidebar.caption("สภาพอากาศ: Normal/Clear คงที่ · เวลางานฐาน 100%")

st.sidebar.subheader("2) พนักงานและเวลา")
scenario_hint = st.session_state.get("scenario_selector", "S1")
default_workers = {"S1": 4, "S2": 4, "S3": 5}.get(scenario_hint, 4)
n_workers = st.sidebar.number_input(
    "จำนวนทรัพยากรรวม (m)", 1, 30, default_workers, 1,
    key=f"workers_{scenario_hint}",
    help="S3 นับ DEICE_TEAM เป็นทรัพยากรเฉพาะเพิ่ม 1 ทีม ไม่ใช่ Cleaner",
)
T = st.sidebar.number_input(
    "เวลาที่กำหนด T (นาที)", 5, 300, 30, 5,
    help=(
        "S1/S2: เวลาจนงาน Cleaning เสร็จ · S3: เวลาจน Winter Extension "
        "(De-icing) เสร็จ โดย Cleaning ต้องเสร็จก่อน"
    ),
)

st.sidebar.subheader("3) นโยบายและข้อจำกัด")
scenario = st.sidebar.selectbox(
    "Scenario", list(SCENARIOS),
    format_func=lambda s: f"{s} — {SCENARIOS[s]}",
    key="scenario_selector",
)
calc_mode = st.sidebar.radio(
    "รูปแบบการคำนวณ", list(MODE_TH), format_func=lambda k: MODE_TH[k]
)
follow_lag = st.sidebar.number_input(
    "ระยะไล่ตาม k (นาที)", 0, 5, DEFAULT_FOLLOW_LAG, 1,
    help=(
        "ค่าเริ่มต้น k=1 เป็นสมมติฐานแทนระยะห่างของ work front ภายใน Zone; "
        "ควรอ่านร่วมกับ Sensitivity k=0,1,2 ในบทที่ 4"
    ),
)
use_hygiene = st.sidebar.checkbox(
    "กฎสุขอนามัย: ครัวก่อนห้องน้ำ", True,
    help="ใช้เฉพาะกรณีที่พนักงานคนเดียวกันถูกมอบหมายทั้ง Galley และ Lavatory",
)
enforce_T = True if calc_mode == "min_workers" else st.sidebar.checkbox("บังคับ Cmax ≤ T", True)
max_seconds = st.sidebar.slider("เวลาคำนวณสูงสุดต่อกรณี (วินาที)", 5, 120, 30, 5)

cfg = scenario_settings(scenario)


# ==================================================================
# Tasks / model builders
# ==================================================================
def default_tasks(s: str) -> list[Task]:
    return build_tasks(
        aircraft, active_kinds, duration_factor,
        include_deicing=scenario_settings(s)["include_deicing"],
    )


signature = f"{aircraft}|{cleaning_type}|{active_kinds}|normal|{scenario}"
if st.session_state.get("signature") != signature:
    st.session_state["signature"] = signature
    st.session_state["tasks_df"] = tasks_to_df(default_tasks(scenario), aircraft)
    for key in ("skill_df", "skill_key", "result", "data", "compare", "minw_table", "partition_table"):
        st.session_state.pop(key, None)


def tasks_for_scenario(base_tasks: list[Task], s: str) -> list[Task]:
    normal = [t for t in base_tasks if t.kind != "DEI"]
    if not scenario_settings(s)["include_deicing"]:
        return normal
    deice = [t for t in default_tasks("S3") if t.kind == "DEI"]
    return normal + deice


def build_problem(tasks: list[Task], m_total: int, s: str,
                  service_count: int | None = None,
                  a_override: dict | None = None,
                  enforce: bool = True) -> ProblemData:
    c = scenario_settings(s)
    workers, dedicated = build_scenario_workers(m_total, s)
    if service_count is None:
        service_count = required_service_workers(tasks, int(T), s) if c["zone_based"] else 0
    a = a_override if a_override is not None else build_capability(
        workers, tasks, zone_based=c["zone_based"],
        service_worker_count=service_count,
        dedicated_deicing_worker=dedicated,
    )
    balance_workers = [w for w in workers if w != dedicated]
    return ProblemData(
        aircraft=aircraft,
        workers=workers,
        tasks=tasks,
        T=int(T),
        a=a,
        P=build_precedence(tasks),
        follow_lag=int(follow_lag),
        hygiene_galley_first=use_hygiene,
        enforce_time_limit=enforce,
        objective_mode="Time + Workload",
        scenario=s,
        balance_workers=balance_workers,
        service_worker_count=service_count if c["zone_based"] else None,
    )


def solve_policy(tasks: list[Task], m_total: int, s: str,
                 a_override: dict | None = None,
                 enforce: bool = True):
    """Solve one scenario, including an outer search over Service-team size."""
    c = scenario_settings(s)
    if not c["zone_based"]:
        data = build_problem(tasks, m_total, s, service_count=0,
                             a_override=a_override, enforce=enforce)
        res = solve_model(data, max_seconds=max_seconds)
        return data, res, pd.DataFrame([{
            "Service Workers": 0, "Cmax": res.cmax, "Status": res.status,
            "Feasible": res.feasible,
        }])

    options = service_worker_count_options(tasks, int(T), s, m_total)
    if not options:
        # Build the lower-bound allocation only so the solver returns a clear infeasible reason.
        lb = required_service_workers(tasks, int(T), s)
        data = build_problem(tasks, m_total, s, service_count=lb, enforce=enforce)
        res = solve_model(data, max_seconds=max_seconds)
        return data, res, pd.DataFrame([{
            "Service Workers": lb, "Cmax": res.cmax, "Status": res.status,
            "Feasible": res.feasible,
        }])

    best_svc, data, res, table = solve_best_variant(
        lambda svc: build_problem(tasks, m_total, s, service_count=svc, enforce=enforce),
        options,
        max_seconds=max_seconds,
    )
    table = table.rename(columns={"Variant": "Service Workers"})
    if data is None:
        # retain a representative problem for diagnostics
        data = build_problem(tasks, m_total, s, service_count=options[0], enforce=enforce)
        res = solve_model(data, max_seconds=max_seconds)
    return data, res, table


preview_tasks = default_tasks(scenario)
policy_lb = minimum_total_workers_for_policy(preview_tasks, int(T), scenario)
if int(n_workers) < policy_lb:
    st.sidebar.warning(
        f"Lower Bound ตามนโยบาย = {policy_lb} resources; จำนวนที่กรอกต่ำกว่านี้มีโอกาส Infeasible สูง"
    )


# ==================================================================
# Header
# ==================================================================
st.title("✈️ Aircraft Cleaning Optimization")
st.caption(
    "Time-indexed Binary Optimization · OR-Tools CP-SAT · "
    "Normal/Clear baseline · Literature-calibrated assumptions"
)

h1, h2, h3, h4 = st.columns(4)
h1.metric("Aircraft", aircraft)
h2.metric("Cleaning", cleaning_type)
h3.metric("Modeled resources", f"{n_workers}")
h4.metric("T", f"{T} นาที")

if cfg["zone_based"]:
    svc_lb = required_service_workers(preview_tasks, int(T), scenario)
    svc_opts = service_worker_count_options(preview_tasks, int(T), scenario, int(n_workers))
    st.info(
        f"{scenario}: LAV+GAL workload = {service_workload_minutes(preview_tasks)} นาที · "
        f"T_clean = {cleaning_time_window(preview_tasks, int(T), scenario)} นาที · "
        f"Service-team lower bound = {svc_lb} · "
        f"ระบบจะลอง Service-team sizes = {svc_opts if svc_opts else 'ไม่มีช่วงที่เป็นไปได้'}"
    )
if cfg["include_deicing"]:
    st.warning(
        "S3 เป็น Winter De-icing Extension ไม่ใช่สภาพ Normal/Clear: "
        "DEICE_TEAM เป็นทรัพยากรเฉพาะ และเริ่มหลัง Cleaning ทุกงานเสร็จ"
    )

setup, result_tab, gantt_tab, compare_tab, model_tab = st.tabs([
    "01 · Input & Model Setup",
    "02 · Optimization Results",
    "03 · Gantt & Workforce",
    "04 · Scenario Analysis",
    "05 · Mathematical Model & Assumptions",
])


# ==================================================================
# TAB 01
# ==================================================================
with setup:
    st.subheader("รายการงาน (เซต J)")
    st.caption(
        "เวลาเริ่มต้นเป็น benchmark จากงานวิจัยและสมมติฐานของโครงงาน; "
        "สามารถแก้ duration เพื่อใช้ข้อมูลจริงได้ภายหลัง"
    )
    tasks_df = st.data_editor(
        st.session_state["tasks_df"], num_rows="fixed", use_container_width=True, hide_index=True,
        disabled=["Task ID", "ประเภท", "ประเภทงานจริง", "Zone", "Work Unit / Zone จริง", "ชื่องาน"],
        column_config={
            "เลือก": st.column_config.CheckboxColumn("ใช้"),
            "d_j (นาที)": st.column_config.NumberColumn("เวลา (นาที)", min_value=1, max_value=120),
        },
        key="task_editor",
    )
    st.session_state["tasks_df"] = tasks_df
    tasks = df_to_tasks(tasks_df)

    cleaning_tasks = [t for t in tasks if t.kind != "DEI"]
    total_clean = sum(t.duration for t in cleaning_tasks)
    svc_work = service_workload_minutes(tasks)
    svc_lb = required_service_workers(tasks, int(T), scenario) if cfg["zone_based"] else 0
    policy_lb = minimum_total_workers_for_policy(tasks, int(T), scenario)

    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Tasks", len(tasks))
    a2.metric("Cleaning workload", f"{total_clean} นาที")
    a3.metric("LAV+GAL workload", f"{svc_work} นาที")
    a4.metric("Service LB", f"{svc_lb}" if cfg["zone_based"] else "Flexible")
    a5.metric("Workforce LB", f"{policy_lb}")

    workers_now, dedicated_now = build_scenario_workers(int(n_workers), scenario)
    preview_service = svc_lb
    skill_key = f"{signature}|{n_workers}|T={T}|svcLB={preview_service}|{','.join(t.id for t in tasks)}"
    if st.session_state.get("skill_key") != skill_key:
        st.session_state["skill_key"] = skill_key
        st.session_state["skill_df"] = skill_matrix_df(
            workers_now, tasks, cfg["zone_based"], dedicated_now, preview_service
        )

    st.subheader("ความสามารถของพนักงาน (aᵢⱼ)")
    if scenario == "S1":
        st.caption("S1 Flexible: แก้ capability ได้")
        skill_df = st.data_editor(
            st.session_state["skill_df"], use_container_width=True, hide_index=True,
            disabled=["พนักงาน"], key="skill_editor",
        )
        st.session_state["skill_df"] = skill_df
    else:
        st.caption(
            "ตารางนี้เป็น preview ที่ Service-team lower bound; ตอน Run ระบบจะลองขนาด Service team "
            "ตั้งแต่ Lower Bound ขึ้นไปและเลือกตารางที่ Cmax ต่ำที่สุด"
        )
        st.dataframe(st.session_state["skill_df"], use_container_width=True, hide_index=True)

    run_btn = st.button(
        "🚀 คำนวณตารางงาน" if calc_mode == "schedule" else "🚀 หาจำนวนพนักงานน้อยที่สุด",
        type="primary", use_container_width=True,
    )

    if run_btn:
        if not tasks:
            st.error("ไม่มีงานที่เลือก")
        elif calc_mode == "schedule":
            a_manual = df_to_capability(st.session_state["skill_df"], tasks) if scenario == "S1" else None
            data, res, ptable = solve_policy(tasks, int(n_workers), scenario, a_manual, enforce_T)
            st.session_state["data"] = data
            st.session_state["result"] = res
            st.session_state["partition_table"] = ptable
            st.session_state["minw_table"] = None
        else:
            start = minimum_total_workers_for_policy(tasks, int(T), scenario)

            def min_solve(m: int):
                ts = tasks_for_scenario(tasks, scenario)
                data, res, _ = solve_policy(ts, m, scenario, enforce=True)
                return data, res

            best_m, res, table = find_min_workers(
                min_solve, m_min=start, m_max=30, max_seconds=max_seconds,
            )
            st.session_state["result"] = res
            st.session_state["minw_table"] = table
            if best_m is not None:
                data, res2, ptable = solve_policy(tasks_for_scenario(tasks, scenario), best_m, scenario, enforce=True)
                st.session_state["data"] = data
                st.session_state["result"] = res2
                st.session_state["partition_table"] = ptable
            else:
                st.session_state["data"] = None
                st.session_state["partition_table"] = None

        res = st.session_state.get("result")
        if res and res.feasible:
            st.success(f"พบคำตอบ: Cmax = {res.cmax} นาที · Buffer = {res.buffer} นาที")
        elif res:
            st.error(res.message)
        else:
            st.error("ยังไม่พบคำตอบที่เป็นไปได้")


# ==================================================================
# TAB 02
# ==================================================================
with result_tab:
    result = st.session_state.get("result")
    data = st.session_state.get("data")
    minw = st.session_state.get("minw_table")
    ptable = st.session_state.get("partition_table")

    if minw is not None and len(minw):
        st.subheader("การค้นหาจำนวนพนักงาน")
        st.dataframe(minw, use_container_width=True, hide_index=True)
        feasible_rows = minw[minw["Feasible"]]
        if len(feasible_rows):
            proven = bool(feasible_rows.iloc[0].get("Minimum Proven", False))
            if proven:
                st.success("จำนวนพนักงานที่พบเป็น Minimum ที่พิสูจน์แล้ว: จำนวนที่ต่ำกว่าถูกพิสูจน์ว่า INFEASIBLE")
            else:
                st.warning("พบ workforce ที่ทำได้ แต่ Minimum ยังไม่พิสูจน์ เพราะมีขนาดที่ต่ำกว่าซึ่ง Solver ให้สถานะ UNKNOWN")

    if result is None or data is None:
        st.info("กดคำนวณในแท็บ 01 ก่อน")
    elif not result.feasible:
        st.error(result.message)
        st.caption(f"สถานะ: {status_th(result.status)}")
    else:
        cleaning_workers = [w for w in data.workers if w != "DEICE_TEAM"]
        cleaner_load = result.workload[result.workload.Worker.isin(cleaning_workers)]
        avg_util = float(cleaner_load["Utilization %"].mean()) if len(cleaner_load) else 0.0

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Cmax", f"{result.cmax} นาที")
        c2.metric("T", f"{data.T} นาที")
        c3.metric("Buffer", f"{result.buffer} นาที")
        c4.metric("Cleaning workers", len(cleaning_workers))
        c5.metric("Service workers", data.service_worker_count if data.service_worker_count is not None else "Flexible")
        c6.metric("Cleaner avg util.", f"{avg_util:.1f}%")
        st.caption(
            f"{status_th(result.status)} · Solver gap={result.gap_pct}% · "
            f"Solve time={result.solve_time:.2f}s · Secondary objective balances Cleaning workers only"
        )

        if data.scenario == "S3":
            st.info("S3 แยก DEICE_TEAM ออกจาก Cleaning workforce; ไม่ใช้ workload ของทีม De-icing ใน objective การ balance Cleaner")

        if ptable is not None and len(ptable) > 1:
            st.subheader("การเลือกขนาด Service team")
            st.dataframe(ptable, use_container_width=True, hide_index=True)
            st.caption("ระบบลองหลายขนาด Service team แล้วเลือก Cmax ต่ำสุด; ถ้า Cmax เท่ากันเลือก workload Cleaner ที่สมดุลกว่า")

        st.subheader("ตารางงาน")
        st.dataframe(schedule_display_df(result.schedule, data.aircraft), use_container_width=True, hide_index=True)
        st.subheader("ภาระงานรายทรัพยากร")
        st.dataframe(workload_display_df(result.workload), use_container_width=True, hide_index=True)

        summary = pd.DataFrame([{
            "Aircraft": data.aircraft,
            "Scenario": data.scenario,
            "Cleaning Workers": len(cleaning_workers),
            "Deicing Resource": int("DEICE_TEAM" in data.workers),
            "Service Workers": data.service_worker_count,
            "T": data.T,
            "Cmax": result.cmax,
            "Buffer": result.buffer,
            "Follow lag k": data.follow_lag,
            "Hygiene GAL before LAV": data.hygiene_galley_first,
            "Status": result.status,
            "Model Validation": "Mathematical verification only; no field validation",
        }])
        assumptions = pd.DataFrame(MODEL_ASSUMPTIONS, columns=["Item", "Assumption", "Status/Source type"])
        input_tasks = tasks_to_df(data.tasks, data.aircraft)
        sheets = {
            "Summary": summary,
            "Input Tasks": input_tasks,
            "Capability": capability_to_df(data),
            "Schedule": result.schedule,
            "Workload": result.workload,
            "Assumptions": assumptions,
        }
        if ptable is not None:
            sheets["Service Team Search"] = ptable
        if minw is not None:
            sheets["Workforce Search"] = minw
        st.download_button(
            "⬇️ ดาวน์โหลดผล Excel", to_excel_bytes(sheets),
            "optimization_result.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ==================================================================
# TAB 03
# ==================================================================
with gantt_tab:
    result = st.session_state.get("result")
    data = st.session_state.get("data")
    if result is None or data is None or not result.feasible:
        st.info("ยังไม่มีตารางงาน")
    else:
        st.plotly_chart(gantt_chart(result.schedule, data.workers, data.T, result.cmax), use_container_width=True)
        st.plotly_chart(workload_chart(result.workload), use_container_width=True)


# ==================================================================
# TAB 04
# ==================================================================
with compare_tab:
    st.subheader("Scenario Analysis")
    st.caption("S1/S2 เป็น Normal/Clear cleaning scenarios; S3 เป็น Winter Extension จึงไม่ควรตีความว่าเป็นคู่แข่งภายใต้สภาพเดียวกัน")
    include_winter = st.checkbox("รวม S3 Winter De-icing Extension ในกราฟเพื่อดูผลกระทบ", value=False)
    scenarios_to_compare = ["S1", "S2"] + (["S3"] if include_winter else [])
    m_range = st.slider("ช่วงจำนวนทรัพยากรรวม", 2, 12, (3, 7))

    if st.button("▶ เปรียบเทียบ", use_container_width=True):
        base = df_to_tasks(st.session_state["tasks_df"])
        rows, mins = [], []
        for s in scenarios_to_compare:
            ts = tasks_for_scenario(base, s)
            start_lb = minimum_total_workers_for_policy(ts, int(T), s)
            for m in range(max(m_range[0], start_lb), m_range[1] + 1):
                data_s, res_s, _ = solve_policy(ts, m, s, enforce=False)
                rows.append({
                    "Scenario": s,
                    "Workers": m,
                    "Cleaning Workers": len([w for w in data_s.workers if w != "DEICE_TEAM"]),
                    "Deicing Resource": int("DEICE_TEAM" in data_s.workers),
                    "Service Workers": data_s.service_worker_count,
                    "Cmax": res_s.cmax if res_s.feasible else None,
                    "Status": res_s.status,
                })

            def scenario_min_solve(m: int):
                d, r, _ = solve_policy(ts, m, s, enforce=True)
                return d, r

            mb, rb, search = find_min_workers(
                scenario_min_solve, start_lb, 30, max_seconds,
            )
            proven = False
            if mb is not None and len(search[search.Feasible]):
                proven = bool(search[search.Feasible].iloc[0]["Minimum Proven"])
            mins.append({
                "Scenario": s,
                "Smallest Feasible Found": mb,
                "Minimum Proven": proven,
                "Cmax": rb.cmax if rb else None,
            })
        st.session_state["compare"] = (pd.DataFrame(rows), pd.DataFrame(mins))

    comp = st.session_state.get("compare")
    if comp is not None:
        table, mins = comp
        if len(table):
            st.plotly_chart(compare_chart(table, int(T)), use_container_width=True)
            st.dataframe(table, use_container_width=True, hide_index=True)
        st.subheader("Workforce search")
        st.dataframe(mins, use_container_width=True, hide_index=True)
        if include_winter:
            st.warning("S3 มี De-icing resource เพิ่มและสมมติฐาน Winter Operation จึงควรตีความเป็นผลกระทบของกรณีพิเศษ ไม่ใช่การจัดอันดับกับ S1/S2")


# ==================================================================
# TAB 05
# ==================================================================
with model_tab:
    st.subheader("ตัวแบบคณิตศาสตร์")
    st.markdown(r"""
**Decision variable**

$$x_{ijt}=1$$ เมื่อพนักงาน/ทรัพยากร $i$ เริ่มงาน $j$ ที่เวลา $t$

**Objective แบบ 2 ระดับ**

Primary objective:
$$\min C_{max}$$

Secondary objective เมื่อ $C_{max}$ เท่ากัน:
$$\min L_{max}$$
โดย $L_{max}$ คือภาระงานสูงสุดของ **Cleaning workers** เท่านั้น; ไม่รวม DEICE_TEAM

ใน CP-SAT ใช้น้ำหนักที่ทำให้การลด $C_{max}$ 1 นาทีสำคัญกว่าความแตกต่างของ workload ทุกกรณี

**Main constraints**

1. ทุกงานถูกทำหนึ่งครั้ง
$$\sum_i\sum_t x_{ijt}=1$$

2. ความสามารถพนักงาน
$$x_{ijt}\le a_{ij}$$

3. พนักงานหนึ่งคนทำงานซ้อนไม่ได้

4. งานต่อเนื่องใน Cabin Zone ใช้ follow lag
$$S_k \ge S_j+k,\qquad E_k\ge E_j+k$$
ค่าเริ่มต้น $k=1$ นาที เป็นสมมติฐานแทนการทำงานไล่ตามกันภายใน Work Unit

5. งานข้ามพื้นที่ที่มีลำดับก่อนหลังใช้ Finish-to-Start
$$E_j\le S_k$$

6. Makespan / time limit
$$C_{max}\ge E_j,\qquad C_{max}\le T$$

7. Hygiene assumption: หากคนเดียวกันทำ Galley และ Lavatory
$$Worker(B)=Worker(A)\Rightarrow E_B\le S_A$$

8. S3 Winter Extension
$$E_j\le S_{DEI1}\quad \forall j\in J_{clean}$$
""")

    st.markdown(r"""
**Service-team sizing in S2/S3**

คำนวณ Lower Bound ก่อน:
$$m_{service}^{LB}=\left\lceil\frac{W_{service}}{T_{clean}}\right\rceil$$

แต่ **ไม่ถือว่า Lower Bound คือจำนวนจริง** ระบบจะลอง $m_{service}^{LB},m_{service}^{LB}+1,\ldots$ ภายใต้ workforce ที่มี และเลือก partition ที่ให้ $C_{max}$ ต่ำที่สุด
""")

    st.subheader("สมมติฐานและสถานะข้อมูล")
    st.dataframe(
        pd.DataFrame(MODEL_ASSUMPTIONS, columns=["รายการ", "ค่าที่ใช้", "สถานะ"]),
        use_container_width=True, hide_index=True,
    )
    st.warning(
        "ปัจจุบันถือว่าเป็น Mathematical Verification + Literature-calibrated Assumptions "
        "ยังไม่ใช่ External/Field Validation เพราะไม่มีข้อมูลการปฏิบัติงานจริง"
    )
