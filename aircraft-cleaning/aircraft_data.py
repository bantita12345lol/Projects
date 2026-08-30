"""
aircraft_data.py
------------------------------------------------------------------
คลังข้อมูลอากาศยาน ตัวสร้างรายการงาน และพารามิเตอร์สภาพอากาศ

ความครอบคลุมตามขอบเขตของโครงงาน
    ข้อ 3   อากาศยาน 5 กลุ่ม 9 แบบ
    ข้อ 4.1 การทำความสะอาดแบบจอดระยะสั้น (Quick Transit) งานย่อย 4.1.1 - 4.1.8
    ข้อ 4.2 การทำความสะอาดแบบจอดระยะยาว (Layover) งานย่อย 4.2.1 - 4.2.4

สมมติฐานของข้อมูลอากาศยาน
    ข้อมูลผังห้องโดยสารที่ใช้เป็นค่าประมาณตามขอบเขตข้อ 2 ของโครงงาน
    ซึ่งอนุญาตให้ประมาณค่าด้วยสมมติฐานที่เหมาะสมเมื่อไม่มีข้อมูลจริง
    หลักเกณฑ์ที่ใช้ประมาณมีดังนี้

    (1) จำนวนที่นั่ง ใช้ค่าผังมาตรฐานสองชั้นโดยสารที่ผู้ผลิตระบุ
    (2) การแบ่ง Work Unit ไม่แบ่งตาม Business/Economy class แต่ใช้ชื่อ Z1, Z2, Z3, ...
        โดยตั้งเป้าประมาณ 70-80 ที่นั่งต่อ Zone และกระจายจำนวนที่นั่งให้สมดุลที่สุด
        พร้อมคงจำนวนที่นั่งรวมของอากาศยานแต่ละรุ่นไว้เท่าเดิม
    (3) จำนวนห้องน้ำ ประมาณจากอัตราส่วนมาตรฐานอุตสาหกรรม 1 ห้องต่อผู้โดยสาร 35-50 คน
    (4) จำนวนตำแหน่งครัว ประมาณจากตำแหน่งประตูหลักของอากาศยานแต่ละแบบ
    (5) อากาศยาน A320-200 ใช้ข้อมูลจากผังห้องโดยสารจริงของสายการบิน
        จึงใช้เป็นกรณีอ้างอิงหลักในการทดลอง
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple

# ==================================================================
# 1) คลังข้อมูลอากาศยาน
# ==================================================================
# แนวทางการแบ่ง Work Unit / Zone สำหรับการทดลอง IE
#   - ไม่แบ่งตาม Business / Economy class
#   - ใช้ชื่อทั่วไป Z1, Z2, Z3, ... ตามลำดับพื้นที่ทำงาน
#   - เป้าหมายประมาณ 70-80 ที่นั่งต่อ Zone และกระจายให้สมดุลที่สุด
#   - คงจำนวนที่นั่งรวมของอากาศยานแต่ละรุ่นไว้เท่าเดิม
#
# หมายเหตุด้านคณิตศาสตร์:
#   จำนวนที่นั่งบางรุ่นไม่สามารถแบ่งให้ "ทุก Zone" อยู่ในช่วง 70-80 ที่นั่ง
#   ได้พอดีโดยยังรักษาจำนวนที่นั่งรวม เช่น CRJ900 (90), B737-800 (189),
#   A350-900 (321) และ B777-300ER (348) ดังนั้นใช้ค่าที่ใกล้ช่วงเป้าหมาย
#   และสมดุลที่สุดสำหรับรุ่นดังกล่าว
AIRCRAFT_LIBRARY: Dict[str, dict] = {
    "ATR72-600": {
        "category": "Regional Aircraft",
        "seats": 70,
        "zones": [
            ("Z1", "Zone 1", 70),
        ],
        "n_lav": 1, "n_gal": 1,
        "source": "แบ่ง 1 Work Unit: Z1 = 70 ที่นั่ง (อยู่ในช่วงเป้าหมาย 70-80)",
    },
    "CRJ900": {
        "category": "Regional Aircraft",
        "seats": 90,
        "zones": [
            ("Z1", "Zone 1", 90),
        ],
        "n_lav": 1, "n_gal": 1,
        "source": "ข้อยกเว้น: 90 ที่นั่งไม่สามารถแบ่งทุก Zone ให้อยู่ 70-80 ได้ จึงใช้ Z1 = 90 เพื่อคงจำนวนที่นั่งรวม",
    },
    "A320-200": {
        "category": "Narrow-body Aircraft",
        "seats": 156,
        "zones": [
            ("Z1", "Zone 1", 78),
            ("Z2", "Zone 2", 78),
        ],
        "n_lav": 3, "n_gal": 2,
        "source": "แบ่ง 2 Work Units แบบสมดุล: Z1/Z2 = 78/78 ที่นั่ง",
    },
    "B737-800": {
        "category": "Narrow-body Aircraft",
        "seats": 189,
        "zones": [
            ("Z1", "Zone 1", 63),
            ("Z2", "Zone 2", 63),
            ("Z3", "Zone 3", 63),
        ],
        "n_lav": 3, "n_gal": 2,
        "source": "ข้อยกเว้น: 189 ที่นั่งไม่สามารถแบ่งทุก Zone ให้อยู่ 70-80 ได้; แบบสมดุลที่ใกล้ที่สุดคือ 63/63/63",
    },
    "A330-300": {
        "category": "Wide-body Aircraft",
        "seats": 305,
        "zones": [
            ("Z1", "Zone 1", 77),
            ("Z2", "Zone 2", 76),
            ("Z3", "Zone 3", 76),
            ("Z4", "Zone 4", 76),
        ],
        "n_lav": 9, "n_gal": 8,
        "source": "แบ่ง 4 Work Units แบบสมดุล: 77/76/76/76 ที่นั่ง",
    },
    "B787-9": {
        "category": "Wide-body Aircraft",
        "seats": 298,
        "zones": [
            ("Z1", "Zone 1", 75),
            ("Z2", "Zone 2", 75),
            ("Z3", "Zone 3", 74),
            ("Z4", "Zone 4", 74),
        ],
        "n_lav": 9, "n_gal": 8,
        "source": "แบ่ง 4 Work Units แบบสมดุล: 75/75/74/74 ที่นั่ง",
    },
    "A350-900": {
        "category": "Large Aircraft",
        "seats": 321,
        "zones": [
            ("Z1", "Zone 1", 81),
            ("Z2", "Zone 2", 80),
            ("Z3", "Zone 3", 80),
            ("Z4", "Zone 4", 80),
        ],
        "n_lav": 8, "n_gal": 4,
        "source": "ข้อยกเว้น 1 ที่นั่ง: แบ่ง 4 Work Units = 81/80/80/80 เพื่อคง 321 ที่นั่งรวม",
    },
    "B777-300ER": {
        "category": "Large Aircraft",
        "seats": 348,
        "zones": [
            ("Z1", "Zone 1", 70),
            ("Z2", "Zone 2", 70),
            ("Z3", "Zone 3", 70),
            ("Z4", "Zone 4", 69),
            ("Z5", "Zone 5", 69),
        ],
        "n_lav": 10, "n_gal": 5,
        "source": "ข้อยกเว้น 1 ที่นั่ง/Zone: แบ่ง 5 Work Units = 70/70/70/69/69 เพื่อคง 348 ที่นั่งรวม",
    },
    "A380-800": {
        "category": "Very Large Aircraft",
        "seats": 507,
        "zones": [
            ("Z1", "Zone 1", 73),
            ("Z2", "Zone 2", 73),
            ("Z3", "Zone 3", 73),
            ("Z4", "Zone 4", 72),
            ("Z5", "Zone 5", 72),
            ("Z6", "Zone 6", 72),
            ("Z7", "Zone 7", 72),
        ],
        "n_lav": 14, "n_gal": 6,
        "source": "แบ่ง 7 Work Units แบบสมดุล: 73/73/73/72/72/72/72 ที่นั่ง",
    },
}

DEFAULT_AIRCRAFT = "A320-200"

# ==================================================================
# 2) ประเภทงานย่อย อ้างอิงกลับไปยังข้อในขอบเขตของโครงงาน
# ==================================================================
TASK_KIND_LABEL = {
    "C1":  "Trash Collection",          # 4.1.2 เก็บขยะ
    "C2":  "Vacuum",                    # 4.1.1 ดูดฝุ่น
    "C3":  "Cosmetic",                  # 4.1.6 ภาพลักษณ์ห้องโดยสาร
    "D":   "Seat Area Wipe",            # 4.1.3 ช่องเก็บของที่นั่ง โต๊ะพับ ที่วางแขน
    "E":   "Surface Cleaning",          # 4.1.7 พื้นผิวเพิ่มเติม
    "F":   "Amenity Setup",             # 4.1.8 ผ้าห่ม หมอน ชุดหูฟัง
    "LAV": "Lavatory Cleaning",         # 4.1.4 ห้องน้ำ
    "GAL": "Galley Cleaning",           # 4.1.5 ครัว
    "OVH": "Overhead Bin Cleaning",     # 4.2.2 ช่องเก็บสัมภาระเหนือศีรษะ
    "FD":  "Flight Deck Cleaning",      # 4.2.1 ห้องนักบิน
    "CR":  "Crew Cabin Cleaning",       # 4.2.1 ห้องพักลูกเรือ
    "RC":  "Final Recheck",             # 4.2.4 ตรวจสอบความสะอาดซ้ำ
    "DEI": "Aircraft De-icing Spray",     # S5 งานฉีดน้ำยาละลายน้ำแข็งภายนอกอากาศยาน
}

SCOPE_MAPPING = [
    ("4.1.1", "การดูดฝุ่น", "C2"),
    ("4.1.2", "การเก็บขยะ", "C1"),
    ("4.1.3", "ช่องเก็บของที่นั่ง โต๊ะพับ ที่วางแขน", "D"),
    ("4.1.4", "การทำความสะอาดห้องน้ำ", "A1-An"),
    ("4.1.5", "การทำความสะอาดพื้นที่ครัว", "B1-Bm"),
    ("4.1.6", "การดูแลภาพลักษณ์ห้องโดยสาร", "C3"),
    ("4.1.7", "การทำความสะอาดพื้นผิวเพิ่มเติม", "E"),
    ("4.1.8", "การจัดเตรียมผ้าห่ม หมอน ชุดหูฟัง", "F"),
    ("4.2.1", "ห้องนักบินและห้องพักลูกเรือ", "FD1, CR1"),
    ("4.2.2", "ช่องเก็บสัมภาระเหนือศีรษะ", "OVH"),
    ("4.2.3", "การจัดเตรียมผ้าห่ม หมอน ชุดหูฟัง", "F"),
    ("4.2.4", "การตรวจสอบความสะอาดซ้ำ", "RC1, RC2"),
    ("S5", "การฉีดน้ำยาละลายน้ำแข็งอากาศยาน (De-icing)", "DEI1"),
]

CLEANING_TYPES: Dict[str, List[str]] = {
    "Quick Transit - พื้นฐาน": ["C1", "C2", "C3", "LAV", "GAL"],
    "Quick Transit - เต็มรูปแบบ": ["C1", "D", "C2", "C3", "E", "F", "LAV", "GAL"],
    "Layover - เต็มรูปแบบ": ["C1", "OVH", "D", "C2", "C3", "E", "F",
                              "LAV", "GAL", "FD", "CR", "RC"],
}

DEFAULT_CLEANING_TYPE = "Quick Transit - พื้นฐาน"

# ==================================================================
# 3) การประมาณระยะเวลาของงาน
# ==================================================================
# ค่าระยะเวลาชุดนี้เป็น research-calibrated assumptions สำหรับตัวแบบ IE
# โดยอิงลำดับขนาดจากข้อมูลการทำความสะอาดอากาศยานในงานวิจัย:
#   - Lavatory cleaning ประมาณ 115-120 วินาที/ห้อง
#   - Galley cleaning ประมาณ 100-149 วินาที/จุด
#   - Cockpit cleaning ประมาณ 60 วินาที
#   - Vacuuming ในแบบจำลองภาคสนามอยู่ราว 120 วินาทีในพื้นที่ทำงานหนึ่งช่วง
# และปรับเพิ่มเล็กน้อยสำหรับเวลาเดิน/เตรียมอุปกรณ์ เพื่อให้เหมาะกับการใช้
# เป็นค่าจำนวนนาทีแบบ discrete ใน Time-indexed Optimization Model
#
# งานในพื้นที่ที่นั่งใช้สูตร
#       d_j = round(base + rate x seats_in_zone)
# ค่าสัมประสิทธิ์ถูกปรับให้ Quick Transit ของ A320 (52 ที่นั่ง/Zone)
# ใช้เวลาประมาณ 6 นาทีต่อ Zone เมื่อทำ C1 -> C2 -> C3 ต่อเนื่อง
# ซึ่งอยู่ในระดับที่สอดคล้องกับ turnaround cleaning แบบเร่งด่วนเมื่อแบ่งทีมทำงานขนานกัน
ZONE_DURATION_COEFF = {
    # เก็บขยะ: เดินเก็บขยะ/seat pocket ตามจำนวนที่นั่งใน Work Unit
    "C1":  {"base": 0.5, "rate": 0.035, "min": 1},

    # ดูดฝุ่น: ใกล้เคียง 2 นาทีสำหรับ narrow-body zone และเพิ่มตามขนาดพื้นที่
    "C2":  {"base": 0.5, "rate": 0.030, "min": 2},

    # จัดความเรียบร้อย/ภาพลักษณ์ห้องโดยสาร
    "C3":  {"base": 0.5, "rate": 0.025, "min": 1},

    # เช็ดโต๊ะพับ ที่วางแขน seat pocket และบริเวณที่นั่ง
    "D":   {"base": 0.5, "rate": 0.050, "min": 2},

    # เช็ดพื้นผิวเพิ่มเติม เช่น sidewall/window ledge/contact surfaces
    "E":   {"base": 0.5, "rate": 0.020, "min": 1},

    # เติม/จัด amenity ตามจำนวนที่นั่ง
    "F":   {"base": 0.5, "rate": 0.035, "min": 1},

    # ช่องเก็บสัมภาระเหนือศีรษะ เป็นงาน Layover จึงเพิ่มตามขนาด Zone
    "OVH": {"base": 0.5, "rate": 0.020, "min": 1},
}

# งานที่มีระยะเวลาค่อนข้างคงที่ ไม่ขึ้นกับจำนวนที่นั่งใน Zone
# ปัดเป็นนาทีเต็มเพื่อให้สอดคล้องกับ time-indexed model ที่ใช้หน่วย 1 นาที
FIXED_DURATION = {
    "LAV": 2,   # ~2 นาที/ห้องน้ำ (งานวิจัยรายงานประมาณ 115-120 วินาที)
    "GAL": 2,   # 2 นาที/ตำแหน่งครัว
    "FD":  2,   # ~2 นาที ห้องนักบิน รวมเวลาเข้าพื้นที่และตรวจความเรียบร้อย
    "CR":  3,   # ~3 นาที ห้องพักลูกเรือ
    "RC":  2,   # ~2 นาที ตรวจสอบความสะอาดซ้ำต่อจุด
}

# ข้อจำกัดภาระงานห้องน้ำ + ห้องครัวต่อพนักงาน 1 คน
# พนักงานหนึ่งคนรับงาน LAV และ GAL รวมกันได้ไม่เกิน 25 นาที
SERVICE_WORKLOAD_LIMIT = 25
SERVICE_TASK_KINDS = ("LAV", "GAL")

# ระยะเวลา Aircraft De-icing สำหรับ Scenario S5
# เป็นค่ากลางเชิงแบบจำลอง ไม่ใช่เวลาตายตัวของทุกสนามบิน เพราะเวลาจริงขึ้นกับ
# ปริมาณน้ำแข็ง/หิมะ อัตราการตก จำนวนรถ De-icing และขั้นตอน anti-icing
# แหล่งภาคสนามรายงานช่วงทั่วไปประมาณ 5-20 นาที และ narrow-body benchmark
# บางสนามบินเฉลี่ยใกล้ 18 นาที จึงใช้ค่ากลางตามกลุ่มขนาดดังนี้
DEICING_DURATION_BY_AIRCRAFT = {
    "ATR72-600": 10,
    "CRJ900": 10,
    "A320-200": 15,
    "B737-800": 15,
    "A330-300": 18,
    "B787-9": 18,
    "A350-900": 20,
    "B777-300ER": 22,
    "A380-800": 25,
}

# ==================================================================
# 4) พารามิเตอร์สภาพอากาศ ตามวัตถุประสงค์ข้อ 2
# ==================================================================
# สภาพอากาศไม่เปลี่ยนโครงสร้างของตัวแบบ แต่ส่งผลต่อประสิทธิภาพการทำงาน
# จึงแทนด้วยตัวคูณระยะเวลา gamma ที่คูณกับ d ของทุกงาน
#   d'(j) = ceil( gamma x d(j) )
WEATHER_FACTOR = {
    "ปกติ (Clear)": 1.00,
    "อากาศร้อนจัด (High Heat)": 1.05,
    "ฝนตก (Rain)": 1.10,
    "ฝนตกหนัก (Heavy Rain)": 1.20,
}

WEATHER_REASON = {
    "ปกติ (Clear)": "สภาพการทำงานมาตรฐาน",
    "อากาศร้อนจัด (High Heat)": "ประสิทธิภาพแรงงานลดลงจากความล้า",
    "ฝนตก (Rain)": "พื้นเปียก ต้องเช็ดซ้ำ และเคลื่อนย้ายอุปกรณ์ช้าลง",
    "ฝนตกหนัก (Heavy Rain)": "เพิ่มขั้นตอนป้องกันพื้นลื่นและการขนอุปกรณ์ขึ้นเครื่อง",
}

DEFAULT_WEATHER = "ปกติ (Clear)"


@dataclass
class Task:
    """งานย่อยหนึ่งงานในตัวแบบ สมาชิกของเซต J"""
    id: str
    kind: str
    zone: str
    name: str
    duration: int

    def to_dict(self) -> dict:
        return asdict(self)


def estimate_zone_duration(kind: str, seats: int, gamma: float = 1.0) -> int:
    """
    ประมาณระยะเวลาของงานในพื้นที่ที่นั่ง
    ตัวคูณสภาพอากาศถูกนำไปคูณกับค่าดิบก่อนการปัดเศษ เพื่อไม่ให้งานสั้น ๆ
    ถูกขยายเกินจริงจากการปัดขึ้นทีละหนึ่งนาที
    """
    c = ZONE_DURATION_COEFF[kind]
    raw = c["base"] + c["rate"] * seats
    baseline = max(c["min"], int(round(raw)))
    return max(baseline, max(c["min"], int(round(raw * gamma))))


def fixed_duration(kind: str, gamma: float = 1.0) -> int:
    base = FIXED_DURATION[kind]
    return max(base, int(round(base * gamma)))


# ==================================================================
# 5) ตัวสร้างรายการงาน
# ==================================================================
ZONE_TASK_ORDER = ["C1", "OVH", "D", "C2", "C3", "E", "F"]


def build_tasks(aircraft: str,
                cleaning_kinds: List[str] | None = None,
                weather: str = DEFAULT_WEATHER,
                include_deicing: bool = False) -> List[Task]:
    """
    สร้างรายการงานทั้งหมดของอากาศยานที่เลือก ภายใต้สภาพอากาศที่กำหนด

    include_deicing=True ใช้สำหรับ Scenario S5 และจะเพิ่มงาน DEI1
    (Aircraft De-icing Spray) เป็นงานภายนอกอากาศยานหนึ่งงาน
    """
    spec = AIRCRAFT_LIBRARY[aircraft]
    kinds = set(cleaning_kinds or CLEANING_TYPES[DEFAULT_CLEANING_TYPE])
    gamma = WEATHER_FACTOR.get(weather, 1.0)
    tasks: List[Task] = []

    for zone_id, zone_name, seats in spec["zones"]:
        for kind in ZONE_TASK_ORDER:
            if kind not in kinds:
                continue
            tasks.append(Task(
                id=f"{kind}{zone_id}",
                kind=kind,
                zone=zone_id,
                name=f"{TASK_KIND_LABEL[kind]} - {zone_name}",
                duration=estimate_zone_duration(kind, seats, gamma),
            ))

    if "LAV" in kinds:
        for n in range(1, spec["n_lav"] + 1):
            tasks.append(Task(f"A{n}", "LAV", "LAV",
                              f"Lavatory {n}", fixed_duration("LAV", gamma)))

    if "GAL" in kinds:
        for n in range(1, spec["n_gal"] + 1):
            tasks.append(Task(f"B{n}", "GAL", "GAL",
                              f"Galley {n}", fixed_duration("GAL", gamma)))

    if "FD" in kinds:
        tasks.append(Task("FD1", "FD", "CREW",
                          "Flight Deck", fixed_duration("FD", gamma)))
    if "CR" in kinds:
        tasks.append(Task("CR1", "CR", "CREW",
                          "Crew Cabin", fixed_duration("CR", gamma)))

    if "RC" in kinds:
        if "LAV" in kinds:
            tasks.append(Task("RC1", "RC", "CHECK",
                              "Recheck Lavatory", fixed_duration("RC", gamma)))
        if "GAL" in kinds:
            tasks.append(Task("RC2", "RC", "CHECK",
                              "Recheck Galley", fixed_duration("RC", gamma)))

    # Scenario S5: เพิ่มงานฉีด De-icing ภายนอกอากาศยาน
    # ไม่คูณ weather gamma ซ้ำ เพราะงานนี้เป็นสถานการณ์พิเศษที่ถูกเพิ่มโดย Scenario เอง
    if include_deicing:
        tasks.append(Task(
            "DEI1",
            "DEI",
            "DEICE",
            "Aircraft De-icing Spray",
            DEICING_DURATION_BY_AIRCRAFT.get(aircraft, 12),
        ))

    return tasks


# ==================================================================
# 6) เซต P ลำดับก่อน-หลัง
# ==================================================================
def build_precedence(tasks: List[Task],
                     trash_first_global: bool = False,
                     deicing_last_global: bool = False) -> List[Tuple[str, str]]:
    """
    ลำดับภายในหน่วยพื้นที่  C1 -> OVH -> D -> C2 -> C3 -> E -> F

        เก็บขยะก่อน จากนั้นทำงานที่ทำให้เกิดเศษตกลงพื้น คือทำความสะอาด
        ช่องเก็บสัมภาระและเช็ดพื้นที่ที่นั่ง แล้วจึงดูดฝุ่นเพื่อเก็บเศษที่ตกลงมา
        ตามด้วยการจัดความเรียบร้อย งานพื้นผิวเพิ่มเติม และการจัดของผู้โดยสาร

    งานตรวจสอบซ้ำ RC ต้องทำหลังงานห้องน้ำและครัวเสร็จทั้งหมด

    เมื่อ deicing_last_global=True (Scenario S5) จะกำหนดให้งาน DEI1
    เริ่มได้หลังงาน Cleaning/ground-service อื่นทั้งหมดในแบบจำลองเสร็จแล้ว
    เพื่อแทนการ De-icing ช่วงท้ายของ turnaround ก่อนออกเดินทาง
    """
    ids = {t.id for t in tasks}
    zones = sorted({t.zone for t in tasks if t.zone.startswith("Z")})
    P: List[Tuple[str, str]] = []

    for r in zones:
        present = [f"{k}{r}" for k in ZONE_TASK_ORDER if f"{k}{r}" in ids]
        for a, b in zip(present, present[1:]):
            P.append((a, b))

    if trash_first_global:
        trash = [f"C1{r}" for r in zones if f"C1{r}" in ids]
        vacuum = [f"C2{r}" for r in zones if f"C2{r}" in ids]
        for j in trash:
            for k in vacuum:
                if (j, k) not in P:
                    P.append((j, k))

    if "RC1" in ids:
        P.extend((t.id, "RC1") for t in tasks if t.kind == "LAV")
    if "RC2" in ids:
        P.extend((t.id, "RC2") for t in tasks if t.kind == "GAL")

    # Scenario S5: De-icing เป็นขั้นตอนท้ายของ turnaround
    if deicing_last_global and "DEI1" in ids:
        for t in tasks:
            if t.id != "DEI1" and (t.id, "DEI1") not in P:
                P.append((t.id, "DEI1"))

    return P


# ==================================================================
# 7) เซต B งานที่กีดขวางกัน
# ==================================================================
BLOCKED_BY_VACUUM = ("C1", "C3", "D", "E", "F")


def build_blocking(tasks: List[Task]) -> List[Tuple[str, str]]:
    """
    ขณะดูดฝุ่นในหน่วยพื้นที่หนึ่ง อุปกรณ์จะกีดขวางทางเดิน
    ทำให้พนักงานไม่สามารถเดินผ่านไปทำงานในหน่วยพื้นที่ที่ติดกันได้

    คู่งานในเซต B ต้องเป็นงานข้ามหน่วยพื้นที่เท่านั้น
    คู่ในหน่วยพื้นที่เดียวกันถูกบังคับลำดับด้วยเซต P อยู่แล้ว
    """
    ids = {t.id for t in tasks}
    zones = sorted({t.zone for t in tasks if t.zone.startswith("Z")})
    B: List[Tuple[str, str]] = []

    for idx, r in enumerate(zones):
        vac = f"C2{r}"
        if vac not in ids:
            continue
        neighbours = []
        if idx - 1 >= 0:
            neighbours.append(zones[idx - 1])
        if idx + 1 < len(zones):
            neighbours.append(zones[idx + 1])
        for s in neighbours:
            for kind in BLOCKED_BY_VACUUM:
                other = f"{kind}{s}"
                if other in ids:
                    B.append((vac, other))
    return B


def service_workload_minutes(tasks: List[Task]) -> int:
    """เวลางานห้องน้ำ + ห้องครัวรวมทั้งหมดของชุดงาน (นาที)."""
    return sum(t.duration for t in tasks if t.kind in SERVICE_TASK_KINDS)


def required_service_workers(tasks: List[Task],
                             limit_per_worker: int = SERVICE_WORKLOAD_LIMIT) -> int:
    """
    จำนวนพนักงานขั้นต่ำสำหรับงาน Lavatory + Galley ภายใต้เงื่อนไข
    ภาระงานรวมต่อคนไม่เกิน limit_per_worker นาที.

    ตัวอย่าง: service workload = 46 นาที, limit = 25
             -> ceil(46/25) = 2 คน
    """
    total = service_workload_minutes(tasks)
    if total <= 0:
        return 0
    return max(1, math.ceil(total / max(1, limit_per_worker)))


# ==================================================================
# 8) เซต a_ij ความสามารถของพนักงาน และ Scenario
# ==================================================================
SCENARIOS = {
    "S1": "Flexible - พนักงานทุกคนทำได้ทุกงาน",
    "S2": "Zone-based - แบ่งพนักงานตามหน่วยพื้นที่",
    "S3": "Trash First - เก็บขยะทุกหน่วยพื้นที่เสร็จก่อนดูดฝุ่น",
    "S4": "Zone-based + Trash First",
    "S5": "De-icing at minute 0 + Zone-based + Trash First",
}

SERVICE_ZONES = ("LAV", "GAL", "CREW", "CHECK", "DEICE")


def build_workers(n_workers: int,
                  add_deicing_worker: bool = False) -> List[str]:
    """
    สร้างรายชื่อพนักงาน Cleaning M1..Mn

    ฟังก์ชันนี้รับ n_workers เป็นจำนวนพนักงาน Cleaning ที่ต้องสร้าง (M1..Mn)
    หาก add_deicing_worker=True จะเพิ่ม DEICE1 อีก 1 คน ซึ่งทำเฉพาะงาน
    Aircraft De-icing Spray เท่านั้น

    หมายเหตุ: ใน app.py ของ Scenario S5 ค่า m บน Sidebar หมายถึงจำนวนพนักงานรวม
    ดังนั้น app.py จะส่ง n_workers = m - 1 เข้ามาที่ฟังก์ชันนี้ แล้วจึงเพิ่ม DEICE1
    """
    workers = [f"M{i + 1}" for i in range(n_workers)]
    if add_deicing_worker:
        workers.append("DEICE1")
    return workers


def build_capability(workers: List[str], tasks: List[Task],
                     zone_based: bool = False,
                     dedicated_deicing_worker: str | None = None
                     ) -> Dict[Tuple[str, str], int]:
    """
    สร้าง a_ij โดย 1 หมายถึงทำได้ และ 0 หมายถึงทำไม่ได้

    dedicated_deicing_worker:
        ใช้กับ Scenario S5 เพื่อกำหนดพนักงาน De-icing โดยเฉพาะ
        - พนักงานคนนี้ทำได้เฉพาะงาน kind == "DEI"
        - พนักงาน Cleaning คนอื่นทำงาน DEI ไม่ได้
        - การแบ่ง Zone/Service ของพนักงาน Cleaning ใช้กฎ LAV+GAL ไม่เกิน 25 นาที/คน
    """
    a: Dict[Tuple[str, str], int] = {}

    # --------------------------------------------------------------
    # S5: แยก De-icing worker ออกจาก Cleaning workforce โดยสมบูรณ์
    # --------------------------------------------------------------
    if dedicated_deicing_worker is not None:
        cleaning_workers = [i for i in workers if i != dedicated_deicing_worker]
        cleaning_tasks = [t for t in tasks if t.kind != "DEI"]

        # ใช้ logic เดิมสร้าง capability ของทีม Cleaning ก่อน
        cleaning_a = build_capability(
            cleaning_workers,
            cleaning_tasks,
            zone_based=zone_based,
            dedicated_deicing_worker=None,
        )

        for i in workers:
            for t in tasks:
                if i == dedicated_deicing_worker:
                    # DEICE1 ทำเพียงงาน De-icing เท่านั้น
                    a[(i, t.id)] = 1 if t.kind == "DEI" else 0
                elif t.kind == "DEI":
                    # Cleaner ทุกคนห้ามทำ DEI1
                    a[(i, t.id)] = 0
                else:
                    a[(i, t.id)] = cleaning_a.get((i, t.id), 0)
        return a

    # --------------------------------------------------------------
    # Scenario ปกติ S1-S4: logic เดิม
    # --------------------------------------------------------------
    if not zone_based:
        for i in workers:
            for t in tasks:
                a[(i, t.id)] = 1
        return a

    zones = sorted({t.zone for t in tasks if t.zone.startswith("Z")})
    has_service = any(t.zone in SERVICE_ZONES for t in tasks)

    # --------------------------------------------------------------
    # กฎภาระงาน Service: LAV + GAL รวมกันไม่เกิน 25 นาที/คน
    # ถ้างานรวมเกิน 25 นาที จะกันพนักงาน Service เพิ่มโดยอัตโนมัติ
    # เช่น 46 นาที -> ต้องมีอย่างน้อย ceil(46/25) = 2 คน
    # --------------------------------------------------------------
    n_service_required = required_service_workers(tasks) if has_service else 0

    if has_service and n_service_required > 0:
        if len(workers) > n_service_required:
            # กันพนักงานท้ายรายการตามจำนวนที่ต้องใช้สำหรับ LAV/GAL
            service_workers = workers[-n_service_required:]
            cabin_workers = workers[:-n_service_required]
        else:
            # ถ้ากำลังคนรวมมีน้อยมาก ให้ทุกคนมีสิทธิ์ทั้ง Cabin และ Service
            # Solver จะยังคงบังคับ LAV+GAL <= 25 นาที/คน
            service_workers = workers
            cabin_workers = workers
    else:
        cabin_workers, service_workers = workers, workers

    zone_owner: Dict[str, List[str]] = {r: [] for r in zones}
    for idx, r in enumerate(zones):
        zone_owner[r].append(cabin_workers[idx % len(cabin_workers)])
    for idx in range(len(zones), len(cabin_workers)):
        zone_owner[zones[idx % len(zones)]].append(cabin_workers[idx])

    for i in workers:
        for t in tasks:
            if t.zone in SERVICE_ZONES:
                a[(i, t.id)] = 1 if i in service_workers else 0
            else:
                a[(i, t.id)] = 1 if i in zone_owner.get(t.zone, []) else 0
    return a
