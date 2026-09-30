你为还没有包号的 HTML 表指定包号。只输出 JSON：{"assignments": [...]}。

每个 assignment 含 table_index、package_scope。
package_scope 只能是 known_packages 里的编号。无法判断就不要输出这一张表。
品目号第一段是包号：1-1 属于包 "1"，2-1 属于包 "2"，A-1 属于包 "A"。
表前文字里的供应商名称如果能对应某个包，用那个包。
不要输出 unknown，也不要输出 known_packages 之外的编号。
