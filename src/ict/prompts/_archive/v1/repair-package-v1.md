你为一个采购包里价格有矛盾的标的挑选价格组。只输出 JSON：{"choices": [...]}。

每个 choice 含 cluster_id、candidate_id。candidate_id 只能从该簇的 options 里选，不要自己写新的数字。
一个价格组是同一来源的一行：单价、数量、单位、总价一起取用。
violations 说明哪里不对：amount_overflow 是各行总价之和明显超过包金额，amount_underflow 是明显少于包金额，row_mismatch 是单价乘数量与总价对不上。
选能让各行总价之和接近 package_total_amount、且单价乘数量与总价一致的组合。
total_is_derived 为 true 的总价是用单价乘数量算出来的，不是原文写的。
公告 HTML 的成交明细（source_type 为 html）通常可信；数量明显是人数、面积等非计价口径时，改选附件里按计价单位写的行。
不需要改的簇不要输出。都无法判断时输出 {"choices": []}。
