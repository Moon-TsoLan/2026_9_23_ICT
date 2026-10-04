"""Field meanings for the model, in one place.

Each step declares which payload fields it sends and which fields it expects back;
`llm.load_prompt` appends exactly those definitions to the prompt text. A field therefore has
one wording everywhere and can never be repeated inside a single prompt.

Definitions only: what a value is, and what it is not. No implementation aliases, no advice
about how to reason.
"""

FIELD_SEMANTICS = {
    "object_name": "单个标的名称，一件货物、一项服务或一段工程内容；不是项目名称，也不是包的名称。",
    "category_name": "品目类别名，如“工业机器人”“其他商业保险服务”；不是标的名称。",
    "category_code": "品目目录编码：一个字母加八位数字，如 A02100499。",
    "category_type": "品目大类：A 货物、B 工程、C 服务。",
    "brand": "品牌，如“佳能”“达梦”。",
    "product_supplier": "制造商或生产厂商名称；不是投标供应商名称。",
    "spec_model": "规格型号：型号串或技术参数标识。",
    "unit_price": "单价，即每个计价单位的金额。",
    "quantity": "数量，纯数值；人数、面积、点位数等不是计价数量。",
    "unit": "计价单位，如台、套、项、人。",
    "total_price": "单个标的行的合计金额；整包或整项目的总金额不算它。",
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
    "package_amount": "该包的中标（成交）金额，含 raw_text、amount_yuan、scope、confidence；scope 固定为 package。",
    "amount_alternatives": "同一包里你看到但没有选作 package_amount 的其它金额，每项含 raw_text、amount_yuan、scope、confidence；只作留痕，不参与判定。",
    "package_evidence_text": "证明该包存在的原文片段。",
    "summary_amount": "公告概览层面的项目总额，含 raw_text、amount_yuan、scope、confidence；scope 固定为 announcement。",
    "unclear_reason": "判不清包结构时的原因说明。",
    "table_role": "这张表的作用，取值 cob_detail 标的明细、cob_summary 标的汇总、sub_score 供应商得分、winner 中标信息、agency_fee 代理服务费、other 其它。",
    "row_grain": "这一行代表什么，取值 cob 一个标的、supplier 一个供应商、project 一个项目、other 其它。",
    "package_scope": "这段内容属于哪个包；只写编号本身，不带“第”“包”等前后缀；整篇范围写 announcement，判不出写 unknown。",
    "column_mapping": "业务字段名到表头原文的对应关系；键只能取 object_name、category_name、category_code、brand、spec_model、quantity、unit、unit_price、total_price、supplier_name、score、is_winner，值必须是给到的表头原文。",
    "unmapped_columns": "无法对应到任何业务字段的表头原文。",
    "issues": "你在这一步注意到的问题标记。",
    "candidates": "抽取出的候选对象列表。",
    "package_amounts": "页面原文单独写出的某个包的中标（成交）总额，每项含 package_no 与 raw_text；行项总价、单价乘数量、项目总额不算，进度款与预付也不算。",
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
    "field_states": "该候选每个字段的状态，取值 present 有实值、missing 这份材料没有这一格、points_to_attachment 属性存在但内容不在这份材料里。",
    "deltas": "你对基线分组的修改清单，每项一个操作；不需要修改的组不要输出。",
    "op": "操作类型，取值 merge 两组合并为同一标的、split 一组拆成多个标的、sum 组内若干行是同一标的的拆行需求和、exclude 该候选不是标的、name_from 该组名称取哪条、price_from 该组价格取哪条。",
    "groups": "要合并的基线分组编号列表，至少两个。",
    "parts": "拆分结果，每项是一组 candidate_id；所有 part 合起来必须正好是原组成员，不重不漏。",
    "members": "需求和的那些 candidate_id，至少两个。",
    "name_from": "该组标的名称取自哪条候选的 candidate_id。",
    "price_from": "该组价格四字段整组取自哪条候选的 candidate_id。",
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
    "needs": "本公告还缺少的内容清单，每项含 field、package_no、reason。reason 取值：absent 公告里根本没有这项；coverage_low 公告里已有但填得不全；points_to_attachment 公告写「见附件」；maybe_more_objects 公告列出的标的可能不全，附件里可能有公告没提过的标的。",
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
    "merge-objects-v1": {
        "candidates": "本包全部标的候选，每项含 candidate_id、group_id、origin、来源信息、11 个业务字段与 field_states。",
        "kind": "exclude 操作的理由，只能取：subtotal_row 合计或小计行、project_row 项目全称行、not_an_object 不是一个标的。",
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
                         "unclear_reason", "amount_alternatives"]),
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
    "merge-objects-v1": (["project_name", "package_no", "package_total_amount", "package_amount_raw",
                          "package_amount_suspect", "winner_suppliers", "baseline_groups", "group_id",
                          "candidates", "candidate_id", "origin", "file_name", "file_class", "table_index",
                          "table_role", "table_section", "page_no", "row_text", "quote_supplier",
                          "bidder_supplier", "winner_supplier", "is_winner_quote", "object_name",
                          "category_code", "category_name", "category_type", "brand", "product_supplier",
                          "spec_model", "unit_price", "quantity", "unit", "total_price", "field_states"],
                         ["deltas", "op", "groups", "parts", "members", "group_id", "candidate_id",
                          "name_from", "price_from", "kind", "reason", "confidence"]),
    "repair-package-v1": (["package_no", "package_total_amount", "violations", "clusters", "cluster_id",
                           "object_name", "current_candidate_id", "options", "candidate_id", "file_id",
                           "file_class", "source_type", "source_priority", "unit_price", "quantity",
                           "unit", "total_price", "total_is_derived", "reading_kind", "sources"],
                          ["choices", "cluster_id", "candidate_id"]),
}


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
