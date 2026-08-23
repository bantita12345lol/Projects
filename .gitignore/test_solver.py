from aircraft_data import *
from solver import ProblemData, solve_model

tasks = build_tasks("A320-200")
workers = build_workers(4)

def run(name, P=None, B=None):
    d = ProblemData("A320-200", workers, tasks, 30,
                    build_capability(workers, tasks),
                    P if P is not None else build_precedence(tasks),
                    B if B is not None else build_blocking(tasks),
                    enforce_time_limit=False)
    r = solve_model(d, 20)
    print(f"{name:35} Cmax = {r.cmax}")

run("ครบทุกข้อจำกัด")
run("ปิดการกีดขวาง (B ว่าง)", B=[])
run("ปิดลำดับก่อน-หลัง (P ว่าง)", P=[])
run("ปิดทั้ง P และ B", P=[], B=[])