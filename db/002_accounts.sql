-- Run once before starting the account-enabled API/worker. Safe to re-run.
-- Existing announcement IDs, project IDs, amounts and raw_json stay unchanged.
BEGIN;
SELECT pg_advisory_xact_lock(20261015, 2);

CREATE TABLE IF NOT EXISTS app_account (
    account_id text PRIMARY KEY,
    username text NOT NULL,
    username_key text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    tenant_id text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_session (
    token_hash text PRIMARY KEY,
    account_id text NOT NULL REFERENCES app_account(account_id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_session_expiry ON app_session(expires_at);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='project' AND column_name='tenant_id') THEN
        ALTER TABLE project ADD COLUMN tenant_id text NOT NULL DEFAULT 'default';
        UPDATE project p SET tenant_id=a.tenant_id FROM announcement a
         WHERE p.announcement_id=a.announcement_id;
        ALTER TABLE project DROP CONSTRAINT project_announcement_id_fkey;
        ALTER TABLE announcement DROP CONSTRAINT announcement_pkey;
        ALTER TABLE announcement ADD PRIMARY KEY (tenant_id, announcement_id);
        ALTER TABLE project ADD CONSTRAINT project_announcement_tenant_fkey
            FOREIGN KEY (tenant_id, announcement_id)
            REFERENCES announcement(tenant_id, announcement_id) ON DELETE CASCADE;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='supplier' AND column_name='tenant_id') THEN
        ALTER TABLE supplier ADD COLUMN tenant_id text NOT NULL DEFAULT 'default';
        ALTER TABLE supplier DROP CONSTRAINT supplier_norm_name_key;
        ALTER TABLE supplier ADD UNIQUE (tenant_id, norm_name);
        -- Normally all old data belongs to default. Preserve any pre-existing tenants too.
        INSERT INTO supplier (name, norm_name, tenant_id)
            SELECT DISTINCT s.name,s.norm_name,p.tenant_id FROM supplier s
            JOIN bid b USING(supplier_id) JOIN project p USING(project_id)
            WHERE p.tenant_id <> 'default' ON CONFLICT (tenant_id,norm_name) DO NOTHING;
        UPDATE winner_coop_supplier w SET supplier_id=n.supplier_id
            FROM project p, supplier old, supplier n
            WHERE w.project_id=p.project_id AND w.supplier_id=old.supplier_id
              AND n.tenant_id=p.tenant_id AND n.norm_name=old.norm_name
              AND old.tenant_id<>p.tenant_id;
        UPDATE bid b SET supplier_id=n.supplier_id
            FROM project p, supplier old, supplier n
            WHERE b.project_id=p.project_id AND b.supplier_id=old.supplier_id
              AND n.tenant_id=p.tenant_id AND n.norm_name=old.norm_name
              AND old.tenant_id<>p.tenant_id;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_project_tenant ON project(tenant_id);
COMMIT;
