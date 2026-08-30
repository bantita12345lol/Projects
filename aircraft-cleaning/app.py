"""
app.py
------------------------------------------------------------------
Aircraft Cleaning Optimization — IE Research Dashboard

Run locally
    python -m streamlit run app.py

Deploy on Streamlit Community Cloud
    Main file path: app.py

Notes
    - No external Excel input is required.
    - Optimization logic remains in solver.py / aircraft_data.py.
    - This file focuses on research-dashboard presentation and interaction.
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
    DEFAULT_WEATHER,
    SCENARIOS,
    SCOPE_MAPPING,
    TASK_KIND_LABEL,
    WEATHER_FACTOR,
    WEATHER_REASON,
    Task,
    build_blocking,
    build_capability,
    build_precedence,
    build_tasks,
    build_workers,
)
from solver import ProblemData, compare_scenarios, solve_model


# ==================================================================
# Page configuration / visual theme
# ==================================================================
st.set_page_config(
    page_title="Aircraft Cleaning Optimization",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .block-container {
            max-width: 1500px;
            padding-top: 1.4rem;
            padding-bottom: 2.5rem;
        }
        [data-testid="stSidebar"] {
            border-right: 1px solid #e5e7eb;
        }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 14px 16px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        [data-testid="stMetricLabel"] {
            font-weight: 600;
        }
        .research-header {
            padding: 20px 24px;
            border: 1px solid #dbe3ee;
            border-radius: 16px;
            background: linear-gradient(135deg, #f8fafc 0%, #eef4fb 100%);
            margin-bottom: 14px;
        }
        .research-header h1 {
            margin: 0;
            font-size: 1.9rem;
            line-height: 1.2;
            color: #0f172a;
        }
        .research-header p {
            margin: 8px 0 0 0;
            color: #475569;
            font-size: 0.98rem;
        }
        .section-note {
            padding: 10px 13px;
            background: #f8fafc;
            border-left: 4px solid #334155;
            border-radius: 6px;
            color: #475569;
            margin: 4px 0 14px 0;
        }
        div[data-testid="stDataFrame"] {
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            overflow: hidden;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Stable colors by task type. The same task type keeps the same color
# across workers, zones and experimental runs.
KIND_COLORS = {
    "C1": "#4E79A7",   # Trash Collection
    "C2": "#59A14F",   # Vacuum
    "C3": "#F28E2B",   # Cosmetic
    "D": "#E15759",    # Seat Area Wipe
    "E": "#76B7B2",    # Surface Cleaning
    "F": "#EDC948",    # Amenity Setup
    "LAV": "#B07AA1",  # Lavatory
    "GAL": "#FF9DA7",  # Galley
    "OVH": "#9C755F",  # Overhead Bin
    "FD": "#BAB0AC",   # Flight Deck
    "CR": "#2F4B7C",   # Crew Cabin
    "RC": "#7A5195",   # Recheck
    "DEI": "#00A6A6",  # Aircraft De-icing
}


# ==================================================================
# Display helpers
# ==================================================================
def worker_display_name(worker_id: str) -> str:
    """Convert internal worker IDs into readable dashboard labels."""
    worker_id = str(worker_id).strip()
    if worker_id == "DEICE1":
        return "DEICE1 — พนักงาน De-icing"
    if worker_id.startswith("M") and worker_id[1:].isdigit():
        return f"{worker_id} — พนักงานคนที่ {int(worker_id[1:])}"
    return worker_id


def kind_display_name(kind: str) -> str:
    """Convert C1/C2/LAV/... to the full task-type label."""
    kind = str(kind).strip()
    return TASK_KIND_LABEL.get(kind, kind)


def zone_display_name(zone: str, aircraft_name: str) -> str:
    """Convert Z1/Z2/... into the real aircraft work-unit name."""
    zone = str(zone).strip()
    spec = AIRCRAFT_LIBRARY.get(aircraft_name, {})

    for z_id, z_name, _seats in spec.get("zones", []):
        if z_id == zone:
            return f"{z_name} ({z_id})"

    service_zone_names = {
        "LAV": "Lavatory Area",
        "GAL": "Galley Area",
        "CREW": "Crew / Flight Deck Area",
        "CHECK": "Final Inspection Area",
        "DEICE": "Aircraft Exterior / De-icing Area",
    }
    return service_zone_names.get(zone, zone)


def task_display_name(row) -> str:
    """Prefer the real task name, falling back to Task ID."""
    name = str(row.get("TaskName", "")).strip()
    task_id = str(row.get("Task", "")).strip()
    return name if name else task_id


def task_lookup(tasks: list[Task]) -> dict[str, Task]:
    return {t.id: t for t in tasks}


def tasks_to_df(tasks: list[Task], aircraft_name: str) -> pd.DataFrame:
    """Editable task table plus readable research labels."""
    return pd.DataFrame([
        {
            "เลือก": True,
            "Task ID": t.id,
            "ประเภท": t.kind,
            "ประเภทงานจริง": kind_display_name(t.kind),
            "Zone": t.zone,
            "Work Unit / Zone จริง": zone_display_name(t.zone, aircraft_name),
            "ชื่องาน": t.name,
            "d_j (นาที)": t.duration,
        }
        for t in tasks
    ])


def df_to_tasks(df: pd.DataFrame) -> list[Task]:
    tasks: list[Task] = []
    for _, r in df.iterrows():
        if not bool(r.get("เลือก", True)):
            continue

        tid = str(r.get("Task ID", "")).strip()
        if not tid:
            continue

        try:
            dur = int(r.get("d_j (นาที)", 0))
        except (TypeError, ValueError):
            continue
        if dur <= 0:
            continue

        tasks.append(Task(
            id=tid,
            kind=str(r.get("ประเภท", "C1")).strip() or "C1",
            zone=str(r.get("Zone", "Z1")).strip() or "Z1",
            name=str(r.get("ชื่องาน", tid)).strip() or tid,
            duration=dur,
        ))
    return tasks


def refresh_task_labels(df: pd.DataFrame, aircraft_name: str) -> pd.DataFrame:
    """Refresh readable columns after a user edits task type or zone."""
    out = df.copy()
    if "ประเภท" in out.columns:
        out["ประเภทงานจริง"] = out["ประเภท"].map(kind_display_name)
    if "Zone" in out.columns:
        out["Work Unit / Zone จริง"] = out["Zone"].map(
            lambda z: zone_display_name(z, aircraft_name)
        )
    return out


def skill_matrix_df(
    workers: list[str],
    tasks: list[Task],
    zone_based: bool,
    dedicated_deicing_worker: str | None = None,
) -> pd.DataFrame:
    a = build_capability(
        workers,
        tasks,
        zone_based=zone_based,
        dedicated_deicing_worker=dedicated_deicing_worker,
    )
    data = {"พนักงาน": workers}
    for t in tasks:
        data[t.id] = [bool(a[(i, t.id)]) for i in workers]
    return pd.DataFrame(data)


def df_to_capability(df: pd.DataFrame, tasks: list[Task]) -> dict:
    a = {}
    for _, r in df.iterrows():
        worker = str(r["พนักงาน"])
        for t in tasks:
            a[(worker, t.id)] = 1 if bool(r.get(t.id, True)) else 0
    return a


def enforce_dedicated_deicing_worker(
    a: dict,
    workers: list[str],
    tasks: list[Task],
    dedicated_worker: str | None,
) -> dict:
    """
    บังคับกฎ S5 หลังอ่าน Skill Matrix จากหน้าเว็บ:
    DEICE1 ทำได้เฉพาะ DEI1/งานประเภท DEI และ Cleaner คนอื่นทำ DEI ไม่ได้
    เพื่อไม่ให้ผู้ใช้เผลอแก้ checkbox แล้วทำลายเงื่อนไขของ Scenario S5
    """
    if dedicated_worker is None:
        return a

    for i in workers:
        for t in tasks:
            if i == dedicated_worker:
                a[(i, t.id)] = 1 if t.kind == "DEI" else 0
            elif t.kind == "DEI":
                a[(i, t.id)] = 0
    return a


def schedule_display_df(schedule: pd.DataFrame, aircraft_name: str) -> pd.DataFrame:
    """Readable schedule table for the dashboard and Excel export."""
    df = schedule.copy()
    df["พนักงาน"] = df["Worker"].map(worker_display_name)
    df["ชื่องาน"] = df.apply(task_display_name, axis=1)
    df["ประเภทงาน"] = df["Kind"].map(kind_display_name)
    df["Work Unit / Zone"] = df["Zone"].map(
        lambda z: zone_display_name(z, aircraft_name)
    )
    df["Task ID"] = df["Task"]

    return df[[
        "พนักงาน",
        "ชื่องาน",
        "ประเภทงาน",
        "Work Unit / Zone",
        "Task ID",
        "Start",
        "End",
        "Duration",
    ]].rename(columns={
        "Start": "เริ่ม (นาที)",
        "End": "เสร็จ (นาที)",
        "Duration": "ใช้เวลา (นาที)",
    })


def workload_display_df(workload: pd.DataFrame) -> pd.DataFrame:
    df = workload.copy()
    if "Worker" in df.columns:
        df["Worker"] = df["Worker"].map(worker_display_name)
    return df.rename(columns={
        "Worker": "พนักงาน",
        "Tasks": "จำนวนงาน",
        "BusyMinutes": "เวลาทำงาน (นาที)",
        "IdleMinutes": "เวลาว่าง (นาที)",
        "Utilization %": "Utilization (%)",
    })


def gantt_chart(
    schedule: pd.DataFrame,
    workers: list[str],
    T: int,
    cmax: int,
    aircraft_name: str,
) -> go.Figure:
    """
    Research-style Gantt chart.

    - Y axis: worker name
    - Bar label: actual task name
    - Color: task type (Kind)
    - Hover: worker, task, zone, task ID and timing
    """
    plot_df = schedule.copy()
    plot_df["WorkerDisplay"] = plot_df["Worker"].map(worker_display_name)
    plot_df["TaskDisplay"] = plot_df.apply(task_display_name, axis=1)
    plot_df["KindDisplay"] = plot_df["Kind"].map(kind_display_name)
    plot_df["ZoneDisplay"] = plot_df["Zone"].map(
        lambda z: zone_display_name(z, aircraft_name)
    )

    # Sort by model worker order then start time for stable presentation.
    worker_rank = {w: idx for idx, w in enumerate(workers)}
    plot_df["_worker_rank"] = plot_df["Worker"].map(worker_rank)
    plot_df = plot_df.sort_values(["_worker_rank", "Start", "End"])

    fig = go.Figure()

    # One trace per task kind = one stable legend entry/color per task type.
    ordered_kinds = [k for k in TASK_KIND_LABEL if k in set(plot_df["Kind"])]
    extra_kinds = [k for k in plot_df["Kind"].unique() if k not in ordered_kinds]

    for kind in ordered_kinds + extra_kinds:
        sub = plot_df[plot_df["Kind"] == kind]
        if sub.empty:
            continue

        color = KIND_COLORS.get(kind, "#64748B")
        legend_label = kind_display_name(kind)

        fig.add_trace(go.Bar(
            y=sub["WorkerDisplay"],
            x=sub["Duration"],
            base=sub["Start"],
            orientation="h",
            name=legend_label,
            marker=dict(
                color=color,
                line=dict(color="rgba(15,23,42,0.28)", width=0.7),
            ),
            text=sub["TaskDisplay"],
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=11, color="white"),
            customdata=sub[[
                "Task",
                "TaskDisplay",
                "KindDisplay",
                "ZoneDisplay",
                "Start",
                "End",
                "Duration",
            ]],
            hovertemplate=(
                "<b>%{customdata[1]}</b>"
                "<br>พนักงาน: %{y}"
                "<br>ประเภทงาน: %{customdata[2]}"
                "<br>Work Unit / Zone: %{customdata[3]}"
                "<br>Task ID: %{customdata[0]}"
                "<br>เริ่ม: %{customdata[4]} นาที"
                "<br>เสร็จ: %{customdata[5]} นาที"
                "<br>ระยะเวลา: %{customdata[6]} นาที"
                "<extra></extra>"
            ),
        ))

    # Completion and turnaround reference lines.
    fig.add_vline(
        x=cmax,
        line_dash="dash",
        line_width=2,
        line_color="#111827",
        annotation_text=f"Cmax = {cmax} min",
        annotation_position="top",
    )
    fig.add_vline(
        x=T,
        line_dash="dot",
        line_width=2,
        line_color="#C2410C",
        annotation_text=f"Turnaround T = {T} min",
        annotation_position="top right",
    )

    worker_order = [worker_display_name(w) for w in workers]
    xmax = max(T, cmax, int(plot_df["End"].max()) if not plot_df.empty else 0)

    fig.update_layout(
        title={
            "text": "Optimized Aircraft Cleaning Schedule",
            "x": 0.01,
            "xanchor": "left",
            "font": {"size": 18},
        },
        barmode="overlay",
        bargap=0.28,
        height=max(440, 82 * len(workers) + 190),
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis_title="Time from aircraft arrival (minutes)",
        yaxis_title="Assigned worker",
        yaxis=dict(
            categoryorder="array",
            categoryarray=list(reversed(worker_order)),
            automargin=True,
            showgrid=False,
        ),
        xaxis=dict(
            range=[0, xmax + 2],
            dtick=5,
            showgrid=True,
            gridcolor="#E5E7EB",
            zeroline=False,
        ),
        legend=dict(
            title="Task type",
            orientation="h",
            yanchor="bottom",
            y=1.08,
            xanchor="left",
            x=0,
            bgcolor="rgba(255,255,255,0.85)",
        ),
        hoverlabel=dict(namelength=-1),
        margin=dict(l=20, r=20, t=115, b=30),
        uniformtext_minsize=9,
        uniformtext_mode="hide",
    )
    return fig


def workload_chart(workload: pd.DataFrame) -> go.Figure:
    df = workload.copy()
    df["WorkerDisplay"] = df["Worker"].map(worker_display_name)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["WorkerDisplay"],
        y=df["BusyMinutes"],
        name="Busy time",
        marker_color="#4E79A7",
        text=df["BusyMinutes"],
        textposition="outside",
        hovertemplate="%{x}<br>Busy time: %{y} min<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=df["WorkerDisplay"],
        y=df["IdleMinutes"],
        name="Idle time",
        marker_color="#D1D5DB",
        hovertemplate="%{x}<br>Idle time: %{y} min<extra></extra>",
    ))
    fig.update_layout(
        barmode="stack",
        height=360,
        title="Worker Utilization within Cmax",
        xaxis_title="Worker",
        yaxis_title="Minutes",
        plot_bgcolor="white",
        paper_bgcolor="white",
        yaxis=dict(showgrid=True, gridcolor="#E5E7EB", zeroline=False),
        xaxis=dict(showgrid=False),
        legend=dict(orientation="h", y=1.08, x=0),
        margin=dict(l=15, r=15, t=80, b=20),
    )
    return fig


def scenario_chart(table: pd.DataFrame, T: int) -> go.Figure:
    ok = table.dropna(subset=["Cmax"]).copy()
    fig = go.Figure(go.Bar(
        x=ok["Scenario"],
        y=ok["Cmax"],
        text=ok["Cmax"].astype(int),
        textposition="outside",
        marker_color="#4E79A7",
        customdata=ok[["Workers", "Tasks", "Buffer", "Status"]],
        hovertemplate=(
            "Scenario %{x}"
            "<br>Cmax: %{y} min"
            "<br>Workers: %{customdata[0]}"
            "<br>Tasks: %{customdata[1]}"
            "<br>Buffer: %{customdata[2]} min"
            "<br>Status: %{customdata[3]}<extra></extra>"
        ),
    ))
    fig.add_hline(
        y=int(T),
        line_dash="dot",
        line_color="#C2410C",
        annotation_text=f"Turnaround T = {T}",
    )
    fig.update_layout(
        title="Scenario Comparison by Makespan (Cmax)",
        yaxis_title="Cmax (minutes)",
        xaxis_title="Scenario",
        height=400,
        plot_bgcolor="white",
        paper_bgcolor="white",
        yaxis=dict(showgrid=True, gridcolor="#E5E7EB", zeroline=False),
        margin=dict(l=15, r=15, t=65, b=20),
    )
    return fig


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return buf.getvalue()


# ==================================================================
# Sidebar — experimental inputs
# ==================================================================
st.sidebar.title("⚙️ Experimental Settings")
st.sidebar.caption("กำหนดข้อมูลนำเข้าและเงื่อนไขของตัวแบบ")

st.sidebar.subheader("1) Aircraft & Environment")
aircraft_names = list(AIRCRAFT_LIBRARY.keys())
aircraft = st.sidebar.selectbox(
    "ประเภทอากาศยาน",
    aircraft_names,
    index=aircraft_names.index(DEFAULT_AIRCRAFT),
)
spec = AIRCRAFT_LIBRARY[aircraft]
st.sidebar.caption(
    f"{spec['category']} · {spec['seats']} seats · "
    f"{len(spec['zones'])} work units · LAV {spec['n_lav']} · GAL {spec['n_gal']}"
)

cleaning_options = list(CLEANING_TYPES.keys())
cleaning_type = st.sidebar.selectbox(
    "รูปแบบการทำความสะอาด",
    cleaning_options,
    index=cleaning_options.index(DEFAULT_CLEANING_TYPE),
)

weather_options = list(WEATHER_FACTOR.keys())
weather = st.sidebar.selectbox(
    "สภาพอากาศ",
    weather_options,
    index=weather_options.index(DEFAULT_WEATHER),
)
st.sidebar.caption(
    f"Weather factor γ = {WEATHER_FACTOR[weather]:.2f} · {WEATHER_REASON[weather]}"
)

st.sidebar.subheader("2) Workforce & Time")
n_workers = st.sidebar.number_input("จำนวนพนักงาน (m)", 1, 30, 4, 1)
T = st.sidebar.number_input("Turnaround Time T (นาที)", 5, 300, 30, 5)

st.sidebar.subheader("3) Optimization Policy")
scenario = st.sidebar.selectbox(
    "Scenario",
    list(SCENARIOS.keys()),
    format_func=lambda s: f"{s} — {SCENARIOS[s]}",
)
objective_mode = st.sidebar.selectbox(
    "Objective",
    ["Time Only", "Time + Workload", "Workload Only"],
    index=1,
)
use_blocking = st.sidebar.checkbox(
    "ใช้ข้อจำกัด Aisle Blocking (เซต B)",
    value=True,
)
enforce_T = st.sidebar.checkbox(
    "บังคับ Cmax ≤ T",
    value=True,
    help="ปิดเมื่อต้องการวัดเวลาที่ต้องใช้จริง แม้เกินเวลาจอด",
)
max_seconds = st.sidebar.slider(
    "Solver time limit (วินาที)",
    5,
    120,
    30,
    5,
)

zone_based = scenario in ("S2", "S4", "S5")
trash_first = scenario in ("S3", "S4", "S5")
include_deicing = scenario == "S5"


# ==================================================================
# Initialize / reset tasks when major inputs change
# ==================================================================
signature = f"{aircraft}|{cleaning_type}|{weather}|deicing={include_deicing}"
if st.session_state.get("signature") != signature:
    st.session_state["signature"] = signature
    st.session_state["tasks_df"] = tasks_to_df(
        build_tasks(
            aircraft,
            CLEANING_TYPES[cleaning_type],
            weather,
            include_deicing=include_deicing,
        ),
        aircraft,
    )
    st.session_state.pop("skill_df", None)
    st.session_state.pop("skill_key", None)
    st.session_state.pop("result", None)
    st.session_state.pop("compare", None)

st.sidebar.divider()
if st.sidebar.button(
    "↺ Reset tasks from selected aircraft",
    use_container_width=True,
):
    st.session_state["tasks_df"] = tasks_to_df(
        build_tasks(
            aircraft,
            CLEANING_TYPES[cleaning_type],
            weather,
            include_deicing=include_deicing,
        ),
        aircraft,
    )
    st.session_state.pop("skill_df", None)
    st.session_state.pop("skill_key", None)
    st.session_state.pop("result", None)
    st.session_state.pop("compare", None)
    st.rerun()


# ==================================================================
# Main research dashboard header
# ==================================================================
st.markdown(
    """
    <div class="research-header">
        <h1>✈️ Aircraft Cleaning Optimization</h1>
        <p>
            Industrial Engineering Research Dashboard · Time-indexed Binary Optimization · OR-Tools CP-SAT
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

h1, h2, h3, h4 = st.columns([1.1, 1.2, 1.1, 1.1])
h1.metric("Aircraft", aircraft)
h2.metric("Cleaning policy", cleaning_type.replace(" - ", "\n"))
if include_deicing:
    h3.metric("Workforce", f"{int(n_workers) + 1} total")
else:
    h3.metric("Workforce", f"{int(n_workers)} workers")
h4.metric("Turnaround target", f"{int(T)} min")

st.caption(
    f"Experimental condition: {SCENARIOS[scenario]} · Objective = {objective_mode} · "
    f"Weather = {weather} (γ={WEATHER_FACTOR[weather]:.2f})"
)

if include_deicing:
    st.info(
        f"S5 active: ใช้พนักงาน Cleaning {int(n_workers)} คน + เพิ่ม DEICE1 อีก 1 คน "
        "สำหรับ Aircraft De-icing Spray (DEI1) โดยเฉพาะ · DEI1 เริ่มที่นาที 0 · "
        "พนักงาน DEICE1 ทำงานได้เพียง DEI1 งานเดียว และ Cleaner คนอื่นไม่สามารถทำ DEI1 ได้"
    )


tab_setup, tab_result, tab_gantt, tab_compare, tab_model = st.tabs(
    [
        "01 · Input & Model Setup",
        "02 · Optimization Results",
        "03 · Gantt & Workforce",
        "04 · Scenario Analysis",
        "05 · Mathematical Model",
    ]
)


# ==================================================================
# TAB 1 — Setup
# ==================================================================
with tab_setup:
    st.subheader("Input Data — Task Set J")
    st.markdown(
        '<div class="section-note">'
        'ตารางนี้เก็บ Task ID/Zone code สำหรับตัวแบบ แต่เพิ่มชื่อประเภทงานและชื่อ Work Unit จริงเพื่อให้อ่านผลเชิงวิจัยได้ง่ายขึ้น'
        '</div>',
        unsafe_allow_html=True,
    )

    tasks_df = st.data_editor(
        st.session_state["tasks_df"],
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=["ประเภทงานจริง", "Work Unit / Zone จริง"],
        column_config={
            "เลือก": st.column_config.CheckboxColumn("ใช้", width="small"),
            "Task ID": st.column_config.TextColumn(width="small"),
            "ประเภท": st.column_config.SelectboxColumn(
                "Kind code",
                options=list(TASK_KIND_LABEL.keys()),
                width="small",
            ),
            "ประเภทงานจริง": st.column_config.TextColumn("Task type"),
            "Zone": st.column_config.TextColumn("Zone code", width="small"),
            "Work Unit / Zone จริง": st.column_config.TextColumn("Work Unit / Zone"),
            "ชื่องาน": st.column_config.TextColumn("Task name", width="large"),
            "d_j (นาที)": st.column_config.NumberColumn(
                "Duration d_j (min)", min_value=1, max_value=120, step=1
            ),
        },
        key="task_editor",
    )
    tasks_df = refresh_task_labels(tasks_df, aircraft)
    st.session_state["tasks_df"] = tasks_df
    tasks = df_to_tasks(tasks_df)

    total_work = sum(t.duration for t in tasks)
    total_worker_count = int(n_workers) + (1 if include_deicing else 0)
    lower_bound = -(-total_work // max(1, total_worker_count)) if tasks else 0

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Active tasks", len(tasks))
    a2.metric("Total workload", f"{total_work} min")
    a3.metric("Simple lower bound", f"{lower_bound} min")
    a4.metric("Weather factor γ", f"{WEATHER_FACTOR[weather]:.2f}")

    st.divider()
    st.subheader("Worker Capability Matrix — aᵢⱼ")
    st.caption(
        "✓ = พนักงานสามารถทำงานนั้นได้ · Scenario S2/S4/S5 จะสร้างข้อจำกัดแบบ Zone-based อัตโนมัติ"
    )

    dedicated_deicing_worker = "DEICE1" if include_deicing else None
    workers = build_workers(
        int(n_workers),
        add_deicing_worker=include_deicing,
    )
    skill_key = (
        f"{signature}|{n_workers}|{zone_based}|dedicated={dedicated_deicing_worker}|"
        f"{len(tasks)}|{','.join(t.id for t in tasks)}"
    )
    if st.session_state.get("skill_key") != skill_key:
        st.session_state["skill_key"] = skill_key
        st.session_state["skill_df"] = skill_matrix_df(
            workers,
            tasks,
            zone_based,
            dedicated_deicing_worker=dedicated_deicing_worker,
        )

    skill_df = st.data_editor(
        st.session_state["skill_df"],
        use_container_width=True,
        hide_index=True,
        disabled=["พนักงาน"],
        key="skill_editor",
    )
    st.session_state["skill_df"] = skill_df

    st.divider()
    run = st.button(
        "🚀 Run Optimization",
        type="primary",
        use_container_width=True,
    )

    if run:
        if not tasks:
            st.error("ยังไม่มีงานในรายการ")
        else:
            a = df_to_capability(skill_df, tasks)
            a = enforce_dedicated_deicing_worker(
                a,
                workers,
                tasks,
                dedicated_deicing_worker,
            )
            data = ProblemData(
                aircraft=aircraft,
                workers=workers,
                tasks=tasks,
                T=int(T),
                a=a,
                P=build_precedence(
                    tasks,
                    trash_first_global=trash_first,
                    deicing_last_global=False,
                ),
                B=build_blocking(tasks) if use_blocking else [],
                enforce_time_limit=enforce_T,
                objective_mode=objective_mode,
                scenario=scenario,
            )

            with st.spinner("Solving optimization model with OR-Tools CP-SAT..."):
                result = solve_model(data, max_seconds=max_seconds)

            st.session_state["result"] = result
            st.session_state["data"] = data
            st.session_state["weather_used"] = weather
            st.session_state["cleaning_used"] = cleaning_type

            if result.feasible:
                st.success(
                    f"Feasible solution found — Cmax = {result.cmax} min · "
                    f"Buffer = {result.buffer} min"
                )
            else:
                st.error(result.message)


# ==================================================================
# TAB 2 — Optimization result
# ==================================================================
with tab_result:
    result = st.session_state.get("result")
    data = st.session_state.get("data")

    if result is None:
        st.info("Run Optimization ในแท็บ 01 ก่อนเพื่อสร้างผลลัพธ์")
    elif not result.feasible:
        st.error(result.message)
        st.caption(f"Solver status: {result.status}")
    else:
        total_work = int(result.schedule["Duration"].sum())
        avg_util = float(result.workload["Utilization %"].mean()) if not result.workload.empty else 0.0
        max_util = float(result.workload["Utilization %"].max()) if not result.workload.empty else 0.0
        time_ratio = (result.cmax / data.T * 100) if data.T else 0.0

        st.subheader("Optimization Performance Summary")
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Cmax", f"{result.cmax} min")
        k2.metric("Turnaround T", f"{data.T} min")
        k3.metric("Buffer", f"{result.buffer} min")
        k4.metric("Total work", f"{total_work} min")
        k5.metric("Avg. utilization", f"{avg_util:.1f}%")
        k6.metric("Cmax / T", f"{time_ratio:.1f}%")

        st.caption(
            f"Aircraft {data.aircraft} · Scenario {data.scenario} ({SCENARIOS[data.scenario]}) · "
            f"Objective {data.objective_mode} · Solver status {result.status} · "
            f"Solve time {result.solve_time:.2f} s · Max worker utilization {max_util:.1f}%"
        )

        if result.buffer is not None and result.buffer < 0:
            st.warning(
                "ผลลัพธ์ใช้เวลาเกิน Turnaround Time ที่กำหนด — ควรเพิ่ม workforce, "
                "เพิ่ม T หรือปรับข้อจำกัดของ Scenario"
            )
        elif result.buffer == 0:
            st.warning("ตารางใช้ Turnaround Time เต็มพอดี ไม่มีเวลา Buffer สำหรับความล่าช้า")
        else:
            st.success(f"ตารางอยู่ภายในเป้าหมาย และมี operational buffer {result.buffer} นาที")

        st.divider()
        st.subheader("Detailed Optimized Schedule")
        display_schedule = schedule_display_df(result.schedule, data.aircraft)
        st.dataframe(
            display_schedule,
            use_container_width=True,
            hide_index=True,
            column_config={
                "พนักงาน": st.column_config.TextColumn("Assigned worker", width="medium"),
                "ชื่องาน": st.column_config.TextColumn("Task name", width="large"),
                "ประเภทงาน": st.column_config.TextColumn("Task type", width="medium"),
                "Work Unit / Zone": st.column_config.TextColumn("Work Unit / Zone", width="medium"),
                "Task ID": st.column_config.TextColumn(width="small"),
                "เริ่ม (นาที)": st.column_config.NumberColumn(format="%d"),
                "เสร็จ (นาที)": st.column_config.NumberColumn(format="%d"),
                "ใช้เวลา (นาที)": st.column_config.NumberColumn(format="%d"),
            },
        )

        st.subheader("Worker Workload Summary")
        display_workload = workload_display_df(result.workload)
        st.dataframe(
            display_workload,
            use_container_width=True,
            hide_index=True,
        )

        summary = pd.DataFrame([{
            "Aircraft": data.aircraft,
            "Cleaning Type": st.session_state.get("cleaning_used", cleaning_type),
            "Weather": st.session_state.get("weather_used", weather),
            "Scenario": data.scenario,
            "Objective": data.objective_mode,
            "Workers": len(data.workers),
            "Tasks": len(data.tasks),
            "T": data.T,
            "Cmax": result.cmax,
            "Buffer": result.buffer,
            "Total Work": total_work,
            "Average Utilization (%)": round(avg_util, 1),
            "Status": result.status,
            "Solve Time (s)": round(result.solve_time, 3),
        }])

        st.download_button(
            "⬇️ Download research results (.xlsx)",
            data=to_excel_bytes({
                "Summary": summary,
                "Schedule": display_schedule,
                "Schedule_Raw": result.schedule,
                "Workload": display_workload,
            }),
            file_name=f"result_{data.aircraft}_{data.scenario}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


# ==================================================================
# TAB 3 — Gantt + workforce
# ==================================================================
with tab_gantt:
    result = st.session_state.get("result")
    data = st.session_state.get("data")

    if result is None or not result.feasible:
        st.info("ยังไม่มี feasible solution สำหรับสร้าง Gantt Chart")
    else:
        st.subheader("Optimized Gantt Schedule")
        st.markdown(
            '<div class="section-note">'
            '<b>วิธีอ่าน:</b> แกน Y = พนักงานแต่ละคน · ข้อความในแท่ง = ชื่องานจริง · '
            'สี = ประเภทงาน · เส้นประ Cmax = เวลาที่งานทั้งหมดเสร็จ · เส้นจุด T = เวลาจอดที่กำหนด'
            '</div>',
            unsafe_allow_html=True,
        )

        st.plotly_chart(
            gantt_chart(
                result.schedule,
                data.workers,
                data.T,
                result.cmax,
                data.aircraft,
            ),
            use_container_width=True,
            config={"displaylogo": False, "scrollZoom": True},
        )

        g1, g2 = st.columns([1.6, 1.0])
        with g1:
            st.plotly_chart(
                workload_chart(result.workload),
                use_container_width=True,
                config={"displaylogo": False},
            )
        with g2:
            st.markdown("#### Research interpretation")
            total_work = int(result.schedule["Duration"].sum())
            theoretical_lb = -(-total_work // max(1, len(data.workers)))
            gap = result.cmax - theoretical_lb
            avg_util = float(result.workload["Utilization %"].mean())

            st.metric("Workload lower bound", f"{theoretical_lb} min")
            st.metric("Cmax gap above lower bound", f"{gap} min")
            st.metric("Average worker utilization", f"{avg_util:.1f}%")
            st.caption(
                "Lower bound นี้คำนวณจาก total workload ÷ workforce เท่านั้น "
                "จึงยังไม่รวมผลของ precedence, skill restriction และ aisle blocking"
            )


# ==================================================================
# TAB 4 — Scenario comparison
# ==================================================================
with tab_compare:
    st.subheader("Scenario Analysis")
    st.caption(
        "เปรียบเทียบผลภายใต้ aircraft, task set, workforce, weather และ turnaround time ชุดเดียวกัน "
        "โดยเปลี่ยนนโยบาย capability / precedence และใน S5 จะเพิ่มงาน Aircraft De-icing เข้ามาใน Task Set"
    )

    picked = st.multiselect(
        "เลือก Scenario",
        list(SCENARIOS.keys()),
        default=list(SCENARIOS.keys()),
        format_func=lambda s: f"{s} — {SCENARIOS[s]}",
    )

    if st.button("▶ Run Scenario Comparison", use_container_width=True):
        base_tasks = df_to_tasks(st.session_state["tasks_df"])

        def tasks_for_scenario(s: str) -> list[Task]:
            """
            ใช้ Task ที่ผู้ใช้แก้ไขไว้เป็นฐาน
            - S1-S4: ไม่รวมงาน De-icing
            - S5: เพิ่ม DEI1 ถ้ายังไม่มี
            """
            normal_tasks = [t for t in base_tasks if t.kind != "DEI"]
            if s != "S5":
                return normal_tasks

            existing_deicing = [t for t in base_tasks if t.kind == "DEI"]
            if existing_deicing:
                return normal_tasks + existing_deicing

            default_s5_tasks = build_tasks(
                aircraft,
                CLEANING_TYPES[cleaning_type],
                weather,
                include_deicing=True,
            )
            deicing_tasks = [t for t in default_s5_tasks if t.kind == "DEI"]
            return normal_tasks + deicing_tasks

        def build_fn(s: str) -> ProblemData:
            scenario_tasks = tasks_for_scenario(s)
            zb = s in ("S2", "S4", "S5")
            tf = s in ("S3", "S4", "S5")
            deice_last = False

            # S5 เพิ่มพนักงาน De-icing อีก 1 คน นอกเหนือจาก Cleaning workforce
            scenario_workers = build_workers(
                int(n_workers),
                add_deicing_worker=(s == "S5"),
            )
            dedicated_worker = "DEICE1" if s == "S5" else None

            return ProblemData(
                aircraft=aircraft,
                workers=scenario_workers,
                tasks=scenario_tasks,
                T=int(T),
                a=build_capability(
                    scenario_workers,
                    scenario_tasks,
                    zone_based=zb,
                    dedicated_deicing_worker=dedicated_worker,
                ),
                P=build_precedence(
                    scenario_tasks,
                    trash_first_global=tf,
                    deicing_last_global=deice_last,
                ),
                B=build_blocking(scenario_tasks) if use_blocking else [],
                enforce_time_limit=enforce_T,
                objective_mode=objective_mode,
                scenario=s,
            )

        with st.spinner("Running selected scenarios..."):
            table = compare_scenarios(
                build_fn,
                picked,
                max_seconds=max_seconds,
            )
        st.session_state["compare"] = table

    table = st.session_state.get("compare")
    if table is not None:
        st.dataframe(table, use_container_width=True, hide_index=True)
        ok = table.dropna(subset=["Cmax"])
        if not ok.empty:
            st.plotly_chart(
                scenario_chart(table, int(T)),
                use_container_width=True,
                config={"displaylogo": False},
            )

            best_idx = ok["Cmax"].astype(float).idxmin()
            best = ok.loc[best_idx]
            st.success(
                f"Best Cmax among feasible scenarios: {best['Scenario']} = {int(best['Cmax'])} min"
            )


# ==================================================================
# TAB 5 — Mathematical model
# ==================================================================
with tab_model:
    st.subheader("Mathematical Formulation")
    st.markdown(r"""
**Sets**

$I$ = worker set  ·  $J$ = task set  ·  $R$ = work-unit set  ·  $H_j=\{0,1,\dots,T-d_j\}$

**Decision variable**

$$x_{ijt}=\begin{cases}1 & \text{if worker } i \text{ starts task } j \text{ at time } t\\ 0 & \text{otherwise}\end{cases}
\qquad C_{\max}\in\mathbb{Z}_{\ge 0}$$

**Derived expressions**

$$S_j=\sum_{i\in I}\sum_{t\in H_j} t\,x_{ijt}
\qquad
E_j=\sum_{i\in I}\sum_{t\in H_j} (t+d_j)\,x_{ijt}$$

**Objective**

$$\min\; C_{\max}$$

**Constraints**

1. Each task is assigned exactly once  
   $\displaystyle\sum_{i\in I}\sum_{t\in H_j}x_{ijt}=1\quad\forall j\in J$

2. Worker capability  
   $x_{ijt}\le a_{ij}\quad\forall i,j,t$

3. A worker cannot perform overlapping tasks  
   $\displaystyle\sum_{j\in J}\sum_{t\in H_j:\,t\le\tau<t+d_j}x_{ijt}\le 1$

4. Precedence relationship  
   $\displaystyle E_j\le S_k\quad\forall (j,k)\in P$

5. Makespan linkage  
   $\displaystyle C_{\max}\ge E_j\quad\forall j$

6. Turnaround-time limit  
   $C_{\max}\le T$

7. Aisle blocking  
   Tasks in blocking pair set $B$ cannot be active simultaneously.

8. Variable domains  
   $x_{ijt}\in\{0,1\}$, $C_{\max}\in\mathbb{Z}_{\ge0}$
""")

    st.divider()
    st.markdown("#### Research Scope Mapping")
    st.dataframe(
        pd.DataFrame(
            SCOPE_MAPPING,
            columns=["ข้อในขอบเขต", "งานย่อย", "รหัสในตัวแบบ"],
        ),
        use_container_width=True,
        hide_index=True,
        height=330,
    )
    st.caption(
        "Weather factor γ modifies task duration. Airport/operational policies are represented by "
        "capability parameter aᵢⱼ, precedence set P, blocking set B and Scenario selection. "
        "Scenario S5 additionally introduces DEI1 (Aircraft De-icing Spray), fixes DEI1 to start at minute 0, and adds one dedicated De-icing worker (DEICE1)."
    )

    st.divider()
    tasks_now = df_to_tasks(st.session_state["tasks_df"])
    lookup = task_lookup(tasks_now)

    cA, cB = st.columns(2)
    with cA:
        st.markdown("#### Precedence Set P")
        P = build_precedence(
            tasks_now,
            trash_first_global=trash_first,
            deicing_last_global=False,
        )
        p_display = pd.DataFrame([
            {
                "งานก่อน": lookup[j].name if j in lookup else j,
                "งานหลัง": lookup[k].name if k in lookup else k,
                "Task ID ก่อน": j,
                "Task ID หลัง": k,
            }
            for j, k in P
        ])
        st.dataframe(
            p_display,
            use_container_width=True,
            hide_index=True,
            height=300,
        )

    with cB:
        st.markdown("#### Aisle Blocking Set B")
        B = build_blocking(tasks_now) if use_blocking else []
        if B:
            b_display = pd.DataFrame([
                {
                    "งาน j": lookup[j].name if j in lookup else j,
                    "งาน k": lookup[k].name if k in lookup else k,
                    "Task ID j": j,
                    "Task ID k": k,
                }
                for j, k in B
            ])
            st.dataframe(
                b_display,
                use_container_width=True,
                hide_index=True,
                height=300,
            )
        else:
            st.info("Aisle Blocking constraint is disabled for this run.")

        st.caption(
            "Blocking pairs are defined across adjacent work units; within-zone order is already controlled by set P."
        )
