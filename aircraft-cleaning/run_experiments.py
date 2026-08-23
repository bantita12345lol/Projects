"""
run_experiments.py
------------------------------------------------------------------
รันการทดลองทั้งหมดสำหรับบทที่ 4 ในครั้งเดียว

วิธีใช้
    python run_experiments.py

ผลลัพธ์ที่ได้ (โฟลเดอร์ results/)
    experiment_results.xlsx   ตารางผลทุกการทดลอง 5 sheet
    fig1_workers_vs_cmax.png  กราฟจำนวนพนักงานเทียบ Cmax
    fig2_aircraft.png         จำนวนพนักงานขั้นต่ำของแต่ละเครื่องบิน
    fig3_scenario.png         เปรียบเทียบ Scenario
    fig4_turnaround.png       ผลของเวลาจอด

หมายเหตุ
    ป้ายกำกับกราฟใช้ภาษาอังกฤษ เพื่อเลี่ยงปัญหาฟอนต์ไทยใน matplotlib
    ถ้าต้องการภาษาไทย ให้ติดตั้งฟอนต์ Sarabun แล้วตั้งค่า rcParams
"""

from __future__ import annotations

import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from aircraft_data import (
    CLEANING_TYPES,
    DEFAULT_CLEANING_TYPE,
    DEFAULT_WEATHER,
    WEATHER_FACTOR,
    build_blocking,
    build_capability,
    build_precedence,
    build_tasks,
    build_workers,
)
from solver import ProblemData, solve_model

RESULT_DIR = Path(__file__).resolve().parent / "results"
RESULT_DIR.mkdir(exist_ok=True)

SOLVER_SECONDS = 20.0


def make_problem(aircraft, n_workers, T, scenario="S1",
                 enforce_T=True, objective="Time Only", blocking=True,
                 cleaning=DEFAULT_CLEANING_TYPE, weather=DEFAULT_WEATHER):
    tasks = build_tasks(aircraft, CLEANING_TYPES[cleaning], weather)
    workers = build_workers(n_workers)
    zone_based = scenario in ("S2", "S4")
    trash_first = scenario in ("S3", "S4")
    return ProblemData(
        aircraft=aircraft,
        workers=workers,
        tasks=tasks,
        T=T,
        a=build_capability(workers, tasks, zone_based=zone_based),
        P=build_precedence(tasks, trash_first_global=trash_first),
        B=build_blocking(tasks) if blocking else [],
        enforce_time_limit=enforce_T,
        objective_mode=objective,
        scenario=scenario,
    )


def run(aircraft, n_workers, T, scenario="S1", enforce_T=True,
        cleaning=DEFAULT_CLEANING_TYPE, weather=DEFAULT_WEATHER):
    data = make_problem(aircraft, n_workers, T, scenario, enforce_T,
                        cleaning=cleaning, weather=weather)
    res = solve_model(data, max_seconds=SOLVER_SECONDS)
    return {
        "Aircraft": aircraft,
        "Cleaning": cleaning,
        "Weather": weather,
        "Scenario": scenario,
        "Workers": n_workers,
        "Tasks": len(data.tasks),
        "T (min)": T,
        "Cmax": res.cmax,
        "Buffer": res.buffer,
        "Status": res.status,
        "Feasible": res.feasible,
        "Solve Time (s)": round(res.solve_time, 2),
    }


# ==================================================================
# Verification — พิสูจน์ว่าตัวแบบทำงานถูกต้อง
# ==================================================================
def verification() -> pd.DataFrame:
    print("\n[Verification] ตรวจสอบความถูกต้องของตัวแบบ")
    rows = []
    tasks = build_tasks("A320-200")
    total = sum(t.duration for t in tasks)

    # V1 : พนักงาน 1 คน ปิด Time Limit -> Cmax ต้องเท่ากับผลรวมของ d_j พอดี
    r = run("A320-200", 1, 30, "S1", enforce_T=False)
    rows.append({
        "Test": "V1 พนักงาน 1 คน (ปิด Cmax<=T)",
        "คาดหวัง": f"Cmax = ผลรวม d_j = {total}",
        "ได้": r["Cmax"],
        "ผ่าน": r["Cmax"] == total,
        "อธิบาย": "ยืนยัน Constraint 3 พนักงานคนเดียวทำงานซ้อนไม่ได้",
    })

    # V2 : พนักงานเยอะมาก -> Cmax ต้องเท่ากับความยาว Critical Path
    zones = sorted({t.zone for t in tasks if t.zone.startswith("Z")})
    dur = {t.id: t.duration for t in tasks}
    chain = max(
        dur.get(f"C1{z}", 0) + dur.get(f"C2{z}", 0) + dur.get(f"C3{z}", 0)
        for z in zones
    )
    r = run("A320-200", 20, 60, "S1")
    rows.append({
        "Test": "V2 พนักงาน 20 คน",
        "คาดหวัง": f"Cmax = Critical Path = {chain}",
        "ได้": r["Cmax"],
        "ผ่าน": r["Cmax"] == chain,
        "อธิบาย": "ยืนยัน Constraint 4 ลำดับก่อน-หลังเป็นคอขวดจริง",
    })

    # V3 : เวลาจอดน้อยเกินไป -> ต้องตอบ Infeasible
    r = run("A320-200", 2, 10, "S1")
    rows.append({
        "Test": "V3 พนักงาน 2 คน T=10",
        "คาดหวัง": "INFEASIBLE",
        "ได้": r["Status"],
        "ผ่าน": not r["Feasible"],
        "อธิบาย": "ยืนยัน Constraint 6 บังคับ Cmax <= T",
    })

    # V4 : ตรวจตารางจริงว่าไม่มีงานซ้อนและไม่ผิดลำดับ
    data = make_problem("A320-200", 4, 30, "S1")
    res = solve_model(data, SOLVER_SECONDS)
    sched = res.schedule.set_index("Task")
    overlap = False
    for w, g in res.schedule.groupby("Worker"):
        g = g.sort_values("Start")
        ends = g["End"].tolist()
        starts = g["Start"].tolist()
        for k in range(1, len(g)):
            if starts[k] < ends[k - 1]:
                overlap = True
    prec_ok = all(
        sched.loc[j, "End"] <= sched.loc[k, "Start"]
        for j, k in data.P if j in sched.index and k in sched.index
    )
    rows.append({
        "Test": "V4 ตรวจตารางคำตอบ (4 คน T=30)",
        "คาดหวัง": "ไม่มีงานซ้อน และลำดับถูกต้อง",
        "ได้": f"ซ้อน={overlap} ลำดับถูก={prec_ok}",
        "ผ่าน": (not overlap) and prec_ok,
        "อธิบาย": "ตรวจคำตอบที่ Solver ให้มาโดยตรง",
    })

    df = pd.DataFrame(rows)
    print(df[["Test", "ได้", "ผ่าน"]].to_string(index=False))
    return df


# ==================================================================
# การทดลองที่ 1 — จำนวนพนักงานที่เหมาะสม
# ==================================================================
def experiment_1() -> pd.DataFrame:
    print("\n[การทดลองที่ 1] จำนวนพนักงานที่เหมาะสม (A320-200, T=30)")
    rows = [run("A320-200", n, 30, "S1", enforce_T=False)
            for n in [2, 3, 4, 5, 6, 7, 8, 10]]
    df = pd.DataFrame(rows)
    print(df[["Workers", "Cmax", "Status"]].to_string(index=False))

    ok = df.dropna(subset=["Cmax"])
    plt.figure(figsize=(7, 4.2))
    plt.plot(ok["Workers"], ok["Cmax"], marker="o", color="#185FA5", linewidth=2)
    plt.axhline(30, linestyle="--", color="#A32D2D", label="Turnaround time T = 30")
    for _, r in ok.iterrows():
        plt.annotate(int(r["Cmax"]), (r["Workers"], r["Cmax"]),
                     textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9)
    plt.xlabel("Number of workers (m)")
    plt.ylabel("Cmax (minutes)")
    plt.title("Effect of workforce size on completion time (A320-200)")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(RESULT_DIR / "fig1_workers_vs_cmax.png", dpi=200)
    plt.close()
    return df


# ==================================================================
# การทดลองที่ 2 — เปรียบเทียบข้ามประเภทเครื่องบิน
# ==================================================================
AIRCRAFT_CASES = [
    ("ATR72-600", 20, range(1, 9)),
    ("A320-200", 30, range(2, 11)),
    ("B777-300ER", 45, range(4, 15)),
    ("A380-800", 60, range(6, 19)),
]


def experiment_2() -> pd.DataFrame:
    print("\n[การทดลองที่ 2] จำนวนพนักงานขั้นต่ำของแต่ละเครื่องบิน")
    rows = []
    for aircraft, T, worker_range in AIRCRAFT_CASES:
        found = None
        cmax_at_min = None
        for n in worker_range:
            r = run(aircraft, n, T, "S1")
            if r["Feasible"]:
                found, cmax_at_min = n, r["Cmax"]
                break
        rows.append({
            "Aircraft": aircraft,
            "T (min)": T,
            "Min Workers": found,
            "Cmax": cmax_at_min,
            "Buffer": (T - cmax_at_min) if cmax_at_min is not None else None,
        })
        print(f"  {aircraft:12} T={T:3}  ต้องใช้อย่างน้อย {found} คน  Cmax={cmax_at_min}")

    df = pd.DataFrame(rows)
    ok = df.dropna(subset=["Min Workers"])
    plt.figure(figsize=(7, 4.2))
    plt.bar(ok["Aircraft"], ok["Min Workers"], color="#0F6E56", width=0.55)
    for i, v in enumerate(ok["Min Workers"]):
        plt.text(i, v + 0.15, str(int(v)), ha="center", fontsize=10)
    plt.xlabel("Aircraft type")
    plt.ylabel("Minimum workers required")
    plt.title("Minimum workforce by aircraft type")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(RESULT_DIR / "fig2_aircraft.png", dpi=200)
    plt.close()
    return df


# ==================================================================
# การทดลองที่ 3 — เปรียบเทียบ Scenario
# ==================================================================
def experiment_3() -> pd.DataFrame:
    print("\n[การทดลองที่ 3] เปรียบเทียบ Scenario (A320-200, 4 คน, T=30)")
    rows = [run("A320-200", 4, 30, s, enforce_T=False) for s in ["S1", "S2", "S3", "S4"]]
    df = pd.DataFrame(rows)
    print(df[["Scenario", "Cmax", "Status"]].to_string(index=False))

    ok = df.dropna(subset=["Cmax"])
    plt.figure(figsize=(7, 4.2))
    plt.bar(ok["Scenario"], ok["Cmax"], color="#534AB7", width=0.55)
    for i, v in enumerate(ok["Cmax"]):
        plt.text(i, v + 0.3, str(int(v)), ha="center", fontsize=10)
    plt.axhline(30, linestyle="--", color="#A32D2D", label="T = 30")
    plt.xlabel("Scenario")
    plt.ylabel("Cmax (minutes)")
    plt.title("Scenario comparison (A320-200, 4 workers)")
    plt.grid(axis="y", alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(RESULT_DIR / "fig3_scenario.png", dpi=200)
    plt.close()
    return df


# ==================================================================
# การทดลองที่ 4 — ผลของเวลาจอด
# ==================================================================
def experiment_4() -> pd.DataFrame:
    print("\n[การทดลองที่ 4] ผลของเวลาจอดต่อจำนวนพนักงานขั้นต่ำ (A320-200)")
    rows = []
    for T in [20, 25, 30, 35, 45]:
        found, cmax_at_min = None, None
        for n in range(1, 13):
            r = run("A320-200", n, T, "S1")
            if r["Feasible"]:
                found, cmax_at_min = n, r["Cmax"]
                break
        rows.append({"T (min)": T, "Min Workers": found, "Cmax": cmax_at_min})
        print(f"  T={T:3}  ต้องใช้อย่างน้อย {found} คน  Cmax={cmax_at_min}")

    df = pd.DataFrame(rows)
    ok = df.dropna(subset=["Min Workers"])
    plt.figure(figsize=(7, 4.2))
    plt.plot(ok["T (min)"], ok["Min Workers"], marker="s",
             color="#BA7517", linewidth=2)
    for _, r in ok.iterrows():
        plt.annotate(int(r["Min Workers"]), (r["T (min)"], r["Min Workers"]),
                     textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9)
    plt.xlabel("Turnaround time T (minutes)")
    plt.ylabel("Minimum workers required")
    plt.title("Effect of turnaround time on workforce requirement (A320-200)")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(RESULT_DIR / "fig4_turnaround.png", dpi=200)
    plt.close()
    return df


# ==================================================================
# การทดลองที่ 5 — ผลของสภาพอากาศ
# ==================================================================
def experiment_5() -> pd.DataFrame:
    print("\n[การทดลองที่ 5] ผลของสภาพอากาศ (A320-200, T=30)")
    rows = []
    for w in WEATHER_FACTOR:
        found, cmax_at_min = None, None
        for n in range(1, 13):
            r = run("A320-200", n, 30, "S1", weather=w)
            if r["Feasible"]:
                found, cmax_at_min = n, r["Cmax"]
                break
        base = run("A320-200", 4, 30, "S1", enforce_T=False, weather=w)
        rows.append({
            "Weather": w,
            "gamma": WEATHER_FACTOR[w],
            "Min Workers": found,
            "Cmax (4 workers)": base["Cmax"],
        })
        print(f"  {w:28} gamma={WEATHER_FACTOR[w]:.2f}  "
              f"ขั้นต่ำ {found} คน  Cmax(4 คน)={base['Cmax']}")

    df = pd.DataFrame(rows)
    labels = ["Clear", "High heat", "Rain", "Heavy rain"]
    plt.figure(figsize=(7.4, 4.2))
    plt.bar(labels, df["Cmax (4 workers)"], color="#0891b2", width=0.55)
    for i, v in enumerate(df["Cmax (4 workers)"]):
        plt.text(i, v + 0.3, str(int(v)), ha="center", fontsize=10)
    plt.axhline(30, linestyle="--", color="#A32D2D", label="T = 30")
    plt.xlabel("Weather condition")
    plt.ylabel("Cmax with 4 workers (minutes)")
    plt.title("Effect of weather on completion time (A320-200)")
    plt.grid(axis="y", alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(RESULT_DIR / "fig5_weather.png", dpi=200)
    plt.close()
    return df


# ==================================================================
# การทดลองที่ 6 — เปรียบเทียบรูปแบบการทำความสะอาด
# ==================================================================
def experiment_6() -> pd.DataFrame:
    print("\n[การทดลองที่ 6] เปรียบเทียบรูปแบบการทำความสะอาด (A320-200)")
    rows = []
    for ct in CLEANING_TYPES:
        tasks = build_tasks("A320-200", CLEANING_TYPES[ct])
        T = {"Quick Transit - พื้นฐาน": 30,
             "Quick Transit - เต็มรูปแบบ": 45,
             "Layover - เต็มรูปแบบ": 120}[ct]
        found, cmax_at_min = None, None
        for n in range(1, 15):
            r = run("A320-200", n, T, "S1", cleaning=ct)
            if r["Feasible"]:
                found, cmax_at_min = n, r["Cmax"]
                break
        rows.append({
            "Cleaning Type": ct,
            "Tasks": len(tasks),
            "Total Work (min)": sum(t.duration for t in tasks),
            "T (min)": T,
            "Min Workers": found,
            "Cmax": cmax_at_min,
        })
        print(f"  {ct:28} งาน {len(tasks):2}  T={T:3}  "
              f"ขั้นต่ำ {found} คน  Cmax={cmax_at_min}")

    df = pd.DataFrame(rows)
    labels = ["Quick basic", "Quick full", "Layover"]
    vals = df["Min Workers"].fillna(0)
    plt.figure(figsize=(7.4, 4.2))
    plt.bar(labels, vals, color="#993556", width=0.55)
    for i, v in enumerate(vals):
        plt.text(i, v + 0.12, str(int(v)) if v else "n/a",
                 ha="center", fontsize=10)
    plt.xlabel("Cleaning type")
    plt.ylabel("Minimum workers required")
    plt.title("Workforce requirement by cleaning type (A320-200)")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(RESULT_DIR / "fig6_cleaning_type.png", dpi=200)
    plt.close()
    return df


# ==================================================================
def main():
    t0 = time.time()
    print("=" * 60)
    print("Aircraft Cleaning Optimization — Experiment Runner")
    print("=" * 60)

    ver = verification()
    e1 = experiment_1()
    e2 = experiment_2()
    e3 = experiment_3()
    e4 = experiment_4()
    e5 = experiment_5()
    e6 = experiment_6()

    out = RESULT_DIR / "experiment_results.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        ver.to_excel(writer, sheet_name="Verification", index=False)
        e1.to_excel(writer, sheet_name="Exp1_Workers", index=False)
        e2.to_excel(writer, sheet_name="Exp2_Aircraft", index=False)
        e3.to_excel(writer, sheet_name="Exp3_Scenario", index=False)
        e4.to_excel(writer, sheet_name="Exp4_Turnaround", index=False)
        e5.to_excel(writer, sheet_name="Exp5_Weather", index=False)
        e6.to_excel(writer, sheet_name="Exp6_CleaningType", index=False)

    print("\n" + "=" * 60)
    print(f"เสร็จสิ้น ใช้เวลา {time.time() - t0:.1f} วินาที")
    print(f"ไฟล์ผลลัพธ์อยู่ที่ {RESULT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
