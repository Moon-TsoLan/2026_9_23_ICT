# 主路线多步 Agent 设计契约

版本：v1.2，2026-10-13，已实施于当前工作树。授权：用户“按照上一轮对话的规划，进行流程修改”“本轮先全量优化完成”；禁止本轮 API 与 OCR 测试。完整衔接见[官方兼容规范](官方三表兼容与流程衔接-20261013.md)。v1.1见[历史快照](archive/主路线多步Agent设计契约-v1.1-20261013前.md)。
最高原则见 [AGENTS](../AGENTS.md)；本次授权之外的规则继续冻结。
旧 v1.0 已归档为 [历史快照](archive/主路线多步Agent设计契约-v1.0-20261012前.md)，不再作为当前接收、金额和逐字段采用契约。

## 1. 边界与责任

处理一则公告与它自己的附件，形成按采购包划分的 COB、SUB，并供检索、供应关系探索。
标段和合同包仍放在同一 packages 数组，但保留不同类型与原文名称；未分包单独明确，不能由单个单元推断。内容判断交模型，程序负责容器、来源编号、集合引用、资格校验和计算。
不新增案例编号分支、服务词表、单位同义词表、阈值或正文检索词筛附件。
不处理已撤案的食材预算与报价比例换算特例。

扫描件、图片和复杂容器沿既有解析链路处理，不能再概括成“主路线只支持原生文本”。
是否支持具体容器仍由现有转换和解析能力决定；失败必须记录，不能伪造抽取结果。
本次没有改 OCR 模型、解析端点、页预算、并发旋钮或两个文件名硬忽略词。

## 2. 数据层次

- 公告：`run_id` 对应一次运行，确认存在的包集合由第 1 步理解。
- 包：`project_id` 是当前项目名与包号构成的键，`package_no` 为字符串内部引用，新增 package_kind、package_name、source_package_no。多包金额、COB、SUB 归属由统一模型步骤裁定，展示顺序不等于包顺序。
- 候选行：一条独立观测，不等于一个最终 COB。同名行可以是明细、重复或不同规格；无名行有独立临时组。
- 证据：HTML 表/行、正文段、附件页/行或原生片段的机械编号。`raw_value` 保留原文，`normalized_value` 承载规范值；编号不是模型可新造的业务内容。
- 输出组：模型认可的输出粒度。成员关系、数量是否累加、各字段从谁取值分别表达。
- 最终 COB：必须有可支持的名称。仍缺名的组进入 `unresolved`，不能输出字符串“None”或自行用项目名补位。

公共 `projects.json` 为公告对象：`schema_version=announcement-v2`、announcement_id、project_name、source_project_no、purchaser、announcement_type、summary_amount、packages。公告总额仅来自 HTML，packages 内每项仍是一个包的 Project。09 的 Project/Sub 增加兼容字段；数据库表结构保持原样，raw_json 保存完整成品和 DB ID 映射，图中仍每采购单元一个项目节点。内存 report.result 和 service.result 返回公告对象；report.projects/service.projects 保留包数组兼容入口。
候选未进入字段契约的原值保存在 `unmapped_fields`，不能静默裁掉或自动改作其它层级金额。

`Candidate.binding` 留下原建议与当前归属；`Candidate.evidence` 留下 `table_index`、`row_index`、`row_text`、`source_refs` 和报价主体。
同文件多页不能猜第一张页；来源引用与显式文件/页号冲突时不能接受。

## 3. 执行顺序与产物

| 阶段 | 产物 | 职责 |
|---|---|---|
| 1 公告理解 | `01_announcement_understanding.json` | 包集合、概要品目和金额原文 |
| 2A 表理解 | `02_html_tables.json` | 表角色、列映射、范围建议；既有表提升规则保留 |
| 2B HTML 抽取 | `03_html_candidates.json` | 原始观测与确切行/段来源，名称为空也保留 |
| 2C 统一包归属 | `03b_package_bindings.json` | 校正全部候选及包成交金额归属，含 effective_understanding |
| 3 缺口规划 | `04_project_plans.json` | COB 原缺口保持；新增逐 SUB 报价/审查/原因缺口和供应商名单发现，接通附件开关 |
| 4b 文件目检 | `05_file_decisions.json` | 既有 needs_hit 门；保存模型引用的原生片段 |
| 4b 后原生复用 | `05c_native_extraction.json` | 复用正式抽取器，把已看见的文本变成候选 |
| 4c 解析 | `05b_parsed_pages.json` | 仍需深度解析的文件走既有服务 |
| 5 页定位 | `06_page_decisions.json` | 模型按缺口判断摘要；范围仅为任务切分 |
| 6 附件抽取 | `07_attachment_extraction.json` | 汇集原生及深度解析候选、包金额性质和来源 |
| 7 规范化 | `08_normalized_candidates.json` | 原文字段归一化、价格计算、目录查表；缺名待合并 |
| 8 合并 | `09_merged_projects.json` | delta-v5 事务编译、逐字段采用、计算依赖；包内多 SUB 另作身份与角色判断 |
| 8b 检查修复 | 同上 | 保留检查；不得暗改已绑定价格或恢复模型明确否定的 SUB 中标身份 |
| 合并后供应方判断 | 同上 | 仅对品牌与产品供应商都空的最终 COB 问模型 |
| 落盘报告 | `10_run_report.json`、`projects.json` | 公共结果与运行状态 |

进度注册共 15 个阶段。`08b_merge_evidence.json` 是只读证据通道，不另算一步。
原生或深度附件新观测加入后，复用同一个包归属组件重新裁定；03b 保留 previous 审计。
附件金额单独到达、没有新候选时也进入归属组件；`observation_bindings` 逐一覆盖金额观测。归属失败或未决的金额不能按原建议供值。
01/03 是当时原始输出；07/08 与 03b 的 effective_understanding 是后续有效输入，不能误以为所有早期文件都已被回写。

## 4. 缺口与原生证据复用

needs 在原有 field/package_no/reason 上增加：

- `need_id`：由采购单元、字段、原因及已知供应商目标组成的稳定引用；target_entity 区分 COB 与 SUB。
- `purpose`：`fill_field`、`discover_objects`、`resolve_binding`。
- `target_candidate_ids`：补已有对象时点名；发现清单任务不能冒充已有字段补全。
- `state`：`pending` 或 `evidence_ready`，后者表示抽取证据就绪，最终仍由第 8 步取值。

文本目检只引用实际给模型看见的 `source_segments`，不能引用未展示的页。
程序验证原生抽取确有对应字段、同包、目标在缺口列表中；SUB 还须给唯一来源片段对应目标，未知业务值不能关闭缺口；一个缺口涉及多个目标时必须逐一覆盖。
只有相关补字段缺口都有抽取证据，且目检模型明确建议 `requires_deep_parse=false`，才跳过该文件后续解析。
发现更多标的或供应商任务仍保持 pending；跳过一个文件不宣称全公告清单完整。扫描件和未覆盖缺口继续原链路。

原生输出区分 candidates_extracted、amounts_only、attempted_empty、failed、not_attempted，并记录接收尝试。已读但未形成候选的片段及未决状态传入页定位和合并证据，不能因后续未选中同页就消失；它们不被强制并入选页，也不自动成为供值候选。

## 5. 金额与字段

金额层级与性质分别记录。公告 Amount 有 scope 与 amount_kind；附件包金额 scope=package。性质为 award、budget、ceiling、unknown，缺失默认为 unknown。预算与最高限价原样留证据，不供成交价。
SUB 新增 bid_amount 取最终投标/响应报价，review_status 和 review_failure_reason 保存审查事实；未通过主体保留。所有金额内部元，前端和三表 XLSX 导出换万元四舍五入两位。SUB 独立字段 `supplier_award_amount` 仅承载明确归属当前供应商的成交金额或份额；不共用 COB total_price，不复制包金额给每家供应商。包金额仍使用 package_total_amount。已选定单价及计价数量可靠、总价为空且未被最终组禁止时，程序计算总价并记录 operands、来源与公式。
招标需求能为已有成交对象供名称和描述，不能凭需求清单新增成交对象，也不能供成交计量值。
模型不能编写金额、数量；只能指定来源或求和成员，数值由现有计算器产生。
品目编码与名称复用 2022 目录查表规范化，保留原文；已有冲突不靠新增阈值解决。
规格字段中的定制、国标、按照招标要求提供等原文写法保留；不从别的层级范围、时间或点位参数拼入当前对象规格。
具体 delta 协议见 [第8步规范](第8步标的合并规范.md)。

## 6. 品牌、产品供应商和中标供应商

检索、详情、官方导出使用品牌原值，缺失为空，产品供应商不能补品牌。03 探索可用品牌替代缺失产品供应商并标明独立品牌身份，不覆盖存储原值。
S1 优先使用产品供应商；其为空而品牌存在时用品牌替代节点连到采购单位。
最终两者都空时，合并与修复之后询问模型；只有 confirm 且引用本包已确认 winner_ref 才填产品供应商。
拒绝、未知、调用失败不填；字段显式禁止推断时不再补回。
推断记 `supplier_inferences` 与 `field_bindings.method=inferred`，页面详情注明推断来源。

企业身份与中标/供应角色分离：同一企业一个 `sup:` 节点可以有两种角色；品牌使用独立 `brand:` 命名空间。
数据库不新增同名企业记录；产品供应商仍是 COB 字段。审计放入公告 raw_json，不需迁移表结构。
S1 合作次数按主体与 project_id 去重，中标金额与供应标的金额分别展示，不相加冒充采购总额。

## 7. 失败与验收

结构/协议错误与非法引用、重复计数、资格绕过、组消耗后引用等执行约束分别标 schema 与 plan_constraint，可修复一次；带依赖的 delta 整包校验，失败事务回滚。重试携带上次原回答及具体错误，不能把独立合法信息当作不存在。
原文字段不可用与业务冲突不要求完整重抽；字段解释争议保留 unresolved。校验器内部异常标 implementation，不归咎模型、不重试。合并不可执行的业务计划保留明确回退与 partial，不宣称已接受其字段决定。
附件候选、包金额、need_resolutions 按独立条目接收。合法条目在修复前后保留；错误项含原 JSON、通道、路径和尝试编号写 reception。无包号金额不猜成公告金额，留接收审计；不把这一策略用于拆接互相依赖的 delta。
回退明确记 partial/unresolved，不能当成模型成功。无模型路径保留旧语义用于基线验证。
描述冲突、未解释计量观测、缺名及包归属未决留痕，不静默丢弃。
提示词字段定义唯一来源仍是 fields.py；公告/HTML/页定位/附件 v4，包归属 v3，目检 v3，标的合并 v6（执行协议仍 delta-v5），供应商合并 v2，自供保持 v1。旧回答只用于离线执行语义核验。
本地固定回答验证的是执行语义，真实模型是否正确决策必须另做人工标注集与留出集回归。
标注规范见 [字段标注与评测口径](字段标注与评测口径.md)，运行说明见 [实现细则](../eval/主路线实现细则.md)。
