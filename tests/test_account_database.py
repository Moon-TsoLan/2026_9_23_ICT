"""Real PostgreSQL integration, opt in using a dedicated ict_account_check_* database.

No model/OCR calls. Apply 001 + 002 first. Never point this at business data.
"""
import json
import os
from io import BytesIO
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("ICT_TEST_DATABASE_URL"), reason="独立账号测试库未指定")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool
    from psycopg.rows import dict_row
    from server import db, auth, ingest
    from server.app import app
    from ict import config
    from db.load_runs import load_batch
    patch = pytest.MonkeyPatch()
    test_pool = ConnectionPool(os.environ["ICT_TEST_DATABASE_URL"], kwargs={"autocommit": True, "row_factory": dict_row})
    with test_pool.connection() as conn:
        assert conn.execute("SELECT current_database() AS name").fetchone()["name"].startswith("ict_account_check_")
    patch.setattr(db, "pool", test_pool)
    root = tmp_path_factory.mktemp("account-runtime")
    patch.setattr(config, "TENANTS_ROOT", root / "tenants")
    patch.setattr(config, "JOBS_ROOT", root / "jobs")
    patch.setattr(ingest, "disk_error", lambda path: None)
    from uuid import uuid4
    suffix = uuid4().hex[:8]
    clients, users, projects, cob_ids = {}, {}, {}, {}
    for letter in ["甲", "乙"]:
        client = TestClient(app)
        reply = client.post("/api/auth/register", json={"username": f"用户{letter}{suffix}", "password": "test-password", "tenant_id": "default"})
        assert reply.status_code == 200, reply.text
        user = reply.json()["user"]
        assert user["tenant_id"] != "default"
        clients[letter], users[letter] = client, user
        run = config.tenant_paths(user["tenant_id"]).runs / "same_notice"
        run.mkdir(parents=True)
        package = {"project_id": "same-original", "project_name": f"{letter}专属项目", "source_project_no": "SAME-PROC",
                   "package_no": "1", "package_kind": "contract_package", "source_package_no": "1", "package_name": "第一包", "purchaser": "共同采购单位",
                   "package_total_amount": 10000, "cobs": [
                       {"object_name": f"{letter}专属标的", "brand": f"{letter}专属品牌", "quantity": 1, "total_price": 10000},
                       {"object_name": f"{letter}供货标的", "product_supplier": f"{letter}供应公司", "total_price": 1000}],
                   "subs": [{"supplier_name": "共同公司", "is_winner": True}, {"supplier_name": f"{letter}独有公司", "is_winner": True}]}
        (run / "09_merged_projects.json").write_text(json.dumps({"projects": [package]}, ensure_ascii=False), encoding="utf-8")
        (run / "01_announcement_understanding.json").write_text(json.dumps({"announcement_id": "same_notice", "project_name": f"{letter}专属项目"}), encoding="utf-8")
        (run / "run_state.json").write_text(json.dumps({"status": letter}), encoding="utf-8")
        with test_pool.connection() as conn, conn.transaction():
            with conn.cursor(row_factory=None) as cur:
                # psycopg's default rows are tuples; load_runs uses positional supplier_id.
                from psycopg.rows import tuple_row
                cur.row_factory = tuple_row
                load_batch(cur, [run], user["tenant_id"])
        projects[letter] = db.raw_q("SELECT project_id FROM project WHERE tenant_id=%s", (user["tenant_id"],))[0]["project_id"]
        cob_ids[letter] = db.raw_q("SELECT cob_id FROM cob WHERE project_id=%s ORDER BY cob_id", (projects[letter],))[0]["cob_id"]
    try:
        yield {"clients": clients, "users": users, "projects": projects, "cob_ids": cob_ids, "pool": test_pool, "root": root}
    finally:
        for client in clients.values():
            client.close()
        patch.undo()
        test_pool.close()


def test_same_announcement_and_supplier_do_not_collide(env):
    from server import db
    tenants = [u["tenant_id"] for u in env["users"].values()]
    assert len(db.raw_q("SELECT * FROM announcement WHERE announcement_id='same_notice' AND tenant_id=ANY(%s)", (tenants,))) == 2
    assert len(db.raw_q("SELECT * FROM supplier WHERE name='共同公司' AND tenant_id=ANY(%s)", (tenants,))) == 2
    assert env["projects"]["甲"] != env["projects"]["乙"]


def test_unauthenticated_requests_and_csrf_blocked(env):
    from fastapi.testclient import TestClient
    from server.app import app
    with TestClient(app) as client:
        for path in ["/api/auth/me", "/api/objects/search", "/api/objects/export", "/api/health", "/api/graph/overview"]:
            assert client.get(path).status_code == 401
        assert client.get('/api/ping').json() == {"ok": True}
        assert client.post('/api/auth/register', json={"username": "evil", "password": "123456"},
                           headers={"Origin": "http://elsewhere.example"}).status_code == 403


def test_all_read_surfaces_only_current_account(env):
    for letter, other in [("甲", "乙"), ("乙", "甲")]:
        client = env["clients"][letter]
        for path in ["/api/objects/search", "/api/announcements", "/api/ingest/records", "/api/stats/distribution",
                     "/api/graph/overview", "/api/parties?q=公司", "/api/parties/buyer:" + quote("共同采购单位"),
                     "/api/parties/sup:" + quote("共同公司")]:
            reply = client.get(path)
            assert reply.status_code == 200, (path, reply.text)
            assert f"{other}专属" not in reply.text and f"{other}独有" not in reply.text
        rows = client.get('/api/objects/search').json()
        assert rows['total'] == 2
        assert client.get('/api/health').json()['announcements'] == 1
        assert client.get('/api/runs/same_notice').json()['status'] == letter
        assert client.get(f'/api/objects/{env["cob_ids"][other]}').status_code == 404
        assert client.get('/api/parties/' + quote('proj:' + env['projects'][other], safe='')).status_code == 404


def test_graph_neighborhood_and_all_scenes_scoped(env):
    for letter, other in [("甲", "乙"), ("乙", "甲")]:
        client = env['clients'][letter]
        version = client.get('/api/health').json()['data_version']
        for subject in ['sup:共同公司', 'buyer:共同采购单位', 'brand:' + letter + '专属品牌', 'vendor:' + letter + '供应公司', 'proj:' + env['projects'][letter]]:
            reply = client.get('/api/graph/neighborhood/' + quote(subject, safe=''), params={'limit': 1, 'expected_version': version})
            assert reply.status_code == 200, reply.text
            assert f'{other}专属' not in reply.text
        assert client.get('/api/graph/neighborhood/' + quote('proj:' + env['projects'][other], safe='')).status_code == 404
        for scene in ['S1', 'S2', 'S3', 'S4', 'S5']:
            subjects = ['共同采购单位'] if scene in ['S1', 'S2'] else ['共同公司']
            if scene in ['S4', 'S5']: subjects.append(letter + '独有公司')
            reply = client.post('/api/scenes/' + scene + '/query', json={'subjects': subjects, 'expected_version': version})
            assert reply.status_code == 200, (scene, reply.text)
            assert f'{other}专属' not in reply.text and f'{other}独有' not in reply.text
        other_version = env['clients'][other].get('/api/health').json()['data_version']
        assert client.get('/api/graph/neighborhood/' + quote('sup:共同公司'), params={'expected_version': other_version}).status_code == 409


def test_exports_and_jobs_are_account_scoped(env):
    from openpyxl import load_workbook
    alice, bob = env['clients']['甲'], env['clients']['乙']
    for path in ['/api/objects/export', '/api/announcements/same_notice/export']:
        reply = alice.get(path)
        assert reply.status_code == 200
        workbook = load_workbook(BytesIO(reply.content))
        values = str([[row for row in sheet.values] for sheet in workbook])
        assert '甲专属' in values and '乙专属' not in values
        assert 'same_notice' in values
    reply = alice.post('/api/ingest/jobs', json={'items': [{'announcement_id': 'fresh_notice', 'has_zip': False}], 'tenant_id': env['users']['乙']['tenant_id']})
    assert reply.status_code == 200, reply.text
    job = reply.json(); assert job['tenant_id'] == env['users']['甲']['tenant_id']
    path = '/api/ingest/jobs/' + job['job_id']
    for method in ['get', 'delete']:
        assert getattr(bob, method)(path).status_code == 404
    assert bob.post(path + '/start').status_code == 404
    assert bob.post(path + '/files', files={'file': ('fresh_notice.html', b'html', 'text/html')}).status_code == 404
    assert alice.post(path + '/files', files={'file': ('fresh_notice.html', b'html', 'text/html')}).status_code == 200
    assert alice.delete(path).status_code == 200


def test_overwrite_and_round_reorder_do_not_change_other_account(env):
    from server import db
    from db.load_runs import load_batch
    before = db.raw_q("SELECT * FROM announcement WHERE tenant_id=%s", (env['users']['乙']['tenant_id'],))
    run = env['root'] / 'tenants' / env['users']['甲']['tenant_id'] / 'runs' / 'same_notice'
    with env['pool'].connection() as conn, conn.transaction():
        from psycopg.rows import tuple_row
        cur = conn.cursor(row_factory=tuple_row)
        load_batch(cur, [run], env['users']['甲']['tenant_id'])
        cur.execute('SAVEPOINT account_round_trial')
        earlier = run.parent / 't20260101_earlier'
        earlier.mkdir(exist_ok=True)
        (earlier / '09_merged_projects.json').write_bytes((run / '09_merged_projects.json').read_bytes())
        (earlier / '01_announcement_understanding.json').write_text(
            json.dumps({'announcement_id': earlier.name, 'project_name': '甲专属早期项目'}), encoding='utf-8')
        info = load_batch(cur, [earlier], env['users']['甲']['tenant_id'])
        assert len(info['plan'].renames) == 1
        cur.execute('SELECT round_no FROM project WHERE tenant_id=%s AND announcement_id=%s',
                    (env['users']['甲']['tenant_id'], 'same_notice'))
        assert cur.fetchone()[0] == 2
        cur.execute('SELECT project_id,round_no FROM project WHERE tenant_id=%s', (env['users']['乙']['tenant_id'],))
        assert cur.fetchall() == [(env['projects']['乙'], 1)]
        cur.execute('ROLLBACK TO SAVEPOINT account_round_trial')
    assert before == db.raw_q("SELECT * FROM announcement WHERE tenant_id=%s", (env['users']['乙']['tenant_id'],))
    assert env['clients']['乙'].get('/api/objects/search').json()['total'] == 2


def test_sessions_login_logout_and_duplicates(env):
    from server import db
    client = env['clients']['甲']
    name = env['users']['甲']['username']
    assert client.post('/api/auth/register', json={'username': name, 'password': '123456'}).status_code == 409
    assert client.post('/api/auth/login', json={'username': name, 'password': 'wrong-password'}).status_code == 401
    logged = client.post('/api/auth/login', json={'username': name, 'password': 'test-password'})
    assert logged.status_code == 200
    assert 'HttpOnly' in logged.headers['set-cookie'] and 'SameSite=lax' in logged.headers['set-cookie']
    cookie = client.cookies.get('ict_session')
    assert db.raw_q('SELECT token_hash FROM app_session WHERE token_hash=%s', (cookie,)) == []
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/objects/search').status_code == 401
    assert client.post('/api/auth/login', json={'username': name, 'password': 'test-password'}).status_code == 200


def test_session_expiry_requires_login(env):
    from server import auth, db
    client = env['clients']['乙']
    hashed = auth.token_hash(client.cookies.get('ict_session'))
    db.raw_q("UPDATE app_session SET expires_at=now()-interval '1 second' WHERE token_hash=%s RETURNING token_hash", (hashed,))
    assert client.get('/api/health').status_code == 401
    assert client.get('/api/auth/me').status_code == 401
    assert client.post('/api/auth/login', json={'username': env['users']['乙']['username'], 'password': 'test-password'}).status_code == 200


def test_registered_account_can_bind_legacy_only_as_operator(env):
    from server import auth, db
    from server.manage_accounts import bind_legacy
    from fastapi.testclient import TestClient
    from server.app import app
    from uuid import uuid4
    existing = db.raw_q("SELECT username FROM app_account WHERE tenant_id='default'")
    if not existing:
        with TestClient(app) as client:
            name = 'legacy_' + uuid4().hex[:8]
            assert client.post('/api/auth/register', json={'username': name, 'password': 'test-password'}).status_code == 200
            assert client.get('/api/objects/search').json()['total'] == 0
            bind_legacy(name)
            assert client.get('/api/auth/me').status_code == 401
    else:
        name = existing[0]['username']
    with TestClient(app) as client:
        assert client.post('/api/auth/login', json={'username': name, 'password': 'test-password'}).status_code == 200
        assert client.get('/api/objects/search').json()['items'][0]['object_name'] == '历史标的'
        assert client.get('/api/health').json()['announcements'] == 1
    with pytest.raises(ValueError):
        bind_legacy(env['users']['甲']['username'])


def test_concurrent_accounts_keep_independent_context(env):
    def read(letter):
        reply = env['clients'][letter].get('/api/objects/search')
        assert reply.status_code == 200
        assert all(item['object_name'].startswith(letter) for item in reply.json()['items'])
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(read, ['甲', '乙'] * 6))
