你只从当前包的已选页面抽取候选。只输出 JSON：{"candidates": [...], "package_amounts": [...]}。

每条候选除了 entity_type、package_no、file_id、fields、issues，再带上 row_text 与 table_index：row_text 是这一行的原文，各格用 | 连接，照抄不改写，跨行合并出来的行把几行接在一起；table_index 是这一行所在表的编号，就是输入 tables 里给出的那个编号，这一行不在任何表里就省略。

这份材料里出现几家投标人时，用 bidder_supplier 写清这一行属于哪家投标（响应）供应商；只有一家、或页面没写就省略，不要从标的名称或品牌里猜。

输入 current_package 里已经给出 winner_supplier 时，照抄进每条候选的 winner_supplier，不要重新判断。它为空时，才从页面里找中标（成交）供应商：找得到就写进 winner_supplier，找不到就省略。

fields 的键只能使用下面列出的名字，不要把表头原文或自拟英文名当作键。
标的 cob 的键：object_name、category_code、category_name、category_type、brand、product_supplier、spec_model、unit_price、quantity、unit、total_price。
供应商 sub 的键：supplier_name、score、is_winner。
产品名称、货物名称写入 object_name。品牌或品牌/型号写入 brand。规格型号写入 spec_model。制造商写入 product_supplier。供应商名称写入 supplier_name。
示例：文件名为「B包分项报价表.pdf」，表中一行是「HIS子系统升级、新蓝海、1、400000.00、400000.00」时，输出
{"entity_type":"cob","package_no":"B","file_id":"a003","table_index":0,"row_text":"HIS子系统升级 | 新蓝海 | 1 | 400000.00 | 400000.00","bidder_supplier":"山东新蓝海科技股份有限公司","fields":{"object_name":"HIS子系统升级","brand":"新蓝海","quantity":"1","unit_price":"400000.00","total_price":"400000.00"},"issues":[]}
不要输出 item_name、brand_model、bidder_name。
品目号是行序号，不要写入品目字段。没有品目列时不要编造。
仅有品目名称：一行「1-1、其他商业保险服务、团体重大疾病保险」，category_name 为「其他商业保险服务」，object_name 为「团体重大疾病保险」。
仅有品目编码：一行「A02100499、口腔种植手术机器人、雅客智慧」，category_code 为「A02100499」，object_name 为「口腔种植手术机器人」，brand 为「雅客智慧」。
品目名称和品目编码都有：一行「A02050906、工业机器人、潜伏式搬运机器人」，category_code 为「A02050906」，category_name 为「工业机器人」，object_name 为「潜伏式搬运机器人」。同一格里同时有编码和名称时，拆进这两个字段。
fields 的值必须出现在当前输入中，不要推断未见字段。
价格与金额要连单位一起写。单位可能写在格内，也可能只写在这一列的表头里：表头是「单价（万元）」、格里是 532 时，值写成「532万元」，因为程序只认值里出现的单位。格内已带单位的照抄格内，别换成你自己的换算。
文件名或当前包给出「B包」「标包A」时，package_no 写 "B" 或 "A"，不要因为页内正文没再写包号就填 null。
同一行有多个标的时拆成多个候选，不要重复同一实体。
联合体保持全称。
候选的 package_no：当前包已给出时只输出这个编号；无法确定时填 null，并在 issues 中写 package_scope_unknown。package_amounts 里的 package_no 按原文写，可以是别的包。
只有单元格原文就是「详见附件」或「见附件」时，不要把这句话当成业务值。「按照招标要求提供」要原样写入对应字段。

页面原文把某个包的中标（成交）总额单独写出来时（例如「B包中标金额：1930000元」「中标（成交）总金额：¥1,486,000.00」），把包号和这句话的原文抄进 package_amounts；一个包最多一条，页面没这样写就输出空数组。
