# 前端效果改造 · 交付总览（2026-09-28）

## 一句话
`web/` 前端全量重建（星图视觉保留、逻辑修复）+ 新增 `server/` FastAPI 薄后端，四个工作区全部接上你注入 Docker Postgres 的真实演示数据，S1–S5 五场景端到端跑通。

## 运行方式
```bash
# 1. 数据库（已 healthy 则跳过）
docker compose up -d
# 2. 后端 API（项目根目录）
D:\python\python.exe -m uvicorn server.app:app --port 8000
# 3. 前端（web/ 目录）
node node_modules/vite/bin/vite.js --port 5173
# 打开 http://localhost:5173  →  演示路线：采集 → 检索 → 探索
```
（当前两个服务已在后台运行，直接打开 http://localhost:5173 即可）

## 交付清单

### 新增：后端薄层 `server/`（Python · FastAPI + psycopg3）
| 文件 | 职责 |
|---|---|
| `server/db.py` | 连接池 + `.env` 加载（换库只改 `DATABASE_URL`） |
| `server/scenes.py` | S1–S5 场景查询与图/叙事组装、星图总览采样 |
| `server/app.py` | 12 个 REST 端点（检索/详情/CSV 导出/五场景/总览/主体/统计/公告/流水线回放） |

- 流水线回放：`GET /api/runs/{公告id}` 直接读 `work/runs/*/run_state.json`，黄金 10 篇可看完整 11 步。
- 上传/触发处理返回 **501 明确占位**（接真实管线是下一轮，前端已做优雅降级提示）。

### 重建：前端 `web/`（Vue 3.5 + Vite 7 + TS + Tailwind 4，**零新增依赖**）
- 四工作区四路由全懒加载：`/ingest` 采集 · `/search` 检索 · `/explore` 探索 · `/party/:id` 档案。
- 双域主题（记录域浅 / 宇宙域深）随路由自动切换；⌘K 命令面板接 `/api/parties` 模糊搜索。
- 星图 `StarMap.vue`：视觉参数零改动，逻辑修复——静态边合并为单次 draw call（暖金合作/冷蓝竞争）、标签 LOD≤80 且随节点一起淡化、聚焦邻域边独立曲线+角色标签、脉冲一次 + #n 排名徽标 + 淡化 18% 不删除、raycast 节流。
- 布局 `useGraphLayout.ts`：原版力导算法泛化（任意场景图可排），按 √（n/25) 等比放大并保持原视觉密度。
- 已删除被替代的旧文件：`universe.ts`、`styles.css`、4 个旧视图。

## 实测数据
| 指标 | 结果 |
|---|---|
| 场景查询服务端耗时 | **5–25 ms**（红线是 1000ms） |
| 场景图前端渲染（软渲染 SwiftShader） | 224–511 ms（真 GPU 更快） |
| 总览规模 | 536 节点 · 1806 关系（高频主体采样） |
| tsc 类型检查 | 0 错误 |
| 首屏包（/ingest，gzip） | 43 KB（three 独立 chunk 132KB 仅 /explore 加载） |

## 我对原方案的三处调整（已验证可行）
1. **零新增依赖**：未加 vue-virtual-scroller（服务端分页 20 行/页已够用）与 echarts（分布图纯 CSS 实现，省 ~300KB）。
2. **星图不搬 Worker / 不上 InstancedMesh**：采样后 ≤600 节点主线程力导仅百毫秒级；真正的性能赢面在静态边合并 + 标签 LOD + 每帧只算聚焦边。
3. **「数字先出」用 `nextTick` 而非 requestIdleCallback**：星图 rAF 循环会让 idle 回调在低端机上永不触发（实测踩坑）。

## 待办（下一轮）
- 接入页上传/触发处理接真实抽取管线（当前 501 占位）。
- S4 演示数据交集偏薄（全库最大交集 3 个采购单位），需要时按 `db/演示数据说明.md` 的规则重灌。
- 演示视频路线：采集（公告列表+流水线）→ 检索（筛选+详情+导出）→ 探索（五场景+星图）。

## 验证截图
`work/ui_check/`：ingest / search / party / final_overview（星图总览）/ final_s1（S1 场景高亮淡化）/ s5_map（S5 多主体+排名徽标）。
