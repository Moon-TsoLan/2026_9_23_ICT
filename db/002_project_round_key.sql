-- =====================================================================
-- ict · 迁移：project 表改用「项目编号 + 包号 + 轮次」
--
-- 用途：把**已存在**的库升级到 db/001_schema.sql 的新结构（幂等，可重复执行）。
-- 新建的库不需要本文件——001_schema.sql 里已经是新结构。
--
-- 用法（容器内看不到 db/，要从宿主机喂进去；三个库分别执行，不要合并）：
--   Get-Content db\002_project_round_key.sql -Raw | docker exec -i ict-pg psql -U ict -d <库名> -v ON_ERROR_STOP=1
--
-- 本文件只补列、回填、换唯一键，**不改写历史行的 project_id**（改主键会触碰 cob/bid 外键）。
-- 所以迁移后历史行的 id 仍是旧的「项目名|包号」，而 package_key 是新的「项目编号|包号」；
-- 两者都自洽，查询与 API 只按 project_id 取数，不受影响。若之后用
-- `load_runs.py --reset` 重灌（有 work/runs 的库），id 会自动收敛到新格式。
--
-- 已存在的同 (package_key) 多行会按公告号顺序拿到 round_no = 1,2,3…，因此本文件
-- 不会因为重复而失败，也不需要先清空。
-- =====================================================================

ALTER TABLE project ADD COLUMN IF NOT EXISTS package_key       text;
ALTER TABLE project ADD COLUMN IF NOT EXISTS round_no          integer;
ALTER TABLE project ADD COLUMN IF NOT EXISTS source_project_no text;

-- 回填：优先用项目编号；模型没抽到编号时退回项目名（与 load_runs.py 的兜底一致）
UPDATE project
   SET package_key = coalesce(source_project_no, project_name) || '|' || package_no
 WHERE package_key IS NULL;

-- 轮次：同一个 package_key 按公告号排序（`tYYYYMMDD_…` 的字典序≈时间序）
WITH ranked AS (
  SELECT project_id,
         row_number() OVER (PARTITION BY package_key ORDER BY announcement_id, project_id) AS rn
  FROM project
)
UPDATE project p
   SET round_no = r.rn
  FROM ranked r
 WHERE p.project_id = r.project_id
   AND p.round_no IS NULL;

ALTER TABLE project ALTER COLUMN package_key SET NOT NULL;
ALTER TABLE project ALTER COLUMN round_no SET DEFAULT 1;
ALTER TABLE project ALTER COLUMN round_no SET NOT NULL;

-- 旧约束（项目名+包号）被 package_key 取代
ALTER TABLE project DROP CONSTRAINT IF EXISTS project_announcement_id_project_name_package_no_key;
ALTER TABLE project DROP CONSTRAINT IF EXISTS project_package_key_round_no_key;
ALTER TABLE project ADD CONSTRAINT project_package_key_round_no_key UNIQUE (package_key, round_no);

CREATE INDEX IF NOT EXISTS idx_project_package_key ON project (package_key);
