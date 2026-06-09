import os
import oracledb as cx_Oracle
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv
import urllib.request
import json

load_dotenv("env")
LINE_TOKEN = os.getenv("LINE_CHANNEL_TOKEN")

approve_bp = Blueprint("approve_bp", __name__)

# ─────────────────────────────
# Oracle Connection
# ─────────────────────────────
def get_conn():
    return cx_Oracle.connect(
        user     = os.getenv("ORACLE_USER", "SBLDB"),
        password = os.getenv("ORACLE_PASSWORD", "***REMOVED***"),
        dsn      = os.getenv("ORACLE_DSN", "***REMOVED_DSN***")
    )

# ─────────────────────────────
# UPDATE FUNCTION
# ─────────────────────────────
def update_status(req_id: str, status: str):
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()

        # 🔍 1. เช็คสถานะก่อน
        cur.execute("""
            SELECT STATUS
            FROM IT_HELPDESK_APPROVER
            WHERE REQUEST_ID = :req_id
        """, {"req_id": req_id})

        row = cur.fetchone()

        if not row:
            return "NOT_FOUND"

        current_status = row[0]

        # ❌ Reject → ห้ามทุกอย่าง
        if current_status == "Reject":
            return "DUPLICATE"

        # ❌ Approve → ห้าม approve ซ้ำ
        if current_status == "Approve" and status == "Approve":
            return "DUPLICATE"

        # ❌ Done → ห้ามแก้แล้ว
        if current_status == "Done":
            return "DUPLICATE"

        # ✅ 2. update approver
        cur.execute("""
            UPDATE IT_HELPDESK_APPROVER
            SET STATUS = :status,
                DATE_UPDATE = SYSDATE
            WHERE REQUEST_ID = :req_id
        """, {"status": status, "req_id": req_id})

        # ✅ 3. update main table
        cur.execute("""
            UPDATE IT_HELPDESK_REQUEST
            SET REQUEST_STATUS = :main_status
            WHERE REQUEST_ID = :req_id
        """, {
            "main_status": "4" if status == "Approve" else ("5" if status == "Done" else "3"),
            "req_id": req_id
        })
        print(f"[DB] checking req_id={req_id}")
        print(f"[DB] current_status={row}")
        print(f"[DB] updating → {status}")
        
        conn.commit()
        return "OK"

    except cx_Oracle.Error as e:
        print("[ERROR]", e)
        if conn:
            conn.rollback()
        return "ERROR"

    finally:
        if conn:
            conn.close()

def get_request_detail(req_id):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT REQUEST_CATEGORY,
               REQUEST_REMARK,
               REQUESTER_FNAME,
               REQUESTER_LNAME,
               REQUEST_DATE
        FROM IT_HELPDESK_REQUEST
        WHERE REQUEST_ID = :req_id
    """, {"req_id": req_id})

    row = cur.fetchone()

    if not row:
        conn.close()
        return None

    # 🔥 FIX ตรงนี้
    remark = row[1]
    if hasattr(remark, "read"):
        remark = remark.read()

    data = {
        "category": row[0],
        "remark": remark,
        "name": f"{row[2]} {row[3]}",
        "date": str(row[4]) if row[4] else "-"
    }

    conn.close()
    return data

def render_result_page(req_id, status):
    detail = get_request_detail(req_id)

    if not detail:
        return "ไม่พบข้อมูล"

    # 🔹 mapping status
    approve_box = ""
    done_box = ""
    reject_box = ""

    if status == "Approve":
        approve_box = """
        <div class="status success">อนุมัติเรียบร้อย</div>
        """

    elif status == "Reject":
        reject_box = """
        <div class="status reject">ยกเลิกแล้ว</div>
        """

    elif status == "Done":
        approve_box = """
        <div class="status success">อนุมัติเรียบร้อย</div>
        """
        done_box = """
        <div class="status done">IT ดำเนินการเสร็จสิ้น</div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <!-- Google Font -->
        <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600&display=swap" rel="stylesheet">

        <title>IT Helpdesk</title>

        <style>
            body {{
                font-family: 'Sarabun', sans-serif;
                background: #f4f6fb;
                margin: 0;
                padding: 20px;
            }}

            .container {{
                max-width: 600px;
                margin: auto;
            }}

            .header {{
                background: linear-gradient(135deg, #5b5ef4, #7a7df6);
                color: white;
                padding: 20px;
                border-radius: 14px;
                font-size: 22px;
                font-weight: 600;
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}

            .card {{
                background: white;
                padding: 20px;
                border-radius: 14px;
                margin-top: 15px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.06);
            }}

            .title {{
                font-weight: 600;
                color: #5b5ef4;
                margin-bottom: 5px;
            }}

            .text {{
                color: #333;
                margin-bottom: 12px;
            }}

            .meta {{
                font-size: 14px;
                color: #666;
            }}

            .status {{
                padding: 16px;
                border-radius: 12px;
                text-align: center;
                font-size: 18px;
                font-weight: 600;
                margin-top: 10px;
            }}

            .success {{
                background: #e6f7ed;
                color: #1a7f37;
            }}

            .reject {{
                background: #fdeaea;
                color: #c62828;
            }}

            .done {{
                background: #e8f0ff;
                color: #1e40af;
            }}
        </style>
    </head>

    <body>
        <div class="container">

            <div class="header">
                IT Helpdesk #{req_id}
            </div>

            <div class="card">
                <div class="title">เรื่อง</div>
                <div class="text">{detail['category']}</div>

                <div class="title">รายละเอียด</div>
                <div class="text">{detail['remark']}</div>

                <div class="meta">
                    ขอโดย: {detail['name']}<br>
                    วันที่: {detail['date']}
                </div>
            </div>

            <div class="card">
                {approve_box}
                {reject_box}
                {done_box}
            </div>

        </div>
    </body>
    </html>
    """

    return html

# ─────────────────────────────
# send_line
# ─────────────────────────────
def send_line_to_requester(req_id, status):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT R.REQUESTER_EMPCODE, E.LINE_ID
        FROM IT_HELPDESK_REQUEST R
        JOIN SBP_EMPLOYEE E ON R.REQUESTER_EMPCODE = E.EMP_ID
        WHERE R.REQUEST_ID = :req_id
    """, {"req_id": req_id})

    row = cur.fetchone()
    conn.close()

    if not row or not row[1]:
        print("ไม่พบ LINE ผู้แจ้ง")
        return

    # line_id = row[1]
    line_id = "U1a079046647a4390627f067ee7e045ca"

    if status == "Approve":
        msg = f"IT helpdesk คำขอ #{req_id} ได้รับการอนุมัติแล้ว"
    elif status == "Reject":
        msg = f"IT helpdesk คำขอ #{req_id} ยกเลิกแล้ว"
    else:
        msg = f"IT helpdesk คำขอ #{req_id} ดำเนินการเสร็จสิ้นแล้ว"
    payload = {
        "to": line_id,
        "messages": [{"type": "text", "text": msg}]
    }

    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_TOKEN}"
        }
    )

    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print("[LINE ERROR]", e)

def get_current_status(req_id):
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT STATUS FROM IT_HELPDESK_APPROVER
        WHERE REQUEST_ID = :req_id
    """, {"req_id": req_id})

    row = cur.fetchone()
    conn.close()

    return row[0] if row else None
# ─────────────────────────────
# API: APPROVE
# ─────────────────────────────
@approve_bp.route("/api/approve")
def approve():
    req_id = request.args.get("ref", "").strip()
    req_id = req_id.zfill(7)

    print(f"[APPROVE API] req_id={req_id}")

    result = update_status(req_id, "Approve")

    print(f"[APPROVE RESULT] {result}")

    if result == "OK":
        send_line_to_requester(req_id, "Approve")
        return render_result_page(req_id, "Approve")

    elif result == "DUPLICATE":
        current = get_current_status(req_id)
        return render_result_page(req_id, current)

    else:
        return "ERROR"
# ─────────────────────────────
# API: REJECT
# ─────────────────────────────
@approve_bp.route("/api/reject")
def reject():
    req_id = request.args.get("ref", "").strip()
    req_id = req_id.zfill(7)

    result = update_status(req_id, "Reject")

    print(f"[REJECT API] req_id={req_id}")
    print(f"[REJECT RESULT] {result}")

    if result == "OK":
        send_line_to_requester(req_id, "Reject")
        return render_result_page(req_id, "Reject")

    elif result == "DUPLICATE":
        current = get_current_status(req_id)
        return render_result_page(req_id, current)

    else:
        return "ERROR"

# ─────────────────────────────
# API: Done
# ─────────────────────────────
@approve_bp.route("/api/done")
def done():
    req_id = request.args.get("ref", "").strip()
    req_id = req_id.zfill(7)

    result = update_status(req_id, "Done")

    if result == "OK":
        send_line_to_requester(req_id, "Done")
        return render_result_page(req_id, "Done")

    elif result == "DUPLICATE":
        current = get_current_status(req_id)
        return render_result_page(req_id, current)

    else:
        return "ERROR"