import html as _html
import os
from flask import Blueprint, request, jsonify
import urllib.request
import json

import config

LINE_TOKEN = config.LINE_TOKEN
approve_bp = Blueprint("approve_bp", __name__)
def check_transfer_complete(req_id):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                T.TRANSFER_TYPE,
                A.STATUS,
                T.SENDER_NAME,
                T.RECEIVER_NAME,
                T.RECEIVER_APPROVED_AT,
                T.MANAGER_APPROVE_BY,
                T.MANAGER_APPROVE_DATE
            FROM IT_HELPDESK_TRANSFER T
            LEFT JOIN IT_HELPDESK_APPROVER A ON T.REQUEST_ID = A.REQUEST_ID
            WHERE T.REQUEST_ID = :rid
        """, {
            "rid": req_id
        })
        row = cur.fetchone()
        if not row:
            print("[CHECK COMPLETE] NOT FOUND")
            return False
        (
            transfer_type,
            approver_status,
            receiver_name,
            receiver_approved_at,
            manager_approve_by,
            manager_approve_date

        ) = row

        # print("========== CHECK COMPLETE ==========")
        # print("REQ ID :", req_id)
        # print("TRANSFER TYPE :", transfer_type)
        # print("APPROVER STATUS :", approver_status)
        # print("RECEIVER NAME :", receiver_name)
        # print("RECEIVER APPROVED :", receiver_approved_at)
        # print("MANAGER APPROVE :", manager_approve_by)
        # print("MANAGER APPROVE DATE :", manager_approve_date)

        # ─────────────────────────────────────
        # TRANSFER
        # receiver confirm → RECEIVER_CONFIRMED (รอ IT ปิดงาน)
        # ─────────────────────────────────────
        if transfer_type == "TRANSFER":
            receiver_confirmed = (
                approver_status == "Approve" and
                receiver_name and
                receiver_approved_at
            )
            print("RECEIVER CONFIRMED =", receiver_confirmed)
            if receiver_confirmed:
                print("=== TRANSFER: RECEIVER CONFIRMED → WAITING IT ===")
                cur.execute("""
                    UPDATE IT_HELPDESK_TRANSFER
                    SET STATUS = 'RECEIVER_CONFIRMED'
                    WHERE REQUEST_ID = :rid
                """, {"rid": req_id})
                conn.commit()
                print("[TRANSFER WAITING IT]", req_id)
            return receiver_confirmed

        # ─────────────────────────────────────
        # DISPOSE
        # ลายเซ็นครบ → WAITING_IT (IT ต้องปิดงานเอง)
        # ─────────────────────────────────────
        elif transfer_type == "DISPOSE":
            all_signed = (
                approver_status == "Approve" and
                receiver_name and
                receiver_approved_at and
                manager_approve_by and
                manager_approve_date
            )
            print("ALL SIGNED =", all_signed)
            if all_signed:
                print("=== DISPOSE: ALL SIGNED → WAITING_IT ===")
                cur.execute("""
                    UPDATE IT_HELPDESK_TRANSFER
                    SET STATUS = 'WAITING_IT'
                    WHERE REQUEST_ID = :rid
                """, {"rid": req_id})
                conn.commit()
                print("[DISPOSE WAITING_IT]", req_id)
            return all_signed

        # ─────────────────────────────────────
        # SALE / REPAIR / BORROW
        # receiver confirmed → WAITING_IT (IT ต้องปิดงานเอง)
        # ─────────────────────────────────────
        else:
            receiver_done = (
                approver_status == "Approve" and
                receiver_name and
                receiver_approved_at
            )
            print("RECEIVER DONE =", receiver_done)
            if receiver_done:
                print(f"=== {transfer_type}: RECEIVER DONE → WAITING_IT ===")
                cur.execute("""
                    UPDATE IT_HELPDESK_TRANSFER
                    SET STATUS = 'WAITING_IT'
                    WHERE REQUEST_ID = :rid
                """, {"rid": req_id})
                conn.commit()
                print(f"[{transfer_type} WAITING_IT]", req_id)
            return receiver_done
    except Exception as e:
        print( "[CHECK COMPLETE ERROR]", e )
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass
            
# ─────────────────────────────
# Oracle Connection
# ─────────────────────────────
def get_conn():
    return config.connect()

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

        # ❌ Reject → ห้ามแก้ไขทุกกรณี
        if current_status == "Reject":
            return "DUPLICATE"

        # ❌ Approve → ห้ามกด Approve ซ้ำ และห้ามกด Reject ทับ
        if current_status == "Approve" and status in ("Approve", "Reject"):
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
    except config.db_error() as e:
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

# ─────────────────────────────
# รายการเอกสารทั้งหมดของผู้อนุมัติคนนี้
# ─────────────────────────────
# Waiting            → รออนุมัติ
# Approve / Done     → อนุมัติแล้ว
# Reject             → ยกเลิก
_STATUS_GROUP = {
    "Waiting": "waiting",
    "Approve": "approved",
    "Done":    "approved",
    "Reject":  "rejected",
}
_STATUS_TEXT = {
    "Waiting": "รออนุมัติ",
    "Approve": "อนุมัติแล้ว",
    "Done":    "IT ดำเนินการเสร็จสิ้น",
    "Reject":  "ยกเลิก",
}
# กันหน้าบวมถ้าคนอนุมัติมีเอกสารสะสมเยอะ
_LIST_LIMIT = 100


def get_approver_of(req_id):
    """หา EMP_APPROVER ของใบนี้ — หน้า /api/approve มีแต่ ref ไม่มีรหัสผู้อนุมัติ"""
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT EMP_APPROVER FROM IT_HELPDESK_APPROVER
            WHERE REQUEST_ID = :rid
        """, {"rid": req_id})
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    except Exception as e:
        print("[GET APPROVER ERROR]", e)
        return None
    finally:
        if conn:
            try: conn.close()
            except: pass


def get_approver_docs(emp_approver):
    """ดึงเอกสารทั้งหมดที่ส่งมาให้ emp_approver คนนี้ พร้อมสถานะ"""
    if not emp_approver:
        return []
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM (
                SELECT A.REQUEST_ID,
                       A.STATUS,
                       A.DATE_CREATE,
                       A.DATE_UPDATE,
                       R.REQUEST_CATEGORY,
                       R.REQUESTER_FNAME,
                       R.REQUESTER_LNAME,
                       T.TRANSFER_TYPE_NAME,
                       T.COMPANY_NAME
                FROM IT_HELPDESK_APPROVER A
                JOIN IT_HELPDESK_REQUEST  R ON R.REQUEST_ID = A.REQUEST_ID
                LEFT JOIN IT_HELPDESK_TRANSFER T ON T.REQUEST_ID = A.REQUEST_ID
                WHERE A.EMP_APPROVER = :emp
                ORDER BY A.DATE_CREATE DESC
            ) WHERE ROWNUM <= :lim
        """, {"emp": emp_approver, "lim": _LIST_LIMIT})

        docs = []
        for row in cur.fetchall():
            vals = [v.read() if hasattr(v, "read") else v for v in row]
            (rid, st, created, updated, cat, fname, lname, ttype, company) = vals
            st = (st or "").strip()
            docs.append({
                "request_id": rid,
                "status":     st,
                "group":      _STATUS_GROUP.get(st, "waiting"),
                "status_text": _STATUS_TEXT.get(st, st or "-"),
                "date":       created.strftime("%d/%m/%Y %H:%M") if created else "-",
                "updated":    updated.strftime("%d/%m/%Y %H:%M") if updated else "",
                "title":      ttype or cat or "-",
                "requester":  f"{fname or ''} {lname or ''}".strip() or "-",
                "company":    company or "",
            })
        return docs
    except Exception as e:
        print("[GET APPROVER DOCS ERROR]", e)
        return []
    finally:
        if conn:
            try: conn.close()
            except: pass


def _doc_rows_html(docs, group, current_req_id):
    """สร้าง <li> ของแต่ละ tab — ถ้าไม่มีเอกสารให้ขึ้นข้อความแทนตารางว่าง"""
    rows = [d for d in docs if d["group"] == group]
    if not rows:
        return '<div class="empty">ไม่มีรายการ</div>'

    out = []
    for d in rows:
        # ใบที่เพิ่งกดมาให้ไฮไลต์ไว้ จะได้รู้ว่าอันไหนคือใบที่เพิ่งทำ
        is_cur = str(d["request_id"]) == str(current_req_id)
        cls    = "doc cur" if is_cur else "doc"
        badge  = f'<span class="b {d["group"]}">{_html.escape(d["status_text"])}</span>'
        cur_tag = '<span class="now">ใบนี้</span>' if is_cur else ""
        company = (f'<div class="c">{_html.escape(d["company"])}</div>'
                   if d["company"] else "")
        # เป็น div ไม่ใช่ลิงก์ — /api/approve?ref= สั่งอนุมัติทันทีที่เปิด
        # ถ้าทำแถวเป็นลิงก์ไปหน้านั้น เผลอแตะก็อนุมัติไปแล้ว
        out.append(f"""
          <div class="{cls}">
            <div class="r1">
              <span class="no">#{d['request_id']}</span>{cur_tag}{badge}
            </div>
            <div class="t">{_html.escape(d['title'])}</div>
            {company}
            <div class="m">ขอโดย: {_html.escape(d['requester'])} · {d['date']}</div>
          </div>""")
    return "".join(out)


def render_doc_list(req_id, emp_approver):
    """ส่วน 'เอกสารของฉัน' ที่แปะไว้ใต้ผลอนุมัติ — แท็บ รออนุมัติ/อนุมัติแล้ว/ยกเลิก"""
    docs = get_approver_docs(emp_approver)
    if not docs:
        return ""

    n_wait = sum(1 for d in docs if d["group"] == "waiting")
    n_appr = sum(1 for d in docs if d["group"] == "approved")
    n_rej  = sum(1 for d in docs if d["group"] == "rejected")

    return f"""
        <div class="card list-card">
          <div class="list-title">เอกสารของฉัน</div>
          <div class="tabs">
            <button class="tab active" onclick="showTab(event,'waiting')">รออนุมัติ <i>{n_wait}</i></button>
            <button class="tab" onclick="showTab(event,'approved')">อนุมัติแล้ว <i>{n_appr}</i></button>
            <button class="tab" onclick="showTab(event,'rejected')">ยกเลิก <i>{n_rej}</i></button>
          </div>
          <div class="pane" id="pane-waiting">{_doc_rows_html(docs, 'waiting', req_id)}</div>
          <div class="pane" id="pane-approved" style="display:none">{_doc_rows_html(docs, 'approved', req_id)}</div>
          <div class="pane" id="pane-rejected" style="display:none">{_doc_rows_html(docs, 'rejected', req_id)}</div>
          <a class="go-list" href="/api/approve-list?emp={_html.escape(str(emp_approver))}">
            เปิดหน้ารวมเพื่อกดอนุมัติ
          </a>
        </div>
        <script>
          function showTab(e, name) {{
            ['waiting','approved','rejected'].forEach(function (n) {{
              document.getElementById('pane-' + n).style.display = (n === name) ? 'block' : 'none';
            }});
            document.querySelectorAll('.tab').forEach(function (t) {{ t.classList.remove('active'); }});
            e.currentTarget.classList.add('active');
          }}
        </script>"""


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

    # รายการเอกสารทั้งหมดของผู้อนุมัติคนนี้ — ล้มแล้วต้องไม่ทำให้หน้าผลอนุมัติพัง
    try:
        doc_list = render_doc_list(req_id, get_approver_of(req_id))
    except Exception as e:
        print("[DOC LIST ERROR]", e)
        doc_list = ""

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

            /* ── เอกสารของฉัน ── */
            .list-card {{ padding: 16px; }}
            .list-title {{
                font-weight: 600; font-size: 16px;
                color: #5b5ef4; margin-bottom: 12px;
            }}
            .tabs {{ display: flex; gap: 6px; margin-bottom: 12px; }}
            .tab {{
                flex: 1; padding: 8px 4px; font-family: inherit; font-size: 13px;
                border: 1px solid #e3e6f0; background: #fff; color: #666;
                border-radius: 9px; cursor: pointer;
            }}
            .tab.active {{ background: #5b5ef4; border-color: #5b5ef4; color: #fff; }}
            .tab i {{ font-style: normal; opacity: .75; }}
            .doc {{
                display: block; text-decoration: none; color: inherit;
                border: 1px solid #eceef5; border-radius: 11px;
                padding: 11px 13px; margin-bottom: 9px;
            }}
            .doc.cur {{ border-color: #5b5ef4; background: #f7f7ff; }}
            .doc .r1 {{ display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }}
            .doc .no {{ font-weight: 600; font-size: 14px; }}
            .doc .now {{
                font-size: 11px; background: #5b5ef4; color: #fff;
                padding: 1px 7px; border-radius: 20px;
            }}
            .doc .b {{
                margin-left: auto; font-size: 11px;
                padding: 2px 9px; border-radius: 20px;
            }}
            .doc .b.waiting  {{ background: #fff4e0; color: #b26a00; }}
            .doc .b.approved {{ background: #e7f7ec; color: #1e7a3c; }}
            .doc .b.rejected {{ background: #fdeaea; color: #c62828; }}
            .doc .t {{ font-size: 14px; color: #333; }}
            .doc .c {{ font-size: 12px; color: #5b5ef4; margin-top: 2px; }}
            .doc .m {{ font-size: 12px; color: #999; margin-top: 4px; }}
            .empty {{
                text-align: center; color: #aaa;
                font-size: 13px; padding: 18px 0;
            }}
            .go-list {{
                display: block; text-align: center; text-decoration: none;
                margin-top: 4px; padding: 11px; border-radius: 10px;
                background: #5b5ef4; color: #fff; font-size: 14px; font-weight: 600;
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
            {doc_list}
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

    #line_id = row[1]   # LINE_ID จริงจาก SBP_EMPLOYEE
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

# ─────────────────────────────
# IT EMP IDs ที่รับ Noti (fix ไว้ก่อน เปลี่ยนทีหลัง)
# ─────────────────────────────
IT_TEAM_EMP_IDS = [
    "2560847",   # คนที่ 1 (พี่โบว์)
    "2550563",   # คนที่ 2 (พี่น้อยหน่า)
    "4670008",   # คนที่ 3 (บุษ เทส)
]

def send_line_to_it_team(req_id):
    """
    ส่ง LINE แจ้งงานใหม่ไปหา IT team
    เฉพาะ typeform = 5 (เว็บไซต์และโปรแกรม) เท่านั้น
    """
    conn = None
    try:
        conn = get_conn()
        cur  = conn.cursor()

        # ดึงรายละเอียด request
        cur.execute("""
            SELECT
                R.REQUEST_TYPEFORM,
                R.REQUEST_CATEGORY,
                R.REQUEST_REMARK,
                R.REQUESTER_FNAME,
                R.REQUESTER_LNAME,
                R.REQUESTER_DEPT,
                R.REQUEST_DATE
            FROM IT_HELPDESK_REQUEST R
            WHERE R.REQUEST_ID = :req_id
        """, {"req_id": req_id})

        row = cur.fetchone()
        if not row:
            print(f"[IT NOTI] ไม่พบ request_id={req_id}")
            return

        def rv(v):
            return v.read() if hasattr(v, "read") else v

        typeform  = str(rv(row[0]) or "")
        category  = rv(row[1]) or "-"
        remark    = rv(row[2]) or "-"
        fname     = rv(row[3]) or ""
        lname     = rv(row[4]) or ""
        dept      = rv(row[5]) or "-"
        req_date  = str(rv(row[6]) or "-")
        full_name = f"{fname} {lname}".strip() or "-"

        # เฉพาะ typeform 5 เท่านั้น
        if typeform != "5":
            print(f"[IT NOTI] typeform={typeform} ไม่ใช่ 5 → ข้าม")
            return

        print(f"[IT NOTI] typeform=5 → ส่งให้ IT team req_id={req_id}")

        # ดึง LINE_ID ของ IT แต่ละคน
        for emp_id in IT_TEAM_EMP_IDS:
            cur.execute("""
                SELECT LINE_ID, NAME
                FROM SBP_EMPLOYEE
                WHERE EMP_ID = :emp_id
            """, {"emp_id": emp_id})

            emp_row = cur.fetchone()
            if not emp_row or not emp_row[0]:
                print(f"[IT NOTI] ไม่พบ LINE_ID ของ {emp_id}")
                continue

            line_id  = emp_row[0]
            emp_name = emp_row[1] or emp_id

            _send_it_flex(line_id, req_id, category, remark, full_name, dept, req_date)
            print(f"[IT NOTI] ส่งให้ {emp_name} ({emp_id}) สำเร็จ")

    except Exception as e:
        print(f"[IT NOTI ERROR] {e}")
    finally:
        if conn:
            try: conn.close()
            except: pass


def _send_it_flex(to_line_id, req_id, category, remark, requester_name, dept, req_date):
    """ส่ง Flex Message แจ้งงานใหม่พร้อมทำไปหา IT"""

    payload = {
        "to": to_line_id,
        "messages": [
            {
                "type": "flex",
                "altText": f"[IT งานใหม่] #{req_id} {category}",
                "contents": {
                    "type": "bubble",
                    "size": "mega",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "backgroundColor": "#1a3c6e",
                        "paddingAll": "16px",
                        "contents": [
                            {
                                "type": "text",
                                "text": "Task ใหม่จาก IT helpdesk",
                                "color": "#ffffff",
                                "size": "sm",
                                "weight": "bold"
                            },
                            {
                                "type": "text",
                                "text": f"#{req_id}",
                                "color": "#93c5fd",
                                "size": "xl",
                                "weight": "bold"
                            }
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
                                    {"type": "text", "text": "ประเภทงาน", "weight": "bold", "color": "#1a3c6e", "size": "sm"},
                                    {"type": "text", "text": category, "wrap": True, "size": "md"}
                                ]
                            },
                            {"type": "separator", "margin": "sm"},
                            {
                                "type": "box", "layout": "vertical", "spacing": "xs", "margin": "sm",
                                "contents": [
                                    {"type": "text", "text": "รายละเอียด", "weight": "bold", "color": "#1a3c6e", "size": "sm"},
                                    {"type": "text", "text": str(remark)[:200] if remark else "-", "wrap": True, "size": "sm", "color": "#555555"}
                                ]
                            },
                            {"type": "separator", "margin": "sm"},
                            {
                                "type": "box", "layout": "vertical", "margin": "sm", "spacing": "xs",
                                "contents": [
                                    {"type": "text", "text": f"ผู้แจ้ง : {requester_name}", "size": "sm", "color": "#333333"},
                                    {"type": "text", "text": f"แผนก : {dept}", "size": "sm", "color": "#666666"},
                                    {"type": "text", "text": f"วันที่ : {req_date}", "size": "sm", "color": "#888888"}
                                ]
                            }
                        ]
                    },
                    "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "paddingAll": "12px",
                        "contents": []
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
            print(f"[IT FLEX OK] req_id={req_id} to={to_line_id} http={resp.status}")
    except urllib.error.HTTPError as e:
        print(f"[IT FLEX ERROR] http={e.code} {e.read().decode()}")
    except Exception as e:
        print(f"[IT FLEX ERROR] {e}")


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
# RECEIVER CONFIRM FUNCTION
# ─────────────────────────────
def confirm_receiver(req_id):
    conn = None
    try:
        print("========== RECEIVER CONFIRM ==========")
        print("REQ ID =", req_id)

        conn = get_conn()
        cur = conn.cursor()
        # ─────────────────────────────────────
        # CHECK CURRENT STATUS
        # ─────────────────────────────────────
        cur.execute("""
            SELECT
                RECEIVER_STATUS
            FROM IT_HELPDESK_TRANSFER
            WHERE REQUEST_ID = :req_id
        """, {
            "req_id": req_id
        })
        row = cur.fetchone()

        if not row:
            print("NOT FOUND")
            return "NOT_FOUND"

        current_status = row[0]
        print("CURRENT STATUS =", current_status)

        # ─────────────────────────────────────
        # DUPLICATE
        # ─────────────────────────────────────
        if current_status == "Confirmed":
            print("DUPLICATE")
            return "DUPLICATE"

        # ─────────────────────────────────────
        # UPDATE RECEIVER
        # ─────────────────────────────────────
        cur.execute("""
            UPDATE IT_HELPDESK_TRANSFER
            SET
                RECEIVER_STATUS = 'Confirmed',
                RECEIVER_APPROVED_AT = SYSDATE
            WHERE REQUEST_ID = :req_id
        """, {
            "req_id": req_id
        })
        conn.commit()
        print("UPDATE RECEIVER SUCCESS")

        # ─────────────────────────────────────
        # CHECK COMPLETE
        # ─────────────────────────────────────
        completed = check_transfer_complete(req_id)
        print("CHECK COMPLETE =", completed)
        return "OK"

    except Exception as e:
        print("[CONFIRM RECEIVER ERROR]",e)
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return "ERROR"
    
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

# ─────────────────────────────
# API: APPROVE
# ─────────────────────────────
@approve_bp.route("/api/approve")
def approve():
    req_id = request.args.get("ref", "").strip()
    print(f"[APPROVE API] req_id={req_id}")
    result = update_status(req_id, "Approve")
    print(f"[APPROVE RESULT] {result}")
    if result == "OK":
            send_line_to_requester(req_id, "Approve")
            send_line_to_it_team(req_id)
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
    result = update_status(req_id, "Done")
    if result == "OK":
        send_line_to_requester(req_id, "Done")
        return render_result_page(req_id, "Done")
    elif result == "DUPLICATE":
        current = get_current_status(req_id)
        return render_result_page(req_id, current)
    else:
        return "ERROR"
    
# ─────────────────────────────
# API: RECEIVER CONFIRM
# ─────────────────────────────
@approve_bp.route("/api/receiver-confirm")
def receiver_confirm():
    req_id = request.args.get("ref", "").strip()
    print(f"[RECEIVER CONFIRM] req_id={req_id}")
    result = confirm_receiver(req_id)
    print(f"[RECEIVER RESULT] {result}")

# success
    if result == "OK":
        return """
        <!DOCTYPE html>
        <html lang="th">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700&display=swap" rel="stylesheet">
            <title>ยืนยันรับทรัพย์สิน</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body {
                    font-family: 'Sarabun', sans-serif;
                    background: #f4f6fb;
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    padding: 20px;
                }
                .card {
                    background: #fff;
                    border-radius: 16px;
                    padding: 40px 32px;
                    max-width: 400px;
                    width: 100%;
                    box-shadow: 0 4px 24px rgba(0,0,0,.08);
                    text-align: center;
                }
                .icon-wrap {
                    width: 64px;
                    height: 64px;
                    background: #d1fae5;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin: 0 auto 20px;
                }
                .icon-wrap svg {
                    width: 32px;
                    height: 32px;
                    stroke: #15803d;
                    stroke-width: 2.5;
                    fill: none;
                    stroke-linecap: round;
                    stroke-linejoin: round;
                }
                .title {
                    font-size: 1.25rem;
                    font-weight: 700;
                    color: #15803d;
                    margin-bottom: 8px;
                }
                .sub {
                    font-size: 0.875rem;
                    color: #6b7280;
                    line-height: 1.6;
                }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="icon-wrap">
                    <svg viewBox="0 0 24 24">
                        <polyline points="20 6 9 17 4 12"/>
                    </svg>
                </div>
                <div class="title">ยืนยันรับทรัพย์สินแล้ว</div>
                <div class="sub">ระบบได้บันทึกรายการเรียบร้อยแล้ว</div>
            </div>
        </body>
        </html>
        """
    
# duplicate
    elif result == "DUPLICATE":
        return """
        <!DOCTYPE html>
        <html lang="th">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700&display=swap" rel="stylesheet">
            <title>ยืนยันรับทรัพย์สิน</title>
            <style>
                * { box-sizing: border-box; margin: 0; padding: 0; }
                body {
                    font-family: 'Sarabun', sans-serif;
                    background: #f4f6fb;
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    padding: 20px;
                }
                .card {
                    background: #fff;
                    border-radius: 16px;
                    padding: 40px 32px;
                    max-width: 400px;
                    width: 100%;
                    box-shadow: 0 4px 24px rgba(0,0,0,.08);
                    text-align: center;
                }
                .icon-wrap {
                    width: 64px;
                    height: 64px;
                    background: #fef3c7;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    margin: 0 auto 20px;
                }
                .icon-wrap svg {
                    width: 32px;
                    height: 32px;
                    stroke: #92400e;
                    stroke-width: 2.5;
                    fill: none;
                    stroke-linecap: round;
                    stroke-linejoin: round;
                }
                .title {
                    font-size: 1.25rem;
                    font-weight: 700;
                    color: #92400e;
                    margin-bottom: 8px;
                }
                .sub {
                    font-size: 0.875rem;
                    color: #6b7280;
                    line-height: 1.6;
                }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="icon-wrap">
                    <svg viewBox="0 0 24 24">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="12" y1="8" x2="12" y2="12"/>
                        <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                </div>
                <div class="title">ยืนยันรายการนี้แล้ว</div>
                <div class="sub">รายการนี้ถูกยืนยันการรับทรัพย์สินไปแล้ว</div>
            </div>
        </body>
        </html>
        """
    # error
    else:

        return "ERROR"