"""Request account scope. Worker/CLI pass tenant IDs explicitly, never from request JSON."""
from contextvars import ContextVar
from contextlib import contextmanager
import re

TENANT = ContextVar("ict_tenant", default=None)


def current_tenant():
    return TENANT.get() or "default"


def validate_tenant(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("无效账号数据空间")
    return value


@contextmanager
def tenant_scope(value):
    token = TENANT.set(validate_tenant(value))
    try:
        yield
    finally:
        TENANT.reset(token)


def scoped_read(sql, params=()):
    """Scope every business read, including nested subqueries and legacy SQL views.

    SQL is server-owned; clients never supply SQL or a tenant. Qualified public tables
    occur only inside this boundary. CTEs also scope supplier registries and exports.
    """
    tenant = TENANT.get()
    if tenant is None:
        raise RuntimeError("业务查询缺少登录账号作用域")
    scope = """
        announcement AS (SELECT * FROM public.announcement WHERE tenant_id=%s),
        project AS (SELECT * FROM public.project WHERE tenant_id=%s),
        supplier AS (SELECT * FROM public.supplier WHERE tenant_id=%s),
        cob AS (SELECT c.* FROM public.cob c JOIN project p USING(project_id)),
        bid AS (SELECT b.* FROM public.bid b JOIN project p USING(project_id)
                JOIN supplier s USING(supplier_id)),
        winner_coop_supplier AS (
            SELECT w.* FROM public.winner_coop_supplier w JOIN project p USING(project_id)
            JOIN supplier s USING(supplier_id)),
        v_cob_record AS (SELECT v.* FROM public.v_cob_record v JOIN project p USING(project_id))
    """
    statement = sql.lstrip()
    if re.match(r"WITH\s", statement, re.IGNORECASE):
        statement = "WITH " + scope + ", " + statement[4:]
    else:
        # The optional leading comment belongs to our fixed exploration query labels.
        start = re.sub(r"\A(?:/\*.*?\*/\s*)*", "", statement, flags=re.DOTALL)
        if not re.match(r"SELECT\s", start, re.IGNORECASE):
            raise ValueError("业务查询入口只允许只读SQL")
        statement = "WITH " + scope + " " + statement
    return statement, (tenant, tenant, tenant, *params)
