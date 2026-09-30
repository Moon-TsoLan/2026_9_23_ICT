你判断一张政府采购 HTML 表的角色，并给出列映射。只输出 JSON。

输入：一张表（table_index、before_text、headers、rows 或表头预览）、本公告的包号候选 package_candidates、以及同篇其它表的标题列表。

字段：
- table_role：cob_detail、cob_summary、sub_score、winner、agency_fee、other 之一。
- row_grain：cob、supplier、project、other 之一。
- package_scope：某个包号、announcement 或 unknown；编号只写本身，例如 3、A，不要写成"第3包""包3"这类形式。
- column_mapping：键用正式字段名（object_name、category_name、category_code、brand、spec_model、quantity、unit、unit_price、total_price、supplier_name、score、is_winner），值必须是该表表头的原文。
- unmapped_columns：对不上正式字段的表头。
- issues：从 object_name_grain_suspect、summary_row、points_to_attachment、multiple_packages_in_one_table、multi_value_cell、header_merge_needed、package_scope_unknown、column_mapping_uncertain、other 中取。
- confidence：0 到 1 的数。

判断准则：
1. table_role 描述这张表的主体内容：展示标的明细行（每行一个标的、含名称，通常还有品牌、规格、数量、单价、总价）时用 cob_detail；展示供应商得分或审查结果时用 sub_score；展示中标供应商时用 winner；只做汇总时用 cob_summary；代理服务费表用 agency_fee；以上都不是时用 other。
2. 只有表里存在能映射到 object_name 的列时，才可能判为 cob_detail，否则用 other。节名或表头出现"主要标的""标的信息""采购内容"只是线索，不能替代列内容。
3. 品目名称、品目编号及品目名称映射到 category_name；单元格是政府采购目录编码时映射到 category_code。
4. 品目号（如 1-1、3-1-1）是行序号，写入 unmapped_columns，不映射到品目字段。
5. 一张表覆盖多个包时，package_scope 写 announcement，并在 issues 记 multiple_packages_in_one_table。
6. column_mapping 的值必须是表头原文；对不上正式字段的表头放进 unmapped_columns。

输出：
{"table_index": <int>, "table_role": "<枚举>", "package_scope": "<包号|announcement|unknown>", "row_grain": "<枚举>", "column_mapping": {"<正式字段名>": "<表头原文>"}, "unmapped_columns": ["<表头>"], "issues": ["<枚举>"], "confidence": "<number|null>"}

无法确定角色时 table_role 写 other；无法确定包号时 package_scope 写 unknown。
