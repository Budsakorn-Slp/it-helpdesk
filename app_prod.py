import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv
load_dotenv("env")
import oracledb as cx_Oracle

from flask import (Flask, render_template, request, redirect, url_for, jsonify, session)
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from approve_api import (
    approve_bp,
    check_transfer_complete
)
from transfer_pdf import transfer_pdf_bp
from flask import jsonify


app = Flask(__name__)
app.register_blueprint(approve_bp)
app.register_blueprint(transfer_pdf_bp)

#cx_Oracle.init_oracle_client(lib_dir=r"C:\instantclient_11_2")
cx_Oracle.init_oracle_client()
app.secret_key = os.getenv("SECRET_KEY", "change_this_in_production")

# ใช้สำหรับตอน test บน local ตัวเอง
#app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
#app.config["SESSION_COOKIE_SECURE"]   = False

# สำหรับ deploy จริงบน server ที่มี HTTPS แล้ว ให้ใช้ config นี้แทน
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"]   = True

# ── LINE ────────────────────────────────────────────────────────
LINE_TOKEN   = os.getenv("LINE_CHANNEL_TOKEN", "").strip()
# สำหรับ deploy จริง ให้ตั้งเป็น URL ของ server ที่มี HTTPS เช่น https://helpdesk.sbdsapp.com
APP_BASE_URL = "https://helpdesk.sbdsapp.com"

# สำหรับทดสอบบน local ตัวเอง ให้ตั้งเป็น URL ที่เข้าถึงได้จากมือถือ เช่น ผ่าน ngrok หรือใช้ IP ของเครื่อง
#APP_BASE_URL = "http://***REMOVED_HOST***:5090"

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
        # print("DEBUG LINE_ID:", line_id)
        # print("ROW FROM DB:", row)
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

# ═════════════════════════════════════════════════════════════
#  GET EMPLOYEE LINE ID จาก EMP_CODE (สำหรับส่ง LINE ไปหาผู้อนุมัติที่เป็นเจ้าหน้าที่คลัง)
# ════════════════════════════════════════════════════════════
def get_employee_line(emp_code):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                NAME,
                LINE_ID
            FROM SBP_EMPLOYEE
            WHERE EMP_ID = :emp
        """, {
            "emp": emp_code
        })
        row = cur.fetchone()
        if not row:
            return None
        return {
            "name": row[0],
            "line_id": row[1]
        }
    except Exception as e:
        print("[GET EMP LINE ERROR]", e)
        return None
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass


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
def gen_request_id(cursor) -> int:
    """คืน int — ใช้ MAX(REQUEST_ID)+1 เพราะ table ไม่มี sequence"""
    cursor.execute("SELECT NVL(MAX(REQUEST_ID), 0) + 1 FROM IT_HELPDESK_REQUEST")
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] else 1

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
    "request_typeproblem", "asset_name", "asset_code", "date_start", "date_finish","requester_email"
}

INSERT_SQL = """
INSERT INTO IT_HELPDESK_REQUEST (
    REQUEST_ID, REQUEST_DATE, REQUEST_TYPEFORM, REQUEST_TYPEPROBLEM,
    REQUEST_CATEGORY,
    REQUESTER_FNAME, REQUESTER_LNAME, REQUESTER_FNAME_EN, REQUESTER_LNAME_EN,
    REQUESTER_TEL, REQUESTER_EMPCODE, REQUESTER_POSITION,
    REQUESTER_DEPT, REQUESTER_SITE, REQUESTER_EMAIL, REQUESTER_SHOWROOM,
    REQUESTER_COSTCENTER, REQUESTER_SUPERVISOR, REQUESTER_IP,
    REQUEST_FILE,
    REQUEST_STATUS, REQUEST_LEVEL, REQUEST_ACTION,
    REQUEST_SOLUTION, REQUEST_RECOMMEND,
    REQUEST_SA, REQUEST_PROGRAMMER,
    DATE_KNOW, DATE_START, DATE_FINISH, DATE_USE,
    ASSET_CODE, ASSET_NAME, ASSET_SERIAL, ASSET_PRODUCT, ASSET_LOT_NO,
    PO_PR_NO, PO_PO_NO, PO_TAX_NO,
    UPDATED_BY,
    REQUEST_REMARK
) VALUES (
    :request_id, :request_date, :request_typeform, :request_typeproblem,
    :request_category,
    :requester_fname, :requester_lname, '', '',
    :requester_tel, :requester_empcode, '',
    :requester_dept, '', :requester_email, '',
    :requester_costcenter, '', :requester_ip,
    :request_file,
    :request_status, '', '',
    '', '',
    '', '',
    '', :date_start, :date_finish, '',
    :asset_code, :asset_name, '', '', '',
    '', '', '',
    '',
    :request_remark
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

        request_data = {k: data.get(k, "") for k in _REQUEST_KEYS}
        cur.execute(INSERT_SQL, request_data)

        emp_approver = data.get("emp_approver", "")
        # ถ้าเป็นประเภทที่ไม่ต้องอนุมัติ ให้ใส่ Approve เลย
        approver_status = data.get("approver_status", "Waiting")
        cur.execute(INSERT_APPROVER_SQL, {
            "request_id":        req_id,
            "requester_empcode": data.get("requester_empcode", ""),
            "emp_approver":      emp_approver,
            "req_type":          data.get("request_typeform", ""),
            "req_status":        approver_status,
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
    

def send_manager_flex(
    to_line_id: str,
    req_id: str,
    data: dict,
    action: str = "manager"
) -> bool:

    if not LINE_TOKEN:
        return False

    base = APP_BASE_URL
    category = data.get("request_category", "-")
    remark   = data.get("request_remark", "-")

    # ─────────────────────────────
    # ACTION TYPE
    # ─────────────────────────────

    if action == "receiver":
        title_text  = "Receiver Approval"
        button_text = "Confirm รับทรัพย์สิน"
        approve_url = (
            f"{base}/api/receiver-confirm"
            f"?ref={req_id}"
        )
    else:
        title_text  = "IT Helpdesk"
        button_text = "Approve อนุมัติ"
        approve_url = (
            f"{base}/api/manager-confirm"
            f"?ref={req_id}"
        )
    payload = {
        "to": to_line_id,
        "messages": [
            {
                "type": "flex",
                "altText": f"[Approval] #{req_id}",
                "contents": {
                    "type": "bubble",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#5b5ef4",
                        "paddingAll": "16px",
                        "contents": [
                            {
                                "type": "text",
                                "text": title_text,
                                "weight": "bold",
                                "color": "#ffffff",
                                "size": "lg"
                            },
                            {
                                "type": "text",
                                "text": f"Request ID: {req_id}",
                                "color": "#ffffff",
                                "size": "sm"
                            }
                        ]
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "text",
                                "text": category,
                                "weight": "bold",
                                "wrap": True
                            },
                            {
                                "type": "text",
                                "text": remark[:200] if remark else "-",
                                "size": "sm",
                                "wrap": True,
                                "color": "#666666"
                            }
                        ]
                    },

                    "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "button",
                                "style": "primary",
                                "color": "#16a34a",
                                "action": {
                                    "type": "uri",
                                    "label": button_text,
                                    "uri": approve_url
                                }
                            }
                        ]
                    }
                }
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}"
    }

    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as res:
            print(
                f"[LINE PUSH OK] request_id={req_id} "
                f"to={to_line_id} "
                f"http={res.status}"
            )
            return True
    except urllib.error.HTTPError as e:
        print(
            "[LINE PUSH ERROR]",
            e.read().decode()
        )
        return False
    except Exception as e:
        print(
            "[LINE PUSH EXCEPTION]",
            e
        )
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

    # ฟอร์ม 4 เบิกยืมโอนย้าย
    if cat_id == 4:
        return render_template("form4.html", cat=cat, cat_id=cat_id)

    # default ฟอร์มอื่น
    return render_template("form_generic.html", cat=cat, cat_id=cat_id)

# ══════════════════════════════════════════════════════════════
#  HELPER: Generate DOC_NO  →  A{YYYY}{NNNN}  เช่น A20260001
# ══════════════════════════════════════════════════════════════
def gen_doc_no(cursor) -> str:
    year = datetime.now().strftime("%Y")
    cursor.execute("""
        SELECT NVL(MAX(TO_NUMBER(SUBSTR(DOC_NO, 6))), 0) + 1
        FROM   IT_HELPDESK_TRANSFER
        WHERE  SUBSTR(DOC_NO, 2, 4) = :yr
    """, {"yr": year})
    row = cursor.fetchone()
    running = int(row[0]) if row and row[0] else 1
    return f"A{year}{running:04d}"


def gen_transfer_id(cursor) -> int:
    """MAX(ID)+1 สำหรับ IT_HELPDESK_TRANSFER (ไม่มี sequence)"""
    cursor.execute("SELECT NVL(MAX(ID), 0) + 1 FROM IT_HELPDESK_TRANSFER")
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] else 1


def gen_asset_id(cursor) -> int:
    """MAX(ID)+1 สำหรับ IT_HELPDESK_ASSET (ไม่มี sequence)"""
    cursor.execute("SELECT NVL(MAX(ID), 0) + 1 FROM IT_HELPDESK_ASSET")
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] else 1


# ══════════════════════════════════════════════════════════════
#  ROUTE: /submit/<cat_id>
# ══════════════════════════════════════════════════════════════
@app.route("/submit/<int:cat_id>", methods=["POST"])
def submit(cat_id):
    cat = get_categories().get(cat_id)
    if not cat or cat.get("external_url"):
        return redirect(url_for("index"))

    emp = inject_employee().get("employee") or {}
    fname, lname = split_name(emp.get("name", ""))

    # ── ไฟล์แนบ ─────────────────────────────────────────────
    file_saved = None
    f = request.files.get("attachment")
    if f and f.filename and allowed_file(f.filename):
        safe = secure_filename(f.filename)
        file_saved = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe}"
        f.save(os.path.join(app.config["UPLOAD_FOLDER"], file_saved))

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    SKIP_APPROVAL_KEYWORDS = ["คอมพิวเตอร์"]
    skip_approval = any(kw in cat["name"] for kw in SKIP_APPROVAL_KEYWORDS)

    costcenter = str(emp.get("emp_costcenter", "") or "").strip() or "0"
    empcode    = str(emp.get("emp_id",         "") or "").strip() or "UNKNOWN"

    op_type = request.form.get("op_type", "").strip()  # borrow | withdraw | transfer

    # ── ตัวแปรกลาง (init ก่อนทุก branch) ───────────────────
    asset_name      = ""
    asset_code      = ""
    date_start      = ""
    date_finish     = ""
    request_remark  = request.form.get("detail", "").strip()
    typeproblem     = op_type.upper()
    borrow_site     = ""
    borrow_division = ""
    borrow_cc_id    = ""
    buyer_name      = ""
    buyer_addr      = ""
    buyer_price     = ""
    transfer_data   = {}
    transfer_assets = []   # ← init เสมอ ป้องกัน NameError

    # ══════════════════════════════════════════════════════════
    #  BORROW  (radio ยืม — แยกจาก sub-type BORROW ของโอนย้าย)
    # ══════════════════════════════════════════════════════════
    if op_type == "borrow":
        asset_name           = request.form.get("borrow_asset_request",    "").strip()
        date_start           = request.form.get("borrow_start",            "").strip()
        date_finish          = request.form.get("borrow_end",              "").strip()
        borrow_detail        = request.form.get("borrow_detail",           "").strip()
        borrow_site          = request.form.get("borrow_site",             "").strip()
        borrow_division      = request.form.get("borrow_division",         "").strip()
        borrow_cc_id         = request.form.get("borrow_cost_center",      "").strip()
        borrow_cc_code       = request.form.get("borrow_cost_code",        "").strip()
        borrow_site_name     = request.form.get("borrow_site_name",        "").strip()
        borrow_division_name = request.form.get("borrow_division_name",    "").strip()
        borrow_cost_name     = request.form.get("borrow_cost_center_name", "").strip()

        if borrow_cc_code:
            costcenter = borrow_cc_code

        # ── แยก type ออกจาก BORROW ของโอนย้าย ──────────────
        typeproblem = "BORROW_DIRECT"

        request_remark = (
            f"[ยืม] {asset_name}\n"
            f"Site: {borrow_site_name}\n"
            f"Division: {borrow_division_name}\n"
            f"CostCenter: {borrow_cc_code}\n"
            f"ช่วง: {date_start} ถึง {date_finish}"
        )
        if borrow_detail:
            request_remark += f"\nหมายเหตุ: {borrow_detail}"

        transfer_data = {
            "transfer_type":      "BORROW_DIRECT",
            "transfer_type_name": "ยืม",
            "company_name":       "",
            "from_dept":          "",
            "from_site":          "",
            "from_division":      "",
            "from_costcenter":    "",
            "from_cost_code":     "",
            "from_location":      "",
            "to_dept":            request.form.get("dept", "").strip(),
            "to_site":            borrow_site_name or borrow_site,
            "to_division":        borrow_division_name or borrow_division,
            "to_costcenter":      borrow_cc_id,
            "to_cost_code":       borrow_cc_code,
            "to_location":        "",
            "buyer_name":         "",
            "buyer_address":      "",
            "buyer_price":        "",
            "sender_name":        "",
            "remark":             request_remark,
        }
        transfer_assets = (
            [{"item_no": 1, "asset_code": "", "asset_name": asset_name, "asset_remark": ""}]
            if asset_name else []
        )

    # ══════════════════════════════════════════════════════════
    #  WITHDRAW  (radio เบิก — ไม่ชนกับ type ใดใน sub-type โอนย้าย)
    # ══════════════════════════════════════════════════════════
    elif op_type == "withdraw":
        asset_name             = request.form.get("withdraw_asset_request",  "").strip()
        withdraw_detail        = request.form.get("withdraw_detail",         "").strip()
        withdraw_site          = request.form.get("withdraw_site",           "").strip()
        withdraw_division      = request.form.get("withdraw_division",       "").strip()
        withdraw_cc_id         = request.form.get("withdraw_cost_center",    "").strip()
        withdraw_cc_code       = request.form.get("withdraw_cost_code",      "").strip()
        withdraw_site_name     = request.form.get("withdraw_site_name",      "").strip()
        withdraw_division_name = request.form.get("withdraw_division_name",  "").strip()

        if withdraw_cc_code:
            costcenter = withdraw_cc_code

        borrow_site     = withdraw_site
        borrow_division = withdraw_division
        borrow_cc_id    = withdraw_cc_id

        # typeproblem = "WITHDRAW" (ตั้งไว้แล้วจาก op_type.upper() — ไม่ต้อง override)

        request_remark = (
            f"[เบิก] {asset_name}\n"
            f"Site: {withdraw_site_name or withdraw_site}\n"
            f"Division: {withdraw_division_name or withdraw_division}\n"
            f"CostCenter: {withdraw_cc_code}"
        )
        if withdraw_detail:
            request_remark += f"\nหมายเหตุ: {withdraw_detail}"

        transfer_data = {
            "transfer_type":      "WITHDRAW",
            "transfer_type_name": "เบิก",
            "company_name":       "",
            "from_dept":          "",
            "from_site":          "",
            "from_division":      "",
            "from_costcenter":    "",
            "from_cost_code":     "",
            "from_location":      "",
            "to_dept":            request.form.get("dept", "").strip(),
            "to_site":            withdraw_site_name or withdraw_site,
            "to_division":        withdraw_division_name or withdraw_division,
            "to_costcenter":      withdraw_cc_id,
            "to_cost_code":       withdraw_cc_code,
            "to_location":        "",
            "buyer_name":         "",
            "buyer_address":      "",
            "buyer_price":        "",
            "sender_name":        "",
            "remark":             request_remark,
        }
        transfer_assets = (
            [{"item_no": 1, "asset_code": "", "asset_name": asset_name, "asset_remark": ""}]
            if asset_name else []
        )

    # ══════════════════════════════════════════════════════════
    #  TRANSFER
    # ══════════════════════════════════════════════════════════
    elif op_type == "transfer":
        transfer_sub = (request.form.get("transfer_sub_type") or "TRANSFER").strip()
        type_map = {
            "TRANSFER": "โอนย้ายระหว่างหน่วยงาน",
            "DISPOSE":  "ตัดบัญชี/สูญหาย",
            "SALE":     "เพื่อขาย",
            "REPAIR":   "ส่งซ่อม",
            "BORROW":   "ยืม",
        }
        # ชื่อไทยเก็บไว้ใน request_remark และ transfer_data["transfer_type_name"] เท่านั้น
        typeproblem      = transfer_sub.upper()
        typeproblem_name = type_map.get(transfer_sub, transfer_sub)

        # FROM
        from_dept     = request.form.get("t1_from_dept",        "").strip()
        from_site     = request.form.get("t1_from_site",        "").strip()
        from_div      = request.form.get("t1_from_division",    "").strip()
        from_cc       = request.form.get("t1_from_cost_code",   "").strip()
        from_cc_id    = request.form.get("t1_from_cost_center", "").strip()
        from_location = request.form.get("t1_from_location",    "").strip()

        # TO
        to_dept       = request.form.get("t1_to_dept",          "").strip()
        to_site       = request.form.get("t1_to_site",          "").strip()
        to_div        = request.form.get("t1_to_division",       "").strip()
        to_cc         = request.form.get("t1_to_cost_code",     "").strip()
        to_cc_id      = request.form.get("t1_to_cost_center",   "").strip()
        to_location   = request.form.get("t1_to_location",      "").strip()
        t1_detail     = request.form.get("t1_detail",           "").strip()

        # SENDER NAME (เฉพาะ TRANSFER)
        sender_name = ""
        if transfer_sub == "TRANSFER":
            sender_name = request.form.get("sender_name", "").strip()

        # BUYER (เฉพาะ SALE)
        buyer_name  = request.form.get("t1_buyer_name",    "").strip()
        buyer_addr  = request.form.get("t1_buyer_address", "").strip()
        buyer_price = request.form.get("t1_buyer_price",   "").strip()

        # assets array
        asset_codes   = request.form.getlist("t1_asset_code[]")
        asset_names   = request.form.getlist("t1_asset_name[]")
        asset_remarks = request.form.getlist("t1_asset_remark[]")

        asset_code = ", ".join(c for c in asset_codes if c)
        asset_name = ", ".join(n for n in asset_names if n)

        # สร้าง asset_lines สำหรับ remark
        asset_lines = []
        for i, (ac, an, ar) in enumerate(zip(asset_codes, asset_names, asset_remarks), 1):
            if ac or an:
                line = f"{i}. {ac} {an}"
                if ar:
                    line += f" ({ar})"
                asset_lines.append(line)

        # remark
        request_remark = f"[โอนย้าย: {typeproblem_name}]\n"

        if transfer_sub == "SALE":
            request_remark += f"ต้นทาง: {from_site} / {from_div} / {from_cc} | Location: {from_location}\n"
            if buyer_name:
                request_remark += f"ผู้ซื้อ: {buyer_name}\n"
            if buyer_addr:
                request_remark += f"ที่อยู่: {buyer_addr}\n"
            if buyer_price:
                request_remark += f"ราคา: {buyer_price}\n"
            borrow_site     = from_site
            borrow_division = from_div
            borrow_cc_id    = from_cc_id
        else:
            request_remark += (
                f"ต้นทาง: {from_site} / {from_div} / {from_cc} | Location: {from_location}\n"
                f"ปลายทาง: {to_site} / {to_div} / {to_cc} | Location: {to_location}\n"
            )
            borrow_site     = to_site
            borrow_division = to_div
            borrow_cc_id    = to_cc_id

        if asset_lines:
            request_remark += "สินทรัพย์:\n" + "\n".join(asset_lines) + "\n"
        if t1_detail:
            request_remark += f"หมายเหตุ: {t1_detail}"

        transfer_data = {
            "transfer_type":      transfer_sub,
            "transfer_type_name": typeproblem_name,
            "company_name":       request.form.get("company", ""),
            "from_dept":          from_dept,
            "from_site":          from_site,
            "from_division":      from_div,
            "from_costcenter":    from_cc_id,
            "from_cost_code":     from_cc,
            "from_location":      from_location,
            "to_dept":            to_dept,
            "to_site":            to_site,
            "to_division":        to_div,
            "to_costcenter":      to_cc_id,
            "to_cost_code":       to_cc,
            "to_location":        to_location,
            "buyer_name":         buyer_name,
            "buyer_address":      buyer_addr,
            "buyer_price":        buyer_price,
            "sender_name":        sender_name,
            "remark":             request_remark,
        }

        transfer_assets = [
            {
                "item_no":      i,
                "asset_code":   ac,
                "asset_name":   an,
                "asset_remark": ar,
            }
            for i, (ac, an, ar) in enumerate(zip(asset_codes, asset_names, asset_remarks), 1)
            if ac or an
        ]

    # ══════════════════════════════════════════════════════════
    #  รวม data dict สำหรับ REQUEST
    # ══════════════════════════════════════════════════════════
    data = {
        "request_date":          now_str,
        "request_typeform":      cat["typeform"],
        "request_category":      cat["name"],
        "requester_fname":       fname,
        "requester_lname":       lname,
        "requester_tel":         request.form.get("phone", "").strip(),
        "requester_email":       request.form.get("email", "").strip(),
        "request_remark":        request_remark,
        "request_file":          file_saved or "",
        "requester_empcode":     empcode,
        "requester_dept":        request.form.get("dept", "").strip(),
        "requester_costcenter":  costcenter,
        "request_status":        "0" if skip_approval else "4",
        "requester_ip":          request.remote_addr or "",
        "emp_approver":          emp.get("approver", "") if not skip_approval else "",
        "approver_status":       "Approve" if skip_approval else "Waiting",
        "request_typeproblem":   typeproblem,
        "asset_name":            asset_name,
        "asset_code":            asset_code,
        "date_start":            date_start,
        "date_finish":           date_finish,
        "borrow_site":           borrow_site,
        "borrow_division":       borrow_division,
        "borrow_cost_center_id": borrow_cc_id,
        "buyer_name":            buyer_name,
        "buyer_address":         buyer_addr,
        "buyer_price":           buyer_price,
    }

    print("====== RAW FORM ======")
    print(dict(request.form))
    print("====== FINAL DATA ======")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    # ══════════════════════════════════════════════════════════
    #  DATABASE TRANSACTION
    #  ทุก op_type ใช้ transaction เดียวกัน:
    #  REQUEST + APPROVER + TRANSFER + ASSETS
    # ══════════════════════════════════════════════════════════
    conn = None
    request_id  = None
    try:
        conn = get_conn()
        cur  = conn.cursor()

        # 1. INSERT IT_HELPDESK_REQUEST
        req_id = gen_request_id(cur)
        data["request_id"] = req_id
        cur.execute(INSERT_SQL, {k: data.get(k, "") for k in _REQUEST_KEYS})
        print(f"[INSERT REQUEST OK] request_id={req_id}")

        # 2. INSERT IT_HELPDESK_APPROVER
        cur.execute(INSERT_APPROVER_SQL, {
            "request_id":        req_id,
            "requester_empcode": data.get("requester_empcode", ""),
            "emp_approver":      data.get("emp_approver", ""),
            "req_type":          data.get("request_typeform", ""),
            "req_status":        data.get("approver_status", "Waiting"),
        })
        print(f"[INSERT APPROVER OK]")

        # 3. INSERT IT_HELPDESK_TRANSFER (เฉพาะ op_type == 'transfer' เท่านั้น)
        #    borrow และ withdraw เป็นคำขอธรรมดา ไม่สร้างเอกสารโอนย้าย
        if op_type == "transfer":
            doc_no      = gen_doc_no(cur)
            transfer_id = gen_transfer_id(cur)

            buyer_price_val = None
            try:
                buyer_price_val = float(transfer_data["buyer_price"]) if transfer_data.get("buyer_price") else None
            except (ValueError, TypeError):
                buyer_price_val = None

            cur.execute("""
                INSERT INTO IT_HELPDESK_TRANSFER (
                    ID, DOC_NO, REQUEST_ID,
                    TRANSFER_TYPE, TRANSFER_TYPE_NAME, COMPANY_NAME,
                    FROM_DEPT, FROM_SITE, FROM_DIVISION,
                    FROM_COSTCENTER, FROM_COST_CODE, FROM_LOCATION,
                    TO_DEPT, TO_SITE, TO_DIVISION,
                    TO_COSTCENTER, TO_COST_CODE, TO_LOCATION,
                    BUYER_NAME, BUYER_ADDRESS, BUYER_PRICE,
                    SENDER_NAME,
                    REQUEST_BY, REQUEST_DATE,
                    REMARK, STATUS, CREATED_AT
                ) VALUES (
                    :transfer_id, :doc_no, :request_id,
                    :transfer_type, :transfer_type_name, :company_name,
                    :from_dept, :from_site, :from_division,
                    :from_costcenter, :from_cost_code, :from_location,
                    :to_dept, :to_site, :to_division,
                    :to_costcenter, :to_cost_code, :to_location,
                    :buyer_name, :buyer_address, :buyer_price,
                    :sender_name,
                    :request_by, SYSDATE,
                    :remark, 'PENDING', CURRENT_TIMESTAMP
                )
            """, {
                "transfer_id":        transfer_id,
                "doc_no":             doc_no,
                "request_id":         req_id,
                "transfer_type":      transfer_data["transfer_type"],
                "transfer_type_name": transfer_data["transfer_type_name"],
                "company_name":       transfer_data.get("company_name", ""),
                "from_dept":          transfer_data.get("from_dept", ""),
                "from_site":          transfer_data.get("from_site", ""),
                "from_division":      transfer_data.get("from_division", ""),
                "from_costcenter":    transfer_data.get("from_costcenter", ""),
                "from_cost_code":     transfer_data.get("from_cost_code", ""),
                "from_location":      transfer_data.get("from_location", ""),
                "to_dept":            transfer_data.get("to_dept", ""),
                "to_site":            transfer_data.get("to_site", ""),
                "to_division":        transfer_data.get("to_division", ""),
                "to_costcenter":      transfer_data.get("to_costcenter", ""),
                "to_cost_code":       transfer_data.get("to_cost_code", ""),
                "to_location":        transfer_data.get("to_location", ""),
                "buyer_name":         transfer_data.get("buyer_name", ""),
                "buyer_address":      transfer_data.get("buyer_address", ""),
                "buyer_price":        buyer_price_val,
                "request_by":         empcode,
                "sender_name":        transfer_data.get("sender_name", ""),
                "remark":             transfer_data.get("remark", ""),
            })
            print(f"[INSERT TRANSFER OK] doc_no={doc_no}  transfer_id={transfer_id}  type={transfer_data['transfer_type']}")

            # 4. INSERT IT_HELPDESK_ASSET (loop)
            for a in transfer_assets:
                asset_id = gen_asset_id(cur)
                cur.execute("""
                    INSERT INTO IT_HELPDESK_ASSET (
                        ID, TRANSFER_ID, ITEM_NO,
                        ASSET_CODE, ASSET_NAME, ASSET_REMARK,
                        CREATED_AT
                    ) VALUES (
                        :asset_id, :transfer_id, :item_no,
                        :asset_code, :asset_name, :asset_remark,
                        CURRENT_TIMESTAMP
                    )
                """, {
                    "asset_id":     asset_id,
                    "transfer_id":  transfer_id,
                    "item_no":      a["item_no"],
                    "asset_code":   a["asset_code"],
                    "asset_name":   a["asset_name"],
                    "asset_remark": a["asset_remark"],
                })
                print(f"  [ASSET] id={asset_id} #{a['item_no']} {a['asset_code']} {a['asset_name']}")
        else:
            print(f"[SKIP TRANSFER] op_type={op_type} — ไม่สร้าง TRANSFER record")

        # 5. COMMIT
        conn.commit()
        request_id = req_id
        print(f"[COMMIT OK] request_id={request_id}")

    except cx_Oracle.Error as e:
        if conn:
            try: conn.rollback()
            except: pass
        print(f"[ORACLE ERROR] {e}")
        return jsonify({"status": "error", "message": f"เกิดข้อผิดพลาดในการบันทึก: {e}"}), 500

    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        print(f"[INSERT ERROR] {type(e).__name__}: {e}")
        return jsonify({"status": "error", "message": "เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง"}), 500

    finally:
        if conn:
            try: conn.close()
            except: pass

    # ══════════════════════════════════════════════════════════
    #  SEND LINE (หลัง commit เท่านั้น)
    # ══════════════════════════════════════════════════════════
    if not skip_approval:
        # dev: fix LINE ID / prod: ดึงจาก DB
        #approver_line_id = "U1a079046647a4390627f067ee7e045ca"
        approver_line_id = get_approver_line_id(emp.get("approver", ""))

        if approver_line_id:
            send_line_flex(approver_line_id, str(request_id), data)
        else:
            print("[LINE] ไม่มี LINE_ID ของ approver — ข้ามการส่ง")

    return render_template("success.html", ticket=request_id, cat=cat, op_type=op_type)

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
            # =====================================================
            # MAIN REQUEST
            # =====================================================
            base_sql = """
                SELECT
                    R.REQUEST_ID,
                    R.REQUEST_DATE,
                    R.REQUEST_TYPEFORM,
                    R.REQUESTER_FNAME,
                    R.REQUESTER_LNAME,
                    R.REQUESTER_DEPT,
                    R.REQUESTER_TEL,
                    R.REQUEST_STATUS,
                    R.REQUEST_FILE,
                    R.REQUESTER_EMPCODE,
                    NULL AS UPDATED_AT,
                    R.REQUEST_REMARK,
                    R.REQUEST_CATEGORY,
                    R.REQUEST_TYPEPROBLEM,
                    A.STATUS AS APPROVER_STATUS,
                    T.STATUS AS TRANSFER_STATUS,
                    T.RECEIVER_STATUS,
                    T.MANAGER_APPROVE_DATE,
                    T.TRANSFER_TYPE
                FROM IT_HELPDESK_REQUEST R
                LEFT JOIN IT_HELPDESK_APPROVER A ON R.REQUEST_ID = A.REQUEST_ID
                LEFT JOIN IT_HELPDESK_TRANSFER T ON R.REQUEST_ID = T.REQUEST_ID
                WHERE NVL(R.REQUEST_TYPEPROBLEM,'-') NOT LIKE 'TEST%'
            """

            if req_id:
                cur.execute(
                    base_sql +
                    " AND R.REQUEST_ID = :val "
                    " ORDER BY R.REQUEST_ID DESC",
                    {"val": req_id}
                )
            else:
                cur.execute(
                    base_sql +
                    " AND R.REQUESTER_EMPCODE = :val "
                    " ORDER BY R.REQUEST_ID DESC",
                    {"val": emp_id}
                )

            # =====================================================
            # READ ROW
            # =====================================================

            def read_row(row):
                out = []
                for v in row:
                    if hasattr(v, "read"):
                        out.append(v.read() or "")
                    else:
                        out.append(v)
                return out
            cols = [d[0].lower() for d in cur.description]
            results = [
                dict(zip(cols, read_row(r)))
                for r in cur.fetchall()
            ]

            # =====================================================
            # TRANSFER IDS — ดึงจาก IT_HELPDESK_TRANSFER โดยตรง
            # ไม่ต้อง maintain list type อีกต่อไป
            # =====================================================

            all_request_ids = [str(r["request_id"]) for r in results]
            transfer_ids = []
            if all_request_ids:
                fmt_all = ",".join([":rid" + str(i) for i in range(len(all_request_ids))])
                bind_all = {"rid" + str(i): v for i, v in enumerate(all_request_ids)}
                cur.execute(
                    f"SELECT REQUEST_ID FROM IT_HELPDESK_TRANSFER WHERE REQUEST_ID IN ({fmt_all})",
                    bind_all
                )
                transfer_ids = [str(row[0]) for row in cur.fetchall()]

            # =====================================================
            # LOAD TRANSFER DATA
            # =====================================================

            if transfer_ids:
                fmt = ",".join([
                    ":rid" + str(i)
                    for i in range(len(transfer_ids))
                ])
                bind = dict(
                    ("rid" + str(i), v)
                    for i, v in enumerate(transfer_ids)
                )
                sql = (
                    "SELECT "
                    " T.REQUEST_ID,"
                    " T.TRANSFER_TYPE,"
                    " T.TRANSFER_TYPE_NAME,"
                    " T.MANAGER_APPROVE_DATE,"
                    " T.RECEIVER_STATUS,"
                    " T.STATUS AS TRANSFER_STATUS,"
                    " FD.COST_COMPANY AS FROM_SITE_NAME,"
                    " FD.COST_DEPARTMENT AS FROM_DIV_NAME,"
                    " T.FROM_COST_CODE,"
                    " NULL AS FROM_LOC_NAME,"
                    " TD.COST_COMPANY  AS TO_SITE_NAME,"
                    " TD.COST_DEPARTMENT AS TO_DIV_NAME,"
                    " T.TO_COST_CODE,"
                    " NULL AS TO_LOC_NAME"
                    " FROM IT_HELPDESK_TRANSFER T"
                    " LEFT JOIN IT_HELPDESK_DEPARTMENT FD"
                    " ON (CASE WHEN REGEXP_LIKE(T.FROM_COSTCENTER, '^[0-9]+$') THEN TO_NUMBER(T.FROM_COSTCENTER) ELSE NULL END) = FD.COST_ID"
                    " LEFT JOIN IT_HELPDESK_DEPARTMENT TD"
                    " ON (CASE WHEN REGEXP_LIKE(T.TO_COSTCENTER, '^[0-9]+$') THEN TO_NUMBER(T.TO_COSTCENTER) ELSE NULL END) = TD.COST_ID"
                    " WHERE T.REQUEST_ID IN (" + fmt + ")"
                )

                cur.execute(sql, bind)
                tcols = [
                    d[0].lower()
                    for d in cur.description
                ]
                transfer_map = dict(
                    (
                        str(row[0]),
                        dict(zip(tcols, row))
                    )
                    for row in cur.fetchall()
                )
                # =====================================================
                # BUILD DISPLAY DATA
                # =====================================================
                for r in results:
                    rid = str(r.get("request_id", ""))
                    if rid not in transfer_map:
                            r["has_transfer"] = False
                            # ✅ เช็ค request_status ก่อนทุก logic
                            if str(r.get("request_status", "")) == "5":
                                r["display_status"] = "เสร็จสิ้น"
                                r["display_class"]  = "done"
                                r["display_remark"] = r.get("request_remark", "")
                                continue
                            # fallback สำหรับ non-transfer
                            apv = str(r.get("approver_status") or "")
                            if apv == "Waiting":
                                r["display_status"] = "รออนุมัติ"
                                r["display_class"]  = "4"
                            elif apv == "Approve":
                                r["display_status"] = "อนุมัติแล้ว / รอ IT ดำเนินการ"
                                r["display_class"]  = "5"
                            elif apv == "Reject":
                                r["display_status"] = "ยกเลิก"
                                r["display_class"]  = "3"
                            elif apv == "Done":
                                r["display_status"] = "เสร็จสิ้น"
                                r["display_class"]  = "done"
                            else:
                                r["display_status"] = "รออนุมัติ"
                                r["display_class"]  = "4"
                            r["display_remark"] = r.get("request_remark", "")
                            continue
                    t = transfer_map[rid]
                    r["has_transfer"] = True
                    # -------------------------------------------------
                    # REMARK
                    # -------------------------------------------------
                    parts = [
                        f"[{t.get('transfer_type_name') or 'โอนย้าย'}]"
                    ]
                    from_s = " / ".join(filter(None, [
                        t.get("from_site_name"),
                        t.get("from_div_name"),
                        t.get("from_cost_code")

                    ]))

                    to_s = " / ".join(filter(None, [
                        t.get("to_site_name"),
                        t.get("to_div_name"),
                        t.get("to_cost_code")

                    ]))

                    if from_s:
                        parts.append(
                            f"ต้นทาง: {from_s}" +
                            (
                                f" | Location: {t['from_loc_name']}"
                                if t.get("from_loc_name")
                                else ""
                            )
                        )
                    if to_s:
                        parts.append(
                            f"ปลายทาง: {to_s}" +
                            (
                                f" | Location: {t['to_loc_name']}"
                                if t.get("to_loc_name")
                                else ""
                            )
                        )

                    # -------------------------------------------------
                    # ASSETS
                    # -------------------------------------------------

                    cur.execute("""
                        SELECT
                            ITEM_NO,
                            ASSET_CODE,
                            ASSET_NAME,
                            ASSET_REMARK
                        FROM IT_HELPDESK_ASSET
                        WHERE TRANSFER_ID = (
                            SELECT ID
                            FROM IT_HELPDESK_TRANSFER
                            WHERE REQUEST_ID = :rid
                        )
                        ORDER BY ITEM_NO
                    """, {
                        "rid": rid
                    })

                    asset_rows = cur.fetchall()

                    if asset_rows:
                        parts.append("สินทรัพย์:")
                        for i, a in enumerate(asset_rows, 1):
                            code   = a[1] or ""
                            name   = a[2] or ""
                            remark = a[3] or ""
                            line = f"{i}. {code} {name}".strip()
                            if remark:
                                line += f" ({remark})"
                            parts.append(line)

                    # -------------------------------------------------
                    # EXTRA REMARK
                    # -------------------------------------------------

                    if r.get("request_remark"):
                        remark_lines = (
                            r["request_remark"]
                            .split("\n")
                        )
                        extra = []
                        for line in remark_lines:
                            if "หมายเหตุ:" in line:
                                extra.append(line)
                        if extra:
                            parts.extend(extra)

                    # =================================================
                    # STATUS DISPLAY
                    # =================================================

                    approver_status = str(
                        r.get("approver_status") or ""
                    )

                    transfer_type = str(
                        t.get("transfer_type") or ""
                    )

                    receiver_status = str(
                        t.get("receiver_status") or ""
                    )

                    manager_approve_date = (
                        t.get("manager_approve_date")
                    )

                    display_status = ""
                    display_class  = ""

                    # -------------------------------------------------
                    # ✅ เช็ค REQUEST_STATUS = 5 ก่อนทุก logic
                    # -------------------------------------------------
                    if str(r.get("request_status", "")) == "5":
                        display_status = "เสร็จสิ้น"
                        display_class  = "done"

                    # -------------------------------------------------
                    # WAITING
                    # -------------------------------------------------
                    elif approver_status == "Waiting":
                        display_status = "รออนุมัติ"
                        display_class  = "4"

                    # -------------------------------------------------
                    # APPROVED
                    # -------------------------------------------------

                    elif approver_status == "Approve":
                        # TRANSFER
                        if transfer_type == "TRANSFER":
                            if receiver_status == "Confirmed":
                                display_status = "อยู่ระหว่างรอปิดงานโดย IT"
                            else:
                                display_status = "อนุมัติแล้ว / รอลายเซ็นเพิ่มเติม"

                        # REPAIR / BORROW → ใช้ receiver_status
                        elif transfer_type in ["REPAIR", "BORROW"]:
                            if receiver_status == "Confirmed":
                                display_status = "อยู่ระหว่างรอปิดงานโดย IT"
                            else:
                                display_status = "อนุมัติแล้ว / รอลายเซ็นเพิ่มเติม"

                        # DISPOSE / SALE → ใช้ manager_approve_date
                        elif transfer_type in ["DISPOSE", "SALE"]:
                            if manager_approve_date:
                                display_status = "อยู่ระหว่างรอปิดงานโดย IT"
                            else:
                                display_status = "อนุมัติแล้ว / รอลายเซ็นเพิ่มเติม"
                        else:
                            display_status = "อนุมัติแล้ว / รอ IT ดำเนินการ"

                        display_class = "5"

                    # -------------------------------------------------
                    # REJECT
                    # -------------------------------------------------

                    elif approver_status == "Reject":
                        display_status = "ยกเลิก"
                        display_class  = "3"

                    # -------------------------------------------------
                    # DONE
                    # -------------------------------------------------

                    elif approver_status == "Done":
                        display_status = "เสร็จสิ้น"
                        display_class  = "done"

                    # -------------------------------------------------
                    # DEFAULT
                    # -------------------------------------------------

                    else:
                        t_status = str(t.get("transfer_status") or "").upper()
                        if t_status == "PENDING":
                            display_status = "รออนุมัติ"
                            display_class  = "4"
                        elif t_status in ("RECEIVER_CONFIRMED", "WAITING_IT"):
                            display_status = "อยู่ระหว่างรอปิดงานโดย IT"
                            display_class  = "5"
                        elif t_status == "DONE":
                            display_status = "เสร็จสิ้น"
                            display_class  = "5"
                        else:
                            display_status = "รออนุมัติ"
                            display_class  = "4"

                    # inject
                    r["display_status"] = display_status
                    r["display_class"]  = display_class

                    # final remark
                    r["display_remark"] = "\n".join(parts)

        except Exception as e:
            error = str(e)
            print(
                f"[TRACK ERROR] "
                f"{type(e).__name__}: {e}"
            )
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

    return render_template(
        "track.html",
        results=results,
        ticket_no=req_id,
        emp_id=emp_id,
        error=error,
        status_map=REQUEST_STATUS_MAP,
        approver_status_map=APPROVER_STATUS_MAP
    )

@app.route("/track/<string:ticket_no>")
def track_detail(ticket_no):
    emp_id = request.args.get("emp_id", "").strip()
    ticket = request.args.get("ticket", "").strip()
    item     = None
    transfer = None
    assets   = []
    error    = None
    conn     = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        # ── REQUEST ──────────────────────────────────────────
        cur.execute("""
            SELECT R.REQUEST_ID, R.REQUEST_DATE, R.REQUEST_TYPEFORM,
                   R.REQUESTER_FNAME, R.REQUESTER_LNAME, R.REQUESTER_DEPT,
                   R.REQUESTER_TEL, R.REQUESTER_EMPCODE, R.REQUESTER_IP,
                   R.REQUEST_REMARK, R.REQUEST_FILE, R.REQUEST_STATUS,
                   R.REQUEST_SOLUTION, R.UPDATED_AT, R.REQUEST_CATEGORY,
                   R.REQUEST_TYPEPROBLEM,
                   A.STATUS AS APPROVER_STATUS,
                   A.EMP_APPROVER,
                   A.RESEND_COUNT,
                   A.LAST_RESEND_AT
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

       # ── TRANSFER detail (typeform = 4) ──────────────
        if item and str(item.get("request_typeform","")) == "4":
            cur.execute("""
                SELECT
                    -- Transfer Header
                    T.ID,
                    T.DOC_NO,
                    T.TRANSFER_TYPE,
                    T.TRANSFER_TYPE_NAME,

                    -- From Info
                    T.FROM_DEPT,
                    T.FROM_SITE,
                    FD.COST_COMPANY     AS FROM_SITE_NAME,
                    T.FROM_DIVISION,
                    FD.COST_DEPARTMENT  AS FROM_DIVISION_NAME,
                    T.FROM_COST_CODE,
                    T.FROM_LOCATION,
                    NULL                AS FROM_LOCATION_NAME,

                    -- To Info
                    T.TO_DEPT,
                    T.TO_SITE,
                    TD2.COST_COMPANY    AS TO_SITE_NAME,
                    T.TO_DIVISION,
                    TD2.COST_DEPARTMENT AS TO_DIVISION_NAME,
                    T.TO_COST_CODE,
                    T.TO_LOCATION,
                    NULL                AS TO_LOCATION_NAME,

                    -- Buyer Info
                    T.BUYER_NAME,
                    T.BUYER_ADDRESS,
                    T.BUYER_PRICE,

                    -- General
                    T.REMARK,
                    T.STATUS,

                    -- Receiver Info
                    T.RECEIVER_EMP_CODE,
                    T.RECEIVER_NAME,
                    T.RECEIVER_LINE_ID,
                    T.RECEIVER_TYPE,
                    T.RECEIVER_STATUS,
                    T.RECEIVER_SENT_AT,
                    T.RECEIVER_APPROVED_AT,
                    T.RECEIVER_RESEND_COUNT,

                    -- Manager Info
                    T.MANAGER_APPROVE_BY,
                    T.MANAGER_APPROVE_DATE,
                    T.MANAGER_EMP_CODE,
                    T.MANAGER_NAME,
                    T.MANAGER_LINE_ID,

                    -- Sender Info
                    T.SENDER_NAME

                FROM IT_HELPDESK_TRANSFER T
                    LEFT JOIN IT_HELPDESK_DEPARTMENT FD
                        ON (CASE WHEN REGEXP_LIKE(T.FROM_COSTCENTER, '^[0-9]+$') THEN TO_NUMBER(T.FROM_COSTCENTER) ELSE NULL END) = FD.COST_ID
                    LEFT JOIN IT_HELPDESK_DEPARTMENT TD2
                        ON (CASE WHEN REGEXP_LIKE(T.TO_COSTCENTER, '^[0-9]+$') THEN TO_NUMBER(T.TO_COSTCENTER) ELSE NULL END) = TD2.COST_ID

                WHERE T.REQUEST_ID = :val
            """, {"val": ticket_no})
            trow = cur.fetchone()
            if trow:
                tcols    = [d[0].lower() for d in cur.description]
                transfer = dict(zip(tcols, [read_val(v) for v in trow]))

                cur.execute("""
                    SELECT ITEM_NO, ASSET_CODE, ASSET_NAME, ASSET_REMARK
                    FROM   IT_HELPDESK_ASSET
                    WHERE  TRANSFER_ID = :tid
                    ORDER  BY ITEM_NO
                """, {"tid": transfer["id"]})
                assets = [
                    dict(zip([d[0].lower() for d in cur.description], row))
                    for row in cur.fetchall()
                ]

    except Exception as e:
        error = str(e)
        print(f"[TRACK_DETAIL ERROR] {type(e).__name__}: {e}")
    finally:
        if conn:
            try: conn.close()
            except: pass

        return render_template(
            "track_detail.html",
            item=item,
            ticket_no=ticket_no,
            emp_id=emp_id,
            ticket=ticket,
            error=error,
            transfer=transfer,
            assets=assets,
            status_map=REQUEST_STATUS_MAP,
            approver_status_map=APPROVER_STATUS_MAP
        )

# ══════════════════════════════════════════════════════════════
#  UPDATE SENDER NAME API (สำหรับ TRANSFER)
# ══════════════════════════════════════════════════════════════
@app.route("/api/update-sender/<string:ticket_no>", methods=["POST"])
def update_sender(ticket_no):
    data        = request.get_json(force=True, silent=True) or {}
    sender_name = (data.get("sender_name") or "").strip()
    if not sender_name:
        return jsonify({"ok": False, "message": "กรอกชื่อผู้ส่ง"}), 400
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE IT_HELPDESK_TRANSFER
            SET SENDER_NAME = :sname
            WHERE REQUEST_ID = :rid
        """, {"sname": sender_name, "rid": ticket_no})
        conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        print("[UPDATE SENDER ERROR]", e)
        return jsonify({"ok": False, "message": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route("/api/resend-approver/<string:ticket_no>", methods=["POST"])
def resend_approver(ticket_no):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        # ─────────────────────────────────────────────
        # โหลด request + approver status
        # ─────────────────────────────────────────────
        cur.execute("""
            SELECT
                R.REQUEST_ID,
                A.STATUS,
                A.EMP_APPROVER,
                A.RESEND_COUNT,
                A.LAST_RESEND_AT
            FROM IT_HELPDESK_REQUEST R
            LEFT JOIN IT_HELPDESK_APPROVER A
                   ON R.REQUEST_ID = A.REQUEST_ID
            WHERE R.REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })
        row = cur.fetchone()
        if not row:
            return jsonify({
                "ok": False,
                "message": "ไม่พบรายการ"
            }), 404
        request_id, approve_status, emp_approver, resend_count, last_resend_at = row

        # ─────────────────────────────────────────────
        # อนุมัติแล้ว → ห้าม resend
        # ─────────────────────────────────────────────
        if approve_status != "Waiting":
            return jsonify({
                "ok": False,
                "message": "รายการนี้ไม่ได้อยู่ในสถานะรออนุมัติ"
            }), 400

        # ─────────────────────────────────────────────
        # จำนวน resend
        # ─────────────────────────────────────────────
        resend_count = int(resend_count or 0)

        # ─────────────────────────────────────────────
        # cooldown 15 นาที
        # ─────────────────────────────────────────────
        if last_resend_at:
            diff = datetime.now() - last_resend_at
            if diff.total_seconds() < 900:
                remain = int(900 - diff.total_seconds())
                return jsonify({
                    "ok": False,
                    "cooldown": True,
                    "remain": remain,
                    "count": resend_count
                })

        # ─────────────────────────────────────────────
        # หา LINE ID ของ approver
        # ─────────────────────────────────────────────

        # DEV MODE
        #approver_line_id = "U1a079046647a4390627f067ee7e045ca"

        # PROD MODE
        approver_line_id = get_approver_line_id(emp_approver)

        print("EMP_APPROVER =", emp_approver)
        print("LINE_ID =", approver_line_id)

        if not approver_line_id:
            return jsonify({
                "ok": False,
                "message": "ไม่พบ LINE ของหัวหน้า"
            }), 400

        # ─────────────────────────────────────────────
        # โหลด request data สำหรับส่ง LINE
        # ─────────────────────────────────────────────
        cur.execute("""
            SELECT
                REQUEST_CATEGORY,
                REQUEST_REMARK,
                REQUEST_DATE,
                REQUESTER_FNAME,
                REQUESTER_LNAME
            FROM IT_HELPDESK_REQUEST
            WHERE REQUEST_ID = :rid
        """, {
            "rid": request_id
        })
        r = cur.fetchone()
        if not r:
            return jsonify({
                "ok": False,
                "message": "ไม่พบข้อมูล request"
            }), 404

        # ─────────────────────────────────────────────
        # helper อ่านค่า LOB
        # ─────────────────────────────────────────────
        def read_val(v):
            return v.read() if hasattr(v, "read") else v

        # ─────────────────────────────────────────────
        # build data สำหรับ flex message
        # ─────────────────────────────────────────────
        data = {
            "request_category": read_val(r[0]),
            "request_remark": read_val(r[1]),
            "request_date": str(read_val(r[2])),
            "requester_fname": read_val(r[3]),
            "requester_lname": read_val(r[4])
        }

        # ─────────────────────────────────────────────
        # ส่ง LINE
        # ─────────────────────────────────────────────
        ok = send_line_flex(
            approver_line_id,
            str(request_id),
            data
        )

        if not ok:
            return jsonify({
                "ok": False,
                "message": "ส่ง LINE ไม่สำเร็จ"
            }), 500

        # ─────────────────────────────────────────────
        # update resend status
        # ─────────────────────────────────────────────
        cur.execute("""
            UPDATE IT_HELPDESK_APPROVER
            SET
                RESEND_COUNT = NVL(RESEND_COUNT, 0) + 1,
                LAST_RESEND_AT = SYSDATE
            WHERE REQUEST_ID = :rid
        """, {
            "rid": request_id
        })
        conn.commit()

        # ─────────────────────────────────────────────
        # success
        # ─────────────────────────────────────────────
        return jsonify({
            "ok": True,
            "count": resend_count + 1
        })
    except Exception as e:
        print("[RESEND ERROR]", e)
        return jsonify({
            "ok": False,
            "message": str(e)
        }), 500

    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

# ══════════════════════════════════════════════════════════════
#  SEND RECEIVER API (สำหรับ transfer)
# ══════════════════════════════════════════════════════════════
@app.route("/api/send-receiver/<string:ticket_no>", methods=["POST"])
def send_receiver(ticket_no):

    conn = None
    try:
        data = request.get_json()
        receiver_type = (
            data.get("receiver_type") or ""
        ).strip()
        emp_code = (
            data.get("emp_code") or ""
        ).strip()
        conn = get_conn()
        cur = conn.cursor()

        # ------------------------------------------------
        # CHECK TRANSFER
        # ------------------------------------------------
        cur.execute("""
            SELECT ID
            FROM IT_HELPDESK_TRANSFER
            WHERE REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })

        row = cur.fetchone()
        if not row:
            return jsonify({
                "ok": False,
                "message": "ไม่พบรายการ transfer"
            }), 404

        # ------------------------------------------------
        # FIX EMP CODE
        # warehouse = fix employee
        # internal = ใช้ emp_code จาก user
        # supplier = ชื่อ text ธรรมดา ไม่ผ่าน LINE
        # ------------------------------------------------

        if receiver_type == "supplier":
            sender_name = (
                data.get("sender_name") or ""
            ).strip()
            if not sender_name:
                return jsonify({
                    "ok": False,
                    "message": "กรอกชื่อ Supplier"
                }), 400

            # ── Supplier ไม่มี LINE — บันทึกชื่อแล้ว Confirm ทันที ──
            cur.execute("""
                UPDATE IT_HELPDESK_TRANSFER
                SET
                    RECEIVER_TYPE          = :rtype,
                    SENDER_NAME            = :sname,
                    RECEIVER_STATUS        = 'Confirmed',
                    RECEIVER_SENT_AT       = SYSDATE,
                    RECEIVER_APPROVED_AT   = SYSDATE,
                    RECEIVER_RESEND_COUNT  =
                        NVL(RECEIVER_RESEND_COUNT, 0) + 1
                WHERE REQUEST_ID = :rid
            """, {
                "rtype": "supplier",
                "sname": sender_name,
                "rid": ticket_no,

            })
            conn.commit()
            check_transfer_complete(ticket_no)
            return jsonify({
                "ok": True
            })
        # เจ้าหน้าที่คลัง พี่น้ำฝน พี่ซุป
        elif receiver_type == "warehouse":
            WAREHOUSE_EMP_IDS = ["4620017", "2550335"]

            cur.execute("""
                SELECT REQUEST_CATEGORY, REQUEST_REMARK, REQUEST_DATE,
                    REQUESTER_FNAME, REQUESTER_LNAME
                FROM IT_HELPDESK_REQUEST WHERE REQUEST_ID = :rid
            """, {"rid": ticket_no})
            r = cur.fetchone()
            if r:
                def read_val(v): return v.read() if hasattr(v, "read") else v
                line_data = {
                    "request_category": read_val(r[0]),
                    "request_remark":   read_val(r[1]),
                    "request_date":     str(read_val(r[2])),
                    "requester_fname":  read_val(r[3]),
                    "requester_lname":  read_val(r[4])
                }
                for wh_emp in WAREHOUSE_EMP_IDS:
                    wh_data = get_employee_line(wh_emp)
                    if wh_data and wh_data["line_id"]:
                        send_manager_flex(wh_data["line_id"], str(ticket_no), line_data, action="receiver")
                        print(f"[WAREHOUSE] ส่งให้ {wh_data['name']} ({wh_emp}) สำเร็จ")
            receiver_emp_code = WAREHOUSE_EMP_IDS[0]

        elif receiver_type == "internal":
            if not emp_code:
                return jsonify({
                    "ok": False,
                    "message": "กรอกรหัสพนักงาน"
                }), 400
            receiver_emp_code = emp_code
        else:
            return jsonify({
                "ok": False,
                "message": "ประเภทผู้รับไม่ถูกต้อง"
            }), 400

        # ------------------------------------------------
        # GET EMPLOYEE + LINE
        # ------------------------------------------------
        emp_data = get_employee_line(
            receiver_emp_code
        )
        if not emp_data:
            return jsonify({
                "ok": False,
                "message": "ไม่พบข้อมูลพนักงาน"
            }), 404

        receiver_name = emp_data["name"]
        receiver_line = emp_data["line_id"]

        if not receiver_line:
            return jsonify({
                "ok": False,
                "message": "พนักงานยังไม่ได้ผูก LINE"
            }), 400

        # ------------------------------------------------
        # REQUEST DATA
        # ------------------------------------------------

        cur.execute("""
            SELECT
                REQUEST_CATEGORY,
                REQUEST_REMARK,
                REQUEST_DATE,
                REQUESTER_FNAME,
                REQUESTER_LNAME
            FROM IT_HELPDESK_REQUEST
            WHERE REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })

        r = cur.fetchone()
        if not r:
            return jsonify({
                "ok": False,
                "message": "ไม่พบ request"
            }), 404

        def read_val(v):
            return v.read() if hasattr(v, "read") else v
        
        line_data = {
            "request_category": read_val(r[0]),
            "request_remark": read_val(r[1]),
            "request_date": str(read_val(r[2])),
            "requester_fname": read_val(r[3]),
            "requester_lname": read_val(r[4])
        }

        # ------------------------------------------------
        # SEND LINE
        # ------------------------------------------------
        ok = send_manager_flex(
            receiver_line,
            str(ticket_no),
            line_data,
            action="receiver"
        )

        if not ok:
            return jsonify({
                "ok": False,
                "message": "ส่ง LINE ไม่สำเร็จ"
            }), 500

        # ------------------------------------------------
        # UPDATE DB
        # ------------------------------------------------

        cur.execute("""
            UPDATE IT_HELPDESK_TRANSFER
            SET
                RECEIVER_TYPE = :rtype,
                RECEIVER_NAME = :rname,
                RECEIVER_EMP_CODE = :remp,
                RECEIVER_LINE_ID = :rline,
                RECEIVER_STATUS = 'Waiting',
                RECEIVER_SENT_AT = SYSDATE,
                RECEIVER_RESEND_COUNT =
                    NVL(RECEIVER_RESEND_COUNT,0) + 1
            WHERE REQUEST_ID = :rid
        """, {
            "rtype": receiver_type,
            "rname": receiver_name,
            "remp": receiver_emp_code,
            "rline": receiver_line,
            "rid": ticket_no
        })
        conn.commit()
        return jsonify({
            "ok": True
        })

    except Exception as e:
        print("[SEND RECEIVER ERROR]", e)
        return jsonify({
            "ok": False,
            "message": str(e)
        }), 500
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

# ══════════════════════════════════════════════════════════════
#   SEND MANAGER API (สำหรับ DISPOSE)
# ══════════════════════════════════════════════════════════════
@app.route("/api/send-manager/<string:ticket_no>", methods=["POST"])
def send_manager(ticket_no):

    conn = None
    try:
        # FIXED MANAGER EMP
        emp_code = "4670008"
        conn = get_conn()
        cur = conn.cursor()

        # -----------------------------------
        # CHECK TRANSFER
        # -----------------------------------

        cur.execute("""
            SELECT
                ID,
                TRANSFER_TYPE
            FROM IT_HELPDESK_TRANSFER
            WHERE REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })

        row = cur.fetchone()

        if not row:

            return jsonify({
                "ok": False,
                "message": "ไม่พบรายการ"
            }), 404

        transfer_id, transfer_type = row

        # -----------------------------------
        # DISPOSE + SALE
        # -----------------------------------

        if transfer_type not in ["DISPOSE", "SALE", "REPAIR", "BORROW"]:

            return jsonify({
                "ok": False,
                "message": "รายการนี้ไม่รองรับการอนุมัติขั้นสุดท้าย"
            }), 400

        # -----------------------------------
        # GET EMPLOYEE
        # -----------------------------------

        emp_data = get_employee_line(emp_code)

        if not emp_data:

            return jsonify({
                "ok": False,
                "message": "ไม่พบพนักงาน"
            }), 404

        manager_name = emp_data["name"]
        manager_line = emp_data["line_id"]

        if not manager_line:

            return jsonify({
                "ok": False,
                "message": "พนักงานยังไม่ได้ผูก LINE"
            }), 400

        # -----------------------------------
        # REQUEST DATA
        # -----------------------------------

        cur.execute("""
            SELECT
                REQUEST_CATEGORY,
                REQUEST_REMARK,
                REQUEST_DATE,
                REQUESTER_FNAME,
                REQUESTER_LNAME
            FROM IT_HELPDESK_REQUEST
            WHERE REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })

        r = cur.fetchone()

        if not r:
            return jsonify({
                "ok": False,
                "message": "ไม่พบ REQUEST"
            }), 404

        def read_val(v):
            return (
                v.read()
                if hasattr(v, "read")
                else v
            )

        line_data = {
            "request_category": read_val(r[0]),
            "request_remark": read_val(r[1]),
            "request_date": str(read_val(r[2])),
            "requester_fname": read_val(r[3]),
            "requester_lname": read_val(r[4])
        }

        # -----------------------------------
        # SEND LINE
        # -----------------------------------

        ok = send_manager_flex(
            manager_line,
            str(ticket_no),
            line_data,
            action="manager"
        )

        if not ok:
            return jsonify({
                "ok": False,
                "message": "ส่ง LINE ไม่สำเร็จ"
            }), 500

        # -----------------------------------
        # UPDATE DB
        # -----------------------------------

        cur.execute("""
            UPDATE IT_HELPDESK_TRANSFER
            SET
                MANAGER_NAME = :mname,
                MANAGER_EMP_CODE = :memp,
                MANAGER_LINE_ID = :mline

            WHERE REQUEST_ID = :rid
        """, {
            "mname": manager_name,
            "memp": emp_code,
            "mline": manager_line,
            "rid": ticket_no
        })

        conn.commit()
        return jsonify({
            "ok": True
        })

    except Exception as e:

        print(
            "[SEND MANAGER ERROR]",
            e
        )

        return jsonify({
            "ok": False,
            "message": str(e)
        }), 500

    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

@app.route("/api/manager-confirm")
def manager_confirm():
    req_id = (
        request.args.get("ref") or ""
    ).strip()

    print("========== MANAGER CONFIRM ==========")
    print("REQ ID =", req_id)

    if not req_id:
        return """
        <h3>ไม่พบเลขเอกสาร</h3>
        """
    conn = None

    try:
        conn = get_conn()
        cur = conn.cursor()
        # LOAD
        cur.execute("""
            SELECT
                MANAGER_NAME,
                TRANSFER_TYPE,
                MANAGER_APPROVE_DATE
            FROM IT_HELPDESK_TRANSFER
            WHERE REQUEST_ID = :rid
        """, {
            "rid": req_id
        })

        row = cur.fetchone()
        if not row:
            print("NOT FOUND")
            return """
            <h3>ไม่พบรายการ</h3>
            """

        manager_name, transfer_type, approved = row

        print("TRANSFER TYPE =", transfer_type)
        print("MANAGER =", manager_name)
        print("APPROVED =", approved)

        # DISPOSE + SALE + REPAIR + BORROW
        if transfer_type not in ["DISPOSE", "SALE", "REPAIR", "BORROW"]:
            print("NOT ALLOWED")
            return """
            <h3>รายการนี้ไม่รองรับการอนุมัติขั้นสุดท้าย</h3>
            """

        # DUPLICATE
        if approved:
            print("ALREADY APPROVED")
            return f"""
            <div style="font-family:sans-serif;padding:40px;">
                <h2>อนุมัติแล้ว</h2>
                <p>{manager_name}</p>
            </div>
            """

        # UPDATE
        cur.execute("""
            UPDATE IT_HELPDESK_TRANSFER
            SET
                MANAGER_APPROVE_BY = MANAGER_NAME,
                MANAGER_APPROVE_DATE = SYSDATE
            WHERE REQUEST_ID = :rid
        """, {
            "rid": req_id
        })

        conn.commit()
        print("UPDATE MANAGER SUCCESS")

        # CHECK COMPLETE
        check_transfer_complete(req_id)

        return f"""
            <div style="font-family:sans-serif; padding:40px;text-align:center;">
            <div style="font-size:70px;margin-bottom:10px;">
                ✅
            </div>
            <h2>อนุมัติเรียบร้อย</h2>
            <div style="color:#666;margin-top:10px;">
                {manager_name}
            </div>
            <div style="margin-top:20px;color:#999;font-size:13px;">
                เลขที่เอกสาร {req_id}
            </div>
        </div>
        """

    except Exception as e:
        print(
            "[MANAGER CONFIRM ERROR]",
            e
        )
        return f"""
        <h3>ERROR</h3>
        <pre>{e}</pre>
        """

    finally:
        if conn:
            try:
                conn.close()
            except:
                pass


# ══════════════════════════════════════════════════════════════
#  RESEND RECEIVER API
# ══════════════════════════════════════════════════════════════
@app.route("/api/resend-receiver/<string:ticket_no>", methods=["POST"])
def resend_receiver(ticket_no):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        # ─────────────────────────────────────────────
        # โหลด receiver status
        # ─────────────────────────────────────────────
        cur.execute("""
            SELECT
                RECEIVER_STATUS,
                RECEIVER_LINE_ID,
                RECEIVER_NAME,
                RECEIVER_RESEND_COUNT,
                RECEIVER_SENT_AT
            FROM IT_HELPDESK_TRANSFER
            WHERE REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })
        row = cur.fetchone()
        if not row:
            return jsonify({
                "ok": False,
                "message": "ไม่พบข้อมูลผู้รับ"
            }), 404
        (
            receiver_status,
            receiver_line_id,
            receiver_name,
            resend_count,
            last_sent_at
        ) = row

        # ─────────────────────────────────────────────
        # confirm แล้ว → ห้าม resend
        # ─────────────────────────────────────────────
        if receiver_status == "Confirmed":
            return jsonify({
                "ok": False,
                "message": "ผู้รับยืนยันแล้ว"
            }), 400

        # ─────────────────────────────────────────────
        # cooldown 15 นาที
        # ─────────────────────────────────────────────
        if last_sent_at:
            diff = datetime.now() - last_sent_at
            if diff.total_seconds() < 900:
                remain = int(900 - diff.total_seconds())
                return jsonify({
                    "ok": False,
                    "cooldown": True,
                    "remain": remain
                })

        # ─────────────────────────────────────────────
        # โหลด request data
        # ─────────────────────────────────────────────
        cur.execute("""
            SELECT
                REQUEST_CATEGORY,
                REQUEST_REMARK,
                REQUEST_DATE,
                REQUESTER_FNAME,
                REQUESTER_LNAME
            FROM IT_HELPDESK_REQUEST
            WHERE REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })

        r = cur.fetchone()
        if not r:
            return jsonify({
                "ok": False,
                "message": "ไม่พบ request"
            }), 404

        def read_val(v):
            return v.read() if hasattr(v, "read") else v
        
        line_data = {
            "request_category": read_val(r[0]),
            "request_remark": read_val(r[1]),
            "request_date": str(read_val(r[2])),
            "requester_fname": read_val(r[3]),
            "requester_lname": read_val(r[4])
        }

        # ─────────────────────────────────────────────
        # ส่ง LINE
        # ─────────────────────────────────────────────
        ok = send_manager_flex(
            receiver_line_id,
            str(ticket_no),
            line_data,
            action="receiver"
        )

        if not ok:
            return jsonify({
                "ok": False,
                "message": "ส่ง LINE ไม่สำเร็จ"
            }), 500

        # ─────────────────────────────────────────────
        # update resend count
        # ─────────────────────────────────────────────
        cur.execute("""
            UPDATE IT_HELPDESK_TRANSFER
            SET
                RECEIVER_RESEND_COUNT =
                    NVL(RECEIVER_RESEND_COUNT,0) + 1,

                RECEIVER_SENT_AT = SYSDATE

            WHERE REQUEST_ID = :rid
        """, {
            "rid": ticket_no
        })

        conn.commit()

        return jsonify({
            "ok": True
        })

    except Exception as e:
        print("[RESEND RECEIVER ERROR]", e)
        return jsonify({
            "ok": False,
            "message": str(e)
        }), 500
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass


@app.route("/debug/db")
def debug_db():
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

        # เพิ่มตรงนี้
        cur.execute("SELECT * FROM IT_HELPDESK_TRANSFER WHERE ROWNUM <= 1")
        trans_cols = [d[0] for d in cur.description]
        lines.append(f"<b>IT_HELPDESK_TRANSFER columns:</b> {', '.join(trans_cols)}")

        conn.close()
    except Exception as e:
        lines.append(f"ERR {type(e).__name__}: {e}")
    return "<br><br>".join(lines), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/liff/set-session", methods=["POST"])
def liff_set_session():
    data = request.get_json(force=True, silent=True) or {}
    line_user_id = data.get("line_user_id", "").strip()
    print(f"[LIFF SET SESSION] line_user_id={line_user_id}")
    if line_user_id:
        session["line_id"] = line_user_id
    return jsonify({"ok": bool(line_user_id)})

@app.route("/api/categories")
def api_categories():
    return jsonify(get_categories())

@app.route("/liff/clear-session", methods=["POST"])
def liff_clear_session():
    session.pop("line_id", None)
    return jsonify({"ok": True})

# ══════════════════════════════════════════════════════════════
#  CASCADE DROPDOWN APIs
# ══════════════════════════════════════════════════════════════
@app.route("/api/dept/sites")
def api_dept_sites():
    """ดึงรายการ Site ที่ไม่ซ้ำจาก COST_COMPANY"""
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT COST_COMPANY
            FROM IT_HELPDESK_DEPARTMENT
            WHERE COST_STATUS = 'Active'
              AND COST_COMPANY IS NOT NULL
            ORDER BY COST_COMPANY
        """)
        return jsonify([{"name": r[0]} for r in cur.fetchall()])
    except Exception as e:
        print(f"[API dept/sites] {e}")
        return jsonify([]), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

@app.route("/api/dept/departments")
def api_dept_departments():
    """ดึงรายการ ฝ่าย ตาม Site (COST_COMPANY)"""
    site = request.args.get("site", "").strip()
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT COST_DEPARTMENT
            FROM IT_HELPDESK_DEPARTMENT
            WHERE COST_STATUS = 'Active'
              AND COST_COMPANY = :site
              AND COST_DEPARTMENT IS NOT NULL
            ORDER BY COST_DEPARTMENT
        """, {"site": site})
        return jsonify([{"name": r[0]} for r in cur.fetchall()])
    except Exception as e:
        print(f"[API dept/departments] {e}")
        return jsonify([]), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

@app.route("/api/dept/costcenters")
def api_dept_costcenters():
    """ดึงรายการ Cost center ตาม Site + ฝ่าย"""
    site = request.args.get("site", "").strip()
    dept = request.args.get("dept", "").strip()
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT COST_ID, COST_COSTCENTER, COST_DESCRIPTION, COST_COSTDEP
            FROM IT_HELPDESK_DEPARTMENT
            WHERE COST_STATUS = 'Active'
              AND COST_COMPANY = :site
              AND COST_DEPARTMENT = :dept
            ORDER BY COST_COSTCENTER
        """, {"site": site, "dept": dept})
        return jsonify([
            {
                "id":   r[0],
                "code": r[1] or "",
                "name": r[2] or "",
                "dep":  r[3] or ""
            }
            for r in cur.fetchall()
        ])
    except Exception as e:
        print(f"[API dept/costcenters] {e}")
        return jsonify([]), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

# Location
@app.route("/api/locations")
def api_locations():
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT LOC_CODE, LOC_NAME
            FROM   IT_HELPDESK_RES_LOCATION
            ORDER BY LOC_CODE
        """)
        return jsonify([
            {"code": r[0], "name": r[1]}
            for r in cur.fetchall()
        ])
    except Exception as e:
        print(f"[API locations] {e}")
        return jsonify([]), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

@app.route('/api/companies')
def api_companies():
    """Return distinct companies (Site dropdown)"""
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT COST_COMPANY
            FROM IT_HELPDESK_DEPARTMENT
            WHERE COST_COMPANY IS NOT NULL
              AND COST_STATUS = 'Active'
            ORDER BY COST_COMPANY
        """)
        return jsonify([{"id": r[0], "name": r[0]} for r in cur.fetchall()])
    except Exception as e:
        print(f"[API companies] {e}")
        return jsonify([]), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route('/api/departments/<comp_id>')
def api_departments(comp_id):
    """Return departments filtered by company (ฝ่าย dropdown)"""
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT DISTINCT COST_COSTDEP, COST_DEPARTMENT
            FROM IT_HELPDESK_DEPARTMENT
            WHERE COST_COMPANY   = :comp
              AND COST_COSTDEP   IS NOT NULL
              AND COST_STATUS    = 'Active'
            ORDER BY COST_DEPARTMENT
        """, {"comp": comp_id})
        return jsonify([
            {"id": r[0], "name": r[1], "code": r[0]}
            for r in cur.fetchall()
        ])
    except Exception as e:
        print(f"[API departments] {e}")
        return jsonify([]), 500
    finally:
        if conn:
            try: conn.close()
            except: pass


@app.route('/api/costcenters/<dept_id>')
def api_costcenters(dept_id):
    """Return cost centers filtered by dept (Cost center dropdown)"""
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()
        cur.execute("""
            SELECT COST_COSTCENTER, COST_DESCRIPTION
            FROM IT_HELPDESK_DEPARTMENT
            WHERE COST_COSTDEP = :dept
              AND COST_STATUS != 'Block'
            ORDER BY COST_COSTCENTER
        """, {"dept": dept_id})
        return jsonify([
            {"id": r[0], "name": r[1] or r[0], "code": r[0]}
            for r in cur.fetchall()
        ])
    except Exception as e:
        print(f"[API costcenters] {e}")
        return jsonify([]), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

# ══════════════════════════════════════════════════════════════
#  CANCEL REQUEST API (เฉพาะสถานะ Waiting เท่านั้น)
# ══════════════════════════════════════════════════════════════
@app.route("/api/cancel/<string:ticket_no>", methods=["POST"])
def cancel_request(ticket_no):
    # ดึง empcode ของ user ที่ login อยู่
    line_id  = session.get("line_id", "")
    employee = get_employee_by_line_id(line_id) if line_id else {}
    empcode  = str((employee or {}).get("emp_id", "") or "").strip() or "UNKNOWN"

    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()

        # 1. เช็คสถานะปัจจุบัน
        cur.execute("""
            SELECT A.STATUS, R.REQUESTER_EMPCODE
            FROM IT_HELPDESK_APPROVER A
            JOIN IT_HELPDESK_REQUEST R ON A.REQUEST_ID = R.REQUEST_ID
            WHERE A.REQUEST_ID = :rid
        """, {"rid": ticket_no})

        row = cur.fetchone()
        if not row:
            return jsonify({"ok": False, "message": "ไม่พบรายการ"}), 404

        current_status, requester_empcode = row

        # 2. อนุญาตยกเลิกเฉพาะ Waiting เท่านั้น
        if current_status != "Waiting":
            return jsonify({"ok": False, "message": "ไม่สามารถยกเลิกได้ เนื่องจากสถานะไม่ใช่ รออนุมัติ"}), 400

        # 3. UPDATE IT_HELPDESK_APPROVER
        cur.execute("""
            UPDATE IT_HELPDESK_APPROVER
            SET STATUS      = 'Reject',
                USER_UPDATE = :user_update,
                DATE_UPDATE = SYSDATE
            WHERE REQUEST_ID = :rid
        """, {"user_update": empcode, "rid": ticket_no})

        # 4. UPDATE IT_HELPDESK_REQUEST (sync สถานะหลัก)
        cur.execute("""
            UPDATE IT_HELPDESK_REQUEST
            SET REQUEST_STATUS = '3'
            WHERE REQUEST_ID = :rid
        """, {"rid": ticket_no})

        conn.commit()
        print(f"[CANCEL] ticket={ticket_no} by={empcode}")
        return jsonify({"ok": True})

    except Exception as e:
        if conn:
            try: conn.rollback()
            except: pass
        print(f"[CANCEL ERROR] {e}")
        return jsonify({"ok": False, "message": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except: pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5090, debug=True)