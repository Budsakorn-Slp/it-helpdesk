# -*- coding: utf-8 -*-
"""config.py — โหลด .env, เลือก Oracle driver และรวม config ที่ใช้ร่วมกันทุก module

เดิมมี app.py (dev/Windows) กับ app_prod.py (prod/Linux) แยกกัน เพราะ
Oracle Client คนละเวอร์ชันทำให้ใช้ driver คนละตัว:
  - Windows dev  : Instant Client 11.2 → ใช้ได้เฉพาะ cx_Oracle
  - Linux  prod  : Instant Client 19+  → ใช้ oracledb ได้
ตอนนี้รวมเหลือ app.py ไฟล์เดียว แล้วให้ที่นี่เลือก driver ตอน runtime แทน
ความต่างระหว่าง dev/prod ทั้งหมดย้ายมาอยู่ใน .env
"""

import importlib
import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# รองรับทั้ง ".env" (มาตรฐาน / ตาม README) และ "env" (ชื่อเดิมที่ใช้อยู่)
for _name in (".env", "env"):
    _path = os.path.join(BASE_DIR, _name)
    if os.path.isfile(_path):
        load_dotenv(_path)
        break
else:
    print("[CONFIG] ไม่พบไฟล์ .env — จะใช้ค่าจาก environment variable ของระบบแทน")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _require(name: str) -> str:
    """อ่าน env var ที่จำเป็น — ถ้าไม่มีให้ล้มตั้งแต่ตอน start ไม่ใช่ตอน connect DB"""
    value = _env(name)
    if not value:
        raise RuntimeError(
            f"ไม่ได้ตั้งค่า {name} — copy .env.example เป็น .env แล้วใส่ค่าให้ครบ"
        )
    return value


# ══════════════════════════════════════════════════════════════
#  ENVIRONMENT — สวิตช์ตัวเดียวที่แยก dev ออกจาก prod
# ══════════════════════════════════════════════════════════════
APP_ENV = _env("APP_ENV", "dev").lower()
IS_PROD = APP_ENV in ("prod", "production")

# ══════════════════════════════════════════════════════════════
#  ORACLE DRIVER
# ══════════════════════════════════════════════════════════════
# oracledb เป็นตัวใหม่ (cx_Oracle เปลี่ยนชื่อมา) แต่ต้องใช้ Oracle Client 19.1+
# ส่วน cx_Oracle 8.x ยังรองรับ client 11.2 ได้ จึงลอง oracledb ก่อนแล้วค่อยถอย
_DRIVER_PREFERENCE = ("oracledb", "cx_Oracle")

_driver = None          # module ที่เลือกได้แล้ว
_driver_error = None    # เก็บสาเหตุไว้รายงานถ้าเลือกไม่ได้เลย


def _try_init(module) -> None:
    """เปิด thick mode — จำเป็นเพราะ Oracle server 11.2 ใช้ thin mode ไม่ได้

    lib_dir อ่านจาก ORACLE_CLIENT_LIB_DIR ถ้าเว้นว่างจะให้ driver หาเองจาก
    PATH (Windows) หรือ LD_LIBRARY_PATH / ldconfig (Linux)
    """
    lib_dir = _env("ORACLE_CLIENT_LIB_DIR")
    if lib_dir:
        module.init_oracle_client(lib_dir=lib_dir)
    else:
        module.init_oracle_client()


def get_driver():
    """คืน module ของ Oracle driver ที่ใช้งานได้จริงบนเครื่องนี้ (เลือกครั้งเดียว)

    ตั้ง ORACLE_DRIVER=oracledb หรือ cx_Oracle ใน .env เพื่อบังคับได้
    ค่าปกติคือ auto = ลองตามลำดับใน _DRIVER_PREFERENCE
    """
    global _driver, _driver_error
    if _driver is not None:
        return _driver

    wanted = _env("ORACLE_DRIVER", "auto").lower()
    names = _DRIVER_PREFERENCE if wanted in ("", "auto") else (wanted,)

    problems = []
    for name in names:
        try:
            module = importlib.import_module(name)
        except ImportError:
            problems.append(f"  - {name}: ไม่ได้ติดตั้ง (pip install {name})")
            continue
        try:
            _try_init(module)
        except Exception as exc:
            # ส่วนใหญ่คือ client เก่าเกินไป (DPI-1050) หรือหา client ไม่เจอ (DPI-1047)
            problems.append(f"  - {name}: {exc}")
            continue

        _driver = module
        print(f"[CONFIG] ใช้ Oracle driver: {name}")
        return _driver

    _driver_error = "เชื่อมต่อ Oracle ไม่ได้ — ไม่มี driver ที่ใช้งานได้:\n" + "\n".join(problems)
    raise RuntimeError(_driver_error)


def oracle_credentials() -> dict:
    """kwargs สำหรับ connect()

    ไม่มี default — credential ต้องมาจาก .env เท่านั้น เพื่อไม่ให้รหัสผ่านจริง
    หลุดอยู่ใน source code (และหลุดขึ้น git ตามไปด้วย)
    """
    return {
        "user":     _require("ORACLE_USER"),
        "password": _require("ORACLE_PASSWORD"),
        "dsn":      _require("ORACLE_DSN"),
    }


def connect():
    """เปิด connection ใหม่ — ทุก module ควรเรียกผ่านตัวนี้ ไม่ import driver เอง"""
    return get_driver().connect(**oracle_credentials())


def db_error():
    """คลาส exception ของ driver ที่เลือก ใช้ except ได้โดยไม่ต้องรู้ว่าเป็นตัวไหน

    ถ้ายังเลือก driver ไม่ได้ให้คืน Exception ไปก่อน — กันไม่ให้ except clause
    ระเบิดทับ error จริงที่กำลังจะถูกจับ
    """
    try:
        return get_driver().Error
    except Exception:
        return Exception


# ══════════════════════════════════════════════════════════════
#  LINE
# ══════════════════════════════════════════════════════════════
LINE_TOKEN = _env("LINE_CHANNEL_TOKEN")

# URL ที่ผู้ใช้กดจาก LINE ต้องเข้าถึงได้จากภายนอก
#   local test : APP_BASE_URL=http://<ip-เครื่องคุณ>:5090  (หรือ URL ของ ngrok)
#   production : APP_BASE_URL=https://<โดเมนจริง>
APP_BASE_URL = _env("APP_BASE_URL", "http://127.0.0.1:5090").rstrip("/")

# ══════════════════════════════════════════════════════════════
#  ปลายทางการแจ้งเตือน
# ══════════════════════════════════════════════════════════════
# ค่าจริงที่ใช้บน production
MANAGER_EMP_ID    = _env("MANAGER_EMP_ID", "1450094")           # คุณพิเดช
WAREHOUSE_EMP_IDS = [x.strip() for x in _env("WAREHOUSE_EMP_IDS", "2550335").split(",") if x.strip()]

# ตอน dev ให้ทุกการแจ้งเตือน (approver / warehouse / manager) วิ่งไปหาคนเดียว
# เพื่อไม่ให้ไปรบกวนคนจริงระหว่างเทส — มีผลเฉพาะเมื่อ APP_ENV != prod
DEV_NOTIFY_EMP_ID  = _env("DEV_NOTIFY_EMP_ID", "4670008")
DEV_NOTIFY_LINE_ID = _env("DEV_NOTIFY_LINE_ID")   # ตั้งไว้ถ้าอยากข้ามการ lookup DB


def notify_emp_ids(prod_ids):
    """คืนรายชื่อ EMP_ID ที่จะส่งแจ้งเตือนจริง

    prod → ใช้ตามที่ส่งเข้ามา / dev → เปลี่ยนเป็น DEV_NOTIFY_EMP_ID ทั้งหมด
    """
    if IS_PROD or not DEV_NOTIFY_EMP_ID:
        return list(prod_ids)
    return [DEV_NOTIFY_EMP_ID]


def notify_emp_id(prod_id):
    """เวอร์ชันคนเดียวของ notify_emp_ids()"""
    ids = notify_emp_ids([prod_id] if prod_id else [])
    return ids[0] if ids else prod_id


# ══════════════════════════════════════════════════════════════
#  Flask
# ══════════════════════════════════════════════════════════════
SECRET_KEY = _env("SECRET_KEY")
DEBUG      = _flag("FLASK_DEBUG", default=not IS_PROD)
PORT       = int(_env("PORT", "5090"))

# cookie ต้องเป็น SameSite=None; Secure เมื่ออยู่หลัง HTTPS
# (LIFF เปิดใน webview ข้าม site — ถ้าไม่ตั้ง session จะหาย)
SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", default=IS_PROD)

# เขียน notify.log หรือไม่ — เดิมมีเฉพาะใน app_prod.py
NOTIFY_LOG = _flag("NOTIFY_LOG", default=True)
