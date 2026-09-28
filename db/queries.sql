-- =====================================================================
-- ict · 查询语句集（只读）
-- 演示库与生产库结构完全一致，这一套命令两边通用，只换 -d 后面的库名。
--
-- 用法（在项目根目录执行；用 stdin 重定向把宿主机上的本文件喂给容器内的 psql，
--     因为 db/ 目录没有挂载进容器，容器内看不到 db/queries.sql）：
--   docker compose exec -T db psql -U ict -d ict_demo \
--     -v kw='' -v purchaser='佛山大学' -v winner='' -v suppliers='' < db/queries.sql
--
-- psql 变量（文本变量必须写成 :'name'，带引号插值才能正确加引号）：
--   :'purchaser'  单个采购单位名        用于 S1 / S2
--   :'winner'     单个中标供应商名      用于 S3
--   :'suppliers'  逗号分隔多个主体名    用于 S4 / S5
--   :'kw'         关键词（模糊检索）     用于第 0 节
-- 用不到的变量传空串即可。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. 标的物全字段检索（供 /api/objects/search）
--    :'kw' 为空时返回全部；否则跨 标的物/项目/品牌/规格/中标方/采购单位 匹配
-- ---------------------------------------------------------------------
SELECT cob_id, object_name, category_name, category_type, brand, spec_model,
       unit_price, quantity, unit, total_price,
       project_name, package_no, purchaser, winner_name, announcement_id
FROM v_cob_record
WHERE :'kw' = ''
   OR object_name  ILIKE '%'||:'kw'||'%'
   OR project_name ILIKE '%'||:'kw'||'%'
   OR brand        ILIKE '%'||:'kw'||'%'
   OR spec_model   ILIKE '%'||:'kw'||'%'
   OR winner_name  ILIKE '%'||:'kw'||'%'
   OR purchaser    ILIKE '%'||:'kw'||'%'
ORDER BY total_price DESC NULLS LAST
LIMIT 20 OFFSET 0;

-- 0b. 主体名称模糊匹配
--     ILIKE 子串命中（gin_trgm_ops 索引可加速），适合中文长名称/简称
SELECT name, round(similarity(norm_name, :'kw')::numeric, 3) AS sim
FROM supplier
WHERE norm_name ILIKE '%'||:'kw'||'%'
ORDER BY sim DESC NULLS LAST
LIMIT 10;
-- 需要容忍错别字、更松的相似度时，先降低阈值再用 % 操作符：
--   SET pg_trgm.similarity_threshold = 0.15;
--   SELECT name FROM supplier WHERE norm_name % :'kw';

-- ---------------------------------------------------------------------
-- S1. 指定采购单位 → 长期/大量合作的中标供应商、产品供应商
--     输出：合作次数、交易总金额
-- ---------------------------------------------------------------------
WITH win AS (
  SELECT s.name AS subject, count(*) AS coop_times, sum(p.package_total_amount) AS amount
  FROM project p
  JOIN bid b      ON b.project_id = p.project_id AND b.is_winner
  JOIN supplier s ON s.supplier_id = b.supplier_id
  WHERE p.purchaser = :'purchaser'
  GROUP BY s.name
), ps AS (
  SELECT c.product_supplier AS subject, count(DISTINCT c.project_id) AS coop_times,
         sum(c.total_price) AS amount
  FROM project p JOIN cob c ON c.project_id = p.project_id
  WHERE p.purchaser = :'purchaser' AND c.product_supplier IS NOT NULL
  GROUP BY c.product_supplier
)
SELECT '中标供应商' AS role, subject, coop_times, amount FROM win
UNION ALL
SELECT '产品供应商' AS role, subject, coop_times, amount FROM ps
ORDER BY coop_times DESC;

-- ---------------------------------------------------------------------
-- S2a. 指定采购单位 → 高频（TOP5）参与投标的主体
-- ---------------------------------------------------------------------
SELECT s.name AS subject, count(*) AS bid_times,
       count(*) FILTER (WHERE b.is_winner) AS win_times
FROM project p
JOIN bid b      ON b.project_id = p.project_id
JOIN supplier s ON s.supplier_id = b.supplier_id
WHERE p.purchaser = :'purchaser'
GROUP BY s.name
ORDER BY bid_times DESC
LIMIT 5;

-- S2b. 同项目的两两协同投标组合（高频组合）
SELECT a.name AS subject_a, b.name AS subject_b, count(*) AS together_times
FROM bid x
JOIN bid y      ON y.project_id = x.project_id AND y.supplier_id > x.supplier_id
JOIN project p  ON p.project_id = x.project_id
JOIN supplier a ON a.supplier_id = x.supplier_id
JOIN supplier b ON b.supplier_id = y.supplier_id
WHERE p.purchaser = :'purchaser'
GROUP BY a.name, b.name
ORDER BY together_times DESC
LIMIT 5;

-- ---------------------------------------------------------------------
-- S3. 指定中标供应商 → 高频共同竞标主体（TOP5）及全部参与方
-- ---------------------------------------------------------------------
WITH my_projects AS (
  SELECT b.project_id
  FROM bid b JOIN supplier s ON s.supplier_id = b.supplier_id
  WHERE s.name = :'winner' AND b.is_winner
)
SELECT s.name AS subject, count(*) AS co_bid_times,
       sum(p.package_total_amount) AS amount
FROM bid b
JOIN my_projects mp ON mp.project_id = b.project_id
JOIN supplier s     ON s.supplier_id = b.supplier_id
JOIN project p      ON p.project_id = b.project_id
WHERE s.name <> :'winner'
GROUP BY s.name
ORDER BY co_bid_times DESC
LIMIT 5;

-- ---------------------------------------------------------------------
-- S4. 指定多个中标供应商 → 与它们均存在合作关系的采购单位
--     合作 = 在这些采购单位有中标记录；:'suppliers' 形如 '甲公司,乙公司'
-- ---------------------------------------------------------------------
WITH subj AS (
  SELECT supplier_id FROM supplier
  WHERE name = ANY (string_to_array(:'suppliers', ','))
), per AS (
  SELECT p.purchaser, b.supplier_id, count(*) AS times,
         sum(p.package_total_amount) AS amount
  FROM project p
  JOIN bid b ON b.project_id = p.project_id AND b.is_winner
  WHERE b.supplier_id IN (SELECT supplier_id FROM subj)
  GROUP BY p.purchaser, b.supplier_id
)
SELECT purchaser,
       count(*)     AS subject_count,      -- 等于给定主体数即"均有合作"
       sum(times)   AS coop_times,
       sum(amount)  AS coop_amount
FROM per
GROUP BY purchaser
HAVING count(*) = (SELECT count(*) FROM subj)
ORDER BY coop_times DESC;

-- ---------------------------------------------------------------------
-- S5. 指定多个中标供应商 → 共同竞标的项目与竞标结果
-- ---------------------------------------------------------------------
WITH subj AS (
  SELECT supplier_id FROM supplier
  WHERE name = ANY (string_to_array(:'suppliers', ','))
)
SELECT p.project_name, p.package_no, p.purchaser, p.package_total_amount,
       count(DISTINCT b.supplier_id) AS subject_count,
       string_agg(DISTINCT s.name, '、') FILTER (WHERE b.is_winner) AS winners
FROM project p
JOIN bid b      ON b.project_id = p.project_id
JOIN supplier s ON s.supplier_id = b.supplier_id
WHERE b.supplier_id IN (SELECT supplier_id FROM subj)
GROUP BY p.project_id, p.project_name, p.package_no, p.purchaser, p.package_total_amount
HAVING count(DISTINCT b.supplier_id) = (SELECT count(*) FROM subj)
ORDER BY p.package_total_amount DESC NULLS LAST;

-- ---------------------------------------------------------------------
-- 星图总览：节点与边的原始数据（供 /api/graph/overview）
-- ---------------------------------------------------------------------
-- 节点：采购单位（按项目数加权）
SELECT purchaser AS id, purchaser AS label, count(*) AS weight
FROM project WHERE purchaser IS NOT NULL GROUP BY purchaser;

-- 节点：项目（按包金额加权）
SELECT project_id AS id, project_name || ' · 包' || package_no AS label,
       coalesce(package_total_amount, 0) AS weight
FROM project;

-- 边：项目 → 中标供应商
SELECT p.project_id AS a, s.name AS b, 'win' AS role, 1 AS weight
FROM project p JOIN bid b ON b.project_id = p.project_id AND b.is_winner
JOIN supplier s ON s.supplier_id = b.supplier_id;

-- 边：项目 → 投标参与方
SELECT p.project_id AS a, s.name AS b, 'bid' AS role, 1 AS weight
FROM project p JOIN bid b ON b.project_id = p.project_id AND NOT b.is_winner
JOIN supplier s ON s.supplier_id = b.supplier_id;

-- ---------------------------------------------------------------------
-- 全局分布统计（供 /api/stats/distribution）
-- ---------------------------------------------------------------------
SELECT category_type, count(*) AS cob_count, sum(total_price) AS amount
FROM cob GROUP BY category_type ORDER BY amount DESC NULLS LAST;

SELECT brand, count(*) AS cob_count, sum(total_price) AS amount
FROM cob WHERE brand IS NOT NULL
GROUP BY brand ORDER BY cob_count DESC LIMIT 20;
