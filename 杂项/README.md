# 系统性能评测入口

## 本地前后端、任务进程与OCR启动

以下命令用于本机`D:\all_contest\2026_9_23_ICT`的测试代码。各服务分别开PowerShell终端，使用已安装项目依赖的同一个Python环境；远端命令由本地`deploy/sshx.py`执行。这里只记录操作方法，不代表这些服务当前已经启动。

### 1. 数据库和本地后端

先启动Docker Desktop，再在项目根目录执行：

```powershell
Set-Location D:\all_contest\2026_9_23_ICT
docker compose up -d db
docker compose ps
```

数据库应为healthy，本机映射端口为15432；后端仍读取根目录`.env`里的`DATABASE_URL`等既有配置。若`.env`配置的是另一个已有数据库，以该连接为准，启动容器不会自动切换连接。

在后端专用终端执行：

```powershell
Set-Location D:\all_contest\2026_9_23_ICT
$env:PYTHONPATH = Join-Path (Get-Location).Path 'src'
$env:ICT_DATA_ROOT = Join-Path (Get-Location).Path 'var'
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

在另一终端检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/ping
```

返回`ok=true`表示后端可达。

### 2. 本地前端


```powershell
Set-Location D:\all_contest\2026_9_23_ICT\web
npm run dev -- --host 127.0.0.1
```

打开<http://127.0.0.1:5173/ingest>，注册或登录测试账号。前端的`/api`请求由Vite代理到后端8000端口。若新机器尚未安装前端依赖，先在web目录执行一次`npm ci`。

### 3. OCR服务器连接与本地SSH隧道

根目录`.env`已有连接配置，`sshx.py`和`tunnel.py`会自行读取。需核对的键为`ICT_SSH_HOST`、`ICT_SSH_PORT`、`ICT_SSH_USER`、`ssh_password`，以及`ICT_PARSE_URL`。

先验证SSH连接：

```powershell
Set-Location D:\all_contest\2026_9_23_ICT
python deploy/sshx.py whoami
```

成功时显示远端用户、主机及资源信息。再在**隧道专用终端**启动转发，测试全程保持终端运行：

```powershell
Set-Location D:\all_contest\2026_9_23_ICT
python deploy/tunnel.py 6008 127.0.0.1:6008
```

预期显示`forwarding 127.0.0.1:6008 -> 127.0.0.1:6008`。另开终端检查本机到远端解析服务的通路：

```powershell
Invoke-RestMethod http://127.0.0.1:6008/health
```

返回`ok=true`表示6008解析服务通过隧道可达，还需执行：

```powershell
python deploy/sshx.py run "curl -fsS --max-time 10 http://127.0.0.1:6006/v1/models"
```

预期返回包含`PaddlePaddle/PaddleOCR-VL-1.6`的模型列表。系统默认解析地址为`http://127.0.0.1:6008`；若修改本地转发端口，`.env`的`ICT_PARSE_URL`也须对应修改，随后重启worker。若6008已被现有隧道占用，直接检查已有连接，无需再启动第二条。

### 4. OCR服务器启动服务

已配置好的GPU服务器有两层服务：6006为PaddleOCR-VL模型，6008为文档/版面解析worker。先检查上方接口；仅未启动或需要重启时执行以下命令。**这两个脚本带重启语义，会先停止对应旧进程，不应在正在跑测试时执行。**

从本机根目录依次执行：

```powershell
Set-Location D:\all_contest\2026_9_23_ICT
python deploy/sshx.py run "bash /root/deploy/start_vllm.sh" --timeout 300
python deploy/sshx.py run "bash /root/deploy/start_parse.sh" --timeout 300
```

第一条应显示`vllm ready`，第二条应显示`parse ready`；启动后重复第3节的两个接口检查。既有脚本默认6008有4个worker。它们依赖远端既有模型`/root/model/PaddleOCR-VL-1.6`、conda环境`paddleocr-vl`、`/opt/ict-parse/parse.env`和`/root/deploy/parse_service.py`；

### 5. 启动本地任务worker，处理网页上传

前后端可打开网页，但实际抽取还需要下面这个独立进程。OCR通路检查完成后，另开终端执行：

```powershell
Set-Location D:\all_contest\2026_9_23_ICT
$env:PYTHONPATH = Join-Path (Get-Location).Path 'src'
$env:ICT_DATA_ROOT = Join-Path (Get-Location).Path 'var'
python -m ict.worker
```

后端与worker的`ICT_DATA_ROOT`必须一致。仅启动一个worker进程，它扫描所有账号任务；默认普通处理名额4、合并名额4、全局API并发8。需要调整时在启动前设置相应进程环境变量。浏览旧结果无需worker；修改抽取代码或提示词后重启worker才会生效。

## 新注册测试账号的目录

普通注册会生成32位十六进制`account_id`，其`tenant_id`默认与之相同，**目录用tenant_id，不用用户名**。注册本身不会立刻生成完整runs目录，上传、处理后才产生相应文件。

按上面的本地启动配置，新账号目录为：

```text
D:\all_contest\2026_9_23_ICT\var\
  tenants\<tenant_id>\
    incoming\<job_id>\             上传的HTML/ZIP
    attachments\<公告UUID>\        解压附件
    runs\<公告UUID>\               抽取成品及审计，比较时间使用这里
      run_state.json
      10_run_report.json
      projects.json
  jobs\<tenant_id>\<job_id>.json    任务元数据
```

在**已登录测试账号的网页**打开浏览器开发者工具控制台，执行以下同源请求即可查看tenant_id，不需要提供密码：

```javascript
fetch('/api/auth/me').then(r => r.json()).then(data => console.log(data.user.tenant_id))
```

若返回例如`abc123…`，该账号的时间比对输入就是`D:\all_contest\2026_9_23_ICT\var\tenants\abc123…\runs`。若使用另一个`ICT_DATA_ROOT`，把上面的`var`替换成实际根目录。账号创建于线上、任务也由线上worker处理时，文件位于线上应用机对应数据根，本地不会自动同步。

入库成功后，上传原件和解压附件按原流程清理，runs及任务元数据保留。新账号与原账号隔离，但同一个测试账号重复处理同UUID仍复用目录；第二轮前先把该账号runs复制到独立实验目录，才能保留前后时间对照。可用下方`compare_times.py`分别读取两轮保存的runs。

---

## 根目录的两个简易入口（2026-11-12）

网页当前下载的是三表`official_export.xlsx`。结果比对同时支持该文件、单张官方CSV、每张表一份CSV的目录，仅需一个路径：

```powershell
python compare_results.py "C:\Users\86155\Downloads\official_export.xlsx"
python compare_results.py "D:\测试结果\标的表.csv"
python compare_results.py "D:\测试结果\三表CSV"
```

不带参数运行也会提示输入路径。默认金标为`data/50条标注_官方三表_报价补全.xlsx`，复用原匹配与蓝灰评分函数；评分范围为输入中出现金标UUID的案例和提供的表。一个案例只要出现在输入中，该案例本表金标里的漏行仍计入分母；整则完全无导出行的失败案例则无法由CSV识别。未提供的其他表、灰色列不评分，未知UUID单列。金额沿用导出万元口径，不重复换算。CSV支持UTF-8/BOM、GB18030及带引号的逗号、换行。

默认回归基线为上一轮`work/real20-evidence-trim-20261110/runs`，仅比较双方共有案例及共有表。可用`--baseline "另一份导出.xlsx或旧runs目录"`替换，`--gold`替换金标。报告明示基线和范围；无可用基线时不编造回归数。输出自动放入输入旁新建的`comparison_<时间>`目录，也可通过`--out`指定新目录；包含Markdown报告、蓝灰逐格结果、错值/漏行/多抽明细、字段指标、回归及金标哈希。不会覆盖下载文件、金标或旧测试。

**三表不包含时间、token、费用和页处理动作。仅靠下载文件不能评估目检/OCR页覆盖或调用成本；需要相应runs/调用记录，页指标仍使用本说明下方的完整评测入口。** 两个简易脚本均完全离线，不调用API/OCR，不读取凭据、不连接数据库。

耗时比对输入为一轮测试目录（里面有runs）、runs目录或单则目录：

```powershell
python compare_times.py "work\新一轮测试" "work\real20-evidence-trim-20261110"
python compare_times.py "var\tenants\default\runs"
```

第二个路径可省略，此时只汇总当前记录。默认在当前输入目录内新建`timing_comparison_<时间>`，生成报告、逐案例CSV、逐阶段CSV、模型延迟CSV及JSON。比较只用共有已完成案例；未完成、集合差异、缺少步骤和复用旧成品单列。阶段缺失留空，不能当零秒。多轮/多账号目录中同UUID重复会拒绝混合统计，需分别指定具体目录。完整流程与只跑后半程的实验须由使用者按相同范围比较。

### 时间存在哪里、记录范围与隔离

| 记录 | 字段与格式 | 范围 |
|---|---|---|
| `<runs>/<公告UUID>/10_run_report.json` | `duration_ms`，整数毫秒 | 单则提取链总耗时；包括模型/OCR、API闸门、合并排队及返回后接续等待 |
| 同文件`model_calls[]` | `step`、`latency_ms`、`usage`等JSON字段 | 每次实际调用的延迟和token；并发调用的延迟不可当作阶段耗时相加 |
| 同目录`run_state.json` | `steps[].started_at/ended_at`，例如`2026-11-12T10:00:00+08:00`，目前秒精度 | 每阶段墙钟时间；merge_candidates包含排队、执行及接续等待，当前没有持久化的三段精确分拆 |
| 测试目录`manifest.json`及`integrity.json` | `start_time`及`end_time`，`YYYY-MM-DD HH:MM:SS`，旧格式不带时区 | 测试记录首尾窗口，包含准备过程及可能的暂停/恢复；不是单则耗时之和 |
| PostgreSQL `announcement.raw_json.report.duration_ms` | JSON数字，毫秒 | 入库时保存同一份单则提取耗时，不额外计数据库写入耗时 |

`duration_ms`从处理链真正开始执行计到最终报告组装前，使用`perf_counter()`计算。它不含首次获得案例名额前的等待、worker解压、后续数据库入库及原件清理，也不含报告最后一次写盘的时间；并非从网页提交到全部完成的端到端耗时。worker覆盖这些动作的总时间目前只打印到进程日志，没有同等结构化字段。并发提升主要比较相同案例集合的整批时间，单则延迟可能不降。

开发单则默认路径为`work/runs/<UUID>`；实验按`work/<实验名>/runs/<UUID>`隔离。网页worker按`<ICT_DATA_ROOT>/tenants/<tenant_id>/runs/<UUID>`隔离账号与公告，未设置ICT_DATA_ROOT时数据根为项目`var`。`run_id=run_<UUID>`固定，**没有独立执行轮次ID；同账号重跑同公告会复用并覆盖运行目录，数据库公告报告也更新为当前值**。需要保留前后对照时，在再次运行前分别保留两轮目录；任务job_id不构成runs历史轮次隔离。

---

2026-11-12：20例运行器已接入正常处理链的合并挂起调度，普通名额与合并名额分别读取ICT_ANNOUNCEMENT_WORKERS（默认4）和ICT_MERGE_WORKERS（默认4），API仍共用原上限8。manifest记录实际并发配置；单则耗时使用处理链的duration_ms，包含合并等待及接续等待，排除首次取得案例名额前的队列时间。后续同20例实测命令：`python eval/run20_real_20261110.py --ids-manifest work/real20-evidence-trim-20261110/manifest.json --out work/real20-merge-suspend-20261112`。此命令会真实调用API/OCR；本次实现验证没有执行它。必须使用新目录，旧成品不覆盖；评分继续用下方离线入口，不改变金标或页标签。

思考开关实验入口：`python eval/compare_merge_thinking_20261110.py --verify`仅离线核对从冻结上游接续能否复现原成品；去掉`--verify`才会真实调用冻结v8的合并及后续模型（本轮已完成，已有成品自动跳过）。不重复OCR，不改生产默认。结果见`work/real20-no-thinking-20261110/evaluation/report.md`，评分通过本目录`evaluate.score/diff`复用同一套蓝灰规则，页记录复用原组而不当作新OCR执行。后段耗时不能与完整流程耗时直接比较。

所有比较离线执行，不调用模型/OCR，不写数据库。正常系统运行结束后调用此入口。

```powershell
python eval/performance/evaluate.py --runs work/real20-20261110/runs --manifest work/real20-20261110/manifest.json --out work/real20-20261110/evaluation --baseline previous_announcement_only=work/front-half-repair-20261108/offline-final/runs --baseline historical_full=work/e2e-20261017-replays/revised-evaluation/runs
```

后续测试只需替换`--runs`、`--manifest`、`--out`和基线。manifest包含`ids`数组；可用`--page-reference`指定另一份逐页标签。默认页标签为`work/attachment-fieldscope-review-20261109/comparison_rows.json`。

- `table_score.py`是既有`work/e2e-20261017-replays/revised-evaluation/score.py`的逐字节副本，复用对象对齐、数值/文本比较、逐格判定和CSV输出。不改变判定规则，不新增模糊匹配。
- `metric_policy.py`逐字复制既有`eval/report_front_half_repair_20261108.py`的`ratio/policy_metrics`，由`evaluate.py`复用汇总指标和填色允许留空政策；用同一匹配器分别传入蓝色、灰色字段。序号/URL不评分；蓝灰不混合。主体行未匹配仍记漏行，不通过空字段免除。
- 灰色字段非空值另复用`ict.official.score_gold_cell`处理数字/文本等价（例如90与90.0），不手写新的比较规则。灰色完整字段已存在于金标快照；按字段分列，正确空值与正确非空值分开，避免空格多使得分虚高。
- 页级对照复用既有`comparison_rows.json`，按公告、文件ID、真实页号/逻辑片段号关联实际`07_attachment_extraction.json`。图像目检决定、实际OCR返回、抽取入口分开计数；失败、硬忽略和预算损失单列。
- 应执行目检集合为既有冻结页预算内需要图像目检的单位；有效应读页集合来自既有复审标签。两个分母分别报告。无图像的原生页直接抽取，不冒充模型目检。
- 必需字段目标与仅灰色字段目标分开；页标签基于旧公告只读快照、由Luna辅助及主agent复审，尚非独立人工金标。输入公告信息变化造成的目标差异保留说明，不自动改标签。
- 结果含`report.md`、蓝灰逐格CSV、字段/行指标JSON、基线改善/退步CSV、逐页CSV与JSON。比较只使用同一批ids，未完成案例不发布完整批次结论。
- 测试运行器`eval/run20_real_20261110.py`使用与`ict.pipeline.run_announcement`相同的announcement_chain，只在合并处交给MergeScheduler挂起；目录中的评测器只读成品。
