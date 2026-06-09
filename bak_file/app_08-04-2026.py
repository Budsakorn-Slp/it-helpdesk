import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv
load_dotenv("env")
import oracledb as cx_Oracle

from flask import (Flask, render_template, request, redirect, url_for, jsonify, session)
from datetime import datetime
from werkzeug.utils import secure_filename
from approve_api import approve_bp

app = Flask(__name__)
app.register_blueprint(approve_bp)

cx_Oracle.init_oracle_client(lib_dir=r"C:\instantclient_11_2")
app.secret_key = os.getenv("SECRET_KEY", "change_this_in_production")

# ── LINE ────────────────────────────────────────────────────────
LINE_TOKEN   = os.getenv("LINE_CHANNEL_TOKEN", "").strip()
APP_BASE_URL = "https://helpdesk.sbdsapp.com"

print(f"[DEBUG] TOKEN='{LINE_TOKEN[:15]}...'  len={len(LINE_TOKEN)}")

# ── Upload ──────────────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
ALLOWED_EXT   = {"pdf", "png", "jpg", "jpeg", "gif", "doc", "docx", "xls", "xlsx", "zip"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# ══════════════════════════════════════════════════════════════
#  ORACLE
# ══════════════════════════════════════════════════════════════
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
           EP.EMP_COM, EP.EMP_DEPT, EP.EMP_COSTCENTER,
           RE.APPROVER, RE.APPROVER_NAME
    FROM   SBP_EMPLOYEE E
           LEFT JOIN SBP_EMP_PAYROLL    EP ON E.EMP_ID = EP.EMP_ID
           LEFT JOIN SBP_REQ_APPROVER   RE ON E.EMP_ID = RE.EMP_ID
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
            return dict(zip(cols, [v.read() or "" if hasattr(v, "read") else v for v in row]))
        return None
    except cx_Oracle.Error as e:
        print(f"[ORACLE ERROR] get_employee: {e}")
        return None
    finally:
        if conn:
            try: conn.close()
            except: pass

def get_approver_line_id(approver_emp_id):
    """ดึง LINE_ID ของ approver จาก SBP_EMPLOYEE ด้วย EMP_ID"""
    if not approver_emp_id:
        return None
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT LINE_ID FROM SBP_EMPLOYEE WHERE EMP_ID = :emp_id",
            {"emp_id": approver_emp_id}
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except cx_Oracle.Error as e:
        print(f"[ORACLE ERROR] get_approver_line_id: {e}")
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
#  gen_request_id
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
def get_categories():
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT ID, NAME, "DESC", ICON, COLOR, URL
            FROM   IT_HELPDESK_TYPE
            WHERE  STATUS = 'Y'
            ORDER  BY ID
        """)
        cats = {}
        for row in cur.fetchall():
            cat_id, name, desc, icon, color, url = row
            entry = {
                "name":     name  or "",
                "desc":     desc  or "",
                "icon":     icon  or "",
                "color":    color or "",
                "typeform": str(cat_id),
            }
            if url:
                entry["external_url"] = url
            cats[int(cat_id)] = entry
        return cats
    except cx_Oracle.Error as e:
        print(f"[ORACLE ERROR] get_categories: {e}")
        return {}
    finally:
        if conn:
            try: conn.close()
            except: pass

# ══════════════════════════════════════════════════════════════
#  INSERT SQLs
# ══════════════════════════════════════════════════════════════
_REQUEST_KEYS = {
    "request_id", "request_date", "request_typeform", "request_category",
    "requester_fname", "requester_lname", "requester_tel", "requester_empcode",
    "requester_dept", "requester_costcenter", "requester_ip",
    "request_remark", "request_file", "request_status",
}

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

INSERT_APPROVER_SQL = """
    INSERT INTO IT_HELPDESK_APPROVER (
        REQUEST_ID, REQUESTER_EMPCODE, EMP_APPROVER,
        TYPE, STATUS, DATE_CREATE
    ) VALUES (
        :request_id, :requester_empcode, :emp_approver,
        :req_type, :req_status, SYSDATE
    )
"""

def do_insert(data):
    """INSERT เข้า IT_HELPDESK_REQUEST + IT_HELPDESK_APPROVER — คืน request_id หรือ 'ERROR'"""
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()

        req_id = gen_request_id(cur)
        data["request_id"] = req_id

        request_data = {k: data[k] for k in _REQUEST_KEYS if k in data}
        cur.execute(INSERT_SQL, request_data)

        emp_approver = data.get("emp_approver", "")
        cur.execute(INSERT_APPROVER_SQL, {
            "request_id":        req_id,
            "requester_empcode": data.get("requester_empcode", ""),
            "emp_approver":      emp_approver,
            "req_type":          data.get("request_typeform", ""),
            "req_status":        "Waiting",   # ← IT_HELPDESK_APPROVER.STATUS
        })

        conn.commit()
        print(f"[INSERT OK] request_id={req_id}  approver={emp_approver}")
        return req_id

    except cx_Oracle.Error as e:
        print(f"[ORACLE INSERT ERROR] {e}")
        if conn:
            try: conn.rollback()
            except: pass
        return "ERROR"
    finally:
        if conn:
            try: conn.close()
            except: pass

# ══════════════════════════════════════════════════════════════
#  LINE FLEX MESSAGE
# ══════════════════════════════════════════════════════════════
def send_line_flex(to_line_id: str, req_id: str, data: dict) -> bool:
    if not LINE_TOKEN:
        print("[LINE] LINE_CHANNEL_TOKEN ไม่ได้ตั้งค่า — ข้ามการส่ง")
        return False
    if not to_line_id:
        print("[LINE] ไม่มี LINE ID ของ approver — ข้ามการส่ง")
        return False

    base     = APP_BASE_URL
    category = data.get("request_category", "-")
    remark   = data.get("request_remark",   "-")
    fname    = data.get("requester_fname",  "")
    lname    = data.get("requester_lname",  "")
    req_date = data.get("request_date",     datetime.now().strftime("%d/%m/%Y %H:%M"))

    full_name   = f"{fname} {lname}".strip() or "-"
    date_label  = f"วันที่ : {req_date} น."

    payload = {
        "to": to_line_id,
        "messages": [
            {
                "type": "flex",
                "altText": f"[IT Helpdesk] #{req_id}",
                "contents": {
                    "type": "bubble",
                    "size": "mega",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#5b5ef4",
                        "paddingAll": "16px",
                        "contents": [
                            {"type": "text", "text": "IT Helpdesk", "color": "#FFFFFF", "size": "sm", "weight": "bold"},
                            {"type": "text", "text": f"#{req_id}", "color": "#FFFFFF", "size": "xl", "weight": "bold"}
                        ]
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "paddingAll": "16px",
                        "contents": [
                            {
                                "type": "box", "layout": "vertical", "spacing": "xs",
                                "contents": [
                                    {"type": "text", "text": "เรื่อง", "weight": "bold", "color": "#5b5ef4", "size": "sm"},
                                    {"type": "text", "text": category, "wrap": True, "size": "md"}
                                ]
                            },
                            {"type": "separator", "margin": "sm"},
                            {
                                "type": "box", "layout": "vertical", "spacing": "xs", "margin": "sm",
                                "contents": [
                                    {"type": "text", "text": "รายละเอียด", "weight": "bold", "color": "#5b5ef4", "size": "sm"},
                                    {"type": "text", "text": remark[:200] if remark else "-", "wrap": True, "size": "sm", "color": "#555555"}
                                ]
                            },
                            {"type": "separator", "margin": "sm"},
                            {
                                "type": "box", "layout": "vertical", "margin": "sm", "spacing": "xs",
                                "contents": [
                                    {"type": "text", "text": f"ขอโดย : {full_name}", "size": "sm", "color": "#333333"},
                                    {"type": "text", "text": date_label, "size": "sm", "color": "#888888"}
                                ]
                            }
                        ]
                    },
                    "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "paddingAll": "12px",
                        "contents": [
                            {
                                "type": "button", "style": "primary", "color": "#00C300", "height": "sm",
                                "action": {"type": "uri", "label": "Approve อนุมัติ", "uri": f"{base}/api/approve?ref={req_id}"}
                            },
                            {
                                "type": "button", "style": "primary", "color": "#aaa5a5", "height": "sm",
                                "action": {"type": "uri", "label": "Reject ไม่อนุมัติ", "uri": f"{base}/api/reject?ref={req_id}"}
                            },
                            {
                                "type": "button", "style": "secondary", "height": "sm",
                                "action": {"type": "uri", "label": "ดูรายละเอียด", "uri": f"{base}/track/{req_id}"}
                            }
                        ]
                    }
                }
            }
        ]
    }

    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url     = "https://api.line.me/v2/bot/message/push",
        data    = body_bytes,
        method  = "POST",
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {LINE_TOKEN}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        print(f"[LINE PUSH OK] request_id={req_id}  to={to_line_id}  http={status}")
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[LINE PUSH ERROR] http={e.code}  body={body}")
        return False
    except Exception as e:
        print(f"[LINE PUSH ERROR] {e}")
        return False

# ══════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════
@app.route("/")
def index():
    uuid = request.args.get("uuid", "").strip()
    if uuid:
        session["line_id"] = uuid
        return redirect(url_for("index"))
    return render_template("index.html", categories=get_categories())

@app.route("/form/<int:cat_id>")
def form(cat_id):
    cat = get_categories().get(cat_id)
    if not cat:
        return redirect(url_for("index"))
    if cat.get("external_url"):
        return redirect(cat["external_url"])
    return render_template("form_generic.html", cat=cat, cat_id=cat_id)

@app.route("/submit/<int:cat_id>", methods=["POST"])
def submit(cat_id):
    cat = get_categories().get(cat_id)
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

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    data = {
        "request_date":         now_str,
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
        # ── สถานะแรกที่ INSERT: รออนุมัติ (4) ↔ Approver Waiting ──
        "request_status":       "4",
        "requester_ip":         request.remote_addr or "",
        "emp_approver":         emp.get("approver",        ""),
    }

    # ── 1. INSERT 2 tables ──────────────────────────────────────
    req_id = do_insert(data)

    print("=" * 60)
    print(f"[SUBMIT cat={cat_id} typeform={cat['typeform']}] request_id={req_id}")
    for k, v in data.items():
        print(f"  {k:25} = {v}")
    print("=" * 60)

    # ── 2. ส่ง LINE Flex ไปหา approver ─────────────────────────
    if req_id != "ERROR":
        approver_emp_id = emp.get("approver", "")
        if approver_emp_id:
            # approver_line_id = get_approver_line_id(approver_emp_id)
            approver_line_id = "U1a079046647a4390627f067ee7e045ca"
            if approver_line_id:
                send_line_flex(approver_line_id, req_id, data)
            else:
                print(f"[LINE] approver {approver_emp_id} ไม่มี LINE_ID ในระบบ")
        else:
            print("[LINE] ไม่มีข้อมูล approver — ข้ามการส่ง LINE")

    return render_template("success.html", ticket=req_id, cat=cat)

# ══════════════════════════════════════════════════════════════
#  STATUS MAPS
# ══════════════════════════════════════════════════════════════
# REQUEST_STATUS (IT_HELPDESK_REQUEST)
REQUEST_STATUS_MAP = {
    "0":  "รอดำเนินการ",
    "1":  "รอคิว",
    "2":  "กำลังทำ",
    "3":  "ยกเลิก",
    "4":  "รออนุมัติ",
    "5":  "เสร็จ",
    "7":  "รอสั่งซื้อ",
    "8":  "รอยืนยัน",
    "10": "ส่งซ่อม",
    "11": "ยืม",
}

# APPROVER STATUS (IT_HELPDESK_APPROVER) → label + css class
APPROVER_STATUS_MAP = {
    "Waiting": {"label": "รออนุมัติ",    "cls": "4"},
    "Approve": {"label": "อนุมัติแล้ว",  "cls": "5"},
    "Reject":  {"label": "ไม่อนุมัติ",   "cls": "3"},
    "Done":    {"label": "เสร็จสิ้น",    "cls": "5"},
}

# compat alias ที่ template ใช้
STATUS_MAP = REQUEST_STATUS_MAP

# ══════════════════════════════════════════════════════════════
#  TRACK
# ══════════════════════════════════════════════════════════════
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

            # JOIN IT_HELPDESK_APPROVER เพื่อดึง APPROVER STATUS
            base_sql = """
                SELECT R.REQUEST_ID, R.REQUEST_DATE, R.REQUEST_TYPEFORM,
                       R.REQUESTER_FNAME, R.REQUESTER_LNAME, R.REQUESTER_DEPT,
                       R.REQUESTER_TEL, R.REQUEST_STATUS, R.REQUEST_FILE,
                       R.REQUESTER_EMPCODE, R.UPDATED_AT,
                       R.REQUEST_REMARK, R.REQUEST_CATEGORY,
                       A.STATUS AS APPROVER_STATUS
                FROM   IT_HELPDESK_REQUEST R
                       LEFT JOIN IT_HELPDESK_APPROVER A ON R.REQUEST_ID = A.REQUEST_ID
            """
            if req_id:
                cur.execute(base_sql + " WHERE R.REQUEST_ID = :val ORDER BY R.REQUEST_DATE DESC",
                            {"val": req_id})
            else:
                cur.execute(base_sql + " WHERE R.REQUESTER_EMPCODE = :val ORDER BY R.REQUEST_DATE DESC",
                            {"val": emp_id})

            def read_row(row):
                """แปลง LOB (CLOB/BLOB) → str ก่อนปิด connection"""
                out = []
                for v in row:
                    if hasattr(v, "read"):   # Oracle LOB object
                        out.append(v.read() or "")
                    else:
                        out.append(v)
                return out

            cols    = [d[0].lower() for d in cur.description]
            results = [dict(zip(cols, read_row(r))) for r in cur.fetchall()]
        except Exception as e:
            # catch ทั้ง cx_Oracle.Error, InterfaceError, DatabaseError ฯลฯ
            error = str(e)
            print(f"[TRACK ERROR] {type(e).__name__}: {e}")
        finally:
            if conn:
                try: conn.close()
                except: pass

    return render_template("track.html", results=results,
                           ticket_no=req_id, emp_id=emp_id,
                           error=error,
                           status_map=REQUEST_STATUS_MAP,
                           approver_status_map=APPROVER_STATUS_MAP)

@app.route("/track/<string:ticket_no>")
def track_detail(ticket_no):
    item  = None
    error = None
    conn  = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT R.REQUEST_ID, R.REQUEST_DATE, R.REQUEST_TYPEFORM,
                   R.REQUESTER_FNAME, R.REQUESTER_LNAME, R.REQUESTER_DEPT,
                   R.REQUESTER_TEL, R.REQUESTER_EMPCODE, R.REQUESTER_IP,
                   R.REQUEST_REMARK, R.REQUEST_FILE, R.REQUEST_STATUS,
                   R.REQUEST_SOLUTION, R.UPDATED_AT, R.REQUEST_CATEGORY,
                   A.STATUS AS APPROVER_STATUS, A.EMP_APPROVER
            FROM   IT_HELPDESK_REQUEST R
                   LEFT JOIN IT_HELPDESK_APPROVER A ON R.REQUEST_ID = A.REQUEST_ID
            WHERE  R.REQUEST_ID = :val
        """, {"val": ticket_no})
        row = cur.fetchone()
        if row:
            cols = [d[0].lower() for d in cur.description]
            def read_val(v):
                return v.read() or "" if hasattr(v, "read") else v
            item = dict(zip(cols, [read_val(v) for v in row]))
    except Exception as e:
        # catch ทั้ง cx_Oracle.Error, InterfaceError, DatabaseError ฯลฯ
        error = str(e)
        print(f"[TRACK_DETAIL ERROR] {type(e).__name__}: {e}")
    finally:
        if conn:
            try: conn.close()
            except: pass

    return render_template("track_detail.html", item=item,
                           ticket_no=ticket_no, error=error,
                           status_map=REQUEST_STATUS_MAP,
                           approver_status_map=APPROVER_STATUS_MAP)

@app.route("/debug/db")
def debug_db():
    """ทดสอบ DB connection — ลบออกเมื่อใช้งานจริง"""
    lines = []
    try:
        conn = get_conn()
        lines.append("OK get_conn() success")
        cur = conn.cursor()

        # columns of IT_HELPDESK_REQUEST
        cur.execute("SELECT * FROM IT_HELPDESK_REQUEST WHERE ROWNUM <= 1")
        req_cols = [d[0] for d in cur.description]
        lines.append(f"<b>IT_HELPDESK_REQUEST columns:</b> {', '.join(req_cols)}")

        # columns of IT_HELPDESK_APPROVER
        cur.execute("SELECT * FROM IT_HELPDESK_APPROVER WHERE ROWNUM <= 1")
        apv_cols = [d[0] for d in cur.description]
        lines.append(f"<b>IT_HELPDESK_APPROVER columns:</b> {', '.join(apv_cols)}")

        conn.close()
    except Exception as e:
        lines.append(f"ERR {type(e).__name__}: {e}")
    return "<br><br>".join(lines), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/liff/set-session", methods=["POST"])
def liff_set_session():
    data = request.get_json(force=True, silent=True) or {}
    line_user_id = data.get("line_user_id", "").strip()
    if line_user_id:
        session["line_id"] = line_user_id
    return jsonify({"ok": bool(line_user_id)})

@app.route("/api/categories")
def api_categories():
    return jsonify(get_categories())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5090, debug=True)