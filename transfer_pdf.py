import io
import os


import config

from datetime import datetime
from typing import Optional
from flask import (
    Blueprint, render_template, request,
    send_file, abort, current_app
)
from xhtml2pdf import pisa
from xhtml2pdf.default import DEFAULT_FONT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

font_path = os.path.join(os.path.dirname(__file__), "static", "fonts", "THSarabunNew", "THSarabunNew.ttf")
pdfmetrics.registerFont(TTFont("Sarabun", font_path))


def link_callback(uri, rel):
    path = os.path.join(current_app.root_path, uri.lstrip("/"))
    if os.path.isfile(path):
        return path
    raise Exception(f'Media not found: {path}')


FONT_DIR = os.path.join(
    os.path.dirname(__file__),
    "static",
    "fonts",
    "THSarabunNew"
)

pdfmetrics.registerFont(
    TTFont(
        'THSarabun',
        os.path.join(FONT_DIR, 'THSarabunNew.ttf')
    )
)

pdfmetrics.registerFont(
    TTFont(
        'THSarabun-Bold',
        os.path.join(FONT_DIR, 'THSarabunNew Bold.ttf')
    )
)

DEFAULT_FONT['helvetica'] = 'THSarabun'


# ── Blueprint ──────────────────────────────────────────────────
transfer_pdf_bp = Blueprint(
    "transfer_pdf",
    __name__,                          # ✅ แก้จาก name → __name__
    template_folder="templates",
)


# ══════════════════════════════════════════════════════════════
#  DB CONNECTION
# ══════════════════════════════════════════════════════════════
def getconn():
    return config.connect()


# ══════════════════════════════════════════════════════════════
#  SQL
# ══════════════════════════════════════════════════════════════
TRANSFERSQL = """
    SELECT
        T.ID,
        T.DOC_NO,
        T.REQUEST_ID,

        T.TRANSFER_TYPE,
        T.TRANSFER_TYPE_NAME,
        T.COMPANY_NAME,

        -- FROM
        T.FROM_DEPT,

        T.FROM_SITE,
        FD.COST_COMPANY     AS FROM_SITE_NAME,

        T.FROM_DIVISION,
        FD.COST_DEPARTMENT  AS FROM_DIVISION_NAME,

        T.FROM_COSTCENTER,
        T.FROM_COST_CODE,

        T.FROM_LOCATION,
        NULL                AS FROM_LOCATION_NAME,

        -- TO
        T.TO_DEPT,

        T.TO_SITE,
        TD2.COST_COMPANY    AS TO_SITE_NAME,

        T.TO_DIVISION,
        TD2.COST_DEPARTMENT AS TO_DIVISION_NAME,

        T.TO_COSTCENTER,
        T.TO_COST_CODE,

        T.TO_LOCATION,
        NULL                AS TO_LOCATION_NAME,

        -- buyer
        T.BUYER_NAME,
        T.BUYER_ADDRESS,
        T.BUYER_PRICE,

        -- requester
        T.REQUEST_BY,

        TO_CHAR(
            T.REQUEST_DATE,
            'DD/MM/YYYY'
        ) AS REQUEST_DATE,

        -- approve
        T.APPROVE_BY,

        TO_CHAR(
            T.APPROVE_DATE,
            'DD/MM/YYYY'
        ) AS APPROVE_DATE,

        -- approver name
        E.NAME AS APPROVE_BY_NAME,

        -- manager approve
        T.MANAGER_NAME AS MANAGER_APPROVE_NAME,

        T.MANAGER_APPROVE_BY,

        TO_CHAR(
            T.MANAGER_APPROVE_DATE,
            'DD/MM/YYYY'
        ) AS MANAGER_APPROVE_DATE,

        -- receiver
        T.RECEIVER_TYPE,

        T.RECEIVER_NAME,

        T.SENDER_NAME,

        TO_CHAR(
            T.RECEIVER_APPROVED_AT,
            'DD/MM/YYYY'
        ) AS RECEIVER_APPROVED_AT,

        -- misc
        T.REMARK,
        T.STATUS,

        -- requester info
        R.REQUESTER_FNAME,
        R.REQUESTER_LNAME,
        R.REQUESTER_EMPCODE,
        R.REQUESTER_DEPT,
        R.REQUESTER_TEL

    FROM IT_HELPDESK_TRANSFER T

    LEFT JOIN IT_HELPDESK_REQUEST R
        ON T.REQUEST_ID = R.REQUEST_ID

    LEFT JOIN IT_HELPDESK_DEPARTMENT FD
        ON (CASE WHEN REGEXP_LIKE(T.FROM_COSTCENTER, '^[0-9]+$') THEN TO_NUMBER(T.FROM_COSTCENTER) ELSE NULL END) = FD.COST_ID

    LEFT JOIN IT_HELPDESK_DEPARTMENT TD2
        ON (CASE WHEN REGEXP_LIKE(T.TO_COSTCENTER, '^[0-9]+$') THEN TO_NUMBER(T.TO_COSTCENTER) ELSE NULL END) = TD2.COST_ID

    LEFT JOIN (
        SELECT
            REQUEST_ID,
            EMP_APPROVER,
            ROW_NUMBER() OVER (
                PARTITION BY REQUEST_ID
                ORDER BY DATE_CREATE DESC
            ) AS RN
        FROM IT_HELPDESK_APPROVER
    ) A
        ON T.REQUEST_ID = A.REQUEST_ID
       AND A.RN = 1

    LEFT JOIN SBP_EMPLOYEE E
        ON A.EMP_APPROVER = E.EMP_ID

    WHERE T.REQUEST_ID = :request_id
"""

ASSETSQL = """
    SELECT ITEM_NO, ASSET_CODE, ASSET_NAME, ASSET_REMARK
    FROM   IT_HELPDESK_ASSET
    WHERE  TRANSFER_ID = :transfer_id
    ORDER  BY ITEM_NO
"""


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════
def rowto_dict(cur, row) -> dict:
    cols = [d[0].lower() for d in cur.description]
    vals = [v.read() if hasattr(v, "read") else v for v in row]
    return dict(zip(cols, vals))


def fetch(request_id: int) -> tuple:          # ✅ แก้จาก requestid → request_id
    conn = None
    try:
        conn = getconn()
        cur  = conn.cursor()

        cur.execute(TRANSFERSQL, {"request_id": request_id})   # ✅ แก้จาก _TRANSFER_SQL
        row = cur.fetchone()
        if not row:
            return None, []
        transfer = rowto_dict(cur, row)

        cur.execute(ASSETSQL, {"transfer_id": transfer["id"]})  # ✅ แก้จาก _ASSET_SQL
        assets = [rowto_dict(cur, r) for r in cur.fetchall()]   # ✅ แก้จาก _row_to_dict

        return transfer, assets

    except Exception as e:
        current_app.logger.error(f"[transfer_pdf] fetch error: {e}")
        print(f"[transfer_pdf] fetch error: {e}")
        return None, []
    finally:
        if conn:
            try: conn.close()
            except: pass


def renderhtml(transfer: dict, assets: list, preview: bool = False) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    return render_template("pdf_transfer.html", t=transfer, assets=assets, now=now, preview=preview)


# ══════════════════════════════════════════════════════════════
#  PDF GENERATOR
# ══════════════════════════════════════════════════════════════
def htmlto_pdf(html_str: str):
    buf = io.BytesIO()
    result = pisa.CreatePDF(
        src=io.StringIO(html_str),
        dest=buf,
        encoding="utf-8",
        link_callback=link_callback
    )
    if result.err:
        current_app.logger.error(f"[transfer_pdf] pisa error: {result.err}")
        return None
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════
@transfer_pdf_bp.route("/preview-transfer/<int:request_id>")
def preview(request_id: int):
    transfer, assets = fetch(request_id)
    if not transfer:
        abort(404, description=f"ไม่พบข้อมูล transfer สำหรับ request_id={request_id}")
    return renderhtml(transfer, assets, preview=True)


@transfer_pdf_bp.route("/download-transfer/<int:request_id>")
def download(request_id: int):
    transfer, assets = fetch(request_id)
    if not transfer:
        abort(404, description=f"ไม่พบข้อมูล transfer สำหรับ request_id={request_id}")

    html_str  = renderhtml(transfer, assets)
    pdf_bytes = htmlto_pdf(html_str)
    if pdf_bytes is None:
        abort(500, description="เกิดข้อผิดพลาดในการสร้าง PDF")

    doc_no   = transfer.get("doc_no") or str(request_id)
    filename = f"Transfer_{doc_no}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype      = "application/pdf",
        as_attachment = True,
        download_name = filename,
    )