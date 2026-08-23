from aircraft_data import build_tasks, build_workers, build_capability, build_precedence, build_blocking
from solver import ProblemData, solve_model

tasks = build_tasks("A320-200")
workers = build_workers(4)

data = ProblemData(
    aircraft="A320-200", workers=workers, tasks=tasks, T=30,
    a=build_capability(workers, tasks),
    P=build_precedence(tasks),
    B=build_blocking(tasks),
)

result = solve_model(data)
print("จำนวนงาน :", len(tasks))
print("สถานะ    :", result.status)
print("Cmax     :", result.cmax, "นาที")
print(result.schedule.head(10))
