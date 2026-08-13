-- ══════════════════════════════════════════════════════════════
--  002_attachment.sql
--  ตารางเก็บไฟล์แนบของแต่ละคำขอ (แนบได้หลายไฟล์)
-- ══════════════════════════════════════════════════════════════
--
--  ทำไมต้องมีตารางใหม่:
--  IT_HELPDESK_REQUEST.REQUEST_FILE เก็บได้ไฟล์เดียว (ชื่อไฟล์ตรง ๆ)
--  ส่วนนี้ต้องแนบได้สูงสุด 3 ไฟล์ พร้อมเก็บชื่อไฟล์เดิม ขนาด และคนอัปโหลด
--  จะยัดรวมใน VARCHAR2 เดิมแล้วคั่นด้วยคอมมาก็ได้ แต่ลบทีละไฟล์/โชว์ขนาด
--  จะยุ่งและพังง่ายถ้าชื่อไฟล์มีคอมมา
--
--  FILE_NAME = ชื่อไฟล์บนดิสก์ (static/uploads/) มี timestamp กันชนกัน
--  ORIG_NAME = ชื่อไฟล์เดิมที่ผู้ใช้อัปโหลด เอาไว้แสดงผล
--
--  วิธีรัน:  sqlplus <user>/<pass>@<dsn> @migrations/002_attachment.sql
--  รันซ้ำได้ — ถ้ามีอยู่แล้วจะข้ามให้เอง
-- ══════════════════════════════════════════════════════════════

DECLARE
    n NUMBER;
BEGIN
    SELECT COUNT(*) INTO n FROM USER_TABLES
    WHERE TABLE_NAME = 'IT_HELPDESK_ATTACHMENT';

    IF n = 0 THEN
        EXECUTE IMMEDIATE '
            CREATE TABLE IT_HELPDESK_ATTACHMENT (
                ID           NUMBER          NOT NULL,
                REQUEST_ID   NUMBER          NOT NULL,
                FILE_NAME    VARCHAR2(400)   NOT NULL,
                ORIG_NAME    VARCHAR2(400),
                FILE_SIZE    NUMBER,
                UPLOADED_BY  VARCHAR2(200),
                UPLOADED_AT  DATE DEFAULT SYSDATE,
                CONSTRAINT PK_IT_HELPDESK_ATTACHMENT PRIMARY KEY (ID)
            )';
        DBMS_OUTPUT.PUT_LINE('created IT_HELPDESK_ATTACHMENT');
    ELSE
        DBMS_OUTPUT.PUT_LINE('IT_HELPDESK_ATTACHMENT already exists - skipped');
    END IF;

    SELECT COUNT(*) INTO n FROM USER_INDEXES
    WHERE INDEX_NAME = 'IX_ATTACHMENT_REQUEST';

    IF n = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE INDEX IX_ATTACHMENT_REQUEST ON IT_HELPDESK_ATTACHMENT (REQUEST_ID)';
        DBMS_OUTPUT.PUT_LINE('created IX_ATTACHMENT_REQUEST');
    ELSE
        DBMS_OUTPUT.PUT_LINE('IX_ATTACHMENT_REQUEST already exists - skipped');
    END IF;

    -- ใช้ sequence แทน MAX(ID)+1 เพราะสองคนกดอัปโหลดพร้อมกันแล้วจะได้ ID ชนกัน
    SELECT COUNT(*) INTO n FROM USER_SEQUENCES
    WHERE SEQUENCE_NAME = 'SEQ_IT_HELPDESK_ATTACHMENT';

    IF n = 0 THEN
        EXECUTE IMMEDIATE
            'CREATE SEQUENCE SEQ_IT_HELPDESK_ATTACHMENT START WITH 1 INCREMENT BY 1 NOCACHE';
        DBMS_OUTPUT.PUT_LINE('created SEQ_IT_HELPDESK_ATTACHMENT');
    ELSE
        DBMS_OUTPUT.PUT_LINE('SEQ_IT_HELPDESK_ATTACHMENT already exists - skipped');
    END IF;
END;
/
