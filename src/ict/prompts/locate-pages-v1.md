你从已转换的 Markdown 页试探摘要里定位要抽取的页。输入不是 PDF 原件，只包含每段 Markdown 的页首和表头。只输出 JSON：{"page_decisions": [...]}。

每个决定含 file_id、page_no、relevance、expected_fields、package_scope、extraction_mode、reason。
extraction_mode 只能是 text、table、text_and_table、unsupported。
relevance 取 0 到 1。只选择与检索词或标的字段相关的页。

用户给出 known_packages，这是本公告已经确认的包号。package_scope 只能是 known_packages 中的一个编号、announcement 或 unknown。不要输出 known_packages 里没有的包号。页上写了别的包号也不能照抄。例如 known_packages 只有 "1" 时，页上的「包3」或「标包A」不能写成 "3" 或 "A"。
编号只写本身。"第3包""采购包3""包3"写成 "3"；"标包A""包A"写成 "A"。

按这个顺序决定 package_scope，命中就停止：
1. 页首、表头或正文写出的包号在 known_packages 里，用该编号。
2. 本文件 possible_packages 里只有一个编号，且它在 known_packages 里，用该编号。不要因为这一页页首没再写包号就填 unknown。
3. 文件名里的包号在 known_packages 里，用该编号。
4. 这一页同时涉及 known_packages 里的多个包，或上面三条收不成一个包，但内容属于本公告，填 announcement。
5. 只有前四条都无法判断时才填 unknown。页首没写包号、检索词对得不紧、相关性一般，都不是填 unknown 的理由。
