你从一张已判定角色的 HTML 表，或从正文 body_sections 中抽取候选。只输出 JSON：{"candidates":[...]}。

输入：entity_type、package_no、table_role、column_mapping、headers、rows（或 body_sections）。

字段：
- entity_type：与输入一致（cob 或 sub）。
- package_no：只写编号本身，例如 3、A；不要写成"第3包""包3"这类形式。输入里已给出 package_no 或 package_scope 时，沿用该编号；无法确定时写 null。
- fields：cob 的键为 object_name、category_code、category_name、category_type、brand、product_supplier、spec_model、unit_price、quantity、unit、total_price；sub 的键为 supplier_name、score、is_winner。
- issues：可选说明。

判断准则：
1. 产品名称、货物名称、采购标的、报价明细内容写入 object_name；品牌写入 brand；规格型号写入 spec_model；制造商写入 product_supplier。
2. 字段值必须来自本次输入，不得推断未见的值；输入里没有的字段不要输出。
3. 同一单元格用分号或顿号并列多个标的时，拆成多个候选；同一行有多个标的时拆成多个候选；不要重复同一实体。
4. 品目号（如 1-1、3-1-1）是行序号，不写入品目字段。category_code 与 category_name 是政府采购品目分类的编码与名称：只有能确认是品目编号或品目名称时才填，无法确认时不要填。
5. 只有单元格原文是"详见附件"或"见附件"时，该字段不作为业务值（按契约记为指向附件）；"按照招标要求提供"这类文字原样写入。
6. 联合体供应商保持全称，不要拆开；资格性审查未通过的供应商不要输出。
7. 得分填综合得分或评审总得分；只有技术分、商务分，或只有各评委分数时，score 不要填。
8. is_winner 为布尔值；无法判断时省略。
9. body_sections 是表格以外的正文：按其中的标包把列出的供应商抽成 sub，资格审查规则与表格相同。公司名后紧跟、括号前的数字是综合得分；括号内并列的多个数字是各评委分数，不填入 score。

输出：
{"candidates":[{"entity_type":"<cob|sub>","package_no":"<包号|null>","fields":{ "<正式字段名>": "<值>" },"issues":["<说明>"]}]}

输入中没有依据的字段不要推断；无法确定包号时 package_no 写 null，并在 issues 记 package_scope_unknown。
