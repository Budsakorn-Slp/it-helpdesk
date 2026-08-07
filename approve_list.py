# -*- coding: utf-8 -*-
# approve_list.py — หน้ารวมเอกสารของผู้อนุมัติแต่ละคน
#   GET  /api/approve-list?emp=<EMP_ID>          → หน้า list (tab + กางดูสินทรัพย์ + ปุ่ม)
#   POST /api/approve-list/action               → อนุมัติ/ยกเลิก (รายตัว หรือหลายอันพร้อมกัน)

import html as _html
import os
import json
from flask import Blueprint, request, jsonify

import config


def esc(v) -> str:
    """escape ค่าที่มาจาก DB ก่อนยัดลง HTML

    remark / ชื่อสินทรัพย์ ฯลฯ เป็นข้อความที่ผู้ใช้พิมพ์เอง ถ้าไม่ escape
    เครื่องหมาย < > จะทำให้หน้าเพี้ยนหรือแทรก tag ได้
    """
    return _html.escape("" if v is None else str(v))

approve_list_bp = Blueprint("approve_list_bp", __name__)

# ── Oracle Instant Client (guard กันชนกับ blueprint อื่น) ──


def _db_conn():
    return config.connect()


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
    except config.db_error() as e:
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
    """รายการสินทรัพย์ — จอเล็กแสดงเป็นการ์ดซ้อนกัน จอใหญ่เป็นตาราง"""
    if not d["assets"]:
        return ""
    frm = esc(_loc(d.get("from_site"), d.get("from_division"), d.get("from_costcenter"),
                   d.get("from_cost_code"), None))
    to = esc(_loc(d.get("to_site"), d.get("to_division"), d.get("to_costcenter"),
                  d.get("to_cost_code"), None))
    rows = ""
    for i, a in enumerate(d["assets"], 1):
        code   = esc(a.get("asset_code") or "-")
        aname  = esc(a.get("asset_name") or "-")
        remark = esc((a.get("asset_remark") or "").strip() or "-")
        # data-l ใช้เป็น label ตอน layout พับเป็นการ์ดบนมือถือ
        rows += f"""<tr>
            <td data-l="#">{i}</td>
            <td data-l="รหัส">{code}</td>
            <td data-l="ชื่อสินค้า">{aname}</td>
            <td data-l="ต้นทาง">{frm}</td>
            <td data-l="ปลายทาง">{to}</td>
            <td data-l="หมายเหตุ">{remark}</td>
          </tr>"""
    return f"""
      <div class="assets">
        <div class="assets-title">รายการสินทรัพย์ ({len(d['assets'])})</div>
        <div class="twrap">
        <table>
          <thead><tr>
            <th>#</th><th>รหัสสินทรัพย์</th><th>ชื่อสินค้า / รายละเอียด</th>
            <th>ต้นทาง</th><th>ปลายทาง</th><th>หมายเหตุ</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
        </div>
      </div>"""


def _card(d, group):
    rid  = d["request_id"]
    name = f"{d.get('requester_fname') or ''} {d.get('requester_lname') or ''}".strip() or "-"
    cat  = d.get("request_category") or "-"
    ttl  = d.get("transfer_type_name") or cat
    has_tf = bool(d.get("transfer_id"))

    if has_tf:
        frm = esc(_loc(d.get("from_site"), d.get("from_division"), d.get("from_costcenter"),
                       d.get("from_cost_code"), d.get("from_location")))
        to  = esc(_loc(d.get("to_site"), d.get("to_division"), d.get("to_costcenter"),
                       d.get("to_cost_code"), d.get("to_location")))
        n   = len(d["assets"])
        detail = f"""
          <div class="kv"><span class="k">ต้นทาง</span><span class="v">{frm}</span></div>
          <div class="kv"><span class="k">ปลายทาง</span><span class="v">{to}</span></div>
          <div class="kv"><span class="k">สินทรัพย์</span><span class="v">{n} รายการ</span></div>"""
        asset_html = _asset_table(d)
    else:
        remark = esc((d.get("request_remark") or "").strip() or "-")
        detail = f'<div class="remark">{remark}</div>'
        asset_html = ""

    # ปุ่ม / badge ตามกลุ่ม
    if group == "waiting":
        actions = f"""
            <button class="btn approve" onclick="act('{rid}','Approve')">อนุมัติ</button>
            <button class="btn reject"  onclick="act('{rid}','Reject')">ยกเลิก</button>"""
        check = f'<label class="pickwrap"><input type="checkbox" class="pick" value="{rid}" onchange="sync()"><span></span></label>'
        chip  = '<span class="chip wait">รออนุมัติ</span>'
    elif group == "approved":
        actions = ""
        check   = ""
        chip    = '<span class="chip ok">อนุมัติแล้ว</span>'
    else:
        actions = ""
        check   = ""
        chip    = '<span class="chip no">ยกเลิกแล้ว</span>'

    expand_btn = ('<button class="more" onclick="tog(this)" aria-expanded="false">'
                  'ดูสินทรัพย์</button>') if asset_html else ""
    foot = (f'<div class="foot">{expand_btn}<span class="grow"></span>{actions}</div>'
            if (expand_btn or actions) else "")

    return f"""
    <div class="card" data-rid="{rid}">
      <div class="top">
        {check}
        <span class="ref">#{rid}</span>
        {chip}
      </div>
      <div class="ttl">{esc(ttl)}</div>
      <div class="body">{detail}</div>
      <div class="meta">
        <span>{esc(name)}</span><span class="dot">·</span><span>{esc(d.get('req_date') or '-')}</span>
      </div>
      {foot}
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#4f46e5">
<title>เอกสารของฉัน</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:#f2f3f9; --surface:#ffffff; --line:#e7e9f2;
    --text:#171a2b; --muted:#767c95; --brand:#4f46e5; --brand-soft:#eef0fe;
    --ok:#0e9f6e; --ok-soft:#e8f8f1; --no:#e02424; --no-soft:#fdecec;
    --wait:#2563eb; --wait-soft:#e8f0fe;
    --shadow:0 1px 2px rgba(16,20,50,.05), 0 8px 24px -12px rgba(16,20,50,.14);
    --r:16px;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg:#0f1118; --surface:#191c26; --line:#282c3a;
      --text:#e9ebf5; --muted:#9aa0b8; --brand:#8b87ff; --brand-soft:#23233f;
      --ok:#34d399; --ok-soft:#13301f; --no:#f87171; --no-soft:#361a1a;
      --wait:#7aa5ff; --wait-soft:#16203a;
      --shadow:0 1px 2px rgba(0,0,0,.4);
    }}
  }}

  * {{ box-sizing:border-box; -webkit-tap-highlight-color:transparent; }}
  html {{ -webkit-text-size-adjust:100%; }}
  body {{
    font-family:'Sarabun',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    background:var(--bg); color:var(--text); margin:0;
    font-size:15px; line-height:1.55;
    padding:0 0 calc(20px + env(safe-area-inset-bottom));
  }}
  .wrap {{ max-width:820px; margin:0 auto; padding:0 14px; }}

  /* header */
  .head {{
    background:var(--brand); color:#fff;
    padding:calc(18px + env(safe-area-inset-top)) 18px 18px;
  }}
  .head-in {{ max-width:820px; margin:0 auto; display:flex; align-items:center; gap:13px; }}
  .head .ic {{
    width:42px; height:42px; flex:0 0 auto; border-radius:13px;
    background:rgba(255,255,255,.18);
    display:flex; align-items:center; justify-content:center; font-size:21px;
  }}
  .head h1 {{ margin:0; font-size:19px; font-weight:700; letter-spacing:-.2px; }}
  .head small {{ opacity:.82; font-size:12.5px; }}

  /* tabs: เลื่อนแนวนอนได้ ไม่ล้นจอ */
  .tabbar {{
    position:sticky; top:0; z-index:20;
    background:var(--surface); border-bottom:1px solid var(--line);
    margin-bottom:14px;
  }}
  .tabs {{
    max-width:820px; margin:0 auto; display:flex; gap:4px;
    padding:0 10px; overflow-x:auto; scrollbar-width:none;
  }}
  .tabs::-webkit-scrollbar {{ display:none; }}
  .tab {{
    flex:1 0 auto; min-height:48px; background:none; border:none;
    font-family:inherit; font-size:14.5px; font-weight:600; color:var(--muted);
    padding:12px 10px; cursor:pointer; white-space:nowrap;
    border-bottom:2.5px solid transparent; display:flex; align-items:center;
    justify-content:center; gap:7px;
  }}
  .tab.on {{ color:var(--brand); border-bottom-color:var(--brand); }}
  .tab .c {{
    background:var(--brand-soft); color:var(--brand); border-radius:999px;
    font-size:11.5px; font-weight:700; padding:1px 8px; min-width:24px;
  }}
  .tab.on .c {{ background:var(--brand); color:#fff; }}

  /* card */
  .card {{
    background:var(--surface); border:1px solid var(--line); border-radius:var(--r);
    box-shadow:var(--shadow); margin-bottom:11px; overflow:hidden;
  }}
  .top {{ display:flex; align-items:center; gap:10px; padding:13px 15px 0; }}
  .ref {{ font-size:16px; font-weight:700; color:var(--brand); }}
  .chip {{
    margin-left:auto; font-size:11.5px; font-weight:600;
    padding:3px 11px; border-radius:999px; white-space:nowrap;
  }}
  .chip.wait {{ background:var(--wait-soft); color:var(--wait); }}
  .chip.ok   {{ background:var(--ok-soft);   color:var(--ok); }}
  .chip.no   {{ background:var(--no-soft);   color:var(--no); }}

  /* checkbox แตะง่าย (พื้นที่แตะ 44px) */
  .pickwrap {{
    position:relative; width:22px; height:22px; flex:0 0 auto;
    display:inline-flex; cursor:pointer;
  }}
  .pickwrap::before {{ content:""; position:absolute; inset:-11px; }}
  .pickwrap input {{ position:absolute; opacity:0; width:100%; height:100%; margin:0; cursor:pointer; }}
  .pickwrap span {{
    width:22px; height:22px; border:2px solid var(--line); border-radius:7px;
    background:var(--surface); transition:.15s;
  }}
  .pickwrap input:checked + span {{ background:var(--brand); border-color:var(--brand); }}
  .pickwrap input:checked + span::after {{
    content:""; position:absolute; left:7.5px; top:3.5px;
    width:5px; height:10px; border:solid #fff; border-width:0 2.5px 2.5px 0;
    transform:rotate(45deg);
  }}

  .ttl {{ font-size:15.5px; font-weight:600; padding:7px 15px 0; }}
  .body {{ padding:6px 15px 0; }}
  .kv {{ display:flex; gap:8px; font-size:13.5px; margin-bottom:3px; }}
  .kv .k {{ color:var(--muted); flex:0 0 62px; }}
  .kv .v {{ flex:1; min-width:0; word-break:break-word; }}
  .remark {{ font-size:13.5px; white-space:pre-wrap; word-break:break-word; }}
  .meta {{ font-size:12.5px; color:var(--muted); padding:8px 15px 0; }}
  .meta .dot {{ margin:0 6px; }}

  .foot {{
    display:flex; align-items:center; gap:9px;
    padding:12px 15px 14px; margin-top:4px; flex-wrap:wrap;
  }}
  .grow {{ flex:1; }}
  .btn {{
    font-family:inherit; font-size:14px; font-weight:600; border-radius:11px;
    min-height:42px; padding:0 20px; cursor:pointer;
    border:1.5px solid; background:var(--surface); white-space:nowrap;
  }}
  .btn.approve {{ color:#fff; background:var(--ok); border-color:var(--ok); }}
  .btn.reject  {{ color:var(--no); border-color:var(--no-soft); background:var(--no-soft); }}
  .more {{
    font-family:inherit; font-size:13.5px; font-weight:600; color:var(--brand);
    background:none; border:none; padding:6px 0; min-height:38px; cursor:pointer;
  }}
  .more::after {{ content:" \\25be"; }}
  .more[aria-expanded="true"]::after {{ content:" \\25b4"; }}

  /* assets */
  .expand {{ display:none; padding:0 15px 15px; }}
  .expand.open {{ display:block; }}
  .assets-title {{ font-weight:600; font-size:13.5px; margin:2px 0 9px; color:var(--muted); }}
  .twrap {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); }}
  thead th {{ background:var(--bg); color:var(--muted); font-weight:600; white-space:nowrap; }}

  /* จอเล็ก: ตารางพับเป็นการ์ด ไม่ต้องเลื่อนแนวนอน */
  @media (max-width:640px) {{
    table, thead, tbody, tr, td {{ display:block; width:100%; }}
    thead {{ display:none; }}
    tr {{
      border:1px solid var(--line); border-radius:12px;
      padding:6px 12px; margin-bottom:9px;
    }}
    td {{ border:none; padding:5px 0; display:flex; gap:12px; }}
    td::before {{
      content:attr(data-l); color:var(--muted); flex:0 0 84px; font-size:12.5px;
    }}
  }}

  .empty {{ color:var(--muted); font-size:14px; padding:44px 20px; text-align:center; }}
  .pane {{ display:none; }} .pane.on {{ display:block; }}

  /* แถบเลือกหลายรายการ: ลอยล่างจอ */
  .bulk {{
    display:none; position:fixed; left:0; right:0; bottom:0; z-index:30;
    background:var(--surface); border-top:1px solid var(--line);
    padding:11px 14px calc(11px + env(safe-area-inset-bottom));
    box-shadow:0 -6px 24px -12px rgba(16,20,50,.3);
    align-items:center; gap:10px;
  }}
  .bulk.show {{ display:flex; }}
  .bulk .cnt {{ font-size:13.5px; font-weight:600; white-space:nowrap; }}
  .bulk .btn {{ padding:0 15px; }}

  .toast {{
    position:fixed; left:50%; bottom:88px; transform:translate(-50%,10px);
    background:#1f2333; color:#fff; padding:12px 22px; border-radius:12px;
    font-size:14px; opacity:0; transition:.25s; pointer-events:none; z-index:50;
    max-width:90vw; text-align:center;
  }}
  .toast.show {{ opacity:1; transform:translate(-50%,0); }}

  @media (min-width:641px) {{
    .wrap {{ padding:0 20px; }}
    .kv .k {{ flex:0 0 78px; }}
    .bulk {{
      border-radius:14px; left:50%; transform:translateX(-50%);
      bottom:18px; right:auto; width:min(720px,calc(100% - 40px));
      border:1px solid var(--line);
    }}
  }}
</style></head><body>

<div class="head">
  <div class="head-in">
    <div class="ic">&#128203;</div>
    <div><h1>เอกสารของฉัน</h1><small>รหัสพนักงาน {emp}</small></div>
  </div>
</div>

<div class="tabbar">
  <div class="tabs">
    <button class="tab on" onclick="tab(0,this)">รออนุมัติ <span class="c">{n_wait}</span></button>
    <button class="tab" onclick="tab(1,this)">อนุมัติแล้ว <span class="c">{n_appr}</span></button>
    <button class="tab" onclick="tab(2,this)">ยกเลิก <span class="c">{n_rej}</span></button>
  </div>
</div>

<div class="wrap">
  <div class="pane on" id="p0">{sec_wait}</div>
  <div class="pane" id="p1">{sec_appr}</div>
  <div class="pane" id="p2">{sec_rej}</div>
</div>

<div class="bulk" id="bulk">
  <span class="cnt">เลือก <b id="bn">0</b> รายการ</span>
  <span class="grow"></span>
  <button class="btn reject"  onclick="actSel('Reject')">ยกเลิก</button>
  <button class="btn approve" onclick="actSel('Approve')">อนุมัติ</button>
</div>
<div class="toast" id="toast"></div>

<script>
const EMP = {emp_js};

function tab(i, el) {{
  document.querySelectorAll('.tab').forEach(function (t) {{ t.classList.remove('on'); }});
  el.classList.add('on');
  document.querySelectorAll('.pane').forEach(function (p, idx) {{ p.classList.toggle('on', idx === i); }});
  // แถบเลือกหลายรายการมีเฉพาะแท็บรออนุมัติ
  if (i !== 0) document.getElementById('bulk').classList.remove('show');
  else sync();
}}

function tog(el) {{
  var ex = el.closest('.card').querySelector('.expand');
  var open = ex.classList.toggle('open');
  el.setAttribute('aria-expanded', open ? 'true' : 'false');
  el.firstChild.nodeValue = open ? 'ซ่อนสินทรัพย์' : 'ดูสินทรัพย์';
}}

function picks() {{
  return Array.prototype.slice.call(document.querySelectorAll('#p0 .pick:checked'))
    .map(function (c) {{ return c.value; }});
}}
function sync() {{
  var n = picks().length;
  document.getElementById('bulk').classList.toggle('show', n > 0);
  document.getElementById('bn').textContent = n;
}}

function toast(msg) {{
  var t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(function () {{ t.classList.remove('show'); }}, 2200);
}}

async function send(ids, action) {{
  var label = action === 'Approve' ? 'อนุมัติ' : 'ยกเลิก';
  if (!confirm('ยืนยัน' + label + ' ' + ids.length + ' รายการ?')) return;
  try {{
    var r = await fetch('/api/approve-list/action', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{emp: EMP, ids: ids, action: action}})
    }});
    var j = await r.json();
    if (!r.ok) {{ toast('ผิดพลาด: ' + (j.error || '')); return; }}
    toast(label + 'สำเร็จ ' + j.ok.length + ' รายการ');
    setTimeout(function () {{ location.reload(); }}, 700);
  }} catch (e) {{ toast('เชื่อมต่อไม่ได้'); }}
}}

function act(rid, action) {{ send([rid], action); }}
function actSel(action) {{
  var ids = picks();
  if (!ids.length) {{ toast('ยังไม่ได้เลือกรายการ'); return; }}
  send(ids, action);
}}
</script>
</body></html>"""
