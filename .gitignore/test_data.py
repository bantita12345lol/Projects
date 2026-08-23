from aircraft_data import *

AC = "A320-200"
tasks = build_tasks(AC)

print(f"เครื่องบิน : {AC}")
print(f"จำนวนงาน  : {len(tasks)}")
print(f"งานรวม    : {sum(t.duration for t in tasks)} นาที")
print("\nรายการงาน")
for t in tasks:
    print(f"  {t.id:8} {t.kind:4} {t.duration:3} นาที   {t.name}")
print(f"\nเซต P มี {len(build_precedence(tasks))} คู่")
print(f"เซต B มี {len(build_blocking(tasks))} คู่")