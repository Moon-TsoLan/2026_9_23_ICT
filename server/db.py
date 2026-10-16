"""数据库连接池与 .env 加载。

业务只读查询统一添加账号作用域；账号/会话读写走独立raw_q入口。
连接池使用dict_row。
换库只改 .env 里的 DATABASE_URL，本文件不用动。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

# 主机端口是 15432，不是 5432：5432 落在本机的 Windows 动态保留区间里，绑不上。
# 客户端要覆盖时改 .env 的 DATABASE_URL 即可，这里只是兜底默认值。
DSN = os.environ.get("DATABASE_URL", "postgresql://ict:ict_dev_pw@localhost:15432/ict_demo")

pool = ConnectionPool(
    DSN,
    min_size=1,
    max_size=8,
    kwargs={"autocommit": True, "row_factory": dict_row},
)


def raw_q(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    """Account/session storage only. Business endpoints use the scoped q below."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def q(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    from .tenant import scoped_read
    statement, scoped_params = scoped_read(sql, params)
    return raw_q(statement, scoped_params)


def q1(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def num(x: Any) -> float | None:
    """numeric/Decimal → float，None 透传。"""
    return float(x) if x is not None else None


def yuan(x: Any) -> str:
    """金额（元）→ 中文短写法，用于叙事面板的指标卡。"""
    if x is None:
        return "—"
    from ict.official import money_wan
    return f"{money_wan(x):,.2f} 万元"
