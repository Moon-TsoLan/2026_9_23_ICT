你只从当前包的已选页面抽取候选。只输出 JSON：{"candidates": [...]}。

每个候选含 entity_type、package_no、file_id、fields、issues。
entity_type 为 cob 或 sub。fields 的值必须出现在当前输入中，不要推断未见字段。
同一行有多个标的时拆成多个候选，不要重复同一实体。
联合体保持全称。
package_no 只写编号本身。"第3包""采购包3""包3"写成 "3"；"标包A""包A"写成 "A"。不要输出"第3包"。当前包已经给出 package_no 时，只输出这个编号，不要改写成带"第"或"包"的形式。无法确定包号时 package_no 为 null，并在 issues 中写 package_scope_unknown。
原文写「详见附件」时不要把这句话当成业务值。
