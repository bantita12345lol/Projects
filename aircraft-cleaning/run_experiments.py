"""
run_experiments.py
------------------------------------------------------------------
Verification + experiments for Chapter 4.

Run
    python run_experiments.py
    python run_experiments.py verify

Model-status note
    These tests verify mathematical implementation and robustness under
    literature-calibrated assumptions. They do NOT constitute field validation.

Main outputs in results/
    experiment_results.xlsx
    fig1_workers_vs_cmax.png
    fig2_min_workers_aircraft.png
    fig3_min_workers_scenario.png
    fig4_turnaround.png
    fig5_sensitivity.png
    fig6_follow_lag.png
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from aircraft_data import (
    AIRCRAFT_LIBRARY,
    CLEANING_TYPES,
    DEFAULT_CLEANING_TYPE,
    DEFAULT_DURATION_FACTOR,
    DEFAULT_FOLLOW_LAG,
    DURATION_FACTORS,
    LAYOVER,
    QUICK_TRANSIT,
    SCENARIOS,
    ZONE_TASK_ORDER,
    Task,
    build_capability,
    build_precedence,
    build_scenario_workers,
    build_tasks,
    minimum_total_workers_for_scenario,
    required_service_workers,
    service_worker_count_options,
    scenario_settings,
)
from solver import ProblemData, find_min_workers, solve_best_variant, solve_model

RESULT_DIR = Path(__file__).resolve().parent / "results"
RESULT_DIR.mkdir(exist_ok=True)
SOLVER_SECONDS = 20.0
SCENARIO_LIST = list(SCENARIOS.keys())


def build_tasks_for_case(aircraft, scenario, cleaning=DEFAULT_CLEANING_TYPE,
                         factor=DEFAULT_DURATION_FACTOR, extra_kinds=()):
    kinds = list(CLEANING_TYPES[cleaning])
    for k in extra_kinds:
        if k not in kinds:
            kinds.append(k)
    return build_tasks(
        aircraft, kinds, factor,
        include_deicing=scenario_settings(scenario)["include_deicing"],
    )


def make_problem_from_tasks(aircraft, tasks, n_workers, T, scenario="S1",
                            enforce_T=True, objective="Time + Workload",
                            follow_lag=DEFAULT_FOLLOW_LAG, hygiene=True,
                            service_count: int | None = None):
    cfg = scenario_settings(scenario)
    workers, dedicated = build_scenario_workers(n_workers, scenario)
    if service_count is None:
        service_count = required_service_workers(tasks, T, scenario) if cfg["zone_based"] else 0
    a = build_capability(
        workers, tasks,
        zone_based=cfg["zone_based"],
        service_worker_count=service_count,
        dedicated_deicing_worker=dedicated,
    )
    return ProblemData(
        aircraft=aircraft,
        workers=workers,
        tasks=tasks,
        T=T,
        a=a,
        P=build_precedence(tasks),
        follow_lag=follow_lag,
        hygiene_galley_first=hygiene,
        enforce_time_limit=enforce_T,
        objective_mode=objective,
        scenario=scenario,
        balance_workers=[w for w in workers if w != dedicated],
        service_worker_count=service_count if cfg["zone_based"] else None,
    )


def solve_scenario(aircraft, n_workers, T, scenario="S1", enforce_T=True,
                 objective="Time + Workload", follow_lag=DEFAULT_FOLLOW_LAG,
                 hygiene=True, cleaning=DEFAULT_CLEANING_TYPE,
                 factor=DEFAULT_DURATION_FACTOR, extra_kinds=()):
    tasks = build_tasks_for_case(aircraft, scenario, cleaning, factor, extra_kinds)
    cfg = scenario_settings(scenario)

    if not cfg["zone_based"]:
        data = make_problem_from_tasks(
            aircraft, tasks, n_workers, T, scenario, enforce_T,
            objective, follow_lag, hygiene, service_count=0,
        )
        res = solve_model(data, SOLVER_SECONDS)
        return data, res, pd.DataFrame([{
            "Service Workers": 0, "Cmax": res.cmax, "Status": res.status,
            "Feasible": res.feasible,
        }])

    options = service_worker_count_options(tasks, T, scenario, n_workers)
    if not options:
        lb = required_service_workers(tasks, T, scenario)
        data = make_problem_from_tasks(
            aircraft, tasks, n_workers, T, scenario, enforce_T,
            objective, follow_lag, hygiene, service_count=lb,
        )
        res = solve_model(data, SOLVER_SECONDS)
        return data, res, pd.DataFrame([{
            "Service Workers": lb, "Cmax": res.cmax, "Status": res.status,
            "Feasible": res.feasible,
        }])

    _, data, res, table = solve_best_variant(
        lambda svc: make_problem_from_tasks(
            aircraft, tasks, n_workers, T, scenario, enforce_T,
            objective, follow_lag, hygiene, service_count=svc,
        ),
        options,
        max_seconds=SOLVER_SECONDS,
    )
    table = table.rename(columns={"Variant": "Service Workers"})
    if data is None:
        data = make_problem_from_tasks(
            aircraft, tasks, n_workers, T, scenario, enforce_T,
            objective, follow_lag, hygiene, service_count=options[0],
        )
        res = solve_model(data, SOLVER_SECONDS)
    return data, res, table


def run(aircraft, n_workers, T, scenario="S1", enforce_T=True, **kw):
    data, res, _ = solve_scenario(aircraft, n_workers, T, scenario, enforce_T, **kw)
    return {
        "Aircraft": aircraft,
        "Scenario": scenario,
        "Workers": n_workers,
        "Cleaning Workers": len([w for w in data.workers if w != "DEICE1"]),
        "Deicing Resource": int("DEICE1" in data.workers),
        "Service Workers": data.service_worker_count,
        "Tasks": len(data.tasks),
        "T (min)": T,
        "Cmax": res.cmax,
        "Buffer": res.buffer,
        "Status": res.status,
        "Feasible": res.feasible,
        "Gap %": res.gap_pct,
        "Solve Time (s)": round(res.solve_time, 2),
    }


def lower_bound_start(aircraft, T, scenario, cleaning=DEFAULT_CLEANING_TYPE,
                      factor=DEFAULT_DURATION_FACTOR, extra_kinds=()):
    tasks = build_tasks_for_case(aircraft, scenario, cleaning, factor, extra_kinds)
    return minimum_total_workers_for_scenario(tasks, T, scenario)


def min_workers(aircraft, T, scenario="S1", m_max=30, **kw):
    m_min = lower_bound_start(aircraft, T, scenario,
                              kw.get("cleaning", DEFAULT_CLEANING_TYPE),
                              kw.get("factor", DEFAULT_DURATION_FACTOR),
                              kw.get("extra_kinds", ()))

    def solve_m(m):
        data, res, _ = solve_scenario(aircraft, m, T, scenario, True, **kw)
        return data, res

    m, res, table = find_min_workers(
        solve_m, m_min=m_min, m_max=m_max, max_seconds=SOLVER_SECONDS,
    )
    proven = False
    if m is not None and len(table[table.Feasible]):
        proven = bool(table[table.Feasible].iloc[0]["Minimum Proven"])
    return {
        "Aircraft": aircraft,
        "Scenario": scenario,
        "T (min)": T,
        "Smallest Feasible Found": m,
        "Minimum Proven": proven,
        "Cmax at Found": res.cmax if res else None,
    }


# ==================================================================
# Verification
# ==================================================================
def lagged_chain(durations, lag):
    """Minimum chain length implied by S_next>=S_prev+k and E_next>=E_prev+k."""
    start, end = 0, durations[0]
    for d in durations[1:]:
        start = max(start + lag, end + lag - d)
        end = start + d
    return end


def verification() -> pd.DataFrame:
    rows = []
    T_ONLY = dict(objective="Time Only")
    base_data, _, _ = solve_scenario("A320-200", 1, 60, "S1", enforce_T=False, **T_ONLY)
    tasks = base_data.tasks
    dur = {t.id: t.duration for t in tasks}
    zones = sorted({t.zone for t in tasks if t.zone.startswith("Z")})
    zone_kinds = [k for k in ZONE_TASK_ORDER if any(t.kind == k for t in tasks)]
    zone_durs = [dur[f"{k}{zones[0]}"] for k in zone_kinds]

    total = sum(dur.values())
    r = run("A320-200", 1, 60, "S1", enforce_T=False, **T_ONLY)
    rows.append({
        "Test": "V1 พนักงาน 1 คน",
        "Expected": f"Cmax = total workload = {total}",
        "Actual": r["Cmax"],
        "Pass": r["Cmax"] == total,
        "Purpose": "exact assignment + no worker overlap",
    })

    chain = lagged_chain(zone_durs, DEFAULT_FOLLOW_LAG)
    r = run("A320-200", 20, 60, "S1", **T_ONLY)
    rows.append({
        "Test": f"V2 many workers k={DEFAULT_FOLLOW_LAG}",
        "Expected": f"follow-lag critical chain = {chain}",
        "Actual": r["Cmax"],
        "Pass": r["Cmax"] == chain,
        "Purpose": "same-zone follow-lag",
    })

    r = run("A320-200", 2, 5, "S1", **T_ONLY)
    rows.append({
        "Test": "V3 impossible time limit",
        "Expected": "INFEASIBLE",
        "Actual": r["Status"],
        "Pass": r["Status"] == "INFEASIBLE",
        "Purpose": "Cmax <= T",
    })

    data, res, _ = solve_scenario("A320-200", 4, 30, "S2", objective="Time Only")
    ok_overlap = ok_prec = ok_cap = ok_hyg = False
    if res.feasible:
        sch = res.schedule.set_index("Task")
        tk = {t.id: t for t in data.tasks}
        ok_overlap = all(
            (g.sort_values("Start").Start.values[1:] >= g.sort_values("Start").End.values[:-1]).all()
            for _, g in res.schedule.groupby("Worker")
        )
        ok_prec = True
        for j, k in data.P:
            same = tk[j].zone == tk[k].zone and tk[j].zone.startswith("Z")
            if same:
                ok_prec &= sch.loc[k, "Start"] >= sch.loc[j, "Start"] + data.follow_lag
                ok_prec &= sch.loc[k, "End"] >= sch.loc[j, "End"] + data.follow_lag
            else:
                ok_prec &= sch.loc[j, "End"] <= sch.loc[k, "Start"]
        ok_cap = all(data.a[(row.Worker, row.Task)] == 1 for _, row in res.schedule.iterrows())
        ok_hyg = True
        for _, g in res.schedule.groupby("Worker"):
            G, L = g[g.Kind == "GAL"], g[g.Kind == "LAV"]
            if len(G) and len(L):
                ok_hyg &= G.End.max() <= L.Start.min()
    rows.append({
        "Test": "V4 direct schedule audit S2",
        "Expected": "no overlap / precedence / capability / hygiene",
        "Actual": f"overlap={not ok_overlap}, prec={ok_prec}, cap={ok_cap}, hyg={ok_hyg}",
        "Pass": bool(ok_overlap and ok_prec and ok_cap and ok_hyg),
        "Purpose": "audit returned schedule",
    })

    k_big = max(zone_durs)
    expected_big = lagged_chain(zone_durs, k_big)
    r = run("A320-200", 20, 60, "S1", follow_lag=k_big, **T_ONLY)
    rows.append({
        "Test": f"V5 follow-lag k={k_big}",
        "Expected": expected_big,
        "Actual": r["Cmax"],
        "Pass": r["Cmax"] == expected_big,
        "Purpose": "follow-lag equations at another k",
    })

    t_h = [
        Task("A1", "LAV", "LAV", "Lavatory 1", 2),
        Task("B1", "GAL", "GAL", "Galley 1", 3),
    ]
    d1 = ProblemData(
        "test", ["M1"], t_h, 8,
        {("M1", t.id): 1 for t in t_h},
        P=[], hygiene_galley_first=True, objective_mode="Time Only",
    )
    one = solve_model(d1, 5)
    order = False
    if one.feasible:
        q = one.schedule.set_index("Task")
        order = q.loc["B1", "End"] <= q.loc["A1", "Start"]
    rows.append({
        "Test": "V6 hygiene GAL before LAV",
        "Expected": "B1 finishes before A1 for same worker",
        "Actual": order,
        "Pass": bool(order),
        "Purpose": "conditional hygiene scenario",
    })

    # Structural verification of Service-worker sizing: LB is not treated as fixed.
    ts = build_tasks_for_case("A330-300", "S2")
    lb = required_service_workers(ts, 30, "S2")
    opts = service_worker_count_options(ts, 30, "S2", 7)
    rows.append({
        "Test": "V7 Service worker LB is expandable",
        "Expected": f"options start at LB={lb} and include larger values",
        "Actual": str(opts),
        "Pass": bool(opts and opts[0] == lb and len(opts) > 1),
        "Purpose": "avoid fixing workload lower bound as actual staffing",
    })

    df = pd.DataFrame(rows)
    print("\n[Verification — mathematical implementation, not field validation]")
    print(df[["Test", "Actual", "Pass"]].to_string(index=False))
    return df


# ==================================================================
# Experiments
# ==================================================================
NARROW = ["ATR72-600", "CRJ900", "A320-200", "B737-800"]


def exp1_workers_vs_cmax():
    rows = []
    for s in SCENARIO_LIST:
        start = lower_bound_start("A320-200", 60, s)
        for m in range(start, 9):
            rows.append(run("A320-200", m, 60, s, enforce_T=False))
    return pd.DataFrame(rows)


def exp2_min_workers_aircraft(T=30):
    return pd.DataFrame([min_workers(ac, T, "S1") for ac in AIRCRAFT_LIBRARY])


def exp3_min_workers_scenario(T=30):
    return pd.DataFrame([min_workers(ac, T, s) for ac in NARROW for s in SCENARIO_LIST])


def exp4_turnaround(Ts=(15, 20, 25, 30, 40)):
    return pd.DataFrame([min_workers("A320-200", T, s) for s in SCENARIO_LIST for T in Ts])


def exp5_sensitivity(T=30, m=4):
    rows = []
    for f in DURATION_FACTORS:
        r = run("A320-200", m, 60, "S1", enforce_T=False, factor=f)
        mw = min_workers("A320-200", T, "S1", factor=f)
        rows.append({
            "Duration Factor": f,
            f"Cmax ({m} workers)": r["Cmax"],
            f"Smallest Feasible Found (T={T})": mw["Smallest Feasible Found"],
            "Minimum Proven": mw["Minimum Proven"],
        })
    return pd.DataFrame(rows)


def exp6_cleaning_type():
    cases = [
        ("Quick Transit", QUICK_TRANSIT, (), 30),
        ("Layover", LAYOVER, (), 60),
    ]
    rows = []
    for label, ct, extra, T in cases:
        r = run("A320-200", 4, 90, "S1", enforce_T=False, cleaning=ct, extra_kinds=extra)
        mw = min_workers("A320-200", T, "S1", cleaning=ct, extra_kinds=extra)
        rows.append({
            "Cleaning": label,
            "Tasks": r["Tasks"],
            "Cmax (4 workers)": r["Cmax"],
            "T (min)": T,
            "Smallest Feasible Found": mw["Smallest Feasible Found"],
            "Minimum Proven": mw["Minimum Proven"],
        })
    return pd.DataFrame(rows)


def exp7_follow_lag():
    """Sensitivity of the explicit project assumption k."""
    rows = []
    for k in (0, 1, 2):
        r = run("A320-200", 4, 60, "S1", enforce_T=False, follow_lag=k)
        rows.append({"Follow lag k (min)": k, "Cmax": r["Cmax"], "Status": r["Status"]})
    return pd.DataFrame(rows)


def plot_all(e1, e2, e3, e4, e5, e7):
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for s in SCENARIO_LIST:
        d = e1[e1.Scenario == s]
        ax.plot(d.Workers, d.Cmax, marker="o", label=s)
    ax.set(xlabel="Number of modeled resources", ylabel="Cmax (min)",
           title="A320-200 Quick Transit: resources vs Cmax")
    ax.grid(alpha=.3); ax.legend(); fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig1_workers_vs_cmax.png", dpi=200)

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar(e2.Aircraft, e2["Smallest Feasible Found"])
    ax.set(ylabel="Smallest feasible workforce found", title="Quick Transit, S1, T=30")
    plt.xticks(rotation=30); fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig2_min_workers_aircraft.png", dpi=200)

    fig, ax = plt.subplots(figsize=(8, 4.2))
    piv = e3.pivot(index="Aircraft", columns="Scenario", values="Smallest Feasible Found").reindex(NARROW)
    piv.plot(kind="bar", ax=ax)
    ax.set(ylabel="Smallest feasible workforce found", title="Scenario workforce comparison (T=30)")
    plt.xticks(rotation=0); fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig3_min_workers_scenario.png", dpi=200)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    for s in SCENARIO_LIST:
        d = e4[e4.Scenario == s]
        ax.plot(d["T (min)"], d["Smallest Feasible Found"], marker="o", label=s)
    ax.set(xlabel="Turnaround / model window T (min)", ylabel="Smallest feasible workforce found",
           title="A320-200: time window vs workforce")
    ax.grid(alpha=.3); ax.legend(); fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig4_turnaround.png", dpi=200)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.plot(e5["Duration Factor"], e5.iloc[:, 1], marker="o")
    ax.set(xlabel="Task-duration factor", ylabel="Cmax (min)",
           title="Sensitivity to assumed task durations (A320, S1)")
    ax.grid(alpha=.3); fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig5_sensitivity.png", dpi=200)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.plot(e7["Follow lag k (min)"], e7["Cmax"], marker="o")
    ax.set(xlabel="Follow lag k (min)", ylabel="Cmax (min)",
           title="Sensitivity to follow-lag assumption (A320, S1, 4 workers)")
    ax.grid(alpha=.3); fig.tight_layout()
    fig.savefig(RESULT_DIR / "fig6_follow_lag.png", dpi=200)
    plt.close("all")


def main():
    t0 = time.time()
    v = verification()
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        return

    results = {"Verification": v}
    experiments = [
        ("E1 Workers vs Cmax", exp1_workers_vs_cmax),
        ("E2 Min Workers Aircraft", exp2_min_workers_aircraft),
        ("E3 Min Workers Scenario", exp3_min_workers_scenario),
        ("E4 Turnaround", exp4_turnaround),
        ("E5 Duration Sensitivity", exp5_sensitivity),
        ("E6 Cleaning Type", exp6_cleaning_type),
        ("E7 Follow Lag Sensitivity", exp7_follow_lag),
    ]
    for name, fn in experiments:
        print(f"\n[{name}] ...", flush=True)
        results[name] = fn()
        print(results[name].to_string(index=False))

    with pd.ExcelWriter(RESULT_DIR / "experiment_results.xlsx", engine="openpyxl") as w:
        for name, df in results.items():
            df.to_excel(w, sheet_name=name[:31], index=False)

    plot_all(
        results["E1 Workers vs Cmax"],
        results["E2 Min Workers Aircraft"],
        results["E3 Min Workers Scenario"],
        results["E4 Turnaround"],
        results["E5 Duration Sensitivity"],
        results["E7 Follow Lag Sensitivity"],
    )
    print(f"\nเสร็จใน {time.time() - t0:.0f} วินาที · ผลอยู่ใน {RESULT_DIR}")


if __name__ == "__main__":
    main()
