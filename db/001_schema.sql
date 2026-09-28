-- =====================================================================
-- ict · 招采实体与关系 —— 初始 schema
-- 对应契约：doc/ICT结构、语义与项目流程规定.md
--   Project{project_id=<项目名>|<包号>, cobs[], subs[]}
-- 说明：DDL 只建结构，不含任何数据。演示库与生产库共用本文件，结构完全一致。
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- 中文名称模糊匹配

-- ---------------------------------------------------------------------
-- 名称归一化：去空白（含全角空格）+ 去联合体成员说明括号
-- 契约要求：联合体整体算一家；完全同名视为同一家
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION norm_name(txt text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT btrim(
           regexp_replace(
             regexp_replace(coalesce(txt, ''), '[[:space:]　]+', '', 'g'),
             '[（(][^）)]*成员[^）)]*[）)]', '', 'g'
           )
         )
$$;

-- ---------------------------------------------------------------------
-- 1) 公告
-- ---------------------------------------------------------------------
CREATE TABLE announcement (
  announcement_id     text PRIMARY KEY,              -- t20260202_26139731
  title               text,
  source_project_no   text,
  announcement_type   text,                          -- winning_announcement | deal_announcement | unknown
  summary_amount_yuan numeric(18,2),
  run_status          text,                          -- pending|running|success|partial|failed
  review_required     boolean NOT NULL DEFAULT false,
  created_at          timestamptz NOT NULL DEFAULT now(),
  raw_json            jsonb                          -- 兜底：任务一原始结构化结果
);

-- ---------------------------------------------------------------------
-- 2) 项目（= 项目名 + 包号，一个包一行）
-- ---------------------------------------------------------------------
CREATE TABLE project (
  project_id           text PRIMARY KEY,             -- <project_name>|<package_no>
  announcement_id      text NOT NULL REFERENCES announcement(announcement_id) ON DELETE CASCADE,
  project_name         text NOT NULL,
  package_no           text NOT NULL,                -- 字符串："1" / "4" / "A"
  purchaser            text,
  package_total_amount numeric(18,2),
  source_project_no    text,
  UNIQUE (announcement_id, project_name, package_no)
);

-- ---------------------------------------------------------------------
-- 3) 标的物（COB）
-- ---------------------------------------------------------------------
CREATE TABLE cob (
  cob_id           bigserial PRIMARY KEY,
  project_id       text NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  object_name      text NOT NULL,
  category_code    text,
  category_name    text,
  category_type    char(1) CHECK (category_type IN ('A','B','C')),   -- 货物/工程/服务
  brand            text,
  product_supplier text,                              -- 产品供应商（未必参与投标）
  spec_model       text,
  unit_price       numeric(18,2),                     -- 人民币元
  quantity         numeric(18,4),
  unit             text,
  total_price      numeric(18,2),
  source_type      text CHECK (source_type IN ('html','pdf','docx','doc','xlsx')),
  source_file_id   text
);

-- ---------------------------------------------------------------------
-- 4) 主体（归一化）
-- ---------------------------------------------------------------------
CREATE TABLE supplier (
  supplier_id bigserial PRIMARY KEY,
  name        text NOT NULL,                          -- 原文写法
  norm_name   text NOT NULL UNIQUE                    -- 归一化后（去空白/去成员括号）
);

-- ---------------------------------------------------------------------
-- 5) 投标记录（SUB）：项目 × 主体，一次竞标一行
-- ---------------------------------------------------------------------
CREATE TABLE bid (
  project_id  text   NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  supplier_id bigint NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
  score       numeric(8,3),                           -- 综合得分/评审总得分，可为空
  is_winner   boolean NOT NULL DEFAULT false,
  PRIMARY KEY (project_id, supplier_id)
);

-- ---------------------------------------------------------------------
-- 6) 合作产品供应商（仅中标供应商填写）
-- ---------------------------------------------------------------------
CREATE TABLE winner_coop_supplier (
  project_id            text   NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
  supplier_id           bigint NOT NULL REFERENCES supplier(supplier_id) ON DELETE CASCADE,
  product_supplier_name text   NOT NULL,
  PRIMARY KEY (project_id, supplier_id, product_supplier_name)
);

-- ---------------------------------------------------------------------
-- 索引
-- ---------------------------------------------------------------------
CREATE INDEX idx_project_announcement ON project (announcement_id);
CREATE INDEX idx_project_purchaser    ON project (purchaser);
CREATE INDEX idx_project_name_trgm    ON project USING gin (project_name gin_trgm_ops);
CREATE INDEX idx_cob_project          ON cob (project_id);
CREATE INDEX idx_cob_category         ON cob (category_code);
CREATE INDEX idx_cob_brand            ON cob (brand);
CREATE INDEX idx_cob_object_trgm      ON cob USING gin (object_name gin_trgm_ops);
CREATE INDEX idx_cob_supplier_trgm    ON cob USING gin (product_supplier gin_trgm_ops);
CREATE INDEX idx_supplier_norm_trgm   ON supplier USING gin (norm_name gin_trgm_ops);
CREATE INDEX idx_bid_supplier         ON bid (supplier_id);
CREATE INDEX idx_bid_winner           ON bid (project_id) WHERE is_winner;
CREATE INDEX idx_announcement_raw     ON announcement USING gin (raw_json);

-- ---------------------------------------------------------------------
-- 视图：标的物视图记录（Project + COB + 中标方），直接供应 /api/objects/search
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_cob_record AS
SELECT c.cob_id,
       p.project_id, p.project_name, p.package_no, p.purchaser, p.package_total_amount,
       c.object_name, c.category_code, c.category_name, c.category_type,
       c.brand, c.product_supplier, c.spec_model,
       c.unit_price, c.quantity, c.unit, c.total_price,
       w.name   AS winner_name,
       w.score  AS winner_score,
       p.announcement_id, c.source_type, c.source_file_id
FROM cob c
JOIN project p ON p.project_id = c.project_id
-- 防御：即使某包出现多个中标行，也只取一个，避免标的行被放大
LEFT JOIN LATERAL (
  SELECT s.name, wb.score
  FROM bid wb JOIN supplier s ON s.supplier_id = wb.supplier_id
  WHERE wb.project_id = p.project_id AND wb.is_winner
  ORDER BY wb.score DESC NULLS LAST
  LIMIT 1
) w ON true;
