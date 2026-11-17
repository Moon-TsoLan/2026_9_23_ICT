你为一个采购包里价格有矛盾的标的挑选价格组。只输出 JSON：{"choices": [...]}。

选号范围仅限该簇 options 里给出的 candidate_id，不要自己写新的数字。
一个价格组是同一来源的一行：单价、数量、单位、总价一起取用。
选项里的 reading_kind 为 sum 时，这一组是若干行相加算出来的，它的 candidate_id 是这组读法的编号而不是某一行的编号；照抄它，不要改写，也不要自己算和。
选能让各行总价之和接近 package_total_amount、且单价乘数量与总价一致的组合。
公告 HTML 的成交明细（source_type 为 html）通常可信；数量明显是人数、面积等非计价口径时，改选附件里按计价单位写的行。
不需要改的簇不要输出。都无法判断时输出 {"choices": []}。

字段含义
package_no 采购单元内部引用，必须使用输入目录中的原样引用；与对外展示名称分开。第一步只抄原文编号，缺号可为 null，由封装分配引用。
package_total_amount 某个包的中标（成交）金额；不是单个标的行的总价，也不是公告层面的项目总额。
violations 该包未通过的检查项，取值 amount_overflow 各行总价之和明显超过包金额、amount_underflow 明显少于包金额、row_mismatch 单价乘数量与总价对不上。
clusters 需要选价格组的标的清单，每项含 cluster_id、object_name、current_candidate_id、options。
cluster_id 同一个标的对应的一组候选价格。
object_name 当前成交输出单元的标的名称：货物、服务或工程；整体服务确实以项目全称命名时可用原文，不为无名行猜填项目名。
current_candidate_id 当前使用的价格组编号。
options 该标的可选的价格组集合。
candidate_id 候选对象的编号。
file_id 本则公告内文件的编号。
file_class 上一步判定的文件内容类型。
source_type 这条记录来自 html（公告正文）还是附件文件。
source_priority 这条记录来源的可信度分值，越大越优先；HTML 明细与中标/成交类来源高，采购需求类低。
unit_price 单价，即每个计价单位的金额。
quantity 数量，纯数值；人数、面积、点位数等不是计价数量。
unit 计价单位，如台、套、项、人。
total_price 当前最终标的的中标成交合计金额。抽取时只抄该标的成交金额，预算、最高限价、报价模板不填；合并时模型确认整体成交对象与本包金额对应后，可显式引用 p:amount；程序计算值单独记录依赖。
total_is_derived true 表示这个总价是单价乘数量算出来的，原文里没有写。
reading_kind 这组价格读法的类型，取值 single 取自某一行、sum 由若干行相加算出。
sources 组成这组读法的 candidate_id 列表；single 只有一项，sum 含参与相加的全部行。
choices 为每个标的选定的价格组，每项含 cluster_id、candidate_id。
