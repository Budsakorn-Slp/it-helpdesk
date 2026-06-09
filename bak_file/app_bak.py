from approve_api import approve_bp 
import os
from dotenv import load_dotenv
load_dotenv()
import oracledb as cx_Oracle

try:
    cx_Oracle.init_oracle_client()
    print("✅ Oracle Thick mode loaded")
except Exception as e:
    print("❌ Oracle client load failed:", e)

from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, session)
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.register_blueprint(approve_bp) 
app.secret_key = os.getenv("SECRET_KEY", "change_this_in_production")

# ── Upload ─────────────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
ALLOWED_EXT   = {"pdf", "png", "jpg", "jpeg", "gif", "doc", "docx", "xls", "xlsx", "zip"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# ══════════════════════════════════════════════════════════════
#  ORACLE — connection เดียว ใช้ทั้ง employee lookup + INSERT
# ══════════════════════════════════════════════════════════════
# cx_Oracle.init_oracle_client(lib_dir=r"/opt/oracle/instantclient_19_30")

def get_conn():
    return cx_Oracle.connect(
        user     = os.getenv("ORACLE_USER",     "SBLDB"),
        password = os.getenv("ORACLE_PASSWORD", "***REMOVED***"),
        dsn      = os.getenv("ORACLE_DSN",      "***REMOVED_DSN***")      
    )

# ══════════════════════════════════════════════════════════════
#  EMPLOYEE LOOKUP
# ══════════════════════════════════════════════════════════════
EMPLOYEE_SQL = """
    SELECT E.EMP_ID, E.MOBILE, E.NAME, E.LINE_ID,
           EP.EMP_COM, EP.EMP_DEPT, EP.EMP_COSTCENTER
    FROM   SBP_EMPLOYEE E
           LEFT JOIN SBP_EMP_PAYROLL EP ON E.EMP_ID = EP.EMP_ID
    WHERE  E.LINE_ID = :line_id
"""

def get_employee_by_line_id(line_id):
    if not line_id:
        return None
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(EMPLOYEE_SQL, {"line_id": line_id})
        row = cur.fetchone()
        if row:
            cols = [d[0].lower() for d in cur.description]
            return dict(zip(cols, row))
        return None
    except cx_Oracle.Error as e:
        error_obj, = e.args
        print(f"[ORACLE ERROR] get_employee: {error_obj.message}")
        return None
    finally:
        if conn:
            try: conn.close()
            except: pass

def split_name(full_name):
    if not full_name:
        return "", ""
    parts = full_name.strip().rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (full_name, "")

@app.context_processor
def inject_employee():
    line_id  = session.get("line_id", "")
    employee = get_employee_by_line_id(line_id) if line_id else None
    return {"employee": employee}

# ══════════════════════════════════════════════════════════════
#  gen_request_id — สร้าง REQUEST_ID 7 หลัก
# ══════════════════════════════════════════════════════════════
def gen_request_id(cursor):
    cursor.execute("""
        SELECT LPAD(NVL(MAX(TO_NUMBER(REQUEST_ID)), 0) + 1, 7, '0')
        FROM   IT_HELPDESK_REQUEST
    """)
    row = cursor.fetchone()
    return row[0] if row else "0000001"

# ══════════════════════════════════════════════════════════════
#  CATEGORIES
# ══════════════════════════════════════════════════════════════
CATEGORIES = {
    1: {"name": "แจ้งปัญหาคอมพิวเตอร์",          "desc": "อีเมล, ไดรฟ์ U, เครื่องพิมพ์, อุปกรณ์ต่อพ่วง",         "icon": "monitor",       "color": "red",    "typeform": "1"},
    2: {"name": "แจ้งปัญหาอินเทอร์เน็ต / โทรศัพท์",          "desc": "Wi-Fi ใช้ไม่ได้, เน็ตช้า, เชื่อมต่อไม่ได้",             "icon": "wifi",          "color": "orange", "typeform": "2"},
    3: {"name": "ขอสิทธิ์การเข้าถึง",              "desc": "ไดรฟ์ U, Server, Internet, โปรแกรม",            "icon": "key",           "color": "blue",   "typeform": "3"},
    4: {"name": "เบิก / ยืม / โอนย้าย",           "desc": "เบิกอุปกรณ์, ยืมครุภัณฑ์, โอนย้ายสินทรัพย์ IT",        "icon": "repeat",        "color": "teal",   "typeform": "4"},
    5: {"name": "ขอแก้ไข / ขอโปรแกรมใหม่",       "desc": "แก้ไขโปรแกรมที่ใช้งาน หรือขอติดตั้งโปรแกรมใหม่",       "icon": "code",          "color": "green",  "typeform": "5"},
    6: {"name": "ขอสั่งซื้ออุปกรณ์ IT / Software", "desc": "คลิกเพื่อกรอกแบบฟอร์มจัดซื้อในระบบ",                  "icon": "shopping-cart", "color": "purple", "typeform": "6",
        "external_url": "https://liff.line.me/1656347339-bqzWnmKB"},
}

# ══════════════════════════════════════════════════════════════
#  INSERT helper — ใช้ร่วมทุก submit
# ══════════════════════════════════════════════════════════════
INSERT_SQL = """
    INSERT INTO IT_HELPDESK_REQUEST (
        REQUEST_ID, REQUEST_DATE, REQUEST_TYPEFORM, REQUEST_TYPEPROBLEM,
        REQUEST_CATEGORY,
        REQUESTER_FNAME, REQUESTER_LNAME, REQUESTER_FNAME_EN, REQUESTER_LNAME_EN,
        REQUESTER_TEL, REQUESTER_EMPCODE, REQUESTER_POSITION,
        REQUESTER_DEPT, REQUESTER_SITE, REQUESTER_EMAIL, REQUESTER_SHOWROOM,
        REQUESTER_COSTCENTER, REQUESTER_SUPERVISOR, REQUESTER_IP,
        REQUEST_REMARK, REQUEST_FILE,
        REQUEST_STATUS, REQUEST_LEVEL, REQUEST_ACTION,
        REQUEST_SOLUTION, REQUEST_RECOMMEND,
        REQUEST_SA, REQUEST_PROGRAMMER,
        DATE_KNOW, DATE_START, DATE_FINISH, DATE_USE,
        ASSET_CODE, ASSET_NAME, ASSET_SERIAL, ASSET_PRODUCT, ASSET_LOT_NO,
        PO_PR_NO, PO_PO_NO, PO_TAX_NO,
        UPDATED_BY
    ) VALUES (
        :request_id, :request_date, :request_typeform, '',
        :request_category,
        :requester_fname, :requester_lname, '', '',
        :requester_tel, :requester_empcode, '',
        :requester_dept, '', '', '',
        :requester_costcenter, '', :requester_ip,
        :request_remark, :request_file,
        :request_status, '', '',
        '', '',
        '', '',
        '', '', '', '',
        '', '', '', '', '',
        '', '', '',
        ''
    )
"""

def do_insert(data):
    """INSERT เข้า IT_HELPDESK_REQUEST — คืน request_id หรือ 'ERROR'"""
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        req_id = gen_request_id(cur)
        data["request_id"] = req_id
        cur.execute(INSERT_SQL, data)
        conn.commit()
        print(f"[INSERT OK] request_id={req_id}")
        return req_id
    except cx_Oracle.Error as e:
        error_obj, = e.args
        print(f"[ORACLE INSERT ERROR] {error_obj.message}")
        return "ERROR"
    finally:
        if conn:
            try: conn.close()
            except: pass

# ══════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html", categories=CATEGORIES)

@app.route("/form/<int:cat_id>")
def form(cat_id):
    cat = CATEGORIES.get(cat_id)
    if not cat:
        return redirect(url_for("index"))
    # ถ้ามี external_url → redirect ออกไปเลย ไม่แสดงฟอร์ม
    if cat.get("external_url"):
        return redirect(cat["external_url"])
    return render_template("form_generic.html", cat=cat, cat_id=cat_id)


# ── SUBMIT ทุกหมวด (1-5) — route เดียว ─────────────────────────
@app.route("/submit/<int:cat_id>", methods=["POST"])
def submit(cat_id):
    cat = CATEGORIES.get(cat_id)
    if not cat or cat.get("external_url"):
        return redirect(url_for("index"))

    emp = inject_employee().get("employee") or {}
    fname, lname = split_name(emp.get("name", ""))

    # รับไฟล์แนบ
    file_saved = None
    f = request.files.get("attachment")
    if f and f.filename and allowed_file(f.filename):
        safe       = secure_filename(f.filename)
        file_saved = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe}"
        f.save(os.path.join(app.config["UPLOAD_FOLDER"], file_saved))

    data = {
        "request_date":         datetime.now().strftime("%d/%m/%Y %H:%M"),
        "request_typeform":     cat["typeform"],
        "request_category":     cat["name"],
        "requester_fname":      fname,
        "requester_lname":      lname,
        "requester_tel":        request.form.get("phone",  "").strip(),
        "request_remark":       request.form.get("detail", "").strip(),
        "request_file":         file_saved or "",
        "requester_empcode":    emp.get("emp_id",          ""),
        "requester_dept":       request.form.get("dept",   "").strip(),
        "requester_costcenter": emp.get("emp_costcenter",  "0"),
        "request_status":       "0",
        "requester_ip":         request.remote_addr or "",
    }

    req_id = do_insert(data)

    print("=" * 60)
    print(f"[SUBMIT cat={cat_id} typeform={cat['typeform']}] request_id={req_id}")
    for k, v in data.items():
        print(f"  {k:25} = {v}")
    print("=" * 60)

    return render_template("success.html", ticket=req_id, cat=cat)

# ══════════════════════════════════════════════════════════════
#  TRACK
# ══════════════════════════════════════════════════════════════
STATUS_MAP = {
    "0":"รอดำเนินการ","1":"รอคิว","2":"กำลังทำ","3":"ยกเลิก",
    "4":"รออนุมัติ","5":"เสร็จ","7":"รอสั่งซื้อ",
    "8":"รอยืนยัน","10":"ส่งซ่อม","11":"ยืม",
}

@app.route("/track")
def track():
    req_id  = request.args.get("ticket", "").strip()
    emp_id  = request.args.get("emp_id", "").strip()
    results = []
    error   = None

    if req_id or emp_id:
        conn = None
        try:
            conn = get_conn()
            cur  = conn.cursor()
            if req_id:
                cur.execute("""
                    SELECT REQUEST_ID, REQUEST_DATE, REQUEST_TYPEFORM,
                           REQUESTER_FNAME, REQUESTER_LNAME, REQUESTER_DEPT,
                           REQUESTER_TEL, REQUEST_STATUS, REQUEST_FILE,
                           REQUESTER_EMPCODE, UPDATED_AT
                    FROM   IT_HELPDESK_REQUEST
                    WHERE  REQUEST_ID = :val
                    ORDER BY REQUEST_DATE DESC
                """, {"val": req_id})
            else:
                cur.execute("""
                    SELECT REQUEST_ID, REQUEST_DATE, REQUEST_TYPEFORM,
                           REQUESTER_FNAME, REQUESTER_LNAME, REQUESTER_DEPT,
                           REQUESTER_TEL, REQUEST_STATUS, REQUEST_FILE,
                           REQUESTER_EMPCODE, UPDATED_AT
                    FROM   IT_HELPDESK_REQUEST
                    WHERE  REQUESTER_EMPCODE = :val
                    ORDER BY REQUEST_DATE DESC
                """, {"val": emp_id})
            cols    = [d[0].lower() for d in cur.description]
            results = [dict(zip(cols, r)) for r in cur.fetchall()]
        except cx_Oracle.Error as e:
            error_obj, = e.args
            error = error_obj.message
        finally:
            if conn:
                try: conn.close()
                except: pass

    return render_template("track.html", results=results,
                           ticket_no=req_id, emp_id=emp_id,
                           error=error, status_map=STATUS_MAP)


@app.route("/track/<string:ticket_no>")
def track_detail(ticket_no):
    item  = None
    error = None
    conn  = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT REQUEST_ID, REQUEST_DATE, REQUEST_TYPEFORM,
                   REQUESTER_FNAME, REQUESTER_LNAME, REQUESTER_DEPT,
                   REQUESTER_TEL, REQUESTER_EMPCODE, REQUESTER_IP,
                   REQUEST_REMARK, REQUEST_FILE, REQUEST_STATUS,
                   REQUEST_SOLUTION, UPDATED_AT
            FROM   IT_HELPDESK_REQUEST
            WHERE  REQUEST_ID = :val
        """, {"val": ticket_no})
        row = cur.fetchone()
        if row:
            cols = [d[0].lower() for d in cur.description]
            item = dict(zip(cols, row))
    except cx_Oracle.Error as e:
        error_obj, = e.args
        error = error_obj.message
    finally:
        if conn:
            try: conn.close()
            except: pass

    return render_template("track_detail.html", item=item,
                           ticket_no=ticket_no, error=error,
                           status_map=STATUS_MAP)


@app.route("/liff/set-session", methods=["POST"])
def liff_set_session():
    data = request.get_json(force=True, silent=True) or {}
    line_user_id = data.get("line_user_id", "").strip()
    if line_user_id:
        session["line_id"] = line_user_id
    return jsonify({"ok": bool(line_user_id)})


@app.route("/api/categories")
def api_categories():
    return jsonify(CATEGORIES)


if __name__ == "__main__":
    app.run(debug=True, port=5090)
