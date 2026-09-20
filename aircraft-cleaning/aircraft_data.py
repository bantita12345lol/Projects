"""
aircraft_data.py
------------------------------------------------------------------
ข้อมูลอากาศยาน งานทำความสะอาด และ Scenario สำหรับตัวแบบ
Aircraft Cleaning Optimization

ข้อสรุปการออกแบบโมเดล (Final)
- D1-B: งานถัดไปในโซนเดียวกันเริ่มตามหลังงานก่อนอย่างน้อย k นาที
        ค่าเริ่มต้น k = 1 นาที และปรับได้บนเว็บ
- D2-A: S1 Flexible / S2 Zone-based / S3 De-icing Extension ท้ายสุด
- D3-C: workload/T_clean ใช้เป็น Lower Bound ของ Service workers เท่านั้น
        การแก้ปัญหาจะลองจำนวน Service workers ที่เป็นไปได้และเลือกตารางที่ดีที่สุด
- D4-A: ถ้าคนเดียวกันทำ Galley และ Lavatory ต้องทำ Galley ก่อน Lavatory
- D5-A: S3 ใช้พนักงาน De-icing เฉพาะ และเริ่มหลัง Cleaning ทุกงานเสร็จ
- สภาพอากาศ: ใช้ Normal/Clear เป็นเงื่อนไขฐานคงที่ ไม่เป็นตัวแปรของโมเดล
- ข้อมูลเวลางาน: ใช้ค่าที่ปรับเทียบจากงานวิจัย + สมมติฐานของโครงงาน
  เพื่อสร้าง benchmark เมื่อไม่มีข้อมูลภาคสนาม จึงเรียกว่า literature-calibrated assumptions
  ไม่ใช่ external validation
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple

# ==================================================================
# 1) Aircraft library
# ==================================================================
AIRCRAFT_LIBRARY: Dict[str, dict] = {
    "ATR72-600": {
        "category": "Regional Aircraft", "seats": 70, "seats_abreast": 4,
        "zones": [("Z1", "Zone 1", 70)],
        "n_lav": 1, "n_gal": 1,
        "source": "Study configuration assumption: 1 Work Unit, Z1 = 70 seats",
    },
    "CRJ900": {
        "category": "Regional Aircraft", "seats": 90, "seats_abreast": 4,
        "zones": [("Z1", "Zone 1", 90)],
        "n_lav": 1, "n_gal": 1,
        "source": "Study configuration assumption: 1 Work Unit, Z1 = 90 seats",
    },
    "A320-200": {
        "category": "Narrow-body Aircraft", "seats": 180, "seats_abreast": 6,
        "zones": [("Z1", "Zone 1", 90), ("Z2", "Zone 2", 90)],
        "n_lav": 3, "n_gal": 2,
        "source": "180-seat single-class A320 as used in the cleaning study of Schultz et al.; Work Units 90/90",
    },
    "B737-800": {
        "category": "Narrow-body Aircraft", "seats": 189, "seats_abreast": 6,
        "zones": [("Z1", "Zone 1", 63), ("Z2", "Zone 2", 63), ("Z3", "Zone 3", 63)],
        "n_lav": 3, "n_gal": 2,
        "source": "Study configuration assumption: balanced Work Units 63/63/63",
    },
    "A330-300": {
        "category": "Wide-body Aircraft", "seats": 305, "seats_abreast": 8,
        "zones": [("Z1", "Zone 1", 77), ("Z2", "Zone 2", 76),
                  ("Z3", "Zone 3", 76), ("Z4", "Zone 4", 76)],
        "n_lav": 9, "n_gal": 8,
        "source": "Study configuration assumption: balanced Work Units 77/76/76/76",
    },
    "B787-9": {
        "category": "Wide-body Aircraft", "seats": 298, "seats_abreast": 9,
        "zones": [("Z1", "Zone 1", 75), ("Z2", "Zone 2", 75),
                  ("Z3", "Zone 3", 74), ("Z4", "Zone 4", 74)],
        "n_lav": 9, "n_gal": 8,
        "source": "Study configuration assumption: balanced Work Units 75/75/74/74",
    },
    "A350-900": {
        "category": "Large Aircraft", "seats": 321, "seats_abreast": 9,
        "zones": [("Z1", "Zone 1", 81), ("Z2", "Zone 2", 80),
                  ("Z3", "Zone 3", 80), ("Z4", "Zone 4", 80)],
        "n_lav": 8, "n_gal": 4,
        "source": "Study configuration assumption: Work Units 81/80/80/80",
    },
    "B777-300ER": {
        "category": "Large Aircraft", "seats": 348, "seats_abreast": 10,
        "zones": [("Z1", "Zone 1", 70), ("Z2", "Zone 2", 70),
                  ("Z3", "Zone 3", 70), ("Z4", "Zone 4", 69),
                  ("Z5", "Zone 5", 69)],
        "n_lav": 10, "n_gal": 5,
        "source": "Study configuration assumption: Work Units 70/70/70/69/69",
    },
    "A380-800": {
        "category": "Very Large Aircraft", "seats": 507, "seats_abreast": 10,
        "zones": [("Z1", "Zone 1", 73), ("Z2", "Zone 2", 73),
                  ("Z3", "Zone 3", 73), ("Z4", "Zone 4", 72),
                  ("Z5", "Zone 5", 72), ("Z6", "Zone 6", 72),
                  ("Z7", "Zone 7", 72)],
        "n_lav": 14, "n_gal": 6,
        "source": "Study configuration assumption: Work Units 73/73/73/72/72/72/72",
    },
}

DEFAULT_AIRCRAFT = "A320-200"

# ==================================================================
# 2) Tasks / cleaning types
# ==================================================================
TASK_KIND_LABEL = {
    "C1": "เก็บขยะ",
    "C2": "ดูดฝุ่น",
    "C3": "จัดความเรียบร้อย",
    "D": "เช็ดช่องเก็บของ/โต๊ะพับ/ที่วางแขน",
    "E": "ทำความสะอาดพื้นผิวเพิ่มเติม",
    "F": "จัดผ้าห่ม/หมอน/หูฟัง",
    "LAV": "ห้องน้ำ",
    "GAL": "ครัว",
    "OVH": "ช่องเก็บสัมภาระเหนือศีรษะ",
    "FD": "ห้องนักบิน",
    "CR": "ห้องพักลูกเรือ",
    "RC": "ตรวจความสะอาดซ้ำ",
    "DEI": "De-icing",
}

SCOPE_MAPPING = [
    ("4.1.1", "การดูดฝุ่น", "C2"),
    ("4.1.2", "การเก็บขยะ", "C1"),
    ("4.1.3", "เช็ดบริเวณที่นั่ง", "D"),
    ("4.1.4", "การทำความสะอาดห้องน้ำ", "A1-An"),
    ("4.1.5", "การทำความสะอาดพื้นที่ครัว", "B1-Bm"),
    ("4.1.6", "การจัดความเรียบร้อยห้องโดยสาร", "C3"),
    ("4.1.7", "การทำความสะอาดพื้นผิวเพิ่มเติม", "E"),
    ("4.1.8", "การจัดเตรียมผ้าห่ม/หมอน/หูฟัง", "F"),
    ("4.2.1", "ห้องนักบินและห้องพักลูกเรือ", "FD1, CR1"),
    ("4.2.2", "ช่องเก็บสัมภาระเหนือศีรษะ", "OVH"),
    ("4.2.4", "การตรวจสอบความสะอาดซ้ำ", "RC1, RC2"),
    ("S3", "Aircraft De-icing (กรณีขยาย)", "DEI1"),
]

QUICK_TRANSIT = "Quick Transit"
LAYOVER = "Layover"

# Quick Transit = งานหลักตามขอบเขตข้อ 4.1.1-4.1.6 (รวม 4.1.3 เช็ดบริเวณที่นั่ง)
# งาน 4.1.7 (ทำเมื่อมีเวลาเหลือ) และ 4.1.8 (เฉพาะบางสายการบิน) เป็นงานไม่บังคับ
# จึงไม่รวมใน Quick Transit และรวมไว้ใน Layover ซึ่งมีเวลาจอดนานพอ
CLEANING_TYPES: Dict[str, List[str]] = {
    QUICK_TRANSIT: ["C1", "D", "C2", "C3", "LAV", "GAL"],
    LAYOVER: ["C1", "OVH", "D", "C2", "C3", "E", "F",
              "LAV", "GAL", "FD", "CR", "RC"],
}

DEFAULT_CLEANING_TYPE = QUICK_TRANSIT
DEFAULT_DURATION_FACTOR = 1.00
DURATION_FACTORS = (0.80, 0.90, 1.00, 1.10, 1.20)
DEFAULT_FOLLOW_LAG = 1

# ==================================================================
# 3) Literature-calibrated task-duration assumptions
# ==================================================================
# เมื่อไม่มีข้อมูลภาคสนาม โครงงานใช้ benchmark จากงานวิจัย cleaning-process:
#   seat-row item removal ≈ 3 s/row
#   seat-row cleaning       ≈ 12 s/row
#   seat-row restocking     ≈ 6 s/row
#   seat-row vacuuming      ≈ 10 s/row
#   lavatory cleaning       ≈ 115-120 s/unit
#   galley cleaning         ≈ 100-149 s/unit
#   cockpit cleaning        ≈ 60 s/unit
#
# สำหรับงาน Cabin ระดับ Zone จะประมาณจำนวนแถวจาก seats/seats_abreast แล้วบวก
# allowance 0.5 นาทีต่อ task-zone สำหรับการเตรียมอุปกรณ์/การเคลื่อนที่ระยะสั้น
# ก่อนปัดขึ้นเป็นนาทีเต็ม. ค่านี้เป็น calibration assumption ไม่ใช่ field validation.

ZONE_SETUP_ALLOWANCE_MIN = 0.5
ZONE_TASK_SECONDS_PER_ROW = {
    "C1": 3,   # item/trash removal proxy
    "C2": 10,  # vacuuming
    "C3": 6,   # restocking / cabin appearance proxy
    "D": 12,   # seat-area cleaning
    "E": 20,   # conservative additional-surface/disinfection proxy
    "F": 6,    # amenity/restocking proxy
    "OVH": 8,  # project assumption; no direct row-level benchmark available
}

FIXED_DURATION = {
    "LAV": 2,   # 115-120 s -> 2 min
    "GAL": 3,   # 149 s benchmark -> conservative 3 min
    "FD": 2,    # 60 s cleaning + access/setup allowance
    "CR": 3,    # project assumption
    "RC": 2,    # project assumption
}

# Winter-extension benchmark. Airport master-plan benchmarking reports average
# deicing-pad occupancy of about 15 min for regional jets, 19 min narrow-body,
# and 22 min wide-body. A380 is conservatively set at 25 min as a project assumption.
DEICING_DURATION_BY_AIRCRAFT = {
    "ATR72-600": 15, "CRJ900": 15,
    "A320-200": 19, "B737-800": 19,
    "A330-300": 22, "B787-9": 22, "A350-900": 22, "B777-300ER": 22,
    "A380-800": 25,
}

MODEL_ASSUMPTIONS = [
    ("สภาพอากาศ", "ปกติ (Normal/Clear) คงที่", "ขอบเขตโครงงาน"),
    ("ความละเอียดของเวลา", "ช่องละ 1 นาที", "สมมติฐานของตัวแบบ"),
    ("เวลางานในห้องโดยสาร", "วินาทีต่อแถว × จำนวนแถว + 0.5 นาทีเตรียมงาน/เดิน แล้วปัดขึ้นเป็นนาที", "ปรับเทียบจาก Schultz et al. (2020)"),
    ("ห้องน้ำ", "2 นาทีต่อห้อง", "งานวิจัยรายงาน ~115–120 วินาที"),
    ("ครัว", "3 นาทีต่อจุด", "งานวิจัยรายงาน 100–149 วินาที (ใช้ค่าสูง)"),
    ("ระยะไล่ตาม k", "1 นาที (ปรับได้)", "สมมติฐานของโครงงาน · ทดสอบความไว k = 0, 1, 2"),
    ("ขนาดทีมห้องน้ำ/ครัว (S2)", "เริ่มจาก ⌈ภาระงาน ÷ เวลา⌉ แล้วลองจำนวนที่มากขึ้น เลือกแบบที่เสร็จเร็วที่สุด", "นโยบายของตัวแบบ"),
    ("สุขอนามัย", "คนเดียวกันทำทั้งครัวและห้องน้ำ ต้องทำครัวก่อน", "สมมติฐานของโครงงาน"),
    ("เวลาเดินระหว่างจุด", "รวมอยู่ใน 0.5 นาทีเตรียมงานต่อโซน", "ข้อจำกัดของตัวแบบ"),
    ("De-icing", "เฉพาะ S3 ทำหลังทำความสะอาดเสร็จทุกงาน", "กรณีขยายผล"),
    ("ข้อมูลอากาศยาน", "จำนวนที่นั่ง ห้องน้ำ ครัว เป็นค่าสำหรับการศึกษา", "สมมติฐานของโครงงาน"),
]

ZONE_TASK_ORDER = ["C1", "OVH", "D", "C2", "C3", "E", "F"]
SERVICE_TASK_KINDS = ("LAV", "GAL")
SERVICE_ZONES = ("LAV", "GAL", "CREW", "CHECK")


@dataclass
class Task:
    id: str
    kind: str
    zone: str
    name: str
    duration: int

    def to_dict(self) -> dict:
        return asdict(self)


def _scaled_minutes(value: float, factor: float) -> int:
    """Discrete 1-minute model; round to nearest minute and keep >=1."""
    return max(1, int(round(value * factor)))


def estimate_zone_duration(kind: str, seats: int, seats_abreast: int,
                           factor: float = 1.0) -> int:
    """Estimate zone duration from an approximate number of seat rows."""
    rows = max(1, math.ceil(seats / max(1, seats_abreast)))
    raw_min = ZONE_SETUP_ALLOWANCE_MIN + rows * ZONE_TASK_SECONDS_PER_ROW[kind] / 60.0
    # ceil baseline to avoid understating a task because the model uses whole minutes
    baseline = max(1, math.ceil(raw_min))
    return max(1, _scaled_minutes(baseline, factor))


def fixed_duration(kind: str, factor: float = 1.0) -> int:
    return _scaled_minutes(FIXED_DURATION[kind], factor)


def cleaning_kinds(cleaning_type: str) -> List[str]:
    return list(CLEANING_TYPES[cleaning_type])


def build_tasks(aircraft: str,
                cleaning_kinds_: List[str] | None = None,
                duration_factor: float = DEFAULT_DURATION_FACTOR,
                include_deicing: bool = False) -> List[Task]:
    spec = AIRCRAFT_LIBRARY[aircraft]
    kinds = set(cleaning_kinds_ or CLEANING_TYPES[DEFAULT_CLEANING_TYPE])
    tasks: List[Task] = []

    for zone_id, zone_name, seats in spec["zones"]:
        for kind in ZONE_TASK_ORDER:
            if kind not in kinds:
                continue
            tasks.append(Task(
                id=f"{kind}{zone_id}", kind=kind, zone=zone_id,
                name=f"{TASK_KIND_LABEL[kind]} - {zone_name.replace('Zone', 'โซน')}",
                duration=estimate_zone_duration(kind, seats, spec.get("seats_abreast", 6), duration_factor),
            ))

    if "LAV" in kinds:
        for n in range(1, spec["n_lav"] + 1):
            tasks.append(Task(f"A{n}", "LAV", "LAV", f"ห้องน้ำ {n}",
                              fixed_duration("LAV", duration_factor)))
    if "GAL" in kinds:
        for n in range(1, spec["n_gal"] + 1):
            tasks.append(Task(f"B{n}", "GAL", "GAL", f"ครัว {n}",
                              fixed_duration("GAL", duration_factor)))
    if "FD" in kinds:
        tasks.append(Task("FD1", "FD", "CREW", "ห้องนักบิน",
                          fixed_duration("FD", duration_factor)))
    if "CR" in kinds:
        tasks.append(Task("CR1", "CR", "CREW", "ห้องพักลูกเรือ",
                          fixed_duration("CR", duration_factor)))
    if "RC" in kinds:
        if "LAV" in kinds:
            tasks.append(Task("RC1", "RC", "CHECK", "ตรวจซ้ำห้องน้ำ",
                              fixed_duration("RC", duration_factor)))
        if "GAL" in kinds:
            tasks.append(Task("RC2", "RC", "CHECK", "ตรวจซ้ำครัว",
                              fixed_duration("RC", duration_factor)))

    if include_deicing:
        # De-icing duration is a scenario-specific duration and is not scaled
        # by the cleaning-duration sensitivity factor.
        tasks.append(Task("DEI1", "DEI", "DEICE", "De-icing",
                          DEICING_DURATION_BY_AIRCRAFT.get(aircraft, 15)))
    return tasks


# ==================================================================
# 4) Precedence set P
# ==================================================================
def build_precedence(tasks: List[Task]) -> List[Tuple[str, str]]:
    """
    Returns precedence pairs.

    - Cabin tasks within the same zone are interpreted by solver.py as
      a follow-lag pipeline: S_k >= S_j + k and E_k >= E_j + k.
    - Cross-area pairs use strict finish-to-start precedence E_j <= S_k.
    - Recheck tasks occur after the corresponding service tasks.
    - If DEI1 exists, every non-DEI task must finish before DEI1 starts.
    """
    ids = {t.id for t in tasks}
    zones = sorted({t.zone for t in tasks if t.zone.startswith("Z")})
    P: List[Tuple[str, str]] = []

    for z in zones:
        present = [f"{kind}{z}" for kind in ZONE_TASK_ORDER if f"{kind}{z}" in ids]
        P.extend(zip(present, present[1:]))

    if "RC1" in ids:
        P.extend((t.id, "RC1") for t in tasks if t.kind == "LAV")
    if "RC2" in ids:
        P.extend((t.id, "RC2") for t in tasks if t.kind == "GAL")

    # S3: De-icing is the final modeled activity.
    if "DEI1" in ids:
        for t in tasks:
            if t.id != "DEI1":
                P.append((t.id, "DEI1"))

    return list(dict.fromkeys(P))


# ==================================================================
# 5) Scenario / workforce capability
# ==================================================================
SCENARIOS = {
    "S1": "ยืดหยุ่น — ทุกคนทำได้ทุกงาน",
    "S2": "แบ่งหน้าที่ — ทีมห้องน้ำ/ครัว + ทีมห้องโดยสารประจำโซน",
    "S3": "S2 + De-icing หลังทำความสะอาดเสร็จ (กรณีฤดูหนาว)",
}


def scenario_settings(scenario: str) -> dict:
    if scenario == "S1":
        return {"zone_based": False, "include_deicing": False}
    if scenario == "S2":
        return {"zone_based": True, "include_deicing": False}
    if scenario == "S3":
        return {"zone_based": True, "include_deicing": True}
    raise KeyError(f"Unknown scenario: {scenario}")


def minimum_total_workers_for_structure(scenario: str) -> int:
    """ขั้นต่ำเชิงโครงสร้างเท่านั้น; ยังไม่คิด workload ของ LAV/GAL."""
    if scenario == "S1":
        return 1
    if scenario == "S2":
        return 2  # อย่างน้อย 1 Service + 1 Cabin
    if scenario == "S3":
        return 3  # อย่างน้อย 1 Service + 1 Cabin + 1 De-icing
    return 1


def service_workload_minutes(tasks: List[Task]) -> int:
    """ภาระงาน Lavatory + Galley รวม (นาที)."""
    return sum(t.duration for t in tasks if t.kind in SERVICE_TASK_KINDS)


def deicing_duration_minutes(tasks: List[Task]) -> int:
    """ระยะเวลา De-icing รวมของชุดงาน (ปกติมี DEI1 งานเดียว)."""
    return sum(t.duration for t in tasks if t.kind == "DEI")


def cleaning_time_window(tasks: List[Task], T: int, scenario: str) -> int:
    """
    เวลาที่ทีม Cleaning มีจริงก่อนกิจกรรมสุดท้ายของ Scenario.

    S1/S2: T_clean = T
    S3:    T_clean = T - d_DEI เพราะ De-icing ต้องทำหลัง Cleaning ทั้งหมด
    """
    if scenario_settings(scenario)["include_deicing"]:
        return int(T) - deicing_duration_minutes(tasks)
    return int(T)


def required_service_workers(tasks: List[Task], T: int, scenario: str) -> int:
    """
    D3-C: จำนวน Service workers ขั้นต่ำคำนวณจาก workload จริง ไม่ใช้เลข 25 นาที.

        m_service = ceil(W_service / T_clean)

    ถ้าไม่มีงาน LAV/GAL คืน 0. หาก T_clean <= 0 จะคืนจำนวนงาน Service
    เป็นค่าที่สูงพอสำหรับ capability; อย่างไรก็ดีโมเดลจะยัง INFEASIBLE เพราะ
    ไม่มีช่วงเวลาสำหรับ Cleaning ก่อน De-icing.
    """
    if not scenario_settings(scenario)["zone_based"]:
        return 0
    work = service_workload_minutes(tasks)
    if work <= 0:
        return 0
    window = cleaning_time_window(tasks, T, scenario)
    if window <= 0:
        return max(1, sum(1 for t in tasks if t.kind in SERVICE_TASK_KINDS))
    return max(1, math.ceil(work / window))


def service_worker_count_options(tasks: List[Task], T: int, scenario: str,
                                 n_workers_total: int) -> List[int]:
    """
    Candidate Service-team sizes for S2/S3.

    workload/T_clean provides only a LOWER BOUND.  When more cleaning workers
    are available, the outer search may allocate additional workers to Service
    if that reduces Cmax.  At least one Cabin worker is preserved whenever
    cabin-zone tasks exist.
    """
    cfg = scenario_settings(scenario)
    if not cfg["zone_based"]:
        return [0]

    extra_deice = 1 if cfg["include_deicing"] else 0
    n_cleaning = max(0, int(n_workers_total) - extra_deice)
    if n_cleaning <= 0:
        return []

    lb = required_service_workers(tasks, T, scenario)
    has_cabin = any(t.zone.startswith("Z") and t.kind != "DEI" for t in tasks)
    min_cabin = 1 if has_cabin else 0
    max_service = n_cleaning - min_cabin
    if max_service < lb:
        return []
    return list(range(lb, max_service + 1))


def minimum_total_workers_for_policy(tasks: List[Task], T: int, scenario: str) -> int:
    """
    Lower bound ที่สอดคล้องกับนโยบาย S1-S3.

    S1: ทุกคนยืดหยุ่น -> ceil(W_clean/T)

    S2/S3: Service workers ถูกแยกจาก Cabin workers จึงคำนวณเป็นสองก้อน
        m_service = ceil(W_service/T_clean)
        m_cabin   = ceil(W_cabin/T_clean)
        m_total   = m_service + m_cabin (+1 DEICE_TEAM ใน S3)

    เป็นเพียงจุดเริ่มค้นหา; precedence, follow-lag, hygiene และการแบ่งโซน
    อาจทำให้จำนวนที่ต้องใช้จริงสูงกว่านี้.
    """
    cfg = scenario_settings(scenario)
    normal = [t for t in tasks if t.kind != "DEI"]
    if not normal:
        return 1 if cfg["include_deicing"] else 0

    window = cleaning_time_window(tasks, T, scenario)
    if window <= 0:
        return minimum_total_workers_for_structure(scenario)

    total_clean = sum(t.duration for t in normal)
    extra_deice = 1 if cfg["include_deicing"] else 0

    if not cfg["zone_based"]:
        return max(1, math.ceil(total_clean / window)) + extra_deice

    service_work = service_workload_minutes(normal)
    cabin_work = sum(t.duration for t in normal if t.zone.startswith("Z"))
    # งาน CREW/CHECK ที่ไม่ใช่ LAV/GAL เปิดให้ cleaner ทุกคนทำได้ จึงไม่ล็อกไว้ในก้อน Service
    flexible_other = total_clean - service_work - cabin_work

    m_service = math.ceil(service_work / window) if service_work > 0 else 0
    m_cabin = math.ceil(cabin_work / window) if cabin_work > 0 else 0

    # ต้องมี Cabin worker อย่างน้อย 1 คนถ้ามีงาน Cabin; งาน flexible_other
    # ไม่เพิ่มขั้นต่ำเฉพาะกลุ่ม เพราะสามารถกระจายให้ Cleaner ที่มีอยู่ได้.
    if cabin_work > 0:
        m_cabin = max(1, m_cabin)
    if service_work > 0:
        m_service = max(1, m_service)

    cleaning_lb = m_service + m_cabin
    # กรณีมีแต่งาน flexible อื่น ๆ ให้มี cleaner อย่างน้อย 1 คน
    if cleaning_lb == 0 and flexible_other > 0:
        cleaning_lb = 1

    return max(minimum_total_workers_for_structure(scenario), cleaning_lb + extra_deice)


def build_scenario_workers(n_workers_total: int, scenario: str) -> Tuple[List[str], str | None]:
    """
    n_workers_total = จำนวนพนักงานรวมที่ผู้ใช้กำหนด.

    S1/S2: M1..Mm
    S3:    M1..M(m-1) + DEICE_TEAM
    """
    if n_workers_total < 1:
        return [], None
    include_deicing = scenario_settings(scenario)["include_deicing"]
    n_cleaning = n_workers_total - (1 if include_deicing else 0)
    if n_cleaning < 0:
        n_cleaning = 0
    workers = [f"M{i+1}" for i in range(n_cleaning)]
    deicing_worker = None
    if include_deicing:
        deicing_worker = "DEICE_TEAM"
        workers.append(deicing_worker)
    return workers, deicing_worker


def build_capability(workers: List[str], tasks: List[Task],
                     zone_based: bool = False,
                     service_worker_count: int = 0,
                     dedicated_deicing_worker: str | None = None) -> Dict[Tuple[str, str], int]:
    """
    Capability matrix a_ij.

    S1 (zone_based=False)
      - Cleaning workers ทุกคนทำงาน Cleaning ทุกประเภทได้.

    S2/S3 (zone_based=True)
      - จำนวน Service workers ไม่ได้ล็อก 1 คน แต่รับค่าจาก
        required_service_workers(tasks, T, scenario).
      - Service workers รับผิดชอบเฉพาะ LAV/GAL.
      - Cabin workers ที่เหลือถูกกระจายเข้า Work Unit/Zone ให้สมดุล.
        ถ้ามีพนักงานมากกว่า Zone จะมีหลายคนต่อ Zone และทำงานไล่ตามกันด้วย k.
      - งานอื่นที่ไม่ใช่ Cabin และไม่ใช่ LAV/GAL เช่น Crew/Final Check
        เปิดให้ Cleaning workers ทุกคนทำได้.
      - DEICE_TEAM ทำเฉพาะ DEI และ Cleaner ไม่ทำ DEI.
    """
    a: Dict[Tuple[str, str], int] = {}

    cleaning_workers = [w for w in workers if w != dedicated_deicing_worker]

    # Dedicated De-icing worker แยกจากทีม Cleaning โดยสมบูรณ์
    if dedicated_deicing_worker is not None:
        for w in workers:
            for t in tasks:
                if w == dedicated_deicing_worker:
                    a[(w, t.id)] = 1 if t.kind == "DEI" else 0
                elif t.kind == "DEI":
                    a[(w, t.id)] = 0

    normal_tasks = [t for t in tasks if t.kind != "DEI"]

    if not zone_based:
        for w in cleaning_workers:
            for t in normal_tasks:
                a[(w, t.id)] = 1
        return a

    if not cleaning_workers:
        return a

    n_service = max(0, int(service_worker_count))
    n_service = min(n_service, len(cleaning_workers))
    service_workers = cleaning_workers[:n_service]
    cabin_workers = cleaning_workers[n_service:]
    zones = sorted({t.zone for t in normal_tasks if t.zone.startswith("Z")})

    # กระจาย Cabin workers เข้า Zone แบบสมดุล
    # workers < zones  -> 1 คนอาจรับหลาย Zone
    # workers > zones  -> หลายคนอยู่ Zone เดียวกันได้
    zone_owners: Dict[str, List[str]] = {z: [] for z in zones}
    if cabin_workers and zones:
        if len(cabin_workers) >= len(zones):
            for idx, w in enumerate(cabin_workers):
                zone_owners[zones[idx % len(zones)]].append(w)
        else:
            for idx, z in enumerate(zones):
                zone_owners[z].append(cabin_workers[idx % len(cabin_workers)])

    for w in cleaning_workers:
        for t in normal_tasks:
            if t.zone.startswith("Z"):
                a[(w, t.id)] = 1 if w in zone_owners.get(t.zone, []) else 0
            elif t.kind in SERVICE_TASK_KINDS:
                a[(w, t.id)] = 1 if w in service_workers else 0
            else:
                # Crew / Check / งาน non-cabin อื่น ๆ ให้ cleaner ทุกคนช่วยได้
                a[(w, t.id)] = 1

    return a
