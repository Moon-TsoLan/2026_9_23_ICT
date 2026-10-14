"""Field meanings for the model, in one place.

Each step declares which payload fields it sends and which fields it expects back;
`llm.load_prompt` appends exactly those definitions to the prompt text. A field therefore has
one wording everywhere and can never be repeated inside a single prompt.

Definitions only: what a value is, and what it is not. No implementation aliases, no advice
about how to reason.
"""

FIELD_SEMANTICS = {
    "object_name": "当前成交输出单元的标的名称：货物、服务或工程；整体服务确实以项目全称命名时可用原文，不为无名行猜填项目名。",
    "category_name": "品目类别名，如“工业机器人”“其他商业保险服务”；不是标的名称。",
    "category_code": "品目目录编码：一个字母加八位数字，如 A02100499。",
    "category_type": "品目大类：A 货物、B 工程、C 服务。",
    "brand": "品牌，如“佳能”“达梦”。",
    "product_supplier": "该标的产品或服务提供方，可为制造商、明确产品供应方或服务提供方；不能仅凭投标/中标身份认定。末尾模型确认的中标人自供须单独留痕。",
    "spec_model": "属于当前标的输出粒度的规格型号或技术参数标识；服务范围、要求、时间、标准不算，其它组成或点位参数不自动属于整体服务。",
    "unit_price": "单价，即每个计价单位的金额。",
    "quantity": "数量，纯数值；人数、面积、点位数等不是计价数量。",
    "unit": "计价单位，如台、套、项、人。",
    "total_price": "当前最终标的的中标成交合计金额。抽取时只抄该标的成交金额，预算、最高限价、报价模板不填；合并时模型确认整体成交对象与本包金额对应后，可显式引用 p:amount；程序计算值单独记录依赖。",
    "supplier_award_amount": "明确归属于本包当前供应商的中标成交金额或中标份额；不是 COB 总价，不把包金额复制给每家供应商。未写明归属时留空。",
    "package_total_amount": "某个包的中标（成交）金额；不是单个标的行的总价，也不是公告层面的项目总额。",
    "supplier_name": "投标（响应）供应商全称；联合体作为一个整体，不拆分。",
    "score": "综合得分或评审总得分；多位评委各自打的分数不算。",
    "is_winner": "该供应商在本包内是否为中标（成交）供应商；名次、排名、序号不是它。",
    # step-level outputs
    "project_name": "项目名称，公告里的完整项目名。",
    "purchaser": "采购单位（采购人）名称。",
    "source_project_no": "公告原文的项目编号，只作来源标记。",
    "announcement_type": "公告类型，取值 winning_announcement 中标（成交）公告、deal_announcement 结果公告、unknown 无法判断。",
    "package_mode": "包结构，取值 single 单包、multi 多包、unclear 判不清。",
    "packages": "包列表，每项含 package_no、title、package_evidence_text、package_amount。",
    "package_no": "包编号本身，必须是字符串，不含“第”“包”等前后缀；用原文里的数字或字母，不把 A 转成 1，不补前导零。",
    "package_amount": "该包的中标（成交）金额，含 raw_text、amount_yuan、scope、amount_kind、confidence；scope 固定为 package，amount_kind 必须为 award。",
    "amount_alternatives": "同一包里没有选作 package_amount 的其它金额，每项含 raw_text、amount_yuan、scope、amount_kind、confidence；保留原文及性质，不能混为成交价。",
    "package_evidence_text": "证明该包存在的原文片段。",
    "summary_amount": "仅从 HTML 抽取的公告层项目总额，含 raw_text、amount_yuan、scope、amount_kind、confidence；scope 固定为 announcement。层级与预算/限价/成交性质分别记录，不能从附件补公告总额。",
    "announcement_categories": "公告概要一行里写出的品目条目，一项一条，逐字照抄；可能是编码（C16990000）、"
                              "品目名称或带斜杠的目录路径，也可能是空的。只抄公告概要那一行，正文表格里的品目不算。",
    "unclear_reason": "判不清包结构时的原因说明。",
    "table_role": "这张表的作用，取值 cob_detail 标的明细、cob_summary 标的汇总、sub_score 供应商得分、winner 中标信息、agency_fee 代理服务费、other 其它。",
    "row_grain": "这一行代表什么，取值 cob 一个标的、supplier 一个供应商、project 一个项目、other 其它。",
    "package_scope": "这段内容属于哪个包；只写编号本身，不带“第”“包”等前后缀；整篇范围写 announcement，判不出写 unknown。",
    "column_mapping": "业务字段名到表头原文的对应关系；键只能取 object_name、category_name、category_code、brand、spec_model、quantity、unit、unit_price、total_price、supplier_name、score、is_winner、supplier_award_amount，值必须是给到的表头原文。",
    "unmapped_columns": "无法对应到任何业务字段的表头原文。",
    "issues": "你在这一步注意到的问题标记。",
    "candidates": "抽取出的候选对象列表。",
    "package_amounts": "页面原文单独写出的包级金额观测，每项含 package_no、raw_text、amount_kind 和来源；区分 award 成交金额与 budget 预算，行项金额不当作包金额，预算仅留证据。",
    "raw_text": "金额的原文写法，连单位与符号一起照抄，不要换算、不要补零、不要改写成数字。",
    "fields": "该候选的字段值，只写看到的，不写的即为没有。",
    "entity_type": "要抽取的对象类型，取值 cob 标的、sub 供应商；与用户给定的保持一致。",
    "kind": "该文件内容的类型，取值 award_detail 中标（成交）明细表、bid_quote 投标（响应）报价明细或分项报价表或报价一览、winner_detail 供应商与标的的对应表、tender_requirement 采购需求、技术规格、商务条款、评分办法、qualification 资格证明、声明函、承诺函、业绩与信用材料、contract 合同文本、other 文件目录、封面、通知、发票、报酬支付、盖章扫描件。",
    "has_price_table": "该文件里是否有带金额的行项表格。",
    "header": "表头行前三个格子的原文，用 | 连接；没有表格写空串。",
    "found_fields": "目检在这份文件里确实看到对应数据的字段名，只可能来自 needs；needs 之外的不写，needs 为空时是空数组；只出现字段名而没有数据、或需要推断的一律不算。",
    "confidence": "你对本次判断的把握，取 0 到 1；只作留痕，不参与任何取舍。",
    "page_decisions": "选中的页清单，每项含 file_id、page_no、relevance、expected_fields、package_scope、extraction_mode、reason。",
    "expected_fields": "这一页能够提供的业务字段。",
    "relevance": "该页与所需内容的相关程度，0 到 1。",
    "extraction_mode": "这页该按文字 text、表格 table、两者都取 text_and_table，还是不取 unsupported。",
    "reason": "该项判断的依据简述。",
    "choices": "为每个标的选定的价格组，每项含 cluster_id、candidate_id。",
    "cluster_id": "同一个标的对应的一组候选价格。",
    "candidate_id": "候选对象的编号。",
    "current_candidate_id": "当前使用的价格组编号。",
    "options": "该标的可选的价格组集合。",
    "violations": "该包未通过的检查项，取值 amount_overflow 各行总价之和明显超过包金额、amount_underflow 明显少于包金额、row_mismatch 单价乘数量与总价对不上。",
    "assignments": "为表指定包号的结果，每项含 table_index、package_scope。",
    "table_index": "表的编号。",
    "table_section": "该表所在章节标题原文。",
    "row_text": "该候选来自的那一行的原文，各格用 | 连接，照抄不改写；找不到唯一对应行时留空。",
    "quote_supplier": "这份材料属于哪家投标（响应）供应商；材料里没有写就是空，不要猜。",
    "bidder_supplier": "这一行属于哪家投标（响应）供应商；一份材料里有几家投标人时才需要区分，没有写就是空。",
    "winner_supplier": "这份材料写明的中标（成交）供应商；材料里没有写就是空，不要猜。",
    "is_award_notice": "这份材料本身是不是中标（成交）通知书一类文件，取值 true 或 false。",
    # step 8 merge
    "package_amount_raw": "该包金额的原文写法，连单位与符号一起照抄。",
    "package_amount_suspect": "该包金额是否不可信，取值 true 或 false；为 true 时不要拿它对账。",
    "winner_suppliers": "公告已认定的本包中标（成交）供应商名单。",
    "baseline_groups": "规则先按名称完全相同分好的组，每项含 group_id 与 candidate_ids；你的修改以它为基线。",
    "group_id": "基线分组编号。",
    "origin": "这条候选来自哪里，取值 html 公告正文、attachment 附件文件。",
    "is_winner_quote": "这条候选是否出自中标（成交）供应商的材料，取值 true、false 或 null（判不出）。",
    "field_states": "该候选每个字段的状态，取值 present 有实值、missing 这份材料没有这一格、"
                    "points_to_attachment 属性存在但内容不在这份材料里、unparsable 写了字但不是数、"
                    "low_confidence 值的单位有歧义、conflict 另有来源写了不同的值。",
    "price_reading": "规则的事实：这条候选能不能当价格来源，即它的单价或总价是不是真写出了数。",
    "price_ineligible": "规则不给这条候选供价的原因，取值 tender_requirement 采购需求、"
                        "quote_not_winner 出自非中标人的报价。没有这个键表示可以给价。",
    "owner_suspect": "规则不认这条候选能单独成为一个标的的原因；它仍可以给别人补字段。",
    "unparsable_fields": "这份材料确实写了内容、但解析不出数的价格字段名，多为模板占位符；不要当价格用。",
    "rule_suspect": "规则对这条候选的怀疑标记，取值 suspect_html_pointer_row 公告把内容指向附件、"
                    "suspect_project_name_row 名称是项目全称。规则只标记不删除，留不留由你判。",
    "keys": "这次操作涉及的字段名列表。fields_from 只能取品目、品牌、产品供应商、规格型号、数量、单位；"
            "price_from 只能取单价、数量、单位、总价的子集，不写就是四字段整组取。",
    "fields_from": "把某条候选写过的这几个字段补进该组的空缺处；目标可以是别的组或已被 exclude 的行。",
    "deltas": "你对基线分组的修改清单，每项一个操作；不需要修改的组不要输出。",
    "op": "操作类型，取值 merge 两组合并为同一标的、split 一组拆成多个标的、sum 组内若干行是同一标的的"
          "拆行需求和、exclude 该候选不是标的、name_from 该组名称取哪条、price_from 该组价格取哪条、"
          "fields_from 把某条的指定字段补进该组空缺、category_from_announcement 把公告概要里的某条品目"
          "判给该组。",
    "raw_item": "这次操作指名的那条原文，逐字照抄输入里给到的内容，不要改写、不要拼接；"
                "category_from_announcement 用它表示把哪条品目判给该组。",
    "groups": "要合并的基线分组编号列表，至少两个。",
    "parts": "拆分结果，每项是一组 candidate_id；所有 part 合起来必须正好是原组成员，不重不漏。",
    "members": "需求和的那些 candidate_id，至少两个。",
    "name_from": "该组标的名称取自哪条候选的 candidate_id。",
    "price_from": "该组价格取自哪条候选的 candidate_id；四字段整组取，只想要数量或单位时用 keys 指明。",
    "reading_kind": "这组价格读法的类型，取值 single 取自某一行、sum 由若干行相加算出。",
    "sources": "组成这组读法的 candidate_id 列表；single 只有一项，sum 含参与相加的全部行。",
}

IO_NOTES = {
    "announcement_title": "公告标题原文。",
    "summary_table": "公告概览表原文。",
    "html_headings": "公告的章节标题原文列表。",
    "body_sections": "公告正文相关段落原文。",
    "tables": "表格原文，含表头与若干行。",
    "package_hints": "正文中出现过的包标记及其上下文原文。",
    "source_project_no_hint": "正文中项目编号的原文。",
    "before_text": "该表之前的正文原文。",
    "headers": "表头各格原文。",
    "first_rows": "表格前若干行原文。",
    "rows": "表格行原文。",
    "table_rows": "该页表格的行数。",
    "section": "该表所在章节标题原文。",
    "key_value": "该表是否为键值表。",
    "package_candidates": "公告已确认的包号列表。",
    "known_packages": "公告已确认的包号列表。",
    "other_tables": "同一公告里其它表的标题、章节与表头原文。",
    "first_row": "表格第一行原文。",
    "file_name": "文件名称原文。",
    "display_name": "文件名称原文。",
    "name": "文件名称原文。",
    "format": "由文件字节判出的容器类型，不是扩展名。",
    "pages": "文件页数。",
    "table_headers": "表格表头行的原文。",
    "needs": "缺口清单，每项含 need_id、field、package_no、reason、purpose、target_candidate_ids、state。reason：absent 未提供、coverage_low 填写不全、points_to_attachment 见附件、maybe_more_objects 清单可能不全。purpose 区分 fill_field 补已有对象、discover_objects 发现对象、resolve_binding 确认归属；state=pending 未决或 evidence_ready 已有抽取证据，后者不代表最终合并已完成。",
    "field": "一个业务字段名。",
    "content_view": "该文件正文的前若干字原文。",
    "file_class": "上一步判定的文件内容类型。",
    "possible_packages": "文件名里唯一指明的包号，且必须是公告已有的包号；没有就是空列表。",
    "text_head": "该页正文前若干字原文。",
    "row_sample": "该页表格第一行原文。",
    "chars": "该页正文字符数。",
    "page_no": "原文中的页号，从 1 开始。",
    "file_id": "本则公告内文件的编号。",
    "text": "该页正文原文，表格内容另列。",
    "page_contexts": "需要抽取的页面集合。",
    "project_id": "本条记录所属项目（包）的内部编号，照抄输入，不要改写或新造。",
    "current_package": "当前处理的是哪个包，以及它缺什么。",
    "missing_fields": "该包已知标的仍缺少的业务字段名；不涉及公告没列出的标的，那部分看 needs 里的 maybe_more_objects。",
    "source_type": "这条记录来自哪里：html 是公告正文，其余是附件文件。",
    "source_priority": "来源可信度分值，越大越优先；HTML 中标明细与报价明细最高，采购需求最低。",
    "total_is_derived": "true 表示这个总价是用单价乘数量算出来的，原文里没写。",
    "view_chars": "本次给模型看的正文字数。",
    "source_priority": "这条记录来源的可信度分值，越大越优先；HTML 明细与中标/成交类来源高，采购需求类低。",
    "source_type": "这条记录来自 html（公告正文）还是附件文件。",
    "total_is_derived": "true 表示这个总价是单价乘数量算出来的，原文里没有写。",
    "table_index": "表的编号。",
    "table_role": "该表被判定起的作用。",


    "package_no": "当前包号。",
    "clusters": "需要选价格组的标的清单，每项含 cluster_id、object_name、current_candidate_id、options。",
    "cluster_id": "标的分组编号。",
    "object_name": "标的名称。",
    "object_total": None,
}
IO_NOTES.pop("object_total")
IO_NOTES.pop("object_name")          # 业务语义优先，不用 IO 版
IO_NOTES.pop("package_no")          # 同上，保留业务定义
IO_NOTES.pop("table_role")          # 同上

# Step-specific wording where a field's allowed values depend on the step.
STEP_FIELD_OVERRIDES = {
    "screen-file-v1": {
        "confidence": "你对本次判断的把握，取 0 到 1；只留痕。保留与判负只看 found_fields，不看 kind，也不看 confidence。",
    },
    "screen-page-v1": {
        "confidence": "你对本次判断的把握，取 0 到 1；只留痕。保留与判负只看 found_fields，不看 kind，也不看 confidence。",
    },
    "locate-pages-v1": {
        "tables": "这一页上表格的个数（不是表格内容）。",
    },
    "html-candidates-v1": {
        "candidates": "抽取出的候选对象列表，每项含 entity_type、package_no、fields、issues。",
    },
    "attachment-extract-v1": {
        "candidates": "抽取出的候选对象列表，每项含 entity_type、package_no、file_id、fields、issues。",
    },
    "merge-objects-v3": {
        "candidates": "本包全部标的候选，每项含 candidate_id、group_id、origin、来源信息、11 个业务字段、"
                      "field_states，以及规则给出的事实 price_reading 与可能的 rule_suspect、"
                      "price_ineligible、owner_suspect、unparsable_fields。",
        "kind": "exclude 操作的理由，只能取：subtotal_row 合计或小计行、project_row 项目全称行、"
                "index_row 只说明标的存在、内容在附件里的索引行、not_an_object 不是一个标的。",
        "confidence": "你对这条修改的把握，取 0 到 1；只作留痕，规则层不按它取舍。",
    },
    "html-tables-v1": {
        "issues": "你在这一步注意到的问题标记，只能取：object_name_grain_suspect、summary_row、"
                  "points_to_attachment、multiple_packages_in_one_table、multi_value_cell、"
                  "header_merge_needed、package_scope_unknown、column_mapping_uncertain、other。",
    },
}

STEP_FIELDS = {
    "announcement-v1": (["announcement_title", "summary_table", "html_headings", "body_sections", "tables",
                         "table_index", "before_text", "section", "key_value", "headers", "rows",
                         "package_hints", "source_project_no_hint"],
                        ["project_name", "purchaser", "source_project_no", "announcement_type", "package_mode",
                         "packages", "package_no", "package_evidence_text", "package_amount", "summary_amount",
                         "unclear_reason", "amount_alternatives", "announcement_categories"]),
    "html-tables-v1": (["table_index", "before_text", "headers", "first_rows", "section", "key_value",
                        "package_candidates", "other_tables"],
                       ["table_role", "row_grain", "package_scope", "column_mapping", "unmapped_columns",
                       "confidence", "issues"]),
    "resolve-packages-v1": (["known_packages", "tables"], ["assignments", "table_index", "package_scope"]),
    "html-candidates-v1": (["entity_type", "package_no", "table_role", "column_mapping", "headers", "rows",
                            "body_sections"],
                           ["candidates", "entity_type", "package_no", "object_name", "category_name",
                            "category_code", "category_type", "brand", "product_supplier", "spec_model",
                            "unit_price", "quantity", "unit", "total_price", "supplier_name", "score",
                            "is_winner", "issues"]),
    "screen-file-v1": (["file_name", "format", "pages", "table_headers", "needs", "content_view"],
                     ["kind", "has_price_table", "header", "found_fields", "confidence",
                      "quote_supplier", "is_award_notice"]),
    "screen-page-v1": (["file_name", "needs"],
                       ["kind", "has_price_table", "header", "found_fields", "confidence"]),
    "locate-pages-v1": (["known_packages", "needs", "file_id", "display_name", "file_class",
                         "possible_packages", "found_fields", "page_no", "text_head", "table_headers",
                         "table_rows", "row_sample", "tables", "chars"],
                        ["page_decisions", "file_id", "page_no", "relevance", "expected_fields",
                         "package_scope", "extraction_mode", "reason"]),
    "attachment-extract-v1": (["current_package", "project_id", "package_no", "missing_fields", "needs",
                               "page_contexts", "file_id", "file_name", "page_no", "found_fields",
                               "text", "tables", "winner_supplier", "quote_supplier"],
                              ["candidates", "entity_type", "package_no", "object_name", "category_name",
                               "category_code", "category_type", "brand", "product_supplier", "spec_model",
                               "unit_price", "quantity", "unit", "total_price", "supplier_name", "score",
                               "is_winner", "issues", "package_amounts", "raw_text",
                               "row_text", "bidder_supplier", "winner_supplier"]),
    "merge-objects-v3": (["project_name", "package_no", "package_total_amount", "package_amount_raw",
                          "package_amount_suspect", "winner_suppliers", "baseline_groups", "group_id",
                          "announcement_categories", "candidates", "candidate_id", "origin", "file_name",
                          "file_class", "table_index", "table_role", "table_section", "page_no", "row_text",
                          "quote_supplier", "bidder_supplier", "winner_supplier", "is_winner_quote",
                          "object_name", "category_code", "category_name", "category_type", "brand",
                          "product_supplier", "spec_model", "unit_price", "quantity", "unit", "total_price",
                          "field_states", "price_reading", "price_ineligible", "owner_suspect",
                          "unparsable_fields", "rule_suspect"],
                         ["deltas", "op", "groups", "parts", "members", "group_id", "candidate_id",
                          "name_from", "price_from", "fields_from", "keys", "raw_item", "kind", "reason",
                          "confidence"]),
    "repair-package-v1": (["package_no", "package_total_amount", "violations", "clusters", "cluster_id",
                           "object_name", "current_candidate_id", "options", "candidate_id", "file_id",
                           "file_class", "source_type", "source_priority", "unit_price", "quantity",
                           "unit", "total_price", "total_is_derived", "reading_kind", "sources"],
                          ["choices", "cluster_id", "candidate_id"]),
}


FIELD_SEMANTICS.update({
    "source_ref": "程序提供的原文位置编号，照抄引用，不得新造。",
    "source_refs": "该观测或判断依据的原文位置编号列表，只引用输入提供的编号。",
    "source_segments": "有来源编号的原文片段；PDF 为实际页，原生 Word/表格可为逻辑片段，不假称物理页。",
    "source_rows": "有稳定来源编号的原始行、单元格及章节上下文。",
    "evidence_refs": "目检已看见、可供后续抽取的片段编号。",
    "requires_deep_parse": "模型是否认为还需深度解析；false 仅为建议，程序仍验证已抽取证据是否覆盖缺口。",
    "bindings": "逐候选的包归属结果，每项 candidate_id、package_no、source_refs、reason、confidence。",
    "amount_bindings": "逐包金额归属，每项 package_no、amount_ref、amount_kind、reason；未决用 amount_ref=null。",
    "amount_ref": "提供的金额观测编号，不是金额数值。",
    "amount_kind": "金额性质：award 中标成交金额、budget 采购预算、ceiling 最高限价、unknown 未明确；未声明不默认为成交。",
    "scope": "金额层级：announcement 公告、package 包；与金额性质分别表达。",
    "amount_observations": "带编号与原文的金额观测，旧包号是建议而非事实。",
    "table_contexts": "模型已读出的表角色、列映射及范围建议。",
    "suggested_package_no": "上游建议的包号，仍需结合原文裁定。",
    "previous_binding": "该候选此前的归属判断及证据，修订需说明新增依据。",
    "protocol_version": "本次处理协议版本。",
    "package_amount_bindable": "该包金额是否具备供最终整体标的总价的资格；仍需模型确认对应关系。",
    "package_amount_evidence": "当前包金额的原文、性质、层级与已裁定来源；最终使用 p:amount 时保留此记录。",
    "as": "显式声明的新组 g: 或计算读法 r: 编号，不能重复、不能复用消耗的组。",
    "source": "供值来源：c:候选、r:计算读法、或允许的 p:amount 包金额。",
    "target": "字段否定目标：c:候选或 g:最终组。",
    "mode": "描述字段取值模式：fill 仅补空、select 显式选择覆盖。",
    "group_ids": "要合并的当前活组引用列表。",
    "candidate_ids": "当前操作涉及的输入候选编号列表。",
    "quantity_accounting": "未采用计量字段的逐项解释，每项 candidate_id、keys、disposition、relates_to、reason。同一候选可按不同 keys 分别说明；采用单价数量不等于采用它的总价。",
    "disposition": "未采用原因：duplicate 重复观测、alternative 冲突备选、unresolved 未决。",
    "relates_to": "这项未采用观测对应的候选、活组或计算读法引用。",
    "native_reuse": "当前输入来自已在目检中看见的原生片段，仍沿用正式抽取与来源校验。",
    "need_resolutions": "本次观测支持的已有字段缺口，每项 need_id、source_ref、field、target_candidate_id；不是宣称全量已完成。",
    "need_id": "缺口的稳定编号。",
    "target_candidate_id": "本次补充针对的已有候选编号。",
    "final_cobs": "合并及修复后的待判断 COB，每项含稳定 cob_ref 与最终字段。",
    "cob_ref": "当前最终 COB 的唯一引用，同名对象不能混同。",
    "winner_ref": "已确认中标供应商引用，只能选择输入提供的编号。",
    "decisions": "逐最终 COB 的自供判断。",
    "decision": "自供判断：confirm 确认、reject 不支持、uncertain 无法判断。",
    "field_bindings": "已有最终字段取值与否定的审计，forbid_inference 表示禁止推断补全。",
    "row_index": "程序提供的表内行序号，从 0 开始，不是采购文件业务序号。",
    "observation_bindings": "逐附件金额观测复核归属，每项 amount_ref、package_no、amount_kind、reason；逐一覆盖附件金额，未决包号为 null。",
    "evidence_context": "已有原文片段、原生抽取接收审计及缺口状态；看见不代表已抽取或已采用。",
    "native_evidence": "目检已确认的片段、抽取结果与未决缺口；请结合页摘要决定后续处理，不声明它们自动入选。",
})
STEP_FIELDS["html-candidates-v1"][0].extend(["source_rows", "known_packages", "section", "before_text"])
STEP_FIELDS["html-candidates-v1"][1].append("source_refs")
STEP_FIELDS["screen-file-v1"][0].append("source_segments")
STEP_FIELDS["screen-file-v1"][1].extend(["evidence_refs", "requires_deep_parse"])
STEP_FIELDS["attachment-extract-v1"][0].extend(["source_segments", "known_packages", "native_reuse"])
STEP_FIELDS["attachment-extract-v1"][1].extend(["source_ref", "page_no", "amount_kind", "need_resolutions",
                                               "need_id", "target_candidate_id", "field"])
STEP_FIELDS["package-binding-v1"] = (
    ["known_packages", "project_name", "candidates", "source_rows", "body_sections", "table_contexts",
     "amount_observations", "suggested_package_no", "previous_binding"],
    ["bindings", "amount_bindings", "candidate_id", "package_no", "source_refs", "amount_ref",
     "amount_kind", "reason", "confidence"])
STEP_FIELDS["supplier-completion-v1"] = (
    ["project_name", "package_no", "winner_suppliers", "final_cobs", "cob_ref", "winner_ref", "field_bindings"],
    ["decisions", "cob_ref", "decision", "winner_ref", "reason", "confidence"])
STEP_FIELD_OVERRIDES["supplier-completion-v1"] = {
    "winner_suppliers": "本包最终确认的中标人，每项 winner_ref、supplier_name。"}
STEP_FIELD_OVERRIDES["package-binding-v1"] = {
    "known_packages": "公告确认存在的包集合，含标题与原文证据；其中金额与归属仍是上游建议。"}
STEP_FIELDS["merge-objects-v4"] = (
    STEP_FIELDS["merge-objects-v3"][0] + ["protocol_version", "package_amount_bindable", "source_refs", "row_index"],
    STEP_FIELDS["merge-objects-v3"][1] + ["as", "source", "target", "mode", "group_ids", "candidate_ids",
                                        "quantity_accounting", "disposition", "relates_to"])
STEP_FIELD_OVERRIDES["merge-objects-v4"] = {
    **STEP_FIELD_OVERRIDES.get("merge-objects-v3", {}),
    "group_id": "当前活组引用，输入基线组可写 g:0；命名子组使用显式 as 引用，旧组消耗后失效。",
    "op": "操作：exclude、split、merge、sum、name_from、price_from、fields_from、fields_reject、category_from_announcement。",
    "parts": "split 子组，每项 as、members，必须无重叠且完整覆盖原组。",
    "sources": "sum 来源列表，可引用 c:候选或已经声明的 r:读法；每个叶子只能计入一次。",
    "keys": "fields_from 只取描述字段；price_from 取单价、数量、单位、总价；fields_reject 可否定除名称外的字段。",
    "known_packages": "公告确认存在的包集合；其中已填金额与归属建议仍需证据裁定。",
    "fields_from": "点名供值候选、目标活组与描述字段；fill 仅补空，select 显式选择。排除行或跨组来源不自动供值。",
    "price_from": "点名供价候选 c:、计算读法 r: 或具备资格的 p:amount，按 keys 绑定最终计量字段；不能写数值。",
}


# Versioned callers prevent replaying pre-optimization decisions with new semantics.
for _stem in ['announcement', 'html-tables', 'html-candidates', 'screen-file', 'screen-page', 'locate-pages', 'attachment-extract']:
    STEP_FIELDS[_stem + "-v2"] = STEP_FIELDS[_stem + "-v1"]
    STEP_FIELD_OVERRIDES[_stem + "-v2"] = dict(STEP_FIELD_OVERRIDES.get(_stem + "-v1", {}))

for _stem in ["announcement", "html-tables", "html-candidates", "locate-pages", "attachment-extract"]:
    STEP_FIELDS[_stem + "-v3"] = tuple(list(keys) for keys in STEP_FIELDS[_stem + "-v2"])
    STEP_FIELD_OVERRIDES[_stem + "-v3"] = dict(STEP_FIELD_OVERRIDES.get(_stem + "-v2", {}))
for _version in ["announcement-v3", "attachment-extract-v3"]:
    STEP_FIELDS[_version][1].extend(["scope", "amount_kind"])
for _version in ["html-tables-v3", "html-candidates-v3", "attachment-extract-v3"]:
    STEP_FIELDS[_version][1].append("supplier_award_amount")
STEP_FIELDS["locate-pages-v3"][0].append("native_evidence")
STEP_FIELDS["package-binding-v2"] = (list(STEP_FIELDS["package-binding-v1"][0]),
    list(STEP_FIELDS["package-binding-v1"][1]) + ["observation_bindings", "scope"])
STEP_FIELD_OVERRIDES["package-binding-v2"] = dict(STEP_FIELD_OVERRIDES["package-binding-v1"])
STEP_FIELDS["merge-objects-v5"] = (list(STEP_FIELDS["merge-objects-v4"][0]) + ["evidence_context", "package_amount_evidence"],
                                    list(STEP_FIELDS["merge-objects-v4"][1]))
STEP_FIELD_OVERRIDES["merge-objects-v5"] = dict(STEP_FIELD_OVERRIDES["merge-objects-v4"])
STEP_FIELD_OVERRIDES["merge-objects-v5"]["protocol_version"] = "delta-v5：逐字段采用与计量说明，候选否定与最终置空分开。"
FIELD_SEMANTICS.update({
    "excluded": "退出 SUB 输出的候选列表，每项 candidate_id 与 reason。",
    "score_from": "本组用于供应商综合得分的候选编号或 null。",
    "amount_from": "本组用于供应商成交金额的候选编号或 null；只采用该候选 supplier_award_amount。",
    "winner": "结合原文最终成交结果判断的本包实际中标身份，布尔值。",
    "unmapped_fields": "历史模型输出中未进入业务字段契约的原值，保留审计，不自动改名供值。",
})
STEP_FIELDS["merge-subs-v1"] = (["package_no", "candidates", "candidate_id", "fields", "supplier_name",
    "score", "is_winner", "supplier_award_amount", "unmapped_fields"],
    ["groups", "candidate_ids", "name_from", "score_from", "amount_from", "winner", "excluded", "candidate_id", "reason"])
STEP_FIELD_OVERRIDES["merge-subs-v1"] = {"groups": "最终 SUB 分组，每组给候选成员、名称/得分/金额来源、实际中标身份及理由。",
    "name_from": "当前 SUB 组中供应商全称的供值候选编号。"}


# Official output compatibility (2026-10-13). Older prompt versions remain replayable.
FIELD_SEMANTICS.update({
    "package_no": "采购单元内部引用，必须使用输入目录中的原样引用；与对外展示名称分开。第一步只抄原文编号，缺号可为 null，由封装分配引用。",
    "package_scope": "只引用输入目录中的采购单元内部引用；跨单元为 announcement，无法确定为 unknown。不能把相同裸数字的标段和包混为一组。",
    "package_kind": "采购单元类型：lot 标段、contract_package 合同包/采购包、unpartitioned 未分包、unknown 未知。独立采购标段与合同包保留区别，单个单元不等于未分包。",
    "package_name": "对外展示的采购单元名称：标段抄原文标段名称；合同包抄原文包名称（仅有编号则抄原文包标题）；确认未分包填未分包，不能判断为 null。",
    "source_package_no": "原文编号，与程序分配的内部引用分开保留。",
    "package_registry": "公告确认的采购单元目录，含内部引用、原文编号、类型及展示名称；归属必须引用此目录，不根据数字相同合并标段与包。",
    "bid_amount": "本采购单元当前供应商的最终投标/响应报价，原文连单位抄取；首次报价或评审优惠价不能代替最终报价，未明确披露为 null。归一化后单位元。",
    "review_status": "原文明示的审查结论：passed 通过、failed 未通过、unknown 未知；不得根据中标、排名或有得分推断通过。",
    "review_failure_reason": "当前供应商未通过审查的原文原因；只有未通过结论才采用，未披露为 null。",
    "bid_amount_from": "本组最终投标/响应报价的供值候选编号或 null；结合原文报价阶段选择，不写数值。",
    "review_from": "本组审查结论的供值候选编号或 null，需结合原文解决通过/未通过冲突。",
    "review_reason_from": "本组未通过原因的供值候选编号或 null，须与所选 failed 结论对应同次审查。",
    "unresolved_fields": "已有观测但本组不能确认的字段名列表，必须在 reason 说明，业务值保持未知。",
    "source_candidate_ref": "供应商缺口解决所采用观测的唯一原文片段引用，须明确对应 target_candidate_id 指定供应商；同页其他供应商不能代替。",
    "target_entity": "缺口目标实体，cob 标的或 sub 投标供应商；发现一条不等于名单完整。",
    "brand": "原文明示在品牌字段的值，含原文写作定制等非商标写法；未披露为 null，不能用产品供应商或中标人补品牌。",
    "spec_model": "当前标的规格型号字段的原文值，定制、国标、按照招标要求提供等非技术型号写法也保留；见附件不是已知值，不从其它层级的服务范围/时间/要求自行拼入。",
    "source_contexts": "按供应商候选已引用的 source_ref 提供原始行或段落上下文，供最终报价阶段和审查结论判断；不能从中创造没有抽取的供值候选。",
})
FIELD_SEMANTICS["table_role"] = FIELD_SEMANTICS["table_role"].replace("sub_score 供应商得分、", "sub_score 供应商得分、sub_info 供应商报价/审查/混合信息、")
FIELD_SEMANTICS["column_mapping"] = FIELD_SEMANTICS["column_mapping"].replace("supplier_award_amount，", "supplier_award_amount、bid_amount、review_status、review_failure_reason，")
for _stem in ("announcement", "html-tables", "html-candidates", "locate-pages", "attachment-extract"):
    _version = _stem + "-v4"
    STEP_FIELDS[_version] = tuple(list(keys) for keys in STEP_FIELDS[_stem + "-v3"])
    STEP_FIELD_OVERRIDES[_version] = dict(STEP_FIELD_OVERRIDES.get(_stem + "-v3", {}))
    STEP_FIELDS[_version][0].append("package_registry")
    STEP_FIELDS[_version][1].extend(["package_kind", "package_name"] if _stem == "announcement" else
                                   ["bid_amount", "review_status", "review_failure_reason"])
STEP_FIELDS["attachment-extract-v4"][1].append("source_candidate_ref")
for _new, _old in (("package-binding-v3", "package-binding-v2"), ("merge-objects-v6", "merge-objects-v5"),
                   ("merge-subs-v2", "merge-subs-v1"), ("screen-file-v3", "screen-file-v2"), ("screen-page-v3", "screen-page-v2")):
    STEP_FIELDS[_new] = tuple(list(keys) for keys in STEP_FIELDS[_old])
    STEP_FIELD_OVERRIDES[_new] = dict(STEP_FIELD_OVERRIDES.get(_old, {}))
    STEP_FIELDS[_new][0].extend(["package_kind", "package_name", "package_registry", "bid_amount", "review_status", "review_failure_reason"])
STEP_FIELDS["merge-subs-v2"][1].extend(["bid_amount_from", "review_from", "review_reason_from", "unresolved_fields"])
STEP_FIELDS["merge-subs-v2"][0].append("source_contexts")
for _version in ("screen-file-v3", "screen-page-v3", "locate-pages-v4", "attachment-extract-v4"):
    STEP_FIELDS[_version][0].extend(["target_entity", "supplier_name", "bid_amount", "review_status", "review_failure_reason"])


for _new, _old in (("html-tables-v5", "html-tables-v4"),
                   ("html-candidates-v5", "html-candidates-v4"),
                   ("package-binding-v4", "package-binding-v3")):
    STEP_FIELDS[_new] = tuple(list(keys) for keys in STEP_FIELDS[_old])
    STEP_FIELD_OVERRIDES[_new] = dict(STEP_FIELD_OVERRIDES.get(_old, {}))


def field_block(prompt_version: str) -> str:
    """Definitions this step needs: its own payload fields plus its output fields."""
    if prompt_version not in STEP_FIELDS:
        return ""
    incoming, outgoing = STEP_FIELDS[prompt_version]
    overrides = STEP_FIELD_OVERRIDES.get(prompt_version, {})
    lines, seen = [], set()
    for name in list(incoming) + list(outgoing):
        if name in seen:
            continue
        seen.add(name)
        meaning = overrides.get(name) or FIELD_SEMANTICS.get(name) or IO_NOTES.get(name)
        if meaning:
            lines.append("%s %s" % (name, meaning))
    if not lines:
        return ""
    return "字段含义\n" + "\n".join(lines)
