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
    "schedule": "จัดตารางงานจากจำนวนพนักงานที่กำหนด",
    "min_workers": "หาจำนวนพนักงานน้อยที่สุดที่เสร็จทันเวลาจอด",
}


def worker_name(w: str) -> str:
    if w == "DEICE_TEAM":
        return "พนักงาน De-icing"
    if w.startswith("M") and w[1:].isdigit():
        return f"พนักงาน {int(w[1:])} ({w})"
    return w


def zone_name(z: str, aircraft: str) -> str:
    for zid, name, seats in AIRCRAFT_LIBRARY[aircraft]["zones"]:
        if z == zid:
            return f"{name.replace('Zone', 'โซน')} ({seats} ที่นั่ง)"
    return {
        "LAV": "พื้นที่ห้องน้ำ", "GAL": "พื้นที่ครัว", "CREW": "ห้องนักบิน/ห้องพักลูกเรือ",
        "CHECK": "จุดตรวจซ้ำ", "DEICE": "ภายนอกอากาศยาน",
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
        "IdleMinutes": "เวลาว่าง", "Utilization %": "อัตราการใช้งาน (%)",
    })


SHORT_LABEL = {
    "C1": "ขยะ", "D": "เช็ด", "C2": "ดูดฝุ่น", "C3": "จัดเรียบร้อย", "OVH": "ช่องเก็บของ",
    "E": "พื้นผิว", "F": "ผ้าห่ม", "FD": "ห้องนักบิน", "CR": "ห้องลูกเรือ", "DEI": "De-icing",
}


def short_label(task_id: str, kind: str, zone: str) -> str:
    """ข้อความสั้นในแท่ง Gantt เช่น 'ขยะ Z1', 'ห้องน้ำ 2', 'ครัว 1', 'ตรวจซ้ำห้องน้ำ'"""
    if kind == "LAV":
        return f"ห้องน้ำ {task_id[1:]}"
    if kind == "GAL":
        return f"ครัว {task_id[1:]}"
    if kind == "RC":
        return "ตรวจซ้ำห้องน้ำ" if task_id == "RC1" else "ตรวจซ้ำครัว"
    base = SHORT_LABEL.get(kind, kind)
    return f"{base} {zone}" if zone.startswith("Z") else base


def gantt_chart(schedule: pd.DataFrame, workers: list[str], T: int, cmax: int,
                full_range: bool = True) -> go.Figure:
    fig = go.Figure()
    plot = schedule.copy()
    plot["WorkerDisplay"] = plot.Worker.map(worker_name)
    plot["Label"] = [short_label(t, k, z) for t, k, z in zip(plot.Task, plot.Kind, plot.Zone)]
    order = [worker_name(w) for w in workers][::-1]
    for kind in plot.Kind.unique():
        sub = plot[plot.Kind == kind]
        fig.add_trace(go.Bar(
            y=sub.WorkerDisplay, x=sub.Duration, base=sub.Start, orientation="h",
            name=TASK_KIND_LABEL.get(kind, kind), marker_color=KIND_COLORS.get(kind, "#64748B"),
            marker_line=dict(color="white", width=1),
            text=sub.Label, textposition="inside", insidetextanchor="middle",
            textfont=dict(size=12, color="white"),
            customdata=sub[["TaskName", "Start", "End"]],
            hovertemplate="<b>%{customdata[0]}</b><br>นาที %{customdata[1]} – %{customdata[2]}<extra></extra>",
        ))
    if cmax == T:
        fig.add_vline(x=T, line_dash="dash", line_color="#C2410C",
                      annotation_text=f"เสร็จทั้งหมด = เวลาจอด = {T} นาที", annotation_position="top left")
    else:
        fig.add_vline(x=cmax, line_dash="dash", line_color="#1F2937",
                      annotation_text=f"เสร็จทั้งหมด {cmax} นาที", annotation_position="top left")
        fig.add_vline(x=T, line_dash="dot", line_color="#C2410C",
                      annotation_text=f"เวลาจอด {T} นาที", annotation_position="top right")
    fig.update_layout(
        barmode="overlay", height=max(380, 64 * len(workers) + 150),
        xaxis=dict(title="เวลานับจากเริ่มทำความสะอาด (นาที)",
                   dtick=1 if (max(T, cmax) if full_range else cmax) <= 40 else 5,
                   range=[0, (max(T, cmax) if full_range else cmax) + 1],
                   showgrid=True, gridcolor="#EEF2F7"),
        yaxis=dict(title="", categoryorder="array", categoryarray=order),
        uniformtext=dict(minsize=9, mode="hide"),
        legend=dict(title="ประเภทงาน", orientation="h", y=-0.18, itemclick=False, itemdoubleclick=False),
        margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="white",
    )
    return fig


def workload_chart(workload: pd.DataFrame) -> go.Figure:
    d = workload.copy()
    d["WorkerDisplay"] = d.Worker.map(worker_name)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=d.WorkerDisplay, y=d.BusyMinutes, name="เวลาทำงาน"))
    fig.add_trace(go.Bar(x=d.WorkerDisplay, y=d.IdleMinutes, name="เวลาว่าง"))
    fig.update_layout(barmode="stack", height=350, yaxis_title="นาที", title="เวลาทำงานและเวลาว่างของพนักงาน")
    return fig


def compare_chart(table: pd.DataFrame, T: int) -> go.Figure:
    fig = go.Figure()
    for s in table.Scenario.unique():
        d = table[table.Scenario == s]
        fig.add_trace(go.Scatter(x=d.Workers, y=d.Cmax, mode="lines+markers+text", name=s,
                                 text=d.Cmax, textposition="top center"))
    fig.add_hline(y=T, line_dash="dot", line_color="#C2410C", annotation_text=f"เวลาจอด T = {T} นาที")
    fig.update_layout(xaxis_title="จำนวนพนักงานรวม (คน)", yaxis_title="เวลาเสร็จ Cmax (นาที)", height=420,
                      xaxis=dict(dtick=1), title="เวลาเสร็จตามจำนวนพนักงาน แยกตาม Scenario")
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
    f"ห้องน้ำ {spec['n_lav']} · ครัว {spec['n_gal']} · {spec['seats_abreast']} ที่นั่ง/แถว"
)

cleaning_type = st.sidebar.selectbox(
    "รูปแบบการทำความสะอาด", list(CLEANING_TYPES),
    index=list(CLEANING_TYPES).index(DEFAULT_CLEANING_TYPE),
)
st.sidebar.caption(
    "Quick Transit: เก็บขยะ เช็ดที่นั่ง ดูดฝุ่น จัดความเรียบร้อย ห้องน้ำ ครัว · "
    "Layover: เพิ่มช่องเก็บสัมภาระ พื้นผิว ผ้าห่ม/หมอน ห้องนักบิน ห้องพักลูกเรือ และตรวจซ้ำ"
)
active_kinds = cleaning_kinds(cleaning_type)

duration_factor = DEFAULT_DURATION_FACTOR

st.sidebar.subheader("2) พนักงานและเวลา")
scenario_hint = st.session_state.get("scenario_selector", "S1")
default_workers = {"S1": 4, "S2": 4, "S3": 5}.get(scenario_hint, 4)
n_workers = st.sidebar.number_input(
    "จำนวนพนักงานรวม (m)", 1, 30, default_workers, 1,
    key=f"workers_{scenario_hint}",
    help="S3 นับพนักงาน De-icing 1 คนรวมในจำนวนนี้",
)
T = st.sidebar.number_input(
    "เวลาจอด T (นาที)", 5, 300, 30, 5,
    help=(
        "เวลาที่ทำความสะอาดได้จริง ตั้งแต่ผู้โดยสารลงหมดจนถึงก่อนผู้โดยสารขึ้น · "
        "S3: ต้องรวมเวลา De-icing ด้วย"
    ),
)

st.sidebar.subheader("3) รูปแบบการจัดงานและข้อจำกัด")
scenario = st.sidebar.selectbox(
    "Scenario", list(SCENARIOS),
    format_func=lambda s: f"{s} — {SCENARIOS[s]}",
    key="scenario_selector",
)
calc_mode = st.sidebar.radio(
    "สิ่งที่ต้องการหา", list(MODE_TH), format_func=lambda k: MODE_TH[k]
)
follow_lag = st.sidebar.number_input(
    "ระยะไล่ตาม k (นาที)", 0, 5, DEFAULT_FOLLOW_LAG, 1,
    help="งานถัดไปในโซนเดียวกัน (เช่น ดูดฝุ่นตามหลังคนเช็ดที่นั่ง) เริ่มได้หลังงานก่อนเริ่มไปแล้ว k นาที และเสร็จหลังงานก่อนเสร็จ k นาที",
)
use_hygiene = st.sidebar.checkbox(
    "กฎสุขอนามัย: ครัวก่อนห้องน้ำ", True,
    help="ใช้เฉพาะกรณีที่พนักงานคนเดียวกันถูกมอบหมายทั้ง Galley และ Lavatory",
)
enforce_T = True if calc_mode == "min_workers" else st.sidebar.checkbox(
    "ต้องเสร็จภายในเวลาจอด", True, help="ปิดเมื่อต้องการดูว่าต้องใช้เวลาจริงเท่าไรแม้เกินเวลาจอด")
with st.sidebar.expander("ตั้งค่าขั้นสูง"):
    max_seconds = st.slider("เวลาคำนวณสูงสุดต่อครั้ง (วินาที)", 5, 120, 30, 5)

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
        objective_mode="Time Only",
        scenario=s,
        balance_workers=balance_workers,
        service_worker_count=service_count if c["zone_based"] else None,
    )


def solve_policy(tasks: list[Task], m_total: int, s: str,
                 a_override: dict | None = None,
                 enforce: bool = True, seconds: float | None = None):
    """Solve one scenario, including an outer search over Service-team size."""
    c = scenario_settings(s)
    if not c["zone_based"]:
        data = build_problem(tasks, m_total, s, service_count=0,
                             a_override=a_override, enforce=enforce)
        res = solve_model(data, max_seconds=(seconds or max_seconds))
        return data, res, pd.DataFrame([{
            "Service Workers": 0, "Cmax": res.cmax, "Status": res.status,
            "Feasible": res.feasible,
        }])

    options = service_worker_count_options(tasks, int(T), s, m_total)
    if not options:
        # Build the lower-bound allocation only so the solver returns a clear infeasible reason.
        lb = required_service_workers(tasks, int(T), s)
        data = build_problem(tasks, m_total, s, service_count=lb, enforce=enforce)
        res = solve_model(data, max_seconds=(seconds or max_seconds))
        return data, res, pd.DataFrame([{
            "Service Workers": lb, "Cmax": res.cmax, "Status": res.status,
            "Feasible": res.feasible,
        }])

    best_svc, data, res, table = solve_best_variant(
        lambda svc: build_problem(tasks, m_total, s, service_count=svc, enforce=enforce),
        options,
        max_seconds=(seconds or max_seconds),
    )
    table = table.rename(columns={"Variant": "Service Workers"})
    if data is None:
        # retain a representative problem for diagnostics
        data = build_problem(tasks, m_total, s, service_count=options[0], enforce=enforce)
        res = solve_model(data, max_seconds=(seconds or max_seconds))
    return data, res, table


preview_tasks = default_tasks(scenario)
policy_lb = minimum_total_workers_for_policy(preview_tasks, int(T), scenario)
if int(n_workers) < policy_lb:
    st.sidebar.warning(
        f"ตามภาระงาน ต้องใช้พนักงานอย่างน้อย {policy_lb} คน จำนวนที่กรอกอาจไม่พอ"
    )


# ==================================================================
# Header
# ==================================================================
st.title("✈️ Aircraft Cleaning Optimization")
st.caption("Industrial Engineering Research Dashboard · Time-indexed Binary Optimization · OR-Tools CP-SAT")

h1, h2, h3, h4 = st.columns(4)
h1.metric("อากาศยาน (Aircraft)", aircraft)
h2.metric("รูปแบบงาน (Cleaning)", cleaning_type)
h3.metric("พนักงาน (Workforce)", f"{n_workers} คน")
h4.metric("เวลาจอด (Turnaround)", f"{T} นาที")

if cfg["zone_based"]:
    svc_lb = required_service_workers(preview_tasks, int(T), scenario)
    svc_opts = service_worker_count_options(preview_tasks, int(T), scenario, int(n_workers))
    st.caption(
        f"{scenario}: งานห้องน้ำ + ครัวรวม {service_workload_minutes(preview_tasks)} นาที · "
        f"ระบบจะลองจำนวนพนักงานห้องน้ำ/ครัว {', '.join(map(str, svc_opts)) + ' คน' if svc_opts else '— (พนักงานไม่พอ)'} "
        "แล้วเลือกแบบที่เสร็จเร็วที่สุด"
    )
if cfg["include_deicing"]:
    st.info(
        f"S3 (กรณีฤดูหนาว): พนักงาน De-icing 1 คนรวมอยู่ในจำนวนพนักงาน {n_workers} คน และเริ่มหลังทำความสะอาดเสร็จทุกงาน · "
        f"เวลาทำความสะอาดที่เหลือ = {cleaning_time_window(preview_tasks, int(T), scenario)} นาที"
    )

setup, result_tab, gantt_tab, compare_tab, model_tab = st.tabs([
    "01 · Input & Model Setup",
    "02 · Optimization Results",
    "03 · Gantt & Workforce",
    "04 · Scenario Analysis",
    "05 · Mathematical Model",
])


# ==================================================================
# TAB 01
# ==================================================================
with setup:
    st.subheader("รายการงาน (เซต J)")
    st.caption("เวลางานคำนวณจากค่าในงานวิจัย · แก้ได้เฉพาะคอลัมน์ “ใช้” และ “เวลา (นาที)” เช่น เมื่อมีข้อมูลจริง")
    tasks_df = st.data_editor(
        st.session_state["tasks_df"], num_rows="fixed", width="stretch", hide_index=True,
        disabled=["Task ID", "ประเภท", "ประเภทงานจริง", "Zone", "Work Unit / Zone จริง", "ชื่องาน"],
        column_config={
            "เลือก": st.column_config.CheckboxColumn("ใช้", width="small"),
            "Task ID": st.column_config.TextColumn("รหัสงาน", width="small"),
            "ประเภท": None,
            "ประเภทงานจริง": st.column_config.TextColumn("ประเภทงาน"),
            "Zone": None,
            "Work Unit / Zone จริง": st.column_config.TextColumn("พื้นที่"),
            "ชื่องาน": None,
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

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("จำนวนงาน (Tasks)", len(tasks))
    a2.metric("เวลางานรวม (Workload)", f"{total_clean} นาที")
    a3.metric("งานห้องน้ำ + ครัว", f"{svc_work} นาที")
    a4.metric("พนักงานขั้นต่ำตามภาระงาน", f"{policy_lb} คน",
              help="คำนวณจากเวลางานรวม ÷ เวลาจอด ยังไม่คิดลำดับงาน จำนวนที่ต้องใช้จริงอาจมากกว่านี้")

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
        st.caption("✓ = พนักงานคนนั้นทำงานนั้นได้ · แก้ได้ใน S1")
        skill_df = st.data_editor(
            st.session_state["skill_df"], width="stretch", hide_index=True,
            disabled=["พนักงาน"], key="skill_editor",
        )
        st.session_state["skill_df"] = skill_df
    else:
        st.caption("ตัวอย่างการแบ่งหน้าที่ (สร้างอัตโนมัติ) · ตอนคำนวณ ระบบจะลองจำนวนพนักงานห้องน้ำ/ครัวหลายแบบแล้วเลือกแบบที่ดีที่สุด")
        st.dataframe(st.session_state["skill_df"], width="stretch", hide_index=True)

    run_btn = st.button(
        "🚀 คำนวณตารางงาน" if calc_mode == "schedule" else "🚀 หาจำนวนพนักงานน้อยที่สุด",
        type="primary", width="stretch",
    )

    if run_btn:
        if not tasks:
            st.error("ไม่มีงานที่เลือก")
        elif calc_mode == "schedule":
            a_manual = df_to_capability(st.session_state["skill_df"], tasks) if scenario == "S1" else None
            with st.spinner("กำลังคำนวณตารางงาน..."):
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

            with st.spinner("กำลังหาจำนวนพนักงานน้อยที่สุด..."):
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
            d_ = st.session_state.get("data")
            who = f"ใช้พนักงาน {len(d_.workers)} คน · " if d_ is not None else ""
            clean_end = int(res.schedule[res.schedule.Kind != "DEI"]["End"].max())
            if (res.schedule.Kind == "DEI").any():
                st.success(f"{who}ทำความสะอาดเสร็จที่นาทีที่ {clean_end} · De-icing เสร็จที่นาทีที่ {res.cmax} · "
                           f"เหลือเวลาเผื่อ {res.buffer} นาที · ดูผลในแท็บ 02 และ 03")
            else:
                st.success(f"{who}ทำความสะอาดเสร็จที่นาทีที่ {res.cmax} · เหลือเวลาเผื่อ {res.buffer} นาที · ดูผลในแท็บ 02 และ 03")
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
        st.subheader("ผลการหาจำนวนพนักงานน้อยที่สุด")
        view = minw.copy()
        view["ผล"] = view.apply(lambda r: f"เสร็จที่นาทีที่ {int(r['Cmax'])}" if r["Feasible"]
                                else ("ไม่ทันเวลาจอด" if r["Status"] == "INFEASIBLE" else "หมดเวลาคำนวณก่อนสรุปได้"), axis=1)
        st.dataframe(view[["Workers", "ผล"]].rename(columns={"Workers": "จำนวนพนักงาน"}), width="stretch", hide_index=True)
        feasible_rows = minw[minw["Feasible"]]
        if len(feasible_rows) and not bool(feasible_rows.iloc[0].get("Minimum Proven", False)):
            st.warning("จำนวนที่น้อยกว่านี้บางค่าคำนวณไม่ทันในเวลาที่กำหนด จึงยังยืนยันไม่ได้ว่าเป็นจำนวนน้อยที่สุด ลองเพิ่มเวลาคำนวณในตั้งค่าขั้นสูง")

    if result is None or data is None:
        st.info("กดคำนวณในแท็บ 01 ก่อน")
    elif not result.feasible:
        st.error(result.message)
    else:
        cleaning_workers = [w for w in data.workers if w != "DEICE_TEAM"]
        cleaner_load = result.workload[result.workload.Worker.isin(cleaning_workers)]
        avg_util = float(cleaner_load["Utilization %"].mean()) if len(cleaner_load) else 0.0

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("เวลาเสร็จ (Cmax)", f"{result.cmax} นาที")
        c2.metric("เวลาเผื่อ (Buffer)", f"{result.buffer} นาที")
        c6.metric("เวลางานรวม (Workload)", f"{int(result.schedule['Duration'].sum())} นาที")
        c3.metric("พนักงานทำความสะอาด", f"{len(cleaning_workers)} คน")
        c4.metric("พนักงานห้องน้ำ/ครัว", f"{data.service_worker_count} คน" if data.service_worker_count is not None else "ทุกคนช่วยกัน")
        c5.metric("อัตราการใช้งานเฉลี่ย", f"{avg_util:.1f}%")

        if result.status != "OPTIMAL":
            st.warning("ตารางนี้ใช้ได้ แต่คำนวณไม่ทันพิสูจน์ว่าเร็วที่สุด ลองเพิ่มเวลาคำนวณในตั้งค่าขั้นสูง")
        if result.cmax > data.T:
            st.warning("ใช้เวลาเกินเวลาจอด ควรเพิ่มพนักงานหรือเวลาจอด")

        if ptable is not None and len(ptable) > 1:
            with st.expander("การเลือกจำนวนพนักงานห้องน้ำ/ครัว"):
                pv = ptable[["Service Workers", "Cmax"]].rename(columns={"Service Workers": "พนักงานห้องน้ำ/ครัว (คน)", "Cmax": "เวลาเสร็จ (นาที)"})
                st.dataframe(pv, width="stretch", hide_index=True)
                st.caption("เลือกแบบที่เสร็จเร็วที่สุด ถ้าเท่ากันเลือกแบบที่ภาระงานสมดุลกว่า")

        st.subheader("ตารางงาน")
        st.dataframe(schedule_display_df(result.schedule, data.aircraft), width="stretch", hide_index=True)
        st.subheader("ภาระงานรายพนักงาน")
        st.dataframe(workload_display_df(result.workload), width="stretch", hide_index=True)

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
        }])
        assumptions = pd.DataFrame(MODEL_ASSUMPTIONS, columns=["Item", "Assumption", "Source"])
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
            "⬇️ ดาวน์โหลดผล (.xlsx)", to_excel_bytes(sheets),
            "optimization_result.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )


# ==================================================================
# TAB 03
# ==================================================================
with gantt_tab:
    result = st.session_state.get("result")
    data = st.session_state.get("data")
    if result is None or data is None or not result.feasible:
        st.info("ยังไม่มีตารางงาน กดคำนวณในแท็บ 01 ก่อน")
    else:
        st.caption("แต่ละแถว = พนักงาน 1 คน · แท่ง = งาน (สีตามประเภทงาน) · เส้นประ = เวลาที่ทุกงานเสร็จ · เส้นจุดสีส้ม = เวลาจอด · ช่องว่าง = เวลาว่าง")
        view = st.radio("ช่วงเวลาที่แสดง", ["แสดงถึงเวลาจอด", "แสดงเฉพาะช่วงที่มีงาน"],
                        horizontal=True, label_visibility="collapsed")
        st.plotly_chart(gantt_chart(result.schedule, data.workers, data.T, result.cmax,
                                    full_range=(view == "แสดงถึงเวลาจอด")), width="stretch")
        st.plotly_chart(workload_chart(result.workload), width="stretch")


# ==================================================================
# TAB 04
# ==================================================================
with compare_tab:
    st.subheader("เปรียบเทียบ Scenario")
    st.caption("ใช้รายการงานในแท็บ 01 ชุดเดียวกัน · กราฟแสดงเวลาที่ต้องใช้จริง (ไม่บังคับเวลาจอด) · "
               "ตารางล่างแสดงจำนวนพนักงานน้อยที่สุดที่เสร็จทันเวลาจอด T")
    include_winter = st.checkbox("รวม S3 (กรณีฤดูหนาว มี De-icing)", value=False)
    scenarios_to_compare = ["S1", "S2"] + (["S3"] if include_winter else [])
    m_range = st.slider("ช่วงจำนวนพนักงานรวม", 2, 12, (2, 6))
    per_solve = min(float(max_seconds), 10.0)

    if st.button("▶ เปรียบเทียบ", type="primary", width="stretch"):
        base = df_to_tasks(st.session_state["tasks_df"])
        jobs = []
        for s_ in scenarios_to_compare:
            lb = max(minimum_total_workers_for_policy(tasks_for_scenario(base, s_), int(T), s_), 1)
            jobs += [(s_, m) for m in range(max(m_range[0], 2 if s_ != "S1" else 1), m_range[1] + 1)]
        total_jobs = len(jobs) + len(scenarios_to_compare)
        bar = st.progress(0.0, text="กำลังคำนวณ...")
        rows, mins, done = [], [], 0
        for s_, m in jobs:
            ts = tasks_for_scenario(base, s_)
            if scenario_settings(s_)["include_deicing"] and m < 3:
                done += 1
                continue
            bar.progress(done / total_jobs, text=f"{s_} · พนักงาน {m} คน")
            data_s, res_s, _ = solve_policy(ts, m, s_, enforce=False, seconds=per_solve)
            rows.append({"Scenario": s_, "Workers": m, "Cmax": res_s.cmax if res_s.feasible else None})
            done += 1
        for s_ in scenarios_to_compare:
            ts = tasks_for_scenario(base, s_)
            bar.progress(done / total_jobs, text=f"{s_} · หาจำนวนพนักงานน้อยที่สุด")
            start_lb = minimum_total_workers_for_policy(ts, int(T), s_)

            def scenario_min_solve(m: int, ts=ts, s_=s_):
                d, r, _ = solve_policy(ts, m, s_, enforce=True, seconds=per_solve)
                return d, r

            mb, rb, _ = find_min_workers(scenario_min_solve, start_lb, 30, per_solve)
            mins.append({"Scenario": s_, "Min Workers": mb, "Cmax": rb.cmax if rb else None})
            done += 1
        bar.empty()
        st.session_state["compare"] = (pd.DataFrame(rows), pd.DataFrame(mins), int(T))

    comp = st.session_state.get("compare")
    if comp is None:
        st.info("เลือกช่วงจำนวนพนักงานแล้วกด “▶ เปรียบเทียบ” (ใช้เวลาประมาณ 10–60 วินาที)")
    else:
        table, mins, T_used = comp
        if len(table) and table["Cmax"].notna().any():
            st.plotly_chart(compare_chart(table.dropna(subset=["Cmax"]), T_used), width="stretch")
            pivot = table.pivot(index="Workers", columns="Scenario", values="Cmax")
            pivot.index.name = "พนักงาน (คน)"
            st.markdown("**เวลาเสร็จ (นาที) ตามจำนวนพนักงาน**")
            st.dataframe(pivot, width="stretch")
        st.markdown(f"**จำนวนพนักงานน้อยที่สุดที่เสร็จทันเวลาจอด T = {T_used} นาที**")
        mv = mins.copy()
        mv["Min Workers"] = mv["Min Workers"].map(lambda v: f"{int(v)} คน" if pd.notna(v) else "ไม่ทัน (เกิน 30 คน)")
        mv["Cmax"] = mv["Cmax"].map(lambda v: f"{int(v)} นาที" if pd.notna(v) else "–")
        mv["Scenario"] = mv["Scenario"].map(lambda k: f"{k} — {SCENARIOS[k]}")
        st.dataframe(mv.rename(columns={"Min Workers": "พนักงานน้อยที่สุด", "Cmax": "เวลาเสร็จ"}),
                     width="stretch", hide_index=True)
        dl = {"Cmax by workforce": table.rename(columns={"Workers": "Workers", "Cmax": "Cmax (min)"}),
              "Minimum workforce": mins.rename(columns={"Min Workers": "Min Workers", "Cmax": "Cmax (min)"})}
        st.download_button("⬇️ ดาวน์โหลดผลเปรียบเทียบ (.xlsx)", to_excel_bytes(dl),
                           file_name=f"scenario_comparison_{aircraft}_T{int(T_used)}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           width="stretch")
        if include_winter:
            st.caption("S3 มีพนักงาน De-icing และเวลา De-icing รวมอยู่ด้วย จึงใช้ดูผลกระทบของกรณีฤดูหนาว ไม่ได้จัดอันดับแข่งกับ S1/S2")


# ==================================================================
# TAB 05
# ==================================================================
with model_tab:
    st.subheader("ตัวแบบทางคณิตศาสตร์")
    st.caption("แบ่งเวลาเป็นช่องละ 1 นาที แล้วเลือกว่าพนักงานคนไหนเริ่มงานไหนที่นาทีใด "
               "ให้งานสุดท้ายเสร็จเร็วที่สุด โดยไม่ผิดข้อจำกัดข้อใด")
    st.markdown("**ตัวแปรตัดสินใจ**")
    st.latex(r"x_{ijt}=1\ \text{ถ้าพนักงาน } i \text{ เริ่มงาน } j \text{ ที่นาที } t,\ \text{ไม่เช่นนั้น } 0")
    st.latex(r"S_j=\sum_{i}\sum_{t} t\,x_{ijt},\qquad E_j=S_j+d_j")
    st.markdown("**ฟังก์ชันเป้าหมาย** — ทำให้เวลาที่งานทั้งหมดเสร็จน้อยที่สุด")
    st.latex(r"\min\ C_{\max}")
    st.caption("โหมดหาจำนวนพนักงานน้อยที่สุด: แก้ตัวแบบเดียวกันนี้ซ้ำ โดยเริ่มจากจำนวนขั้นต่ำตามภาระงาน "
               "แล้วเพิ่มพนักงานทีละ 1 คน จนพบจำนวนแรกที่ทำเสร็จภายในเวลาจอด · "
               "ตารางที่ให้ Cmax เท่ากันมีได้หลายชุด ระบบจึงเลื่อนทุกงานให้เริ่มเร็วที่สุดหลังได้คำตอบ")
    st.markdown("**ข้อจำกัด**")
    cons = [
        ("(1) ทุกงานทำ 1 ครั้ง", r"\sum_{i}\sum_{t}x_{ijt}=1\quad\forall j"),
        ("(2) ทำได้เฉพาะงานที่รับผิดชอบ (S2/S3 ใช้กำหนดการแบ่งหน้าที่)", r"x_{ijt}\le a_{ij}\quad\forall i,j,t"),
        ("(3) พนักงาน 1 คนทำได้ทีละงาน", r"\sum_{j}\sum_{t:\,t\le\tau<t+d_j}x_{ijt}\le 1\quad\forall i,\tau"),
        ("(4a) งานในโซนเดียวกันไล่ตามกันด้วยระยะห่าง k นาที", r"S_k\ge S_j+k,\qquad E_k\ge E_j+k\quad\forall (j,k)\in P_{zone}"),
        ("(4b) งานอื่นต้องรองานก่อนเสร็จ (ตรวจซ้ำ, De-icing ใน S3)", r"E_j\le S_k\quad\forall (j,k)\in P\setminus P_{zone}"),
        ("(5) Cmax คือเวลาที่งานสุดท้ายเสร็จ", r"C_{\max}\ge E_j\quad\forall j"),
        ("(6) ต้องเสร็จภายในเวลาจอด", r"C_{\max}\le T"),
        ("(7) คนเดียวกันทำทั้งครัว g และห้องน้ำ l ต้องทำครัวก่อน",
         r"x_{igt}+x_{ilt'}\le 1\quad\forall i,g,l,\ t'<t+d_g"),
    ]
    for title, eq in cons:
        st.markdown(title)
        st.latex(eq)
    st.markdown("**จำนวนพนักงานห้องน้ำ/ครัวใน S2 และ S3**")
    st.latex(r"m_{service}^{LB}=\left\lceil W_{service}\,/\,T_{clean}\right\rceil")
    st.caption("ใช้เป็นจุดเริ่มเท่านั้น ระบบลองจำนวน m_LB, m_LB+1, ... แล้วเลือกแบบที่เสร็จเร็วที่สุด · "
               "S3: T_clean = T − เวลา De-icing")
    st.subheader("สมมติฐานของตัวแบบ")
    st.dataframe(pd.DataFrame(MODEL_ASSUMPTIONS, columns=["รายการ", "ค่าที่ใช้", "ที่มา"]),
                 width="stretch", hide_index=True)
    st.caption("เวลางานปรับเทียบจากงานวิจัย เนื่องจากยังไม่มีข้อมูลภาคสนาม · "
               "ตรวจความถูกต้องของตัวแบบด้วยการคำนวณมือ Excel Solver และชุดทดสอบ V1–V7")
