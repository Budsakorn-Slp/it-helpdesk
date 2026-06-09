-- ══════════════════════════════════════════════════════
--  IT Helpdesk — Table Schema
--  แก้ชื่อ table ให้ตรงกับ CATEGORIES ใน app.py
-- ══════════════════════════════════════════════════════

-- หมวด 1
CREATE TABLE IF NOT EXISTS it_request_cat1 (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ticket_no   VARCHAR(30)  NOT NULL UNIQUE,
    emp_id      VARCHAR(20)  NOT NULL,
    phone       VARCHAR(20)  NOT NULL,
    fullname    VARCHAR(100) DEFAULT NULL,
    dept        VARCHAR(100) DEFAULT NULL,
    subject     VARCHAR(255) NOT NULL,
    detail      TEXT         DEFAULT NULL,
    priority    VARCHAR(30)  DEFAULT 'ปกติ',
    status      VARCHAR(30)  DEFAULT 'รอดำเนินการ',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
    resolved_at DATETIME     DEFAULT NULL,
    note        TEXT         DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- หมวด 2
CREATE TABLE IF NOT EXISTS it_request_cat2 (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ticket_no   VARCHAR(30)  NOT NULL UNIQUE,
    emp_id      VARCHAR(20)  NOT NULL,
    phone       VARCHAR(20)  NOT NULL,
    fullname    VARCHAR(100) DEFAULT NULL,
    dept        VARCHAR(100) DEFAULT NULL,
    subject     VARCHAR(255) NOT NULL,
    detail      TEXT         DEFAULT NULL,
    priority    VARCHAR(30)  DEFAULT 'ปกติ',
    status      VARCHAR(30)  DEFAULT 'รอดำเนินการ',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
    resolved_at DATETIME     DEFAULT NULL,
    note        TEXT         DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- หมวด 3
CREATE TABLE IF NOT EXISTS it_request_cat3 (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    ticket_no   VARCHAR(30)  NOT NULL UNIQUE,
    emp_id      VARCHAR(20)  NOT NULL,
    phone       VARCHAR(20)  NOT NULL,
    fullname    VARCHAR(100) DEFAULT NULL,
    dept        VARCHAR(100) DEFAULT NULL,
    subject     VARCHAR(255) NOT NULL,
    detail      TEXT         DEFAULT NULL,
    priority    VARCHAR(30)  DEFAULT 'ปกติ',
    status      VARCHAR(30)  DEFAULT 'รอดำเนินการ',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
    resolved_at DATETIME     DEFAULT NULL,
    note        TEXT         DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Index เพิ่มความเร็วการค้นหา
CREATE INDEX idx_cat1_emp    ON it_request_cat1 (emp_id);
CREATE INDEX idx_cat1_status ON it_request_cat1 (status);
CREATE INDEX idx_cat2_emp    ON it_request_cat2 (emp_id);
CREATE INDEX idx_cat2_status ON it_request_cat2 (status);
CREATE INDEX idx_cat3_emp    ON it_request_cat3 (emp_id);
CREATE INDEX idx_cat3_status ON it_request_cat3 (status);
