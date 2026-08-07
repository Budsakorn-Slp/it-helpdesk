# -*- coding: utf-8 -*-
# approve_list.py — หน้ารวมเอกสารของผู้อนุมัติแต่ละคน
#   GET  /api/approve-list?emp=<EMP_ID>          → หน้า list (tab + กางดูสินทรัพย์ + ปุ่ม)
#   POST /api/approve-list/action               → อนุมัติ/ยกเลิก (รายตัว หรือหลายอันพร้อมกัน)

import os
import json
import cx_Oracle
from flask import Blueprint, request, jsonify

import config

approve_list_bp = Blueprint("approve_list_bp", __name__)

# ── Oracle Instant Client (guard กันชนกับ blueprint อื่น) ──
config.init_oracle_client(cx_Oracle)


def _db_conn():
    return cx_Oracle.connect(**config.oracle_credentials())


def _rows(cur):
    cols = [c[0].lower() for c in cur.description]
    out = []
    for row in cur.fetchall():
        d = {}
        for k, v in zip(cols, row):
            if hasattr(v, "read"):
                v = v.read()
            d[k] = v
        out.append(d)
    return out


# Waiting → tab รออนุมัติ / Approve,Done → อนุมัติแล้ว / Reject → ยกเลิก
_GROUP = {"Waiting": "waiting", "Approve": "approved",
          "Done": "approved", "Reject": "rejected"}

MAIN_SQL = """
    SELECT A.REQUEST_ID,
           A.STATUS                AS APV_STATUS,
           R.REQUEST_CATEGORY,
           R.REQUESTER_FNAME,
           R.REQUESTER_LNAME,
           R.REQUEST_DATE          AS REQ_DATE,
           R.REQUEST_REMARK,
           T.ID                    AS TRANSFER_ID,
           T.TRANSFER_TYPE_NAME,
           T.FROM_SITE, T.FROM_DIVISION, T.FROM_COSTCENTER, T.FROM_COST_CODE, T.FROM_LOCATION,
           T.TO_SITE,   T.TO_DIVISION,   T.TO_COSTCENTER,   T.TO_COST_CODE,   T.TO_LOCATION
    FROM IT_HELPDESK_APPROVER A
    JOIN IT_HELPDESK_REQUEST  R ON R.REQUEST_ID = A.REQUEST_ID
    LEFT JOIN IT_HELPDESK_TRANSFER T ON T.REQUEST_ID = R.REQUEST_ID
    WHERE A.EMP_APPROVER = :emp
    ORDER BY A.DATE_CREATE DESC
"""

ASSET_SQL_TMPL = """
    SELECT TRANSFER_ID, ITEM_NO, ASSET_CODE, ASSET_NAME, ASSET_REMARK
    FROM   IT_HELPDESK_ASSET
    WHERE  TRANSFER_ID IN ({binds})
    ORDER  BY TRANSFER_ID, ITEM_NO
"""


def _fetch(emp_id):
    conn = _db_conn()
    try:
        cur = conn.cursor()
        cur.execute(MAIN_SQL, {"emp": emp_id})
        docs = _rows(cur)

        # ดึง asset ทีเดียวด้วย IN (...)
        tids = [d["transfer_id"] for d in docs if d.get("transfer_id")]
        assets_by_tid = {}
        if tids:
            binds = {f"t{i}": v for i, v in enumerate(tids)}
            sql = ASSET_SQL_TMPL.format(binds=",".join(f":{k}" for k in binds))
            cur.execute(sql, binds)
            for a in _rows(cur):
                assets_by_tid.setdefault(a["transfer_id"], []).append(a)

        for d in docs:
            d["assets"] = assets_by_tid.get(d.get("transfer_id"), [])
        return docs
    finally:
        conn.close()


def _loc(site, div, cc, code, location):
    parts = " / ".join([x for x in (site, div, cc or code) if x])
    loc = f" | Location: {location}" if location else ""
    return (parts + loc) if (parts or loc) else "-"


# ──────────────────────────────────────────────
#  ACTION: อนุมัติ / ยกเลิก  (รายตัว หรือหลายอัน)
# ──────────────────────────────────────────────
@approve_list_bp.route("/api/approve-list/action", methods=["POST"])
def approve_list_action():
    data   = request.get_json(force=True) or {}
    emp    = (data.get("emp") or "").strip()
    ids    = [str(i).strip() for i in (data.get("ids") or []) if str(i).strip()]
    action = (data.get("action") or "").strip()        # "Approve" | "Reject"

    if not emp or not ids or action not in ("Approve", "Reject"):
        return jsonify({"ok": [], "skip": ids, "error": "bad request"}), 400

    main_status = "4" if action == "Approve" else "3"
    ok, skip = [], []

    conn = _db_conn()
    try:
        cur = conn.cursor()
        for rid in ids:
            # อนุมัติ/ยกเลิกได้เฉพาะแถวของ emp นี้ ที่ยัง Waiting เท่านั้น
            cur.execute("""
                SELECT STATUS FROM IT_HELPDESK_APPROVER
                WHERE REQUEST_ID = :id AND EMP_APPROVER = :emp
            """, {"id": rid, "emp": emp})
            r = cur.fetchone()
            if not r or (r[0] or "").strip() != "Waiting":
                skip.append(rid)
                continue

            cur.execute("""
                UPDATE IT_HELPDESK_APPROVER
                SET STATUS = :s, DATE_UPDATE = SYSDATE
                WHERE REQUEST_ID = :id AND EMP_APPROVER = :emp
            """, {"s": action, "id": rid, "emp": emp})
            cur.execute("""
                UPDATE IT_HELPDESK_REQUEST
                SET REQUEST_STATUS = :ms
                WHERE REQUEST_ID = :id
            """, {"ms": main_status, "id": rid})
            ok.append(rid)
        conn.commit()
    except cx_Oracle.Error as e:
        conn.rollback()
        return jsonify({"ok": [], "skip": ids, "error": str(e)}), 500
    finally:
        conn.close()

    # แจ้ง LINE (best-effort — ไม่มีก็ข้าม ไม่ทำให้พัง)
    try:
        from approve_api import send_line_to_requester, send_line_to_it_team
        for rid in ok:
            try:
                send_line_to_requester(rid, action)
                if action == "Approve":
                    send_line_to_it_team(rid)
            except Exception:
                pass
    except Exception:
        pass

    return jsonify({"ok": ok, "skip": skip})


# ──────────────────────────────────────────────
#  RENDER CARD
# ──────────────────────────────────────────────
def _asset_table(d):
    if not d["assets"]:
        return ""
    frm = _loc(d.get("from_site"), d.get("from_division"), d.get("from_costcenter"),
               d.get("from_cost_code"), None)
    to = _loc(d.get("to_site"), d.get("to_division"), d.get("to_costcenter"),
              d.get("to_cost_code"), None)
    rows = ""
    for i, a in enumerate(d["assets"], 1):
        rows += f"""<tr>
            <td>{i}</td>
            <td>{a.get('asset_code') or '-'}</td>
            <td>{a.get('asset_name') or '-'}</td>
            <td>{frm}</td>
            <td>{to}</td>
            <td>{(a.get('asset_remark') or '').strip() or '-'}</td>
          </tr>"""
    return f"""
      <div class="assets">
        <div class="assets-title">รายการสินทรัพย์</div>
        <table>
          <thead><tr>
            <th>#</th><th>รหัสสินทรัพย์</th><th>ชื่อสินค้า / รายละเอียด</th>
            <th>ต้นทาง</th><th>ปลายทาง</th><th>หมายเหตุ</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>"""


def _card(d, group):
    rid  = d["request_id"]
    name = f"{d.get('requester_fname') or ''} {d.get('requester_lname') or ''}".strip() or "-"
    cat  = d.get("request_category") or "-"
    ttl  = d.get("transfer_type_name") or cat
    has_tf = bool(d.get("transfer_id"))

    if has_tf:
        frm = _loc(d.get("from_site"), d.get("from_division"), d.get("from_costcenter"),
                   d.get("from_cost_code"), d.get("from_location"))
        to  = _loc(d.get("to_site"), d.get("to_division"), d.get("to_costcenter"),
                   d.get("to_cost_code"), d.get("to_location"))
        n   = len(d["assets"])
        detail = f"""
          <div class="tline"><b>[{ttl}]</b></div>
          <div class="tline">ต้นทาง: {frm}</div>
          <div class="tline">ปลายทาง: {to}</div>
          <div class="tline">สินทรัพย์: <a class="alink">{n} รายการ</a></div>"""
        asset_html = _asset_table(d)
    else:
        remark = (d.get("request_remark") or "").strip() or "-"
        detail = f'<div class="tline remark">{remark}</div>'
        asset_html = ""

    # ปุ่ม / badge ตามกลุ่ม
    if group == "waiting":
        right = f"""
          <div class="btns">
            <button class="btn approve" onclick="act('{rid}','Approve')">✔ อนุมัติ</button>
            <button class="btn reject"  onclick="act('{rid}','Reject')">✕ ยกเลิก</button>
          </div>"""
        check = f'<input type="checkbox" class="pick" value="{rid}" onchange="sync()">'
        status_line = '<div class="status wait">⏱ สถานะ: รอการอนุมัติโดยหัวหน้า</div>'
    elif group == "approved":
        right = '<div class="badge ok">✔ อนุมัติแล้ว</div>'
        check, status_line = "", '<div class="status okc">✔ อนุมัติแล้ว</div>'
    else:
        right = '<div class="badge no">✕ ยกเลิกแล้ว</div>'
        check, status_line = "", '<div class="status noc">✕ ยกเลิกแล้ว</div>'

    expand = '<span class="caret" onclick="tog(this)">▾</span>' if asset_html else ""

    return f"""
    <div class="card" data-rid="{rid}">
      <div class="card-row">
        {check}
        <div class="ref">#{rid}</div>
        <div class="who">
          <div class="avatar">👤</div>
          <div>
            <div class="wname">ผู้ขอ: {name}</div>
            <div class="wdate">📅 {d.get('req_date') or '-'}</div>
            {status_line}
          </div>
        </div>
        <div class="detail">{detail}</div>
        <div class="action">{right}</div>
        {expand}
      </div>
      <div class="expand">{asset_html}</div>
    </div>"""


# ──────────────────────────────────────────────
#  GET: หน้า list
# ──────────────────────────────────────────────
@approve_list_bp.route("/api/approve-list")
def approve_list():
    emp = request.args.get("emp", "").strip()
    if not emp:
        return "ต้องระบุ ?emp=<EMP_ID>", 400

    docs = _fetch(emp)
    g = {"waiting": [], "approved": [], "rejected": []}
    for d in docs:
        key = _GROUP.get((d.get("apv_status") or "").strip())
        if key:
            g[key].append(d)

    sec_wait = "".join(_card(d, "waiting")  for d in g["waiting"])  or '<div class="empty">— ไม่มี —</div>'
    sec_appr = "".join(_card(d, "approved") for d in g["approved"]) or '<div class="empty">— ไม่มี —</div>'
    sec_rej  = "".join(_card(d, "rejected") for d in g["rejected"]) or '<div class="empty">— ไม่มี —</div>'

    return PAGE.format(
        emp=emp,
        n_wait=len(g["waiting"]), n_appr=len(g["approved"]), n_rej=len(g["rejected"]),
        sec_wait=sec_wait, sec_appr=sec_appr, sec_rej=sec_rej,
        emp_js=json.dumps(emp),
    )


# ──────────────────────────────────────────────
#  TEMPLATE  (ใช้ .format → CSS ใส่ {{ }})
# ──────────────────────────────────────────────
PAGE = """<!DOCTYPE html>
<html lang="th"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>เอกสารของฉัน</title>
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing:border-box; }}
  body {{ font-family:'Sarabun',sans-serif; background:#f4f5fa; margin:0; padding:18px; color:#1f2937; }}
  .wrap {{ max-width:1180px; margin:0 auto; }}
  .head {{ background:#4f46e5; color:#fff; border-radius:16px; padding:20px 26px;
           display:flex; align-items:center; gap:16px; margin-bottom:14px; }}
  .head .ic {{ width:46px; height:46px; background:rgba(255,255,255,.2); border-radius:12px;
               display:flex; align-items:center; justify-content:center; font-size:24px; }}
  .head h1 {{ margin:0; font-size:22px; }}
  .head small {{ opacity:.85; font-size:13px; }}

  .tabs {{ display:flex; gap:30px; border-bottom:2px solid #e5e7eb; margin-bottom:18px; padding:0 6px; }}
  .tab {{ background:none; border:none; font-family:inherit; font-size:16px; font-weight:600;
          color:#9ca3af; padding:12px 4px; cursor:pointer; border-bottom:3px solid transparent;
          margin-bottom:-2px; display:flex; align-items:center; gap:8px; }}
  .tab.on {{ color:#4f46e5; border-bottom-color:#4f46e5; }}
  .tab .c {{ background:#eef2ff; color:#4338ca; border-radius:999px; font-size:12px; padding:1px 10px; }}

  .bulk {{ display:none; background:#fff; border:1px solid #e5e7eb; border-radius:12px;
           padding:10px 16px; margin-bottom:14px; align-items:center; gap:14px; }}
  .bulk.show {{ display:flex; }}
  .bulk .cnt {{ font-weight:600; }}
  .bulk .grow {{ flex:1; }}

  .card {{ background:#fff; border:1px solid #eceef3; border-radius:14px; margin-bottom:12px;
           box-shadow:0 1px 2px rgba(0,0,0,.04); }}
  .card-row {{ display:grid; grid-template-columns:24px 70px 1.3fr 2fr auto 24px;
               gap:16px; align-items:start; padding:16px 18px; }}
  .pick {{ width:18px; height:18px; margin-top:4px; cursor:pointer; }}
  .ref {{ font-size:22px; font-weight:700; color:#4f46e5; }}
  .who {{ display:flex; gap:10px; }}
  .avatar {{ width:38px; height:38px; border-radius:50%; background:#eef2ff;
             display:flex; align-items:center; justify-content:center; font-size:18px; flex:0 0 auto; }}
  .wname {{ font-weight:600; font-size:14px; }}
  .wdate {{ font-size:12px; color:#9ca3af; margin-top:2px; }}
  .status {{ display:inline-block; font-size:12px; border-radius:8px; padding:3px 9px; margin-top:6px; }}
  .status.wait {{ background:#eff6ff; color:#2563eb; }}
  .status.okc  {{ background:#ecfdf5; color:#059669; }}
  .status.noc  {{ background:#fef2f2; color:#dc2626; }}
  .detail {{ font-size:13.5px; line-height:1.6; }}
  .tline.remark {{ white-space:pre-wrap; }}
  .alink {{ color:#4f46e5; font-weight:600; }}

  .action {{ align-self:center; }}
  .btns {{ display:flex; gap:10px; }}
  .btn {{ font-family:inherit; font-size:14px; font-weight:600; border-radius:10px;
          padding:9px 18px; cursor:pointer; border:1.5px solid; background:#fff; white-space:nowrap; }}
  .btn.approve {{ color:#059669; border-color:#a7f3d0; }}
  .btn.approve:hover {{ background:#ecfdf5; }}
  .btn.reject  {{ color:#dc2626; border-color:#fecaca; }}
  .btn.reject:hover {{ background:#fef2f2; }}
  .badge {{ font-size:13px; font-weight:600; border-radius:999px; padding:6px 14px; white-space:nowrap; }}
  .badge.ok {{ background:#ecfdf5; color:#059669; }}
  .badge.no {{ background:#fef2f2; color:#dc2626; }}

  .caret {{ cursor:pointer; color:#9ca3af; font-size:16px; user-select:none; align-self:center; }}
  .expand {{ display:none; padding:0 18px 16px; }}
  .expand.open {{ display:block; }}
  .assets-title {{ font-weight:600; font-size:14px; margin:4px 0 8px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid #eef0f4; }}
  thead th {{ background:#f8f9fc; color:#6b7280; font-weight:600; }}

  .empty {{ color:#9ca3af; font-size:14px; padding:30px; text-align:center; }}
  .pane {{ display:none; }} .pane.on {{ display:block; }}
  .toast {{ position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
            background:#1f2937; color:#fff; padding:12px 22px; border-radius:10px;
            font-size:14px; opacity:0; transition:.25s; pointer-events:none; }}
  .toast.show {{ opacity:1; }}
</style></head><body>
<div class="wrap">
  <div class="head">
    <div class="ic">📋</div>
    <div><h1>เอกสารของฉัน</h1><small>EMP: {emp}</small></div>
  </div>

  <div class="tabs">
    <button class="tab on" onclick="tab(0,this)">รออนุมัติ <span class="c">{n_wait}</span></button>
    <button class="tab" onclick="tab(1,this)">อนุมัติแล้ว <span class="c">{n_appr}</span></button>
    <button class="tab" onclick="tab(2,this)">ยกเลิก <span class="c">{n_rej}</span></button>
  </div>

  <div class="bulk" id="bulk">
    <span class="cnt">เลือก <b id="bn">0</b> รายการ</span>
    <span class="grow"></span>
    <button class="btn approve" onclick="actSel('Approve')">✔ อนุมัติที่เลือก</button>
    <button class="btn reject"  onclick="actSel('Reject')">✕ ยกเลิกที่เลือก</button>
  </div>

  <div class="pane on" id="p0">{sec_wait}</div>
  <div class="pane" id="p1">{sec_appr}</div>
  <div class="pane" id="p2">{sec_rej}</div>
</div>
<div class="toast" id="toast"></div>

<script>
const EMP = {emp_js};

function tab(i, el) {{
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on');
  document.querySelectorAll('.pane').forEach((p,idx)=>p.classList.toggle('on', idx===i));
  // bulk bar เฉพาะ tab รออนุมัติ
  if (i!==0) document.getElementById('bulk').classList.remove('show');
  else sync();
}}

function tog(el) {{
  const ex = el.closest('.card').querySelector('.expand');
  ex.classList.toggle('open');
  el.textContent = ex.classList.contains('open') ? '▴' : '▾';
}}

function picks() {{
  return [...document.querySelectorAll('#p0 .pick:checked')].map(c=>c.value);
}}
function sync() {{
  const n = picks().length;
  const b = document.getElementById('bulk');
  b.classList.toggle('show', n>0);
  document.getElementById('bn').textContent = n;
}}

function toast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2200);
}}

async function send(ids, action) {{
  const label = action==='Approve' ? 'อนุมัติ' : 'ยกเลิก';
  if (!confirm(`ยืนยัน${{label}} ${{ids.length}} รายการ?`)) return;
  try {{
    const r = await fetch('/api/approve-list/action', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{emp:EMP, ids, action}})
    }});
    const j = await r.json();
    if (!r.ok) {{ toast('ผิดพลาด: ' + (j.error||'')); return; }}
    toast(`${{label}}สำเร็จ ${{j.ok.length}} รายการ`);
    setTimeout(()=>location.reload(), 700);
  }} catch(e) {{ toast('เชื่อมต่อไม่ได้'); }}
}}

function act(rid, action) {{ send([rid], action); }}
function actSel(action) {{
  const ids = picks();
  if (!ids.length) {{ toast('ยังไม่ได้เลือกรายการ'); return; }}
  send(ids, action);
}}
</script>
</body></html>"""
