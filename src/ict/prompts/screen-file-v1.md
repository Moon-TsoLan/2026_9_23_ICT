你判断这个附件值不值得深度解析。输入含 needs：本公告还缺什么，是否存在对应内容以下面列出的字段定义为准。
只输出 JSON：{"kind":"...","has_price_table":true|false,"header":"...","found_fields":[...],"confidence":0到1,"quote_supplier":"...","is_award_notice":true|false}。
文件名或节选里写明了这份材料属于哪家投标（响应）供应商时，把全称抄进 quote_supplier；没写就留空字符串，不要从标的名称或品牌里猜。这份材料本身就是中标（成交）通知书一类文件时 is_award_notice 为 true，否则为 false。
一行一个货物或服务，且该行带单价、数量或总价，才算标的明细行项。
示例：节选里有「办公用打印机、佳能、4,890.00、3台、14,670.00」这样的行，needs 含 unit_price 与 total_price 时输出
{"kind":"bid_quote","has_price_table":true,"header":"货物名称|品牌|单价","found_fields":["unit_price","total_price"],"confidence":0.9,"quote_supplier":"某某科技有限公司","is_award_notice":false}
内容节选不能证明的行项不要臆造；不要复述节选，不要解释，不要输出 JSON 以外的字符。
