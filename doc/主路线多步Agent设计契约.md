# 主路线多步 Agent 设计契约

版本：v0.6  
日期：2026-09-27  
适用范围：任务一主提取流程。本文记录当前主路线的数据契约、状态流转，以及已经落地的调用方式。

---

## 1. 设计目标与边界

### 1.1 主路线目标

主路线处理一条公告从输入到结构化入库的完整流程：

```text
公告 HTML
  → 公告与包结构理解
  → HTML 表格理解与候选抽取
  → 每包缺口规划
  → 查询附件索引，取得本公告附件目录
  → 附件文件级筛选
  → 附件页面级定位
  → 附件局部抽取
  → 字段规范化
  → 候选合并去重
  → 最终入库与运行报告
```

主路线中每一步都有明确的输入、输出、状态更新和失败记录。LLM 不依赖对话记忆，只接收由上一步状态投影出的最小上下文。

### 1.2 当前明确排除的内容

以下情况不进入当前主路线的深入处理，只记录失败原因，待主路线跑通后根据结果分析再设计分支：

| 情况 | 当前处理 |
|---|---|
| 图片文件 | 记录 `unsupported_image` |
| 扫描版 PDF | 记录 `scanned_or_low_text_pdf` |
| 加密 PDF | 记录 `encrypted_pdf` |
| 水印导致文本不可读 | 记录 `watermark_interference` |
| 无法解析的 DOC/DOCX/XLSX | 记录 `document_parse_failed` |
| 包结构无法判断 | 记录 `package_structure_unclear` |
| LLM 输出不是 JSON 对象，或表角色不在枚举内 | 记录 `llm_schema_invalid` |
| 附件索引缺失 | 记录 `attachment_index_miss` |

这些记录不是最终业务数据，而是后续分支建设的输入。

### 1.3 主路线的数据原则

1. 一个公告对应一个 `run_id`。
2. 一个项目等于“项目名称 + 包编号”，即 `project_id`。
3. `package_no` 永远是字符串，可为 `"1"`、`"4"`、`"A"`。
4. 附件只能绑定当前公告，不能按项目编号跨公告补字段。
5. LLM 输出先进入候选层，不直接写最终业务表。
6. 候选层只记录来源级溯源：来自 HTML 或来自哪一个附件文件；不记录行级、单元格级证据。
7. `null` 表示未知或缺失，禁止用空字符串、`"无"`、`"详见附件"` 冒充空值。
8. 主路线只处理文本可提取内容；不可提取内容记录失败。
9. 状态文件是每一步的契约输出，字段名和枚举值不得在实现中随意改名。

---

## 2. 全局命名与状态文件

每个公告的运行目录：

```text
work/runs/<announcement_id>/
```

固定输出文件：

| 文件 | 内容 |
|---|---|
| `run_state.json` | 全局运行状态注册表 |
| `01_announcement_understanding.json` | 公告与包结构理解 |
| `02_html_tables.json` | HTML 表格角色理解 |
| `03_html_candidates.json` | HTML 抽取候选 |
| `04_project_plans.json` | 每包缺口规划 |
| `05_file_decisions.json` | 附件文件级筛选 |
| `06_page_decisions.json` | 附件页面级定位 |
| `07_attachment_extraction.json` | 附件局部抽取候选 |
| `08_normalized_candidates.json` | 规范化后的候选 |
| `09_merged_projects.json` | 合并后的项目结构 |
| `10_run_report.json` | 运行报告 |

`run_state.json` 是状态注册表，不保存大体积业务数据，只记录每一步的状态和输出文件位置。

---

## 3. 通用字段约定

### 3.1 ID 规则

| 字段 | 类型 | 定义 |
|---|---|---|
| `run_id` | string | 一次公告处理流程的唯一 ID，格式为 `run_<announcement_id>` |
| `announcement_id` | string | 公告 ID，来自 HTML 文件名去掉扩展名，如 `t20260202_26139731` |
| `project_id` | string | 项目唯一 ID，格式为 `<project_name>|<package_no>`；若项目名包含 `|`，实现时必须先转义 |
| `file_id` | string | 当前 run 内附件文件唯一 ID，格式为 `a001`、`a002` |
| `candidate_id` | string | 当前 run 内候选唯一 ID，格式为 `cand_000001` |
| `source` | object | 候选来源，仅到 HTML 或附件文件级，不落到行或单元格 |

### 3.2 空值语义

| 值 | 含义 |
|---|---|
| `null` | 字段缺失或未知 |
| `""` | 禁止用于业务字段；文本字段缺失时必须写 `null` |
| `"无"` | 原文出现时作为原始证据保留，但规范化业务值仍为 `null` |
| `"详见附件"` | 不作为业务值，字段状态记为 `points_to_attachment`，业务值为 `null` |

### 3.3 置信度

所有 `confidence` 字段：

```text
类型：number
范围：0 <= confidence <= 1
```

含义为该步骤模型或规则的判断置信度，不等于最终准确率。没有可靠依据时可以省略，但不得写负数或大于 1 的数。

---

## 4. 通用枚举定义

### 4.1 `RunStatus`

| 值 | 含义 |
|---|---|
| `pending` | 尚未开始 |
| `running` | 正在处理 |
| `success` | 主路线完整完成，并生成最终项目 |
| `partial` | 主流程完成，但存在失败、缺失或待复核记录 |
| `failed` | 主路线终止，未生成最终项目 |

### 4.2 `StepStatus`

| 值 | 含义 |
|---|---|
| `pending` | 未开始 |
| `running` | 执行中 |
| `success` | 成功完成 |
| `partial` | 完成，但存在失败记录或未解决问题 |
| `failed` | 本步失败 |
| `skipped` | 本步无需执行 |

### 4.3 `FailureCode`

主路线统一失败码：

| 值 | 含义 |
|---|---|
| `no_attachment` | 公告没有附件 |
| `attachment_index_miss` | 附件索引中没有该公告记录 |
| `unsupported_image` | 图片文件不进入主路线 |
| `scanned_or_low_text_pdf` | PDF 文本过少，疑似扫描件 |
| `encrypted_pdf` | PDF 加密或无法读取 |
| `watermark_interference` | 水印导致关键内容不可读 |
| `document_parse_failed` | DOC/DOCX/XLSX 等文档解析失败 |
| `package_structure_unclear` | 无法判断公告包结构 |
| `llm_schema_invalid` | 返回不是 JSON 对象，或枚举不合法。先带错误重试 1 次，仍不合法才记录。接口只有 `json_object`，没有 JSON Schema |
| `llm_call_failed` | LLM 调用失败 |
| `no_text_extractable` | 没有可提取文本。PDF 没有 Markdown 且 Paddle 未接入时，说明为 `unknown:paddle_not_connected`；转换失败时为 `unknown:conversion_failed` |
| `no_candidate_extracted` | 未抽到任何候选 |
| `field_normalization_failed` | 字段规范化失败 |
| `merge_conflict_unresolved` | 合并冲突无法解决 |
| `page_context_truncated` | 同一包送入第 6 步的页面超过 12 页，超出部分未抽取 |
| `consistency_check_failed` | 包金额、标的或中标人一致性检查未通过，运行待复核 |
| `unexpected_error` | 未归类异常 |

### 4.4 `EntityType`

| 值 | 含义 |
|---|---|
| `cob` | 标的物候选 |
| `sub` | 投标供应商候选 |

### 4.5 `FieldStatus`

每个候选字段都有独立状态：

| 值 | 含义 |
|---|---|
| `present` | 原文或规则中有可识别值 |
| `missing` | 原文未提供 |
| `points_to_attachment` | 原文写“详见附件”等，业务值为空但需要查附件 |
| `low_confidence` | 有候选值但证据不足或识别不稳定 |
| `conflict` | 多来源值冲突 |
| `unsupported` | 来源类型不支持该字段提取 |

---

## 5. `run_state.json`

### 5.1 结构

```json
{
  "run_id": "run_t20260202_26139731",
  "announcement_id": "t20260202_26139731",
  "created_at": "2026-09-24T10:00:00+08:00",
  "updated_at": "2026-09-24T10:05:00+08:00",
  "status": "partial",
  "current_step": "merge_candidates",
  "steps": [
    {
      "step": "understand_announcement",
      "status": "success",
      "output_file": "01_announcement_understanding.json",
      "started_at": "2026-09-24T10:00:00+08:00",
      "ended_at": "2026-09-24T10:00:20+08:00",
      "failure_code": null,
      "failure_message": null
    }
  ],
  "counts": {
    "projects": 3,
    "files": 12,
    "selected_files": 4,
    "selected_pages": 9,
    "html_candidates": 18,
    "attachment_candidates": 26,
    "normalized_candidates": 40,
    "final_cobs": 15,
    "final_subs": 9
  },
  "review_required": true
}
```

### 5.2 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `run_id` | string | 是 | `run_<announcement_id>` |
| `announcement_id` | string | 是 | 公告 ID |
| `created_at` | string | 是 | ISO 8601 时间 |
| `updated_at` | string | 是 | ISO 8601 时间 |
| `status` | `RunStatus` | 是 | 全局状态 |
| `current_step` | string | 是 | 当前执行到的步骤名 |
| `steps` | array | 是 | 步骤状态列表，按执行顺序 |
| `counts` | object | 是 | 计数器 |
| `review_required` | boolean | 是 | 是否需要人工复核 |

### 5.3 `steps[].step` 可选值

| 值 | 步骤 |
|---|---|
| `understand_announcement` | Step 1 |
| `understand_html_tables` | Step 2A |
| `extract_html_candidates` | Step 2B |
| `plan_project_gaps` | Step 3 |
| `triage_files` | Step 4 |
| `locate_pages` | Step 5 |
| `extract_attachment_candidates` | Step 6 |
| `normalize_candidates` | Step 7 |
| `merge_candidates` | Step 8 |
| `persist_and_report` | Step 9 |

---

# 前置输入：附件索引

附件解压、递归展开、文件名修复、去重和基础元数据建索引均不属于主路线。主路线只消费已经建好的附件索引。

索引查询结果至少包含：

```json
{
  "announcement_id": "t20260202_26139731",
  "attachment_directory": "work/attachments/t20260202_26139731",
  "files": [
    {
      "file_id": "a001",
      "display_name": "开标记录表.pdf",
      "relative_path": "files/a001_开标记录表.pdf",
      "mime_type": "application/pdf",
      "extension": ".pdf",
      "page_count": 86,
      "text_density": 0.42,
      "readability": "text_extractable"
    }
  ]
}
```

字段定义：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `announcement_id` | string | 是 | 公告 ID |
| `attachment_directory` | string/null | 是 | 该公告附件目录；无附件为 `null` |
| `files` | array | 是 | 附件文件列表 |
| `file_id` | string | 是 | 当前公告内唯一文件 ID |
| `display_name` | string | 是 | 展示文件名 |
| `relative_path` | string | 是 | 相对附件目录的路径 |
| `mime_type` | string/null | 是 | MIME 类型 |
| `extension` | string | 是 | 小写扩展名 |
| `page_count` | integer/null | 是 | 可分页文件的页数 |
| `text_density` | number/null | 是 | 抽取字符数 / 页数；无页数或无法抽取为 `null` |
| `readability` | enum | 是 | 可读性 |

`readability` 可选值沿用：

| 值 | 含义 |
|---|---|
| `unknown` | 尚未探查 |
| `text_extractable` | 文本可提取 |
| `low_text` | 文本很少，疑似扫描件 |
| `parse_failed` | 文件解析失败 |
| `unsupported` | 主路线不支持 |

PDF 若在 `work/attachments-md/` 下有同相对路径的 `.md`，索引把该 PDF 记为 `text_extractable`，提示为 `markdown`，不再用 PDF 文本层判断稀密。没有 Markdown 时仍用 PDF 文本层探测。

主路线对索引只做读取，不修改索引，也不承担解压职责。

---

# Step 1：公告与包结构理解

## 1.1 LLM 输入

输入为 HTML 清洗后的公告上下文，不传完整 HTML：

```json
{
  "announcement_title": "xxx结果公告",
  "summary_table": {
    "采购项目名称": "xxx",
    "品目": "货物/设备",
    "采购单位": "xxx",
    "总中标金额": "￥325.031000 万元",
    "总成交金额": "",
    "评审专家名单": ""
  },
  "html_headings": ["一、中标信息", "二、主要中标标的"],
  "body_sections": [
    {"title": "三、中标（成交）信息", "text": "正文摘录，最长 1600 字"}
  ],
  "tables": [
    {
      "table_index": 0,
      "before_text": "表前文字",
      "section": "四、主要标的信息",
      "key_value": false,
      "headers": ["品目名称", "采购标的", "品牌", "规格型号"],
      "rows": [["其他商业保险服务", "团体重大疾病保险", "无", "无"]]
    }
  ],
  "package_hints": [
    {
      "text": "包1",
      "location": "html_heading",
      "package_evidence_text": "采购包1：xxx",
      "package_no": "1",
      "package_amount": {
        "raw_text": "￥100万元",
        "amount_yuan": 1000000,
        "scope": "package",
        "confidence": 0.7
      }
    }
  ],
  "source_project_no_hint": "SDGP123"
}
```

概要只保留采购项目名称、品目、采购单位、总中标金额、总成交金额、评审专家名单。标题最多 40 条。正文段只收标题含「中标」「成交」「主要标的」「分包」的段，最多 8 段。表最多 12 张；普通表预览 8 行，键值表预览 20 行。包线索最多 40 条。

## 1.2 输出文件

`01_announcement_understanding.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "success",
  "project_name": "xxx",
  "purchaser": "xxx",
  "source_project_no": "SDGP123",
  "announcement_type": "winning_announcement",
  "package_mode": "multi",
  "packages": [
    {
      "package_no": "1",
      "title": "第1包 xxx",
      "project_id": "xxx|1",
      "package_evidence_text": "采购包1：xxx"
    }
  ],
  "summary_amount": {
    "raw_text": "￥325.031000 万元",
    "amount_yuan": 3250310,
    "scope": "announcement",
    "confidence": 0.98
  },
  "unclear_reason": null,
  "model_metadata": {
    "model_name": "deepseek-flash",
    "model_version": "deepseek-flash",
    "prompt_version": "announcement-v1",
    "latency_ms": 1200
  },
  "failures": []
}
```

## 1.3 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `project_name` | string | 是 | 项目名称；无法识别时本 run 终止为 `failed` |
| `purchaser` | string/null | 是 | 采购单位 |
| `source_project_no` | string/null | 是 | 公告原文项目编号，仅作来源，不参与项目合并 |
| `announcement_type` | enum | 是 | 公告类型 |
| `package_mode` | enum | 是 | 包结构模式 |
| `packages` | array | 是 | 包列表 |
| `summary_amount` | object/null | 是 | 公告概要金额 |
| `unclear_reason` | string/null | 是 | 包结构不清楚时的说明 |
| `model_metadata` | object/null | 是 | LLM 调用元数据 |
| `failures` | array | 是 | 失败记录 |

### `announcement_type` 可选值

| 值 | 含义 |
|---|---|
| `winning_announcement` | 中标公告 |
| `deal_announcement` | 成交公告 |
| `unknown` | 无法判断 |

### `package_mode` 可选值

| 值 | 含义 | 主路线处理 |
|---|---|---|
| `single` | 明确单包 | 继续主路线 |
| `multi` | 明确多包 | 继续主路线 |
| `unclear` | 无法判断 | 记录 `package_structure_unclear`，主路线终止为 `failed` |

### `packages[]` 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `package_no` | string | 是 | 包编号，只写编号本身。`"第3包"` 写成 `"3"`，`"标包A"` 写成 `"A"` |
| `title` | string/null | 是 | 包标题 |
| `project_id` | string | 是 | `<project_name>|<package_no>` |
| `package_evidence_text` | string | 是 | 原文中证明该包存在的文本 |
| `package_amount` | object/null | 是 | 包级金额；原文未提供时为 `null` |

### `package_amount` 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `raw_text` | string/null | 是 | 原文金额 |
| `amount_yuan` | number/null | 是 | 换算为人民币元后的数值 |
| `scope` | enum | 是 | 固定为 `package` |
| `confidence` | number/null | 是 | 金额理解置信度 |

### `summary_amount` 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `raw_text` | string/null | 是 | 原文金额 |
| `amount_yuan` | number/null | 是 | 换算为人民币元后的数值 |
| `scope` | enum | 是 | 固定为 `announcement` |
| `confidence` | number/null | 是 | 金额理解置信度 |

## 1.4 语义约束

1. `package_no` 必须是字符串。
2. `project_name` 或 `package_no` 缺失时，本 run 终止为 `failed`，不得生成 `project_id`。
3. 明确单包且原文未写包号时，`package_no` 为 `"1"`。
4. `multi` 时 `packages` 至少有两个，且包号不重复。
5. `single` 时 `packages` 有且仅有一个。
6. `summary_amount.scope` 永远是 `announcement`，不能写成包级金额。
7. `package_amount.scope` 永远是 `package`，只能挂在对应包上。
8. 单包公告中，若 `package_amount` 缺失，可用 `summary_amount` 作为 `package_total_amount`。
9. 多包公告中，若 `package_amount` 缺失，`package_total_amount` 为 `null`，不得用公告总金额摊分。
10. `package_no` 只含编号本身，不带「第」「包」「采购包」「标包」等前后缀。字母包保持字母，不改成数字。

---

## 1.5 v1.0 主路线实现范围

1. 只实现单条公告的主处理流水线。
2. COB 与 SUB 作为两类独立候选分别抽取、分别校验、分别合并；不要求同一次 LLM 调用同时输出两类实体。
3. 文件级筛选与页面级筛选是主路线的必要环节，用于避免把无关长文本送入抽取步骤。
4. 不处理 OCR、水印和图片识别。包范围可以在逐表判断之后用规则补定；无法归属的 HTML 明细可以按补定后的包号进入合并。招标需求文件不能新增标的。
5. `source_priority` 为暂定经验值。HTML「主要标的信息」明细为 100。附件按文件类型：成交明细 100、分项报价 90、招标需求 40。价格、数量、品牌在 HTML 明细已有值时不被附件覆盖。

## 1.7 确定性包归属与标的清单

第 2A 步每张表仍然单独判断。判断之后用规则补包号，顺序是：表前文字里的供应商对上中标供应商所在包；品目号第一段；同表头的一组表用已经确定的包做排除，剩下的包按顺序补上。仍然不确定时，再做一次整篇范围的模型调用，提示词 `resolve-packages-v1`。

位于「主要标的信息」或键值里含名称、服务范围、标的的表，即使被判成 `other`，也按标的明细抽取。

正文是否抽取供应商看内容：同时出现公司或供应商和括号分数，或出现「供应商名称」和金额。不要求标题写着评审专家。

每个包的标的清单：HTML 成交明细是具体行、不是汇总、不指向附件时，清单封闭。附件只按名称补空字段，对不上名称的行丢弃并记原因。否则清单开放，`bid_quote` 或 `award_detail` 可以新增行并替换 HTML 汇总行。`tender_requirement` 永远不能新增标的。

合并先按规范化名称聚类。数量和单价两边都有值且不同，才分成两行。空值不拆行。

第 8 步之后做一致性检查：包内是否至少有一个标的和一个中标人；各行总价之和与包金额是否偏离超过 8%。不通过则记 `consistency_check_failed`，运行标为待复核，不自动再抽一轮。

模型输出的枚举不合法时，带上错误信息重试 1 次。两次都不合法才记 `llm_schema_invalid`。

服务和工程不因为品牌、规格覆盖率低而要求读附件。

---

## 1.6 LLM 调用

每步只接收当次拼好的 JSON，不保留对话。请求使用 `response_format: json_object`，温度为 0，关闭思考。没有把字段枚举做成 JSON Schema。

返回不是 JSON 对象时记 `llm_schema_invalid`。第 2A 步的 `table_role` 不在枚举内时同样记 `llm_schema_invalid`。其余字段由代码收下：候选只保留契约字段；下表别名会改写成正式键，正式键已有值时不覆盖。

| 模型键 | 正式键 |
|---|---|
| `item_name`、`product_name`、`产品名称`、`货物名称`、`采购标的`、`报价明细内容` | `object_name` |
| `品牌`、`brand_model` 的非数字部分 | `brand` |
| `规格型号`、`型号` | `spec_model` |
| `制造商`、`生产厂家` | `product_supplier` |
| `品目名称`、`品目编号及品目名称` | `category_name` |
| `品目编号` | `category_code` |
| `供应商名称`、`bidder_name` | `supplier_name` |
| `综合得分`、`评审总得分` | `score` |

提示词文件在 `src/ict/prompts/`。当前各步版本是 `announcement-v1`、`html-tables-v1`、`html-candidates-v1`、`triage-files-v1`、`locate-pages-v1`、`attachment-extract-v1`。

---

# Step 2A：HTML 表格角色理解

## 2A.1 LLM 输入

每个 HTML 表格生成一条表格上下文：

```json
{
  "table_index": 3,
  "before_text": "三、主要中标标的",
  "headers": ["品目名称", "采购标的", "品牌", "规格型号"],
  "first_rows": [
    ["A01010100", "办公用计算机", "联想", "xxx"]
  ],
  "package_candidates": ["1", "3"]
}
```

## 2A.2 输出文件

`02_html_tables.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "partial",
  "tables": [
    {
      "table_index": 3,
      "table_role": "cob_detail",
      "package_scope": "3",
      "row_grain": "cob",
      "column_mapping": {
        "object_name": "采购标的",
        "category_name": "品目名称",
        "brand": "品牌",
        "spec_model": "规格型号"
      },
      "unmapped_columns": ["备注"],
      "confidence": 0.91,
      "issues": ["points_to_attachment"],
      "status": "success"
    }
  ],
  "failures": []
}
```

## 2A.3 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `table_index` | integer | 是 | HTML 中表格序号，从 0 开始 |
| `table_role` | enum | 是 | 表格业务角色 |
| `package_scope` | string | 是 | 表格适用的包范围 |
| `row_grain` | enum | 是 | 行粒度 |
| `column_mapping` | object | 是 | 业务字段到表头的映射 |
| `unmapped_columns` | array | 是 | 未映射的表头 |
| `confidence` | number/null | 是 | 表格理解置信度 |
| `issues` | array | 是 | 表格问题标签 |
| `status` | enum | 是 | 本表理解状态 |

### `table_role` 可选值

| 值 | 含义 |
|---|---|
| `cob_detail` | 主要标的明细表，行粒度接近 COB |
| `cob_summary` | 标的汇总表，不能直接作为最细 COB |
| `sub_score` | 投标供应商与得分表 |
| `winner` | 仅列出中标/成交供应商的表 |
| `sub_score` | 同时列出投标供应商与得分的表 |
| `agency_fee` | 代理服务费表 |
| `other` | 其它表 |

### `package_scope` 可选值

| 值 | 含义 |
|---|---|
| 具体包号字符串 | 表格只属于该包 |
| `announcement` | 表格覆盖本公告多个包或公告整体 |
| `unknown` | 无法判断包归属 |

### `row_grain` 可选值

| 值 | 含义 |
|---|---|
| `cob` | 一行或一格组对应一个标的物 |
| `supplier` | 一行对应一个供应商 |
| `project` | 一行对应一个项目或包 |
| `other` | 其它粒度 |

### `issues` 可选值

| 值 | 含义 |
|---|---|
| `object_name_grain_suspect` | 采购标的疑似项目名或包名 |
| `summary_row` | 表格为汇总粒度 |
| `points_to_attachment` | 存在“详见附件”类字段 |
| `multiple_packages_in_one_table` | 一个表中混多个包 |
| `multi_value_cell` | 单元格内含多个标的或多个值 |
| `header_merge_needed` | 表头存在合并或层级 |
| `package_scope_unknown` | 包归属不明 |
| `column_mapping_uncertain` | 列映射不确定 |
| `other` | 其它问题 |

## 2A.4 列映射与调用

每张 HTML 表调用一次。`column_mapping` 的键是业务字段名，值必须是该表表头原文；对不上表头的映射丢弃。

品目按列的实际内容映射：

| 表头或单元格 | 映射 |
|---|---|
| 只有品目名称，如「其他商业保险服务」 | `category_name` |
| 只有品目编码，如「A02100499」 | `category_code` |
| 品目编号与品目名称分列，或同一格同时有编码和名称 | 编码写入 `category_code`，名称写入 `category_name` |
| 品目号，如 `1-1`、`3-1-1` | 行序号，写入 `unmapped_columns`，不映射到品目字段 |

「品目编号及品目名称」这一列在只有名称时映射到 `category_name`。同时有「报价明细内容」和「采购标的」时，`object_name` 映射报价明细内容。

---

# Step 2B：HTML 候选抽取

角色不是 `other`、`agency_fee`，且该表理解成功的，每张表调用一次。`sub_score` 与 `winner` 的 `entity_type` 为 `sub`，其余为 `cob`。表的 `package_scope` 为 `announcement` 或 `unknown` 时，传给模型的 `package_no` 为 null。输入包含该表全部行、表头、列映射和表角色。

表格以外的投标人正文再调用一次，提示词仍是 `html-candidates-v1`，`entity_type` 为 `sub`，来源优先级与 `sub_score` 相同。正文入选须满足一条：

1. 标题含「评审专家」，正文里有公司或供应商，并且有括号数字。只有专家姓名的段落不入选。
2. 标题含「未中标」，正文里有公司或供应商。

没有这类正文时不增加调用。括号里并列的多个数字是评委分数，不是综合得分，`score` 不填。

品目从该行单元格抄写，规则与第 2A 步相同。列映射没有品目时，仍抄表头里的品目名称或品目编码。没有编码或名称时不编造。

## 2B.1 输出文件

`03_html_candidates.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "success",
  "candidates": [
    {
      "candidate_id": "cand_000001",
      "entity_type": "cob",
      "project_id": "xxx|3",
      "package_no": "3",
      "source": {
        "source_type": "html",
        "file_id": null
      },
      "source_priority": 70,
      "fields": {
        "object_name": {
          "raw_value": "LCD显示大屏",
          "status": "present",
          "confidence": 0.95
        }
      },
      "issues": []
    }
  ],
  "failures": []
}
```

## 2B.2 候选通用结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `candidate_id` | string | 是 | 候选唯一 ID |
| `entity_type` | `EntityType` | 是 | COB 或 SUB |
| `project_id` | string/null | 是 | 能确定包时必填；不能确定为 `null` |
| `package_no` | string/null | 是 | 能确定包时必填 |
| `source` | object | 是 | 来源级溯源，仅包含 `source_type` 与 `file_id` |
| `source_priority` | integer | 是 | 来源优先级 |
| `fields` | object | 是 | 字段观察结果 |
| `issues` | array | 是 | 候选问题标签 |

### `source_priority` 取值（暂定）

| 值 | 来源 |
|---|---|
| `100` | 中标/成交明细表 |
| `90` | 分项报价表、开标一览表。第 6 步抽出的附件候选当前一律使用这个优先级，不按文件类型区分 |
| `80` | HTML winner 表 |
| `70` | HTML `cob_detail` 或 `sub_score` |
| `60` | HTML `cob_summary` |
| `40` | 招标需求表 |
| `30` | 其它自由文本 |

### 字段观察结构

`fields` 必须显式包含当前 `entity_type` 的全部字段键；原文没有的字段写 `missing`，不得通过省略键表示缺失。

每个字段值：

```json
{
  "raw_value": "52块",
  "normalized_value": null,
  "status": "present",
  "confidence": 0.9
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `raw_value` | string/number/boolean/null | 是 | LLM 或规则观察到的原始值 |
| `normalized_value` | any/null | 是 | Step 7 后填入；Step 2、6 时为 `null` |
| `status` | `FieldStatus` | 是 | 字段状态 |
| `confidence` | number/null | 是 | 字段置信度 |

## 2B.3 COB 可用字段键

| 字段键 | 最终类型 | 说明 |
|---|---|---|
| `object_name` | string | 标的物名称 |
| `category_code` | string/null | 品目编号 |
| `category_name` | string/null | 品目名称 |
| `category_type` | `A`/`B`/`C`/null | 货物、工程、服务 |
| `brand` | string/null | 品牌 |
| `product_supplier` | string/null | 产品供应商 |
| `spec_model` | string/null | 规格型号 |
| `unit_price` | number/null | 单价，人民币元 |
| `quantity` | number/null | 数量 |
| `unit` | string/null | 单位 |
| `total_price` | number/null | 总价，人民币元 |

## 2B.4 SUB 可用字段键

| 字段键 | 最终类型 | 说明 |
|---|---|---|
| `supplier_name` | string | 投标供应商名称 |
| `score` | number/null | 综合得分或评审总得分 |
| `is_winner` | boolean | 是否中标 |

SUB 只收录通过资格性审查的投标供应商。未通过资格审查的主体不生成 SUB 候选。

---

# Step 3：每包缺口规划

## 3.1 输出文件

`04_project_plans.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "success",
  "projects": [
    {
      "project_id": "xxx|3",
      "package_no": "3",
      "cob_candidate_ids": ["cand_000001"],
      "sub_candidate_ids": ["cand_000010"],
      "field_stats": {
        "object_name": {
          "total": 8,
          "present": 8,
          "points_to_attachment": 0,
          "missing": 0,
          "coverage": 1.0
        }
      },
      "missing_fields": ["brand", "unit_price"],
      "suspects": ["html_table_is_summary"],
      "has_attachment": true,
      "needs_attachment": true,
      "search_queries": ["第3包 分项报价", "LCD显示大屏"],
      "status": "success"
    }
  ],
  "failures": []
}
```

## 3.2 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `project_id` | string | 是 | 项目 ID |
| `package_no` | string | 是 | 包编号 |
| `cob_candidate_ids` | array | 是 | 该包 COB 候选 |
| `sub_candidate_ids` | array | 是 | 该包 SUB 候选 |
| `field_stats` | object | 是 | 字段覆盖统计 |
| `missing_fields` | array | 是 | 需要补齐的字段键 |
| `suspects` | array | 是 | 需要查附件的疑点 |
| `has_attachment` | boolean | 是 | 当前公告是否有附件 |
| `needs_attachment` | boolean | 是 | 是否需要深入附件 |
| `search_queries` | array | 是 | 附件检索关键词 |
| `status` | enum | 是 | 规划状态 |

### `field_stats` 定义

每个 COB 字段统计：

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | integer | COB 候选数 |
| `present` | integer | 状态为 `present` 的数量 |
| `points_to_attachment` | integer | 状态为 `points_to_attachment` 的数量 |
| `missing` | integer | 状态为 `missing` 的数量 |
| `coverage` | number | `present / total`，`total=0` 时为 `0` |

`points_to_attachment` 不计入 `coverage`。`field_stats` 只统计 COB 字段，不统计 SUB 字段。

### `suspects` 可选值

| 值 | 含义 |
|---|---|
| `html_table_is_summary` | HTML 表为汇总粒度 |
| `object_name_grain_suspect` | 标的名称疑似项目或包名 |
| `partial_cob_list` | HTML 只列出部分标的 |
| `multi_value_cell` | HTML 单元格含多个值 |
| `package_scope_unknown` | 候选包归属不明 |
| `field_points_to_attachment` | 字段指向附件 |
| `no_cob_candidate` | 没有任何 COB 候选 |

## 3.3 `needs_attachment` 判定

当 `has_attachment=true` 且满足任一条件时为 `true`：

1. 任一 COB 字段状态为 `points_to_attachment`。
2. 任一 HTML 表格为 `cob_summary`。
3. 任一候选存在 `object_name_grain_suspect`。
4. 任一候选存在 `partial_cob_list`。
5. `object_name.coverage < 1`。
6. 官方七字段中任一字段 `coverage < 0.3`。
7. 没有 COB 候选。

官方七字段：

```text
object_name
category_name
brand
spec_model
unit_price
quantity
total_price
```

---

# Step 4：附件文件级筛选

## 4.1 输入

来自附件索引的文件清单与 Step 3 的项目缺口。对可读文件生成探查摘要。PDF 先读 `work/attachments-md/` 中同相对路径的 Markdown；没有 Markdown 时调用 `ICT_PADDLE_COMMAND`。命令为空则本次探查可读性为 `unknown`，原因为 `paddle_not_connected`，不使用 PDF 文本层代替。`first_pages` 取前 3 页，每页 `text_head` 为前 500 个字符，并带上该页表头。

```json
{
  "file_id": "a001",
  "display_name": "开标记录表.pdf",
  "page_count": 86,
  "text_density": 0.42,
  "readability": "text_extractable",
  "first_pages": [
    {
      "page_no": 1,
      "text_head": "前500字摘要",
      "table_headers": [
        ["供应商", "投标报价", "工期"]
      ]
    }
  ]
}
```

## 4.2 输出文件

`05_file_decisions.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "success",
  "file_decisions": [
    {
      "file_id": "a001",
      "file_class": "bid_quote",
      "expected_fields": ["supplier_name", "total_price"],
      "possible_packages": ["3"],
      "priority": 0.9,
      "read_strategy": "target_pages",
      "reason": "文件名和首页表头均指向开标报价",
      "failure_code": null,
      "failure_message": null
    }
  ],
  "failures": []
}
```

## 4.3 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `file_id` | string | 是 | 附件文件 ID |
| `file_class` | enum | 是 | 文件业务类型 |
| `expected_fields` | array | 是 | 预期能提取的字段键 |
| `possible_packages` | array | 是 | 可能涉及的包号或范围 |
| `priority` | number | 是 | 深读优先级，0 到 1 |
| `read_strategy` | enum | 是 | 读取策略 |
| `reason` | string | 是 | 决策理由 |
| `failure_code` | `FailureCode`/null | 是 | 失败码 |
| `failure_message` | string/null | 是 | 失败说明 |

### `file_class` 可选值

| 值 | 含义 |
|---|---|
| `award_detail` | 中标/成交明细 |
| `bid_quote` | 分项报价或开标一览 |
| `winner_detail` | 中标供应商明细 |
| `tender_requirement` | 招标需求或采购需求 |
| `qualification` | 资质证明 |
| `contract` | 合同 |
| `evaluation` | 评审结果 |
| `unrelated` | 与目标字段无关 |
| `unknown` | 无法判断 |

### `read_strategy` 可选值

| 值 | 含义 |
|---|---|
| `skip` | 不读取 |
| `target_pages` | 进入页面级定位 |
| `unsupported` | 主路线不支持，只记录失败 |

当前主路线不设置 `inspect_more`。若文件前几页不足以下判断，先按 `unknown` 并进入页面索引；仍无法定位则记录失败。

## 4.4 选择规则

1. 只对 `read_strategy=target_pages` 的文件进入 Step 5。
2. 每个项目默认最多选择 20 个文件。
3. `priority` 相同时，优先 `award_detail`、`bid_quote`。
4. `readability=low_text` 或 `unsupported` 的文件不进入主路线深读。

---

# Step 5：附件页面级定位

## 5.1 页面索引输入

对被选文件建立页面索引。输入是 Markdown 页首和表头，不是 PDF 原件。字符少于 20 且没有表头的页不送入。每个文件最多送 80 页。一次调用覆盖本公告全部深读文件，并带上第 1 步确认的 `known_packages`。

```json
{
  "known_packages": ["1", "3"],
  "search_queries": ["LCD显示大屏"],
  "files": [
    {
      "file_id": "a001",
      "display_name": "分项报价表.pdf",
      "possible_packages": ["3"],
      "pages": [
        {
          "page_no": 12,
          "source": "markdown",
          "text_head": "前300字摘要",
          "table_headers": [
            ["货物名称", "品牌", "型号", "数量", "单价"]
          ]
        }
      ]
    }
  ]
}
```

没有 Markdown 且 Paddle 未接入或转换失败时，该文件记 `no_text_extractable`，不进入本步的页面列表。

## 5.2 输出文件

`06_page_decisions.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "success",
  "page_decisions": [
    {
      "file_id": "a001",
      "page_no": 12,
      "relevance": 0.95,
      "expected_fields": ["object_name", "brand", "spec_model", "unit_price"],
      "package_scope": "3",
      "extraction_mode": "text_and_table",
      "reason": "页标题为第3包分项报价表",
      "failure_code": null,
      "failure_message": null
    }
  ],
  "failures": []
}
```

## 5.3 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `file_id` | string | 是 | 附件文件 ID |
| `page_no` | integer | 是 | 页码，从 1 开始 |
| `relevance` | number | 是 | 页面相关性，0 到 1 |
| `expected_fields` | array | 是 | 预期抽取字段 |
| `package_scope` | string | 是 | 具体包号、`announcement` 或 `unknown` |
| `extraction_mode` | enum | 是 | 抽取模式 |
| `reason` | string | 是 | 选择理由 |
| `failure_code` | `FailureCode`/null | 是 | 失败码 |
| `failure_message` | string/null | 是 | 失败说明 |

### `extraction_mode` 可选值

| 值 | 含义 |
|---|---|
| `text` | 以页面文本为主 |
| `table` | 以表格结构为主 |
| `text_and_table` | 同时使用文本和表格 |
| `unsupported` | 主路线不支持 |

## 5.4 主路线限制

1. 每个文件默认最多选择 30 页。
2. 每个公告默认最多选择 100 页。
3. `readability=low_text` 的页面记录 `scanned_or_low_text_pdf`，不进入 Step 6。
4. 图片页面记录 `unsupported_image`，不进入 Step 6。

## 5.5 包范围收束

`package_scope` 只能是 `known_packages` 中的编号、`announcement` 或 `unknown`。模型返回后按这个顺序改写，命中即停止：

1. 返回值已经在 `known_packages` 里，或为 `announcement`，保持不变。
2. 该文件的 `possible_packages` 里只有一个编号落在 `known_packages` 中，改成该编号。
3. 文件名里恰好一个包号，且该包号在 `known_packages` 中，而 `possible_packages` 没有公告内包号，改成文件名包号。
4. 返回值不在公告包列表里，且公告只有一个包，改成那个包。`unknown` 不触发这一条。
5. 其余情况改为 `unknown`。

因此页首没写包号、但文件只对应一个公告内的包时，不保留 `unknown`。公告只有一个包时，模型写出的其他编号会收成这个包。一个文件同时对应多个公告内的包、页面又没有写出其中某一个时，可以仍是 `unknown`。

---

# Step 6：附件局部抽取

## 6.1 LLM 输入

按页面决定上的 `package_scope` 分组。每一组调用一次，只传入该组页面。每组最多 12 页。页面文本最长 6000 字，每页最多 4 张表。`unknown` 与 `announcement` 各自成组，传给模型的 `package_no` 为 null。不传入 `known_cobs` 或 `known_subs`。

```json
{
  "current_package": {
    "project_id": "xxx|3",
    "package_no": "3",
    "missing_fields": ["brand", "spec_model", "unit_price"]
  },
  "page_contexts": [
    {
      "file_id": "a001",
      "file_name": "分项报价表.pdf",
      "page_no": 12,
      "text": "页面文本",
      "tables": [
        {
          "table_index": 2,
          "headers": ["货物名称", "品牌", "型号", "数量", "单价"],
          "rows": [
            ["LCD显示大屏", "创维", "SKY55SXEPS", "52块", "15750"]
          ]
        }
      ]
    }
  ]
}
```

不得把全量附件目录或其它包的页面放进同一次调用。品目抄写规则与第 2B 步相同。

模型没有给出 `package_no` 时，先用本组的包号；本组包号也为空时，文件名里恰好有一个包号则用文件名。附件候选的 `source_type` 当前一律记为 `pdf`，`source_priority` 一律为 90。

## 6.2 输出文件

`07_attachment_extraction.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "success",
  "candidates": [
    {
      "candidate_id": "cand_000101",
      "entity_type": "cob",
      "project_id": "xxx|3",
      "package_no": "3",
      "source": {
        "source_type": "pdf",
        "file_id": "a001"
      },
      "source_priority": 90,
      "fields": {
        "object_name": {
          "raw_value": "LCD显示大屏",
          "normalized_value": null,
          "status": "present",
          "confidence": 0.96
        }
      },
      "issues": []
    }
  ],
  "page_quality": [
    {
      "file_id": "a001",
      "page_no": 12,
      "readability": "text_extractable",
      "failure_code": null
    }
  ],
  "failures": []
}
```

## 6.3 `page_quality` 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `file_id` | string | 是 | 附件文件 ID |
| `page_no` | integer | 是 | 页码 |
| `readability` | enum | 是 | `text_extractable`、`low_text`、`parse_failed`、`unsupported` |
| `failure_code` | `FailureCode`/null | 是 | 页面级失败码 |

## 6.4 来源级溯源

候选层不记录行级或单元格级证据，只记录候选来源：

```json
{
  "source_type": "pdf",
  "file_id": "a001"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `source_type` | enum | 是 | `html`、`pdf`、`docx`、`doc`、`xlsx` |
| `file_id` | string/null | 是 | HTML 为 `null`；附件为对应 `file_id` |

抽取提示词必须显式要求模型：

1. 同一行或同一段文本包含多个实体时拆分为多个候选。
2. 不重复输出同一实体。
3. 字段值必须来自当前输入上下文，不得凭空推断。

## 6.5 输出约束

1. 只提取当前包；页面含其它包时不得输出。
2. 无法确定包号的候选 `project_id=null`、`package_no=null`，并加 `package_scope_unknown`。
3. 当前输入上下文中没有出现的字段必须为 `missing`，不得猜测。
4. 原文写“详见附件”时，状态仍为 `points_to_attachment`，业务值为 `null`。
5. 一行含多个标的时必须拆成多个候选。
6. 联合体供应商必须整体输出，不得拆分。

---

# Step 7：候选规范化

## 7.1 输出文件

`08_normalized_candidates.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "partial",
  "candidates": [
    {
      "candidate_id": "cand_000101",
      "entity_type": "cob",
      "project_id": "xxx|3",
      "package_no": "3",
      "source": {
        "source_type": "pdf",
        "file_id": "a001"
      },
      "source_priority": 90,
      "fields": {
        "unit_price": {
          "raw_value": "15750",
          "normalized_value": 15750,
          "status": "present",
          "confidence": 0.98,
          "normalization": {
            "currency": "CNY",
            "unit": "yuan",
            "multiplied_by_10000": false
          }
        }
      },
      "validation_errors": [],
      "warnings": []
    }
  ],
  "failures": []
}
```

## 7.2 规范化规则

### 价格

| 规则 | 输出 |
|---|---|
| 去掉 `￥`、逗号、空白 | 数值 |
| 括号负数 | 负数 |
| 单元格或该列明确“万元” | 原数乘以 10000 |
| 附近其它单元格写“万元”但本格/本列未写 | 不换算 |
| 缺总价且有单价、数量 | 总价 = 单价 × 数量 |
| 缺数量 | 不由总价倒推 |

价格 `normalized_value` 单位固定为人民币元。

名称能唯一对上采购目录时，补上缺失的 `category_code`、`category_name` 和 `category_type`。同一名称对应多条目录时不补。公告概要里的品目不往标的行上填。

### 品目

品目匹配附加结构：

```json
{
  "normalized_value": "A01010100",
  "normalization": {
    "match_type": "exact_code",
    "matched_code": "A01010100",
    "matched_name": "办公用房",
    "matched_level": 3,
    "leaf": false
  }
}
```

### `match_type` 可选值

| 值 | 含义 |
|---|---|
| `exact_code` | 精确编码 |
| `exact_path` | 精确路径 |
| `exact_name` | 精确名称 |
| `normalized_name` | 归一化后名称 |
| `parent_name` | 上级名称，只写到该级 |
| `fuzzy` | 模糊匹配，需人工复核 |
| `unmatched` | 未匹配 |

### 供应商

1. `supplier_name` 去除首尾空白。
2. 联合体整体保留。
3. 未通过资格性审查的主体不生成 SUB 候选。
4. `score` 只接受综合得分或评审总得分；只有技术分、商务分时为 `null`。

### 数量与单位

1. `52块` 规范化为 `quantity=52`、`unit="块"`。
2. `1(套)` 规范化为 `quantity=1`、`unit="套"`。
3. 无法拆分时 `quantity=null`，原始值保留在 `raw_value`。

## 7.3 校验错误标签

| 值 | 含义 |
|---|---|
| `invalid_number` | 数值无法解析 |
| `ambiguous_price_unit` | 万元/元单位不明 |
| `negative_price_unexpected` | 不应出现负数的价格 |
| `category_code_not_found` | 品目编码不存在 |
| `category_conflict` | 编号与名称冲突 |
| `missing_object_name` | COB 缺名称 |
| `missing_supplier_name` | SUB 缺名称 |
| `package_not_found` | 候选包号不在 Step 1 包列表 |
| `quantity_unit_parse_failed` | 数量单位无法拆分 |
| `total_price_mismatch` | 原文总价与单价×数量明显不一致 |

---

# Step 8：候选合并与去重

## 8.1 输出文件

`09_merged_projects.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "status": "partial",
  "projects": [
    {
      "project_id": "xxx|3",
      "source_project_no": "SDGP123",
      "project_name": "xxx",
      "package_no": "3",
      "purchaser": "xxx",
      "package_total_amount": 1000000,
      "cobs": [
        {
          "object_name": "LCD显示大屏",
          "category_code": null,
          "category_name": "LCD显示大屏",
          "category_type": "A",
          "brand": "创维",
          "product_supplier": null,
          "spec_model": "SKY55SXEPS",
          "unit_price": 15750,
          "quantity": 52,
          "unit": "块",
          "total_price": 819000
        }
      ],
      "subs": [
        {
          "supplier_name": "xxx",
          "score": 98.5,
          "is_winner": true,
          "cooperative_product_suppliers": []
        }
      ],
      "provenance": {
        "cob_candidate_ids": ["cand_000101"],
        "sub_candidate_ids": ["cand_000110"]
      }
    }
  ],
  "conflicts": [
    {
      "field": "brand",
      "candidate_ids": ["cand_000001", "cand_000101"],
      "values": ["联想", "Lenovo"]
    }
  ],
  "unmatched_summary_rows": [
    {
      "candidate_id": "cand_000001",
      "reason": "HTML 汇总行未在附件明细中找到对应 COB"
    }
  ],
  "unassigned_candidates": [
    {
      "candidate_id": "cand_000102",
      "reason": "无法确定包号"
    }
  ],
  "failures": []
}
```

合并异常字段定义：

| 字段 | 类型 | 说明 |
|---|---|---|
| `conflicts[].field` | string | 冲突字段键 |
| `conflicts[].candidate_ids` | array | 冲突候选 ID |
| `conflicts[].values` | array | 各候选同一字段的值；在 Step 7 之前为原始值，Step 7 之后优先使用规范化值 |
| `unmatched_summary_rows[].candidate_id` | string | 未匹配到附件明细的 HTML 汇总候选 |
| `unmatched_summary_rows[].reason` | string | 未匹配原因 |
| `unassigned_candidates[].candidate_id` | string | 无法确定包归属的候选 |
| `unassigned_candidates[].reason` | string | 无法归属原因 |

## 8.2 合并顺序

1. 同一 `project_id` 内按业务键合并，不区分候选来自 HTML 还是附件。
2. `package_no` 为空，或包号不在第 1 步包列表中的候选，记入 `unassigned_candidates`，不参与合并，也不把字段补到同名的其它行上。
3. COB 业务键：

```text
object_name + brand + spec_model + quantity + unit
```

4. SUB 业务键：

```text
supplier_name
```

5. 高 `source_priority` 候选优先；同分时优先字段完整度更高的候选，仍同分时保留先生成的候选。
6. 低优先级候选只补空字段，不覆盖非空字段。
7. 冲突字段记录到 `conflicts`。

## 8.3 最终 Project 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `project_id` | string | 是 | 项目名称 + 包编号 |
| `source_project_no` | string/null | 是 | 公告原文项目编号，仅来源 |
| `project_name` | string | 是 | 项目名称 |
| `package_no` | string | 是 | 包编号 |
| `purchaser` | string/null | 是 | 采购单位 |
| `package_total_amount` | number/null | 是 | 包金额，人民币元 |
| `cobs` | array | 是 | 标的物列表 |
| `subs` | array | 是 | 投标供应商列表 |
| `provenance` | object | 是 | 内部溯源信息，导出正式结果时可去除 |

## 8.4 金额语义

| 场景 | `package_total_amount` |
|---|---|
| 单包公告 | 可使用公告概要总金额 |
| 多包公告且有包级金额 | 使用包级金额 |
| 多包公告无包级金额 | `null` |
| 任意场景 | 不得把公告总金额摊到多个包 |

多包公告优先使用 Step 1 中的 `package_amount`。若该包没有 `package_amount`，`package_total_amount` 为 `null`。

## 8.5 `cooperative_product_suppliers`

仅对 `is_winner=true` 的 SUB 生成：

```text
该包全部 COB 的 product_supplier 并集
```

规则：

1. 去掉 `null`。
2. 去重。
3. 中标供应商自己也是产品供应商时保留。

---

# Step 9：入库与运行报告

## 9.1 输出文件

`10_run_report.json`

```json
{
  "run_id": "run_t20260202_26139731",
  "announcement_id": "t20260202_26139731",
  "status": "partial",
  "duration_ms": 14500,
  "counts": {
    "projects": 3,
    "files": 12,
    "selected_files": 4,
    "selected_pages": 9,
    "html_candidates": 18,
    "attachment_candidates": 26,
    "normalized_candidates": 40,
    "final_cobs": 15,
    "final_subs": 9
  },
  "llm_calls": {
    "understand_announcement": 1,
    "understand_html_tables": 5,
    "extract_html_candidates": 5,
    "triage_files": 1,
    "locate_pages": 2,
    "extract_attachment_candidates": 3
  },
  "failure_summary": [
    {
      "failure_code": "scanned_or_low_text_pdf",
      "count": 2
    }
  ],
  "review_required": true
}
```

## 9.2 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `status` | `RunStatus` | 是 | 最终运行状态 |
| `duration_ms` | integer | 是 | 全流程耗时 |
| `counts` | object | 是 | 计数器 |
| `llm_calls` | object | 是 | 每类 LLM 调用次数 |
| `failure_summary` | array | 是 | 失败码汇总 |
| `review_required` | boolean | 是 | 是否需要人工复核 |

---

## 10. 主路线状态流转表

| 步骤 | 前置条件 | 输出 | 失败时处理 |
|---|---|---|---|
| Step 1 | 有 HTML | `01_announcement_understanding.json` | `package_mode=unclear` 时 run 终止为 `failed` |
| Step 2A | Step 1 为 `single` 或 `multi` | `02_html_tables.json` | 单表失败不影响其它表，步骤记 `partial` |
| Step 2B | 至少一个表可抽取 | `03_html_candidates.json` | 无候选时步骤记 `failed`，但可继续查附件 |
| Step 3 | Step 1、2 完成 | `04_project_plans.json` | 无附件且缺字段时 run 记 `partial` |
| Step 4 | `needs_attachment=true` 且附件索引存在 | `05_file_decisions.json` | `needs_attachment=false`、无附件或索引缺失时步骤记 `skipped`，索引缺失另记 `attachment_index_miss` |
| Step 5 | 有 `target_pages` 文件 | `06_page_decisions.json` | 无 `target_pages` 文件时步骤记 `skipped`；有文件但无页面选中时记 `partial` |
| Step 6 | 有可读页面 | `07_attachment_extraction.json` | 无可读页面时步骤记 `skipped`；单页失败不影响其它页面 |
| Step 7 | 有候选 | `08_normalized_candidates.json` | 规范化错误记录在候选上 |
| Step 8 | 有规范化候选 | `09_merged_projects.json` | 冲突未解决时 run 记 `partial` |
| Step 9 | Step 8 完成 | `10_run_report.json` | 输出最终状态和失败汇总 |

---

## 11. 后续分支建立点

主路线跑通后，应按失败码和人工复核结果决定分支优先级：

| 失败码/疑点 | 可能分支 |
|---|---|
| `scanned_or_low_text_pdf` | OCR 分支 |
| `unsupported_image` | 图片识别分支 |
| `watermark_interference` | 水印处理分支 |
| `package_structure_unclear` | 包结构二次理解分支 |
| `package_scope_unknown` | 候选包归属推断分支 |
| `partial_cob_list` | HTML 部分列表与附件完整列表合并分支 |
| `multi_value_cell` | 复合单元格拆分分支 |
| `merge_conflict_unresolved` | 冲突裁决分支 |
| `llm_schema_invalid` | 输出重试与提示词修复分支 |

这些分支不在当前主路线中实现。主路线只负责准确记录它们出现的次数、样本和失败位置。
