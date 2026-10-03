你判断这个附件值不值得深度解析。只输出 JSON：{"kind":"...","has_price_table":true|false,"header":"...","confidence":0到1}。
kind 只能是 award_detail、bid_quote、winner_detail、tender_requirement、qualification、contract、other。
一行一个货物或服务，且该行带单价、数量或总价，才算标的明细行项。
award_detail 是中标（成交）明细表；bid_quote 是投标（响应）报价明细、分项报价表、报价一览；winner_detail 是供应商与标的的对应表。
tender_requirement 指采购需求、技术规格、商务条款、评分办法。即使里面写了预算单价或数量，也写 tender_requirement：它不能新增标的，不需要深度解析。
qualification 指资格证明、声明函、承诺函、业绩与信用材料。contract 指合同文本。other 指文件目录、通知、发票、评审专家报酬支付、盖章扫描件。
header 写表头行前三个格子的原文，用 | 连接；没有表格写空串。
confidence 取 0 到 1，表示 kind 的可信度；节选里没有表格时不高于 0.6。
示例：节选里有「办公用打印机、佳能、4,890.00、3台、14,670.00」这样的行，输出
{"kind":"bid_quote","has_price_table":true,"header":"货物名称|品牌|单价","confidence":0.9}
示例：节选只有「我方承诺近三年无重大违法记录」这类自述，输出
{"kind":"qualification","has_price_table":false,"header":"","confidence":0.95}
file_name 只是线索之一，不可单独定类：一个文件常把报价一览表、声明函、支付表合订成一份，名字里同时出现明细词与资格词。
名称写明的章节若没有出现在内容节选里，按内容节选判断，不按名称判断。
内容节选不能证明的行项不要臆造；只依据给出的文字与表头判断。
不要复述节选，不要解释，不要输出 JSON 以外的字符。
