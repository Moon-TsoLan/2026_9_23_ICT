你从一张已经判定角色的 HTML 表，或从 body_sections 正文中抽取候选。只输出 JSON：{"candidates": [...]}。

fields 的键只能使用下面列出的名字，不要把表头原文或自拟英文名当作键。
标的 cob 的键：object_name、category_code、category_name、category_type、brand、product_supplier、spec_model、unit_price、quantity、unit、total_price。
供应商 sub 的键：supplier_name、score、is_winner。
产品名称、货物名称、采购标的、报价明细内容都写入 object_name。品牌写入 brand。规格型号写入 spec_model。制造商写入 product_supplier。
示例：表头为「产品名称、品牌/型号、数量、单价、总价」，一行是「HIS子系统升级、新蓝海、1、400000.00、400000.00」时，输出
{"entity_type":"cob","package_no":"B","fields":{"object_name":"HIS子系统升级","brand":"新蓝海","quantity":"1","unit_price":"400000.00","total_price":"400000.00"},"issues":[]}
不要输出 item_name、brand_model、product_name。
品目号（如 1-1、3-1-1）是行序号，不要写入品目字段。column_mapping 没有品目时，仍要从表头和该行把品目抄上。没有编码或名称就不要编造。
仅有品目名称：表头「品目号、品目名称、采购标的」，一行「1-1、其他商业保险服务、团体重大疾病保险」，输出
{"entity_type":"cob","package_no":"1","fields":{"object_name":"团体重大疾病保险","category_name":"其他商业保险服务"},"issues":[]}
仅有品目编码：表头「品目编号、货物名称、品牌」，一行「A02100499、口腔种植手术机器人、雅客智慧」，输出
{"entity_type":"cob","package_no":"1","fields":{"object_name":"口腔种植手术机器人","category_code":"A02100499","brand":"雅客智慧"},"issues":[]}
品目名称和品目编码都有：表头「品目编号、品目名称、采购标的」，一行「A02050906、工业机器人、潜伏式搬运机器人」，输出
{"entity_type":"cob","package_no":"1","fields":{"object_name":"潜伏式搬运机器人","category_code":"A02050906","category_name":"工业机器人"},"issues":[]}
一列是「品目编号及品目名称」时，格内只有名称就只写 category_name；格内同时有编码和名称就拆进 category_code 和 category_name。
fields 只写表内或 body_sections 里真实出现的值。同一单元格用分号或顿号并列多个标的时，拆成多个候选。
联合体供应商保持全称，不要拆开。资格性审查未通过的供应商不要输出。
没有综合得分、只有技术分或商务分时，score 不要填。
表格以外的正文是**整篇公告的全部段落**（不只是中标小节），一次都给你：逐段看，出现投标（响应）供应商名单、评审得分、中标（成交）结果的段落都要抽成 sub；与供应商无关的段落（公告期限、联系方式、监督部门、发布媒体等）不要输出任何候选。写在正文里的采购人（采购单位）、采购代理机构、评审专家不是投标（响应）供应商，不要输出；资格审查未通过名单里的主体也不要输出——资格审查规则与表格相同。
包号在正文里有多种写法：「第1包」「（第1包）」「包1」「1包」「A包」都要写进 package_no，只写编号本身（不带「第」「包」），判断不了时省略或填 null。同一家供应商在同篇公告里出现多次时，每次出现各自输出一条，不要自己合并——合并由后面的步骤做。
正文写「中标人」「成交供应商」「第一中标候选人」时，该条写 is_winner 为「是」；写在未中标、落标、候选人名单里的，写「否」或不写。
写成「公司名88.33（84.50、92.50、88.00）」时，紧跟公司名、在括号前的数字是综合得分，填入 score；括号里并列的多个数字是评委分数，不要填入 score。只有括号里的评委分数、没有括号前的数字时，score 不要填。
只有单元格原文就是「详见附件」或「见附件」时，该字段不要写成业务值。「按照招标要求提供」这类文字要原样写入。
缺失字段省略，不要编造。
用户输入里已有包号或表归属时，沿用那个编号；判断不了时填 null，不要编一个接近的字符串。
