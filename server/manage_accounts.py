"""Server operator tools. Never expose legacy-data claiming as a public API."""
import argparse
import getpass

from .auth import create_account, hash_password, username_key
from . import db


def bind_legacy(username):
    """Bind an empty web-registered account to default; never move or merge data."""
    from ict.config import tenant_paths
    _, key = username_key(username)
    with db.pool.connection() as conn, conn.transaction():
        account = conn.execute("SELECT * FROM app_account WHERE username_key=%s FOR UPDATE", (key,)).fetchone()
        if not account:
            raise ValueError("账号不存在")
        if account["tenant_id"] == "default":
            return
        occupied = conn.execute("SELECT 1 FROM app_account WHERE tenant_id='default'").fetchone()
        if occupied:
            raise ValueError("历史数据已绑定其他账号")
        tenant = account["tenant_id"]
        exists = conn.execute("SELECT 1 FROM announcement WHERE tenant_id=%s LIMIT 1", (tenant,)).fetchone()
        paths = tenant_paths(tenant)
        if exists or any(p.exists() and any(p.rglob("*")) for p in [paths.jobs, paths.incoming, paths.runs, paths.attachments]):
            raise ValueError("账号已有数据或任务，不能自动合并历史数据")
        conn.execute("UPDATE app_account SET tenant_id='default' WHERE account_id=%s", (account["account_id"],))
        conn.execute("DELETE FROM app_session WHERE account_id=%s", (account["account_id"],))


def main():
    parser = argparse.ArgumentParser(description="创建赛题账号或重置密码；密码交互输入")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--username", required=True)
    create.add_argument("--legacy-default", action="store_true", help="绑定现有default数据与任务目录")
    reset = sub.add_parser("reset-password")
    reset.add_argument("--username", required=True)
    bind = sub.add_parser("bind-legacy")
    bind.add_argument("--username", required=True)
    args = parser.parse_args()
    if args.action == "bind-legacy":
        bind_legacy(args.username)
        print("账号已绑定default历史数据，请重新登录")
        return
    password = getpass.getpass("密码（6至128位）: ")
    if password != getpass.getpass("再次输入密码: ") or not 6 <= len(password) <= 128:
        raise SystemExit("两次密码需一致，且长度为6至128位")
    if args.action == "create":
        user = create_account(args.username, password, "default" if args.legacy_default else None)
        print("账号已创建，数据空间：" + user["tenant_id"])
    else:
        _, key = username_key(args.username)
        with db.pool.connection() as conn, conn.transaction():
            rows = conn.execute("UPDATE app_account SET password_hash=%s WHERE username_key=%s RETURNING account_id",
                                (hash_password(password), key)).fetchall()
            if not rows:
                raise SystemExit("账号不存在")
            conn.execute("DELETE FROM app_session WHERE account_id=%s", (rows[0]["account_id"],))
        print("密码已重置，原登录会话已失效")


if __name__ == "__main__":
    main()
