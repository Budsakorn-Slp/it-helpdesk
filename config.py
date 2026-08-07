# -*- coding: utf-8 -*-
"""config.py — โหลด .env และรวม config ที่ใช้ร่วมกันทุก module

เดิมแต่ละไฟล์เรียก load_dotenv("env") เอง ซึ่งเป็น path แบบ relative กับ CWD
ถ้ารันจาก directory อื่น (เช่น service / task scheduler) จะโหลดไม่เจอแล้วเงียบ ๆ
ตกไปใช้ค่า default ที่ hard-code ไว้ในโค้ด — ที่นี่จึงอ่านจาก path ของไฟล์นี้แทน
"""

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


def _require(name: str) -> str:
    """อ่าน env var ที่จำเป็น — ถ้าไม่มีให้ล้มตั้งแต่ตอน start ไม่ใช่ตอน connect DB"""
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(
            f"ไม่ได้ตั้งค่า {name} — copy .env.example เป็น .env แล้วใส่ค่าให้ครบ"
        )
    return value


def oracle_credentials() -> dict:
    """kwargs สำหรับ cx_Oracle.connect() / oracledb.connect()

    ไม่มี default — credential ต้องมาจาก .env เท่านั้น เพื่อไม่ให้รหัสผ่านจริง
    หลุดอยู่ใน source code (และหลุดขึ้น git ตามไปด้วย)
    """
    return {
        "user":     _require("ORACLE_USER"),
        "password": _require("ORACLE_PASSWORD"),
        "dsn":      _require("ORACLE_DSN"),
    }


def init_oracle_client(driver) -> None:
    """เรียก init_oracle_client ของ driver ที่ส่งเข้ามา — เงียบถ้า init ซ้ำ

    lib_dir อ่านจาก ORACLE_CLIENT_LIB_DIR ถ้าไม่ตั้งจะปล่อยให้ driver
    หาจาก PATH เอง (thin mode ของ oracledb ไม่ต้องใช้ client เลย)
    """
    lib_dir = (os.getenv("ORACLE_CLIENT_LIB_DIR") or "").strip()
    try:
        if lib_dir:
            driver.init_oracle_client(lib_dir=lib_dir)
        else:
            driver.init_oracle_client()
    except Exception:
        # init ซ้ำจาก blueprint อื่น หรือรัน thin mode — ไม่ใช่ error
        pass


# ── LINE ────────────────────────────────────────────────────────
LINE_TOKEN = (os.getenv("LINE_CHANNEL_TOKEN") or "").strip()

# URL ที่ผู้ใช้กดจาก LINE ต้องเข้าถึงได้จากภายนอก
#   local test : APP_BASE_URL=http://<ip-เครื่องคุณ>:5090  (หรือ URL ของ ngrok)
#   production : APP_BASE_URL=https://<โดเมนจริง>
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "http://127.0.0.1:5090").strip().rstrip("/")

# ── Flask ───────────────────────────────────────────────────────
SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()
DEBUG      = (os.getenv("FLASK_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")
PORT       = int(os.getenv("PORT") or 5090)
