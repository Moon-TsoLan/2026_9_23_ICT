-- =====================================================================
-- ict · 迁移：announcement 加 tenant_id（账号/租户维度）
--
-- 现在系统是单租户，值恒为 'default'；这一列是为了将来加登录后能按账号隔离，
-- 而**不是**等有登录了再补——历史数据的事后归属无法判定，只能全算 default。
--
-- 幂等，可重复执行。对已存在的库：
--   Get-Content db\003_announcement_tenant.sql -Raw | docker exec -i ict-pg psql -U ict -d <库名> -v ON_ERROR_STOP=1
-- =====================================================================

ALTER TABLE announcement ADD COLUMN IF NOT EXISTS tenant_id text;
UPDATE announcement SET tenant_id = 'default' WHERE tenant_id IS NULL;
ALTER TABLE announcement ALTER COLUMN tenant_id SET DEFAULT 'default';
ALTER TABLE announcement ALTER COLUMN tenant_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_announcement_tenant ON announcement (tenant_id);
