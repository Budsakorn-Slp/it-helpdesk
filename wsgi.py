# -*- coding: utf-8 -*-
"""wsgi.py — entry point สำหรับ gunicorn บน Linux

    gunicorn -w 4 -b 0.0.0.0:5090 wsgi:app

หมายเหตุเรื่องจำนวน worker: session ของ Flask เซ็นด้วย SECRET_KEY
ถ้าไม่ตั้ง SECRET_KEY ใน .env แต่ละ worker จะสุ่ม key คนละตัว
ทำให้ผู้ใช้หลุด session แบบสุ่ม — บน prod ต้องตั้งเสมอ
"""

from app import app

__all__ = ["app"]
