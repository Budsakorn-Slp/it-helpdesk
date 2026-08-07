-- ══════════════════════════════════════════════════════════════
--  001_approver_forward.sql
--  เพิ่มคอลัมน์เก็บ "การส่งต่อสิทธิ์อนุมัติ" ใน IT_HELPDESK_APPROVER
-- ══════════════════════════════════════════════════════════════
--
--  ทำไมต้องมีคอลัมน์ใหม่:
--  เดิมตอนส่งต่อเขียนคนเดิมลง USER_UPDATE แต่ช่องนั้นถูก cancel_request()
--  ใช้เก็บ "ใครกดยกเลิก" อยู่แล้ว (app.py) ทำให้แยกไม่ออกว่าแถวไหนคือส่งต่อ
--  แถวไหนคือยกเลิก — แท็บ "ส่งต่อ" จะขึ้นใบที่แค่ยกเลิกปนมาด้วย
--
--  FORWARD_FROM = รหัสพนักงานคนที่ "ส่งต่อออกไป"
--                 (EMP_APPROVER คือคนที่ถือสิทธิ์อยู่ปัจจุบัน = ผู้รับมอบ)
--  FORWARD_AT   = เวลาที่ส่งต่อ
--
--  เป็นการเพิ่มคอลัมน์แบบ nullable ไม่กระทบข้อมูลเดิมและไม่ต้อง rewrite ตาราง
--
--  วิธีรัน:  sqlplus <user>/<pass>@<dsn> @migrations/001_approver_forward.sql
--  รันซ้ำได้ — ถ้ามีคอลัมน์อยู่แล้วจะข้ามให้เอง
-- ══════════════════════════════════════════════════════════════

DECLARE
    n NUMBER;
BEGIN
    SELECT COUNT(*) INTO n
    FROM   USER_TAB_COLUMNS
    WHERE  TABLE_NAME = 'IT_HELPDESK_APPROVER'
      AND  COLUMN_NAME = 'FORWARD_FROM';

    IF n = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE IT_HELPDESK_APPROVER ADD (FORWARD_FROM VARCHAR2(200))';
        DBMS_OUTPUT.PUT_LINE('added FORWARD_FROM');
    ELSE
        DBMS_OUTPUT.PUT_LINE('FORWARD_FROM already exists - skipped');
    END IF;

    SELECT COUNT(*) INTO n
    FROM   USER_TAB_COLUMNS
    WHERE  TABLE_NAME = 'IT_HELPDESK_APPROVER'
      AND  COLUMN_NAME = 'FORWARD_AT';

    IF n = 0 THEN
        EXECUTE IMMEDIATE
            'ALTER TABLE IT_HELPDESK_APPROVER ADD (FORWARD_AT DATE)';
        DBMS_OUTPUT.PUT_LINE('added FORWARD_AT');
    ELSE
        DBMS_OUTPUT.PUT_LINE('FORWARD_AT already exists - skipped');
    END IF;
END;
/

-- ค้นแท็บ "ส่งต่อ" ด้วย FORWARD_FROM เป็นหลัก
DECLARE
    n NUMBER;
BEGIN
    SELECT COUNT(*) INTO n
    FROM   USER_INDEXES
    WHERE  INDEX_NAME = 'IX_APPROVER_FWD_FROM';

    IF n = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX IX_APPROVER_FWD_FROM ON IT_HELPDESK_APPROVER (FORWARD_FROM)';
        DBMS_OUTPUT.PUT_LINE('added IX_APPROVER_FWD_FROM');
    ELSE
        DBMS_OUTPUT.PUT_LINE('IX_APPROVER_FWD_FROM already exists - skipped');
    END IF;
END;
/
