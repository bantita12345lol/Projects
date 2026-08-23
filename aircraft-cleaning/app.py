"""
app.py
------------------------------------------------------------------
Aircraft Cleaning Optimization — Streamlit Web Application

รันในเครื่อง
    python -m streamlit run app.py

Deploy บน Streamlit Cloud
    ชี้ Main file path ไปที่  app.py

หมายเหตุสำคัญ
    App นี้ไม่พึ่งไฟล์ Excel ภายนอกเลย ข้อมูลทั้งหมดถูกสร้างจากหน้าเว็บ
    จึงทำงานได้เหมือนกันทั้ง Local และ Streamlit Cloud
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

st.set_page_config(
    page_title="Aircraft Cleaning Optimization",
    page_icon="✈️",
    layout="wide",
)

ZONE_COLORS = [
    "#2563eb", "#16a34a", "#ea580c", "#9333ea",
    "#0891b2", "#dc2626", "#ca8a04", "#4f46e5",
]


# ==================================================================
# Helper
# ==================================================================
def tasks_to_df(tasks: list[Task]) -> pd.DataFrame:
    return pd.DataFrame([
        {"เลือก": True, "Task ID": t.id, "ประเภท": t.kind, "Zone": t.zone,
         "ชื่องาน": t.name, "d_j (นาที)": t.duration}
        for t in tasks
    ])


def df_to_tasks(df: pd.DataFrame) -> list[Task]:
    tasks = []
    for _, r in df.iterrows():
        if not bool(r.get("เลือก", True)):
            continue
        tid = str(r["Task ID"]).strip()
        if not tid:
            continue
        try:
            dur = int(r["d_j (นาที)"])
        except (TypeError, ValueError):
            continue
        if dur <= 0:
            continue
        tasks.append(Task(
            id=tid,
            kind=str(r.get("ประเภท", "C1")).strip() or "C1",
            zone=str(r.get("Zone", "Z1")).strip() or "Z1",
            name=str(r.get("ชื่องาน", tid)),
            duration=dur,
        ))
    return tasks


def skill_matrix_df(workers: list[str], tasks: list[Task], zone_based: bool) -> pd.DataFrame:
    a = build_capability(workers, tasks, zone_based=zone_based)
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


def gantt_chart(schedule: pd.DataFrame, workers: list[str], T: int, cmax: int) -> go.Figure:
    zones = sorted(schedule["Zone"].unique())
    color_of = {z: ZONE_COLORS[i % len(ZONE_COLORS)] for i, z in enumerate(zones)}

    fig = go.Figure()
    for z in zones:
        sub = schedule[schedule["Zone"] == z]
        fig.add_trace(go.Bar(
            y=sub["Worker"],
            x=sub["Duration"],
            base=sub["Start"],
            orientation="h",
            name=z,
            marker_color=color_of[z],
            text=sub["Task"],
            textposition="inside",
            insidetextanchor="middle",
            hovertemplate=(
                "<b>%{text}</b><br>พนักงาน %{y}"
                "<br>เริ่ม %{base} นาที<br>ใช้เวลา %{x} นาที<extra></extra>"
            ),
        ))

    fig.add_vline(x=cmax, line_dash="dash", line_color="#111827",
                  annotation_text=f"Cmax = {cmax}", annotation_position="top")
    if T >= cmax:
        fig.add_vline(x=T, line_dash="dot", line_color="#dc2626",
                      annotation_text=f"T = {T}", annotation_position="top right")

    fig.update_layout(
        barmode="overlay",
        height=max(320, 60 * len(workers) + 140),
        xaxis_title="เวลา (นาทีนับจากเริ่มจอด)",
        yaxis_title="พนักงาน",
        yaxis=dict(categoryorder="array", categoryarray=list(reversed(workers))),
        xaxis=dict(range=[0, max(T, cmax) + 2], dtick=5),
        legend_title="Work Unit",
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return buf.getvalue()


# ==================================================================
# Sidebar — การตั้งค่าหลัก
# ==================================================================
st.sidebar.title("⚙️ ตั้งค่าตัวแบบ")

st.sidebar.subheader("1) เครื่องบิน")
aircraft_names = list(AIRCRAFT_LIBRARY.keys())
aircraft = st.sidebar.selectbox(
    "ประเภทอากาศยาน",
    aircraft_names,
    index=aircraft_names.index(DEFAULT_AIRCRAFT),
)
spec = AIRCRAFT_LIBRARY[aircraft]
st.sidebar.caption(
    f"{spec['category']} · {spec['seats']} ที่นั่ง · "
    f"{len(spec['zones'])} Work Units · ห้องน้ำ {spec['n_lav']} · ครัว {spec['n_gal']}"
)
st.sidebar.caption(f"ที่มาข้อมูล: {spec['source']}")

cleaning_options = list(CLEANING_TYPES.keys())
cleaning_type = st.sidebar.selectbox(
    "รูปแบบการทำความสะอาด", cleaning_options,
    index=cleaning_options.index(DEFAULT_CLEANING_TYPE),
)
weather_options = list(WEATHER_FACTOR.keys())
weather = st.sidebar.selectbox(
    "สภาพอากาศ", weather_options,
    index=weather_options.index(DEFAULT_WEATHER),
)
st.sidebar.caption(
    f"ตัวคูณระยะเวลา γ = {WEATHER_FACTOR[weather]:.2f} · {WEATHER_REASON[weather]}"
)

st.sidebar.subheader("2) พนักงานและเวลา")
n_workers = st.sidebar.number_input("จำนวนพนักงาน (m)", 1, 30, 4, 1)
T = st.sidebar.number_input("เวลาจอด Turnaround Time T (นาที)", 5, 300, 30, 5)

st.sidebar.subheader("3) เงื่อนไขการทดลอง")
scenario = st.sidebar.selectbox(
    "Scenario", list(SCENARIOS.keys()), format_func=lambda s: f"{s} — {SCENARIOS[s]}"
)
objective_mode = st.sidebar.selectbox(
    "Objective", ["Time Only", "Time + Workload", "Workload Only"], index=1
)
use_blocking = st.sidebar.checkbox("ใช้ข้อจำกัด Aisle Blocking (เซต B)", value=True)
enforce_T = st.sidebar.checkbox("บังคับ Cmax ≤ T", value=True,
                                help="ปิดไว้เมื่อต้องการดูว่าจริง ๆ ต้องใช้เวลาเท่าไร แทนที่จะได้ผลว่า Infeasible")
max_seconds = st.sidebar.slider("เวลาสูงสุดที่ให้ Solver ทำงาน (วินาที)", 5, 120, 30, 5)

zone_based = scenario in ("S2", "S4")
trash_first = scenario in ("S3", "S4")

# ==================================================================
# สร้าง / รีเซ็ตรายการงาน เมื่อเงื่อนไขหลักเปลี่ยน
# ==================================================================
signature = f"{aircraft}|{cleaning_type}|{weather}"
if st.session_state.get("signature") != signature:
    st.session_state["signature"] = signature
    st.session_state["tasks_df"] = tasks_to_df(
        build_tasks(aircraft, CLEANING_TYPES[cleaning_type], weather)
    )
    st.session_state.pop("skill_df", None)
    st.session_state.pop("result", None)

st.sidebar.divider()
if st.sidebar.button("↺ รีเซ็ตรายการงานตามเครื่องบินที่เลือก", use_container_width=True):
    st.session_state["tasks_df"] = tasks_to_df(
        build_tasks(aircraft, CLEANING_TYPES[cleaning_type], weather)
    )
    st.session_state.pop("skill_df", None)
    st.rerun()

# ==================================================================
# Main
# ==================================================================
st.title("✈️ Aircraft Cleaning Optimization")
st.caption(
    "การสร้างตัวแบบแผนการทำความสะอาดเครื่องบินขณะจอดที่สนามบิน · "
    "Time-indexed Binary Optimization Model · OR-Tools CP-SAT"
)

tab_setup, tab_result, tab_gantt, tab_compare, tab_model = st.tabs(
    ["📋 กำหนดงานและพนักงาน", "📊 ผลลัพธ์", "📈 Gantt Chart",
     "🔬 เปรียบเทียบ Scenario", "📐 ตัวแบบคณิตศาสตร์"]
)

# ------------------------------------------------------------------
# TAB 1 — Setup
# ------------------------------------------------------------------
with tab_setup:
    st.subheader("รายการงาน (เซต J)")
    st.caption(
        "แก้ระยะเวลาได้ · ติ๊กออกเพื่อไม่รวมงานนั้น · "
        "กดปุ่ม + ท้ายตารางเพื่อเพิ่มงานใหม่ (ต้องใส่ Task ID, Zone และ d_j)"
    )
    tasks_df = st.data_editor(
        st.session_state["tasks_df"],
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "เลือก": st.column_config.CheckboxColumn(width="small"),
            "ประเภท": st.column_config.SelectboxColumn(
                options=list(TASK_KIND_LABEL.keys()) + ["OVH"], width="small"
            ),
            "d_j (นาที)": st.column_config.NumberColumn(min_value=1, max_value=120, step=1),
        },
        key="task_editor",
    )
    st.session_state["tasks_df"] = tasks_df
    tasks = df_to_tasks(tasks_df)

    c1, c2, c3 = st.columns(3)
    c1.metric("จำนวนงานที่ใช้", len(tasks))
    c2.metric("เวลางานรวม", f"{sum(t.duration for t in tasks)} นาที")
    c3.metric(
        "ขีดจำกัดล่างทางทฤษฎี",
        f"{-(-sum(t.duration for t in tasks) // max(1, n_workers))} นาที",
        help="เวลางานรวมหารด้วยจำนวนพนักงาน — Cmax จะต่ำกว่านี้ไม่ได้",
    )

    st.divider()
    st.subheader("ความสามารถของพนักงาน (a_ij)")
    st.caption("ติ๊ก = พนักงานคนนั้นทำงานนั้นได้ · Scenario S2/S4 จะกำหนดโซนให้อัตโนมัติ")

    workers = build_workers(int(n_workers))
    skill_key = f"{signature}|{n_workers}|{zone_based}|{len(tasks)}"
    if st.session_state.get("skill_key") != skill_key:
        st.session_state["skill_key"] = skill_key
        st.session_state["skill_df"] = skill_matrix_df(workers, tasks, zone_based)

    skill_df = st.data_editor(
        st.session_state["skill_df"],
        use_container_width=True,
        hide_index=True,
        disabled=["พนักงาน"],
        key="skill_editor",
    )
    st.session_state["skill_df"] = skill_df

    st.divider()
    run = st.button("🚀 Run Optimization", type="primary", use_container_width=True)

    if run:
        if not tasks:
            st.error("ยังไม่มีงานในรายการ")
        else:
            a = df_to_capability(skill_df, tasks)
            data = ProblemData(
                aircraft=aircraft,
                workers=workers,
                tasks=tasks,
                T=int(T),
                a=a,
                P=build_precedence(tasks, trash_first_global=trash_first),
                B=build_blocking(tasks) if use_blocking else [],
                enforce_time_limit=enforce_T,
                objective_mode=objective_mode,
                scenario=scenario,
            )
            with st.spinner("กำลังหาคำตอบด้วย OR-Tools CP-SAT..."):
                result = solve_model(data, max_seconds=max_seconds)
            st.session_state["result"] = result
            st.session_state["data"] = data
            st.session_state["weather_used"] = weather
            if result.feasible:
                st.success(f"พบคำตอบแล้ว — Cmax = {result.cmax} นาที (ดูรายละเอียดในแท็บผลลัพธ์)")
            else:
                st.error(result.message)

# ------------------------------------------------------------------
# TAB 2 — Result
# ------------------------------------------------------------------
with tab_result:
    result = st.session_state.get("result")
    data = st.session_state.get("data")

    if result is None:
        st.info("กด **Run Optimization** ในแท็บแรกก่อน")
    elif not result.feasible:
        st.error(result.message)
        st.caption(f"สถานะจาก Solver: {result.status}")
    else:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("เครื่องบิน", data.aircraft)
        c2.metric("พนักงาน", f"{len(data.workers)} คน")
        c3.metric("จำนวนงาน", len(data.tasks))
        c4.metric("Cmax", f"{result.cmax} นาที")
        c5.metric("Buffer", f"{result.buffer} นาที",
                  delta=None if result.buffer >= 0 else "เกินเวลาจอด")

        st.caption(
            f"Scenario {data.scenario} · Objective: {data.objective_mode} · "
            f"สภาพอากาศ {st.session_state.get('weather_used', '-')} · "
            f"สถานะ {result.status} · ใช้เวลาคำนวณ {result.solve_time:.2f} วินาที"
        )

        if result.buffer is not None and result.buffer < 0:
            st.warning("ตารางนี้ใช้เวลาเกินเวลาจอดที่กำหนด — ต้องเพิ่มพนักงานหรือขยายเวลาจอด")

        st.subheader("ตารางการทำงาน")
        st.dataframe(
            result.schedule.rename(columns={
                "Worker": "พนักงาน", "Task": "งาน", "TaskName": "ชื่องาน",
                "Start": "เริ่ม (นาที)", "End": "เสร็จ (นาที)", "Duration": "ใช้เวลา",
            }),
            use_container_width=True, hide_index=True,
        )

        st.subheader("ภาระงานของพนักงาน")
        st.dataframe(result.workload, use_container_width=True, hide_index=True)

        summary = pd.DataFrame([{
            "Aircraft": data.aircraft, "Scenario": data.scenario,
            "Workers": len(data.workers), "Tasks": len(data.tasks),
            "T": data.T, "Cmax": result.cmax, "Buffer": result.buffer,
            "Status": result.status,
        }])
        st.download_button(
            "⬇️ ดาวน์โหลดผลลัพธ์เป็น Excel",
            data=to_excel_bytes({
                "Summary": summary,
                "Schedule": result.schedule,
                "Workload": result.workload,
            }),
            file_name=f"result_{data.aircraft}_{data.scenario}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ------------------------------------------------------------------
# TAB 3 — Gantt
# ------------------------------------------------------------------
with tab_gantt:
    result = st.session_state.get("result")
    data = st.session_state.get("data")
    if result is None or not result.feasible:
        st.info("ยังไม่มีคำตอบสำหรับแสดงผล")
    else:
        st.plotly_chart(
            gantt_chart(result.schedule, data.workers, data.T, result.cmax),
            use_container_width=True,
        )

# ------------------------------------------------------------------
# TAB 4 — Scenario comparison
# ------------------------------------------------------------------
with tab_compare:
    st.subheader("เปรียบเทียบผลของแต่ละ Scenario")
    st.caption("ใช้รายการงาน จำนวนพนักงาน และเวลาจอดชุดเดียวกัน เปลี่ยนเฉพาะ a_ij และเซต P")

    picked = st.multiselect("เลือก Scenario", list(SCENARIOS.keys()),
                            default=list(SCENARIOS.keys()))
    if st.button("▶️ รันเปรียบเทียบ"):
        tasks = df_to_tasks(st.session_state["tasks_df"])
        workers = build_workers(int(n_workers))

        def build_fn(s: str) -> ProblemData:
            zb = s in ("S2", "S4")
            tf = s in ("S3", "S4")
            return ProblemData(
                aircraft=aircraft, workers=workers, tasks=tasks, T=int(T),
                a=build_capability(workers, tasks, zone_based=zb),
                P=build_precedence(tasks, trash_first_global=tf),
                B=build_blocking(tasks) if use_blocking else [],
                enforce_time_limit=enforce_T,
                objective_mode=objective_mode, scenario=s,
            )

        with st.spinner("กำลังรันทุก Scenario..."):
            table = compare_scenarios(build_fn, picked, max_seconds=max_seconds)
        st.session_state["compare"] = table

    table = st.session_state.get("compare")
    if table is not None:
        st.dataframe(table, use_container_width=True, hide_index=True)
        ok = table.dropna(subset=["Cmax"])
        if not ok.empty:
            fig = go.Figure(go.Bar(
                x=ok["Scenario"], y=ok["Cmax"],
                text=ok["Cmax"], textposition="outside", marker_color="#2563eb",
            ))
            fig.add_hline(y=int(T), line_dash="dot", line_color="#dc2626",
                          annotation_text=f"T = {T}")
            fig.update_layout(yaxis_title="Cmax (นาที)", height=380,
                              margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------------------------------
# TAB 5 — Model
# ------------------------------------------------------------------
with tab_model:
    st.subheader("ตัวแบบคณิตศาสตร์")
    st.markdown(r"""
**เซต**

$I$ = เซตพนักงาน  ·  $J$ = เซตงาน  ·  $R$ = เซต Work Unit  ·  $H_j=\{0,1,\dots,T-d_j\}$

**ตัวแปรตัดสินใจ**

$$x_{ijt}=\begin{cases}1 & \text{ถ้าพนักงาน } i \text{ เริ่มงาน } j \text{ ที่เวลา } t\\ 0 & \text{กรณีอื่น}\end{cases}
\qquad C_{\max}\in\mathbb{Z}_{\ge 0}$$

**นิพจน์ที่คำนวณจากตัวแปร** (ไม่ใช่ตัวแปรตัดสินใจ)

$$S_j=\sum_{i\in I}\sum_{t\in H_j} t\,x_{ijt}
\qquad
E_j=\sum_{i\in I}\sum_{t\in H_j} (t+d_j)\,x_{ijt}$$

**Objective**

$$\min\; C_{\max}$$

**Constraints**

1. ทุกงานถูกทำหนึ่งครั้ง $\displaystyle\sum_{i\in I}\sum_{t\in H_j}x_{ijt}=1\quad\forall j\in J$
2. ความสามารถของพนักงาน $x_{ijt}\le a_{ij}\quad\forall i,j,t$
3. ทำงานซ้อนไม่ได้ $\displaystyle\sum_{j\in J}\sum_{t\in H_j:\,t\le\tau<t+d_j}x_{ijt}\le 1\quad\forall i,\ \tau\in\{0,\dots,T-1\}$
4. ลำดับก่อน–หลัง $\displaystyle\sum_{i}\sum_{t\in H_j}(t+d_j)x_{ijt}\le\sum_{i}\sum_{t\in H_k}t\,x_{ikt}\quad\forall (j,k)\in P$
5. เชื่อมกับ $C_{\max}$: $\displaystyle C_{\max}\ge\sum_{i}\sum_{t\in H_j}(t+d_j)x_{ijt}\quad\forall j$
6. Time Limit $C_{\max}\le T$
7. Aisle Blocking $\displaystyle\sum_{i}\sum_{t\in H_j:\,t\le\tau<t+d_j}x_{ijt}+\sum_{i}\sum_{t\in H_k:\,t\le\tau<t+d_k}x_{ikt}\le 1\quad\forall (j,k)\in B,\ \tau$
8. ประเภทตัวแปร $x_{ijt}\in\{0,1\}$, $C_{\max}\in\mathbb{Z}_{\ge0}$
""")

    st.divider()
    st.markdown("**ความครอบคลุมตามขอบเขตของโครงงาน**")
    st.dataframe(
        pd.DataFrame(SCOPE_MAPPING, columns=["ข้อในขอบเขต", "งานย่อย", "รหัสในตัวแบบ"]),
        use_container_width=True, hide_index=True, height=320,
    )
    st.caption(
        "γ คือตัวคูณสภาพอากาศที่คูณกับระยะเวลาของทุกงาน ตอบวัตถุประสงค์ข้อ 2 "
        "ส่วนนโยบายของสนามบินแทนด้วยพารามิเตอร์ a และ Scenario"
    )

    st.divider()
    tasks_now = df_to_tasks(st.session_state["tasks_df"])
    cA, cB = st.columns(2)
    with cA:
        st.markdown("**เซต P — ลำดับก่อน–หลัง**")
        P = build_precedence(tasks_now, trash_first_global=trash_first)
        st.dataframe(pd.DataFrame(P, columns=["งานก่อน (j)", "งานหลัง (k)"]),
                     use_container_width=True, hide_index=True, height=260)
    with cB:
        st.markdown("**เซต B — คู่งานที่ Block กัน**")
        B = build_blocking(tasks_now) if use_blocking else []
        if B:
            st.dataframe(pd.DataFrame(B, columns=["งาน j", "งาน k"]),
                         use_container_width=True, hide_index=True, height=260)
        else:
            st.caption("ปิดการใช้งานข้อจำกัดนี้อยู่")
        st.caption(
            "คู่ Block ต้องเป็นงานข้ามโซนเท่านั้น — คู่ในโซนเดียวกัน เช่น "
            "(C2Z2, C1Z2) ไม่มีผล เพราะเซต P บังคับลำดับไว้อยู่แล้ว"
        )
