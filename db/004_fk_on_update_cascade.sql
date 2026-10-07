-- =====================================================================
-- ict · 迁移：三个子表的外键加上 ON UPDATE CASCADE
--
-- 为什么需要：同一 (项目编号, 包号) 被多则公告复用时，轮次按公告日期排；后到的
-- 更早公告会把已有行往后挤一位，这要改 project.project_id。子表（cob / bid /
-- winner_coop_supplier）的外键如果不带 ON UPDATE CASCADE，改主键会被直接挡住。
--
-- 幂等，可重复执行。对已存在的库：
--   Get-Content db\004_fk_on_update_cascade.sql -Raw | docker exec -i ict-pg psql -U ict -d <库名> -v ON_ERROR_STOP=1
-- =====================================================================

ALTER TABLE cob DROP CONSTRAINT IF EXISTS cob_project_id_fkey;
ALTER TABLE cob ADD  CONSTRAINT cob_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE bid DROP CONSTRAINT IF EXISTS bid_project_id_fkey;
ALTER TABLE bid ADD  CONSTRAINT bid_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE winner_coop_supplier DROP CONSTRAINT IF EXISTS winner_coop_supplier_project_id_fkey;
ALTER TABLE winner_coop_supplier ADD  CONSTRAINT winner_coop_supplier_project_id_fkey
  FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE ON UPDATE CASCADE;
