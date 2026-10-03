你看到的是同一个文件的 1 到 2 张页面缩略图，按给出的顺序编号为 1、2。只判断该文件是否含标的明细行项。只输出 JSON：{"kind":"...","has_price_table":true|false,"header":"...","confidence":0到1}。
kind 只能是 award_detail、bid_quote、winner_detail、tender_requirement、qualification、contract、other。
一行一个货物或服务，且该行带单价、数量或总价，才算标的明细行项。
图上没有行项表格、或表格看不清时：has_price_table 写 false，confidence 不高于 0.5，kind 按可见内容选最接近的一项。
tender_requirement 指采购需求、技术规格、评分办法；qualification 指资格与承诺材料；other 指封面、目录、通知、报酬支付表、纯扫描件。
header 照抄图上表头行前三个格，用 | 连接；没有表格写空串。
随图给出的文件名只是线索：一份 PDF 常把报价一览表、声明函、支付表合订在一起，图上看到什么就判什么。
不要描述版面，不要解释，不要输出 JSON 以外的字符。
