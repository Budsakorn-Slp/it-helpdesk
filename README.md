# IT Helpdesk — Flask Project

ระบบรับคำร้อง IT ผ่าน LINE LIFF — ยืม / เบิก / โอนย้ายทรัพย์สิน
พร้อมสายอนุมัติ (ผู้อนุมัติ → ผู้ส่ง → ผู้รับ → ผู้จัดการ) และออกใบโอนย้ายเป็น PDF

## โครงสร้างไฟล์

```text
it_helpdesk/
├── config.py               ← โหลด .env + credential กลาง (ทุก module import ตัวนี้)
├── app.py                  ← Flask app สำหรับ dev/local (ใช้ cx_Oracle)
├── app_prod.py             ← Flask app สำหรับ production (ใช้ oracledb + notify.log)
├── approve_api.py          ← blueprint: หน้าอนุมัติ/ปฏิเสธรายเอกสาร
├── approve_list.py         ← blueprint: หน้ารวมเอกสารของผู้อนุมัติแต่ละคน
├── transfer_pdf.py         ← blueprint: ออกใบโอนย้ายเป็น PDF (xhtml2pdf)
├── requirements.txt
├── schema.sql
├── .env.example            ← copy เป็น .env แล้วใส่ค่าจริง
│
├── static/
│   ├── css/style.css
│   ├── js/main.js
│   ├── fonts/              ← Anuphan / Quicksand / THSarabunNew (ใช้ใน PDF)
│   └── uploads/            ← ไฟล์แนบ (ไม่ขึ้น git)
│
└── templates/
    ├── base.html           ← layout หลัก
    ├── index.html          ← หน้าเลือกประเภทคำร้อง
    ├── form_generic.html   ← ฟอร์มทั่วไป
    ├── form4.html          ← ฟอร์มยืม / เบิก / โอนย้าย
    ├── track.html          ← ติดตามสถานะ
    ├── track_detail.html   ← รายละเอียดคำร้อง
    ├── pdf_transfer.html   ← template สำหรับ render PDF
    └── success.html
```

## ฐานข้อมูล

ข้อมูลหลักอยู่บน **Oracle** (`IT_HELPDESK_REQUEST`, `IT_HELPDESK_TRANSFER`,
`IT_HELPDESK_ASSET`, `IT_HELPDESK_APPROVER`, `IT_HELPDESK_TYPE`)
ส่วนข้อมูลพนักงานอ่านจาก `SBP_EMPLOYEE` / `SBP_EMP_PAYROLL` / `SBP_REQ_APPROVER`

> `schema.sql` เป็น MySQL DDL ของ scaffold ชุดแรก (`it_request_cat1/2/3`)
> ซึ่ง**ไม่ได้ถูกใช้แล้ว** — โค้ดปัจจุบันไม่มี import mysql ที่ไหนเลย

## วิธีรัน

```bash
# 1. สร้าง virtual env
python -m venv .venv
.venv\Scripts\activate      # Linux/Mac: source .venv/bin/activate

# 2. ติดตั้ง dependencies
pip install -r requirements.txt

# 3. ตั้งค่า environment — copy แล้วแก้ค่าใน .env ให้ครบ
#    ORACLE_USER / ORACLE_PASSWORD / ORACLE_DSN และ SECRET_KEY
cp .env.example .env

# 4. รัน
python app.py            # dev   → http://127.0.0.1:5090
python app_prod.py       # prod
```

สร้าง `SECRET_KEY` ด้วย:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> ⚠️ **ห้าม commit ไฟล์ `.env` / `env`** — มี credential จริงอยู่ข้างใน
> `.gitignore` กันไว้ให้แล้ว ใช้ `.env.example` เป็นตัวอย่างแทน

## Configuration

ค่า config ทั้งหมดอ่านผ่าน `config.py` ไม่มี credential hard-code ในโค้ดแล้ว

| ตัวแปร | ความหมาย |
| --- | --- |
| `ORACLE_USER` / `ORACLE_PASSWORD` / `ORACLE_DSN` | **จำเป็น** — ถ้าไม่ตั้ง app จะ error ทันทีที่ต่อ DB |
| `ORACLE_CLIENT_LIB_DIR` | path ของ Instant Client (เว้นว่างได้ถ้าอยู่ใน PATH) |
| `LINE_CHANNEL_TOKEN` | token ของ LINE Messaging API — ถ้าว่างจะข้ามการส่งแจ้งเตือน |
| `APP_BASE_URL` | URL ที่ผู้ใช้กดจาก LINE ต้องเข้าถึงได้จากภายนอก |
| `SECRET_KEY` | ถ้าไม่ตั้งจะสุ่มให้ → session หลุดทุกครั้งที่ restart |
| `FLASK_DEBUG` | `true` เพื่อเปิด debugger — **ห้ามเปิดบน production** |
| `SESSION_COOKIE_SECURE` | `true` เมื่อรันหลัง HTTPS (เปิด `SameSite=None; Secure`) |
| `PORT` | default `5090` |

## เพิ่มประเภทคำร้อง

ประเภทคำร้องอ่านจากตาราง `IT_HELPDESK_TYPE` (คอลัมน์ `ID, NAME, DESC, ICON, COLOR, URL`
และ `STATUS = 'Y'`) — เพิ่ม/แก้ record ในตารางได้เลย ไม่ต้องแก้โค้ด

## เพิ่ม field เฉพาะหมวด

เปิด template ของหมวดนั้นแล้วแก้ `{% set fields = [] %}` เป็น:

```jinja
{% set fields = [
  {'name':'asset_no', 'label':'รหัสทรัพย์สิน', 'type':'text',
   'placeholder':'เช่น AST-0001', 'required': false},
] %}
```

แล้ว **เพิ่ม column** นั้นใน `schema.sql` + INSERT statement ใน `app.py`
