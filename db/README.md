# 数据库（PostgreSQL · Docker）

演示库与生产库**结构完全相同**，区别只在库名与数据。换库 = 改 `.env` 里的 `DATABASE_URL` / `PGDATABASE`，代码与查询语句都不用动。

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
DATABASE_URL=postgresql://ict:ict_dev_pw@localhost:5432/ict_prod
PGDATABASE=ict_prod
```

**`ict_demo` 不要删**——演示视频、离线演示都还要用。

## 5. 文件说明

| 文件 | 内容 |
|---|---|
| `docker-compose.yml` | Postgres 16 容器定义（编码/排序规则锁定，跨环境一致） |
| `db/001_schema.sql` | 建表 + 索引 + 视图 + 归一化函数（DDL，不含数据） |
| `db/queries.sql` | 只读查询集（检索 + S1–S5 + 星图 + 统计） |

## 6. 注意

- `db/001_schema.sql` 通过 `docker-entrypoint-initdb.d` 挂载，**只在数据卷为空时执行一次**。改了这个文件不会自动重跑，要 `docker compose down -v` 重建，或手动执行 `-f db/001_schema.sql`。
- 排序规则用 `--locale=C`：中文按码点排序（不是拼音），但**开发机与服务器结果完全一致**。业务排序都按次数/金额，不依赖 collation。
- 可选图形界面：用任意 PostgreSQL 客户端（DBeaver / Navicat / pgAdmin）连 `localhost:5432`，库 `ict_demo`，用户 `ict`，密码 `ict_dev_pw`。
