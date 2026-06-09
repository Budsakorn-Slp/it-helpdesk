# IT Helpdesk — Flask Project

## โครงสร้างไฟล์
```
it_helpdesk/
├── app.py                  ← Flask app หลัก + DB config + routes
├── requirements.txt
├── schema.sql              ← สร้าง table MySQL
├── .env.example            ← copy เป็น .env แล้วใส่ค่า DB
│
├── static/
│   ├── css/style.css       ← CSS ทั้งหมด (font + layout + component)
│   ├── js/main.js          ← SVG sprite + form validation
│   └── fonts/
│       ├── Anuphan/        ← .ttf files
│       └── Quicksand/      ← .ttf files
│
└── templates/
    ├── base.html           ← layout หลัก (header + breadcrumb)
    ├── index.html          ← หน้าเลือกประเภท
    ├── _form_base.html     ← macro form ที่ใช้ร่วมกันทุกหมวด
    ├── form1.html          ← ฟอร์มหมวด 1  ← เพิ่ม extra_fields ที่นี่
    ├── form2.html          ← ฟอร์มหมวด 2
    ├── form3.html          ← ฟอร์มหมวด 3
    └── success.html        ← หน้าสำเร็จ
```

## วิธีรัน
```bash
# 1. สร้าง virtual env
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 2. ติดตั้ง dependencies
pip install -r requirements.txt

# 3. ตั้งค่า DB
cp .env.example .env
# แก้ไขค่าใน .env

# 4. สร้าง table
mysql -u root -p it_helpdesk < schema.sql

# 5. รัน
python app.py
```

## เพิ่ม field เฉพาะหมวด
เปิด `form1.html` / `form2.html` / `form3.html` แล้วแก้ `{% set fields = [] %}` เป็น:
```jinja
{% set fields = [
  {'name':'asset_no', 'label':'รหัสทรัพย์สิน', 'type':'text',
   'placeholder':'เช่น AST-0001', 'required': false},
] %}
```
แล้ว **เพิ่ม column** นั้นใน `schema.sql` + `app.py` ใน INSERT statement

## แก้ชื่อหมวด
แก้ใน `app.py` ที่ `CATEGORIES` dict ได้เลย
"# it-helpdesk" 
"# it-helpdesk" 
