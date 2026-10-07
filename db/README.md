# 数据库（PostgreSQL · Docker）

演示库与生产库**结构完全相同**，区别只在库名与数据。换库 = 改 `.env` 里的 `DATABASE_URL` / `PGDATABASE`，代码与查询语句都不用动。

## 现有数据库

| 库名 | 内容 | 用途 |
|---|---|---|
| `ict_demo` | 110 则公告（演示数据） | 离线演示 |
| `ict_batch20261004` | 40 则（2026-10-04 那批，万元修复前） | 旧批次 |
| `ict_report20261006` | **10 则真实抽取结果**（含建表 + 数据 + 序列归位） | 阶段性汇报 |

切库只改 `.env` 的 `DATABASE_URL` 与 `PGDATABASE`，或者用进程级变量临时覆盖：

```powershell
$env:DATABASE_URL = "postgresql://ict:ict_dev_pw@localhost:15432/ict_report20261006"
D:\python\python.exe -m uvicorn server.app:app --port 8000
```

导入一份自包含的 `.sql`（自带建表）时，先建空库再灌，不要灌进已有库（文件里没有 `DROP`，会主键冲突）：

```powershell
docker exec ict-pg psql -U ict -d postgres -c "CREATE DATABASE <新库> OWNER ict ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'"
docker cp <本机.sql> ict-pg:/tmp/seed.sql
docker exec ict-pg psql -U ict -d <新库> -v ON_ERROR_STOP=1 -q -f /tmp/seed.sql
docker exec ict-pg rm -f /tmp/seed.sql
```

## 0. 前置：安装 Docker

本机当前**未安装 Docker**。Windows 上安装：

```powershell
winget install Docker.DockerDesktop
```

装完启动 Docker Desktop（首次启动会要求启用 WSL2，按提示确认）。验证：

```bash
docker --version
docker compose version
```

## 1. 启动 / 停止

```bash
docker compose up -d          # 启动（首次会自动执行 db/001_schema.sql 建表）
docker compose ps             # 看状态，db 应为 healthy
docker compose logs -f db     # 看日志
docker compose down           # 停止（保留数据）
docker compose down -v        # 停止并删除数据卷（清库重建，谨慎）
```

## 2. 验证连接

`.env` 里已写好 `PGHOST/PGPORT/PGUSER/PGDATABASE`，可直接无参连接：

```bash
docker compose exec -T db psql -U ict -d ict_demo -c "\dt"      # 列出自建的表
docker compose exec -T db psql -U ict -d ict_demo -c "\dv"      # 列出视图
docker compose exec -T db psql -U ict -d ict_demo -c "\d cob"   # 看某张表结构
```

## 3. 执行查询

`db/queries.sql` 里是只读查询（标的物检索 + 五大业务场景 + 星图总览），**演示库与生产库通用**。

> 注意：`db/` 没有挂载进容器，所以要用 **stdin 重定向**把宿主机上的文件喂进去，不能写 `-f db/queries.sql`。

```bash
docker compose exec -T db psql -U ict -d ict_demo \
  -v kw='' \
  -v purchaser='佛山大学' \
  -v winner='北京数慧时空信息技术有限公司' \
  -v suppliers='南京柯美齐科技有限公司,深圳市中皓医疗器械有限公司' \
  < db/queries.sql
```

- 变量用不到的传空串即可（`:''` 语法见 `queries.sql` 头部说明）。
- 多主体场景（S4/S5）用**英文逗号**分隔、名称写全称。
- 想单独跑一段，把该段 SQL 存成临时文件同样用 `<` 喂进去。

## 4. 换库（真实数据好了之后）

```bash
# 建同结构的新库（同样用 stdin 重定向，容器内看不到 db/ 路径）
docker compose exec -T db psql -U ict -d postgres -c "CREATE DATABASE ict_prod;"
docker compose exec -T db psql -U ict -d ict_prod < db/001_schema.sql
```

然后把 `.env` 改成：

```
DATABASE_URL=postgresql://ict:ict_dev_pw@localhost:15432/ict_prod
PGDATABASE=ict_prod
```

**`ict_demo` 不要删**——演示视频、离线演示都还要用。

## 4b. 实测批次库（不覆盖演示库）

主路线每跑完一批，结果落在 `work/runs/<announcement_id>/`。想在前端里检索这一批的真实产出，就另建一个
同结构库并用 `db/load_runs.py` 导入，**演示库 `ict_demo` 保持原样**：

```bash
docker exec ict-pg psql -U ict -d postgres -c "CREATE DATABASE ict_batch20261004 OWNER ict   ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'"
Get-Content db\001_schema.sql -Raw | docker exec -i ict-pg psql -U ict -d ict_batch20261004 -v ON_ERROR_STOP=1 -q
python db/load_runs.py --db ict_batch20261004 --runs work/runs --reset
```

只改进程环境变量就能让后端指向批次库，不必动 `.env`：

```powershell
$env:DATABASE_URL = "postgresql://ict:ict_dev_pw@localhost:15432/ict_batch20261004"
D:\python\python.exe -m uvicorn server.app:app --port 8000
```

前端 `npm run dev` 起来后开 `http://localhost:5173/`（vite 绑的是 `localhost`，用 `127.0.0.1` 会连不上）。

导入脚本取的是 `09_merged_projects.json`（含 `provenance`）而不是对外的 `projects.json`，
这样才能把每条标的回溯到 `08_normalized_candidates.json` 里的那条候选，填上 `source_type` 与
`source_file_id`。`announcement.raw_json` 里带了运行报告和第 8 步的审计
（`merge_decisions` / `amount_audit` / `unmatched_summary_rows` / `conflicts` / `checks`），
前端不用改表就能取到。

### project 的唯一键：项目编号 + 包号 + 轮次

`project` 一行 = 一个包的**一轮**。`project_id = <项目编号>|<包号>`，同一（项目编号, 包号）
被多则公告复用时（重新采购、重排包号）第 2 轮起写成 `<项目编号>|<包号>|rN`，并带
`round_no`。轮次按**公告号里的日期戳**排（`announcement.created_at` 是入库时间，不能用）。

`package_key` 是合并前的业务键（= `<项目编号>|<包号>`），`round_no` 是它在公告中的序号。
模型没抽到项目编号时，`package_key` 退回 `<项目名>|<包号>`（详见 `doc/archive/数据库改造计划-已实施.md` §4）。

已存在的库要先跑一次迁移再重灌：

```powershell
Get-Content db\002_project_round_key.sql -Raw | docker exec -i ict-pg psql -U ict -d <库名> -v ON_ERROR_STOP=1
D:\python\python.exe db\load_runs.py --db <库名> --runs work\runs --reset
```

**已知缺口**：`cob` 表只有 `source_type`（html/pdf/docx/…）和 `source_file_id`，没有"文件类"
（`award_detail` / `bid_quote` / `qualification` / `unknown`）这一列。所以 UI 里筛不出"来自资格证明
材料的标的"这类行，只能靠 `source_type='pdf'` 粗筛或直接查 `raw_json`。要按类筛就得给 `cob` 加列，
那是改表结构，等定夺。

## 5. 文件说明

| 文件 | 内容 |
|---|---|
| `docker-compose.yml` | Postgres 16 容器定义（编码/排序规则锁定，跨环境一致） |
| `db/001_schema.sql` | 建表 + 索引 + 视图 + 归一化函数（DDL，不含数据） |
| `db/002_project_round_key.sql` | 迁移：给已存在的库补 `package_key` / `round_no` 并换唯一键（新库不需要） |
| `db/queries.sql` | 只读查询集（检索 + S1–S5 + 星图 + 统计） |
| `db/load_runs.py` | 把 `work/runs/` 的批次结果导入一个同结构库（只读源、幂等 `--reset`） |

## 6. 注意

- `db/001_schema.sql` 通过 `docker-entrypoint-initdb.d` 挂载，**只在数据卷为空时执行一次**。改了这个文件不会自动重跑，要 `docker compose down -v` 重建，或手动执行 `-f db/001_schema.sql`。
- 排序规则用 `--locale=C`：中文按码点排序（不是拼音），但**开发机与服务器结果完全一致**。业务排序都按次数/金额，不依赖 collation。
- 主机端口是 **15432**（不是默认的 5432，原因见下）：用任意 PostgreSQL 客户端
  （DBeaver / Navicat / pgAdmin）连 `localhost:15432`，库 `ict_demo`，用户 `ict`，密码 `ict_dev_pw`。
- **为什么是 15432**：本机的 Windows 动态端口范围被改成了 1024–15000（默认应为 49152–65535），
  Hyper-V/WSL 会从这段里切走保留区间；5432 一旦被切进去，容器起得来、数据库也健康，但主机端口
  永远发布不出来（bind 报 `WSAEACCES`），且重启 Docker 无效——保留区间每次开机重新分配。
  修法：把 `netsh int ipv4 set dynamicport tcp start=49152 num=16384`（需管理员 + 重启）改回默认，
  或者像现在这样用一个 15000 以上的主机端口。
