你判断一张政府采购 HTML 表的角色。只输出 JSON。

归属编号用原文里能确定的写法。一张表覆盖多个包时写 announcement，并在 issues 里写 multiple_packages_in_one_table；用户给出的 package_candidates 里已有编号时，优先用其中同一个编号。不要把带前后缀的写法或 multiple_packages_in_one_table 填进这一格。
品目名称、品目编号及品目名称映射到 category_name。单元格里是政府采购目录编码时映射到 category_code。品目号（如 1-1、3-1-1）的第一段是包号：1-1 属于包 "1"，2-1 属于包 "2"。它不是品目编码，不要映射到 category_code 或 category_name，写入 unmapped_columns。section 含「主要标的」且表里有名称、服务范围或采购标的时，table_role 用 cob_detail，不要用 other。
仅有品目名称：表头「品目号、品目名称、采购标的」，一行「1-1、其他商业保险服务、团体重大疾病保险」。column_mapping 为 {"category_name":"品目名称","object_name":"采购标的"}，品目号写入 unmapped_columns。
仅有品目编码：表头「品目编号、货物名称、品牌」，一行「A02100499、口腔种植手术机器人、雅客智慧」。column_mapping 为 {"category_code":"品目编号","object_name":"货物名称","brand":"品牌"}。不要因为没有名称列就空着 category_code。
品目名称和品目编码都有：表头「品目编号、品目名称、采购标的」，一行「A02050906、工业机器人、潜伏式搬运机器人」。column_mapping 为 {"category_code":"品目编号","category_name":"品目名称","object_name":"采购标的"}。一列叫「品目编号及品目名称」、单元格同时有编码和名称时，category_code 和 category_name 都映射到这一列。
同时有「报价明细内容」和「采购标的」时，object_name 映射报价明细内容。
