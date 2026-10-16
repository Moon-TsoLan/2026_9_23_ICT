"""Small username/password login with opaque, revocable server-side sessions."""
import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from psycopg.errors import UniqueViolation
from starlette.concurrency import run_in_threadpool
from urllib.parse import urlsplit

from .db import raw_q
from .tenant import TENANT, validate_tenant

COOKIE = "ict_session"
SESSION_DAYS = 7
ITERATIONS = 600_000
router = APIRouter(prefix="/api/auth")


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        kind, count, salt, expected = stored.split("$")
        if kind != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(count))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def username_key(username):
    name = username.strip()
    if not re.fullmatch(r"[\w.-]{3,32}", name):
        raise HTTPException(422, "用户名需为3至32位汉字、字母、数字、下划线、点或短横线")
    return name, name.casefold()


def public_account(row):
    return {"account_id": row["account_id"], "username": row["username"], "tenant_id": row["tenant_id"]}


def create_account(username, password, tenant=None):
    name, key = username_key(username)
    if not 6 <= len(password) <= 128:
        raise HTTPException(422, "密码长度为6至128位")
    aid = uuid.uuid4().hex
    tenant = validate_tenant(tenant or aid)
    try:
        return raw_q("""INSERT INTO app_account
            (account_id,username,username_key,password_hash,tenant_id) VALUES (%s,%s,%s,%s,%s)
            RETURNING account_id,username,tenant_id""", (aid, name, key, hash_password(password), tenant))[0]
    except UniqueViolation:
        raise HTTPException(409, "用户名或数据空间已被使用") from None


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def account_for_session(token):
    if not token or len(token) > 256:
        return None
    rows = raw_q("""SELECT a.account_id,a.username,a.tenant_id FROM app_session s
        JOIN app_account a USING(account_id) WHERE s.token_hash=%s AND s.expires_at>now()""",
        (token_hash(token),))
    return rows[0] if rows else None


def session_cookie(response, account, old_token=None):
    if old_token:
        raw_q("DELETE FROM app_session WHERE token_hash=%s RETURNING token_hash", (token_hash(old_token),))
    raw_q("DELETE FROM app_session WHERE expires_at<=now() RETURNING token_hash")
    token = secrets.token_urlsafe(32)
    raw_q("INSERT INTO app_session(token_hash,account_id,expires_at) VALUES (%s,%s,%s) RETURNING token_hash",
          (token_hash(token), account["account_id"], datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        secure=os.environ.get("ICT_COOKIE_SECURE", "0") == "1", samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)


@router.post("/register")
def register(body: Credentials, response: Response, request: Request):
    # A web registration can never claim the pre-existing default data space.
    account = create_account(body.username, body.password)
    session_cookie(response, account, request.cookies.get(COOKIE))
    return {"user": public_account(account)}


@router.post("/login")
def login(body: Credentials, response: Response, request: Request):
    _, key = username_key(body.username)
    rows = raw_q("SELECT * FROM app_account WHERE username_key=%s", (key,))
    account = rows[0] if rows else None
    if not account:
        # Keep a missing user on the same slow hash path.
        hashlib.pbkdf2_hmac("sha256", body.password.encode(), b"ict-missing-user", ITERATIONS)
        raise HTTPException(401, "用户名或密码错误")
    if not verify_password(body.password, account["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    session_cookie(response, account, request.cookies.get(COOKIE))
    return {"user": public_account(account)}


@router.get("/me")
def me(request: Request):
    return {"user": public_account(request.state.account)}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        raw_q("DELETE FROM app_session WHERE token_hash=%s RETURNING token_hash", (token_hash(token),))
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
    return {"ok": True}


def install_auth(app):
    app.include_router(router)

    @app.middleware("http")
    async def authenticate(request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请从本站提交请求"}, status_code=403)
        public = request.url.path in {"/api/auth/login", "/api/auth/register", "/api/auth/logout", "/api/ping"}
        account = await run_in_threadpool(account_for_session, request.cookies.get(COOKIE))
        if not account and not public:
            return JSONResponse({"detail": "请先登录"}, status_code=401, headers={"Cache-Control": "no-store"})
        request.state.account = account
        token = TENANT.set(account["tenant_id"] if account else None)
        try:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            TENANT.reset(token)
