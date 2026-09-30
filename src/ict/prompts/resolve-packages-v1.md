你为还没有包号的 HTML 表指定包号。只输出 JSON：{"assignments":[{"table_index": <int>, "package_scope": "<包号>"}]}。

输入：known_packages（本公告已确认的包号）、若干待定表（table_index、节名、表头、表前文字、品目号样例）。

判断准则：
1. package_scope 只能取 known_packages 里的编号（只写编号本身，例如 3、A，不要带"第""包"等字样）；页上写了别的包号也不能照抄。
2. 品目号第一段是包号（1-1 属于包 "1"，2-1 属于包 "2"，A-1 属于包 "A"）。
3. 表前文字里的供应商名称能对应某个包时，用那个包。

只依据给定信息判断。无法判断的表不要输出；不要输出 unknown，也不要输出 known_packages 之外的编号。
