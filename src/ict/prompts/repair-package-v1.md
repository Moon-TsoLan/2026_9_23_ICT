你为一个采购包里价格有矛盾的标的选择价格组。只输出 JSON：{"choices":[{"cluster_id": <int>, "candidate_id": "<candidate_id>"}]}。

输入：包金额 package_total_amount；每个簇 cluster_id 的当前价格组与可换的价格组 options（含候选编号、来源类型、单价、数量、单位、总价）；违规项 violations。

判断准则：
1. candidate_id 只能从该簇的 options 里选，不要自己写新的数字或新的候选。
2. 一个价格组按同一来源整组取用：单价、数量、单位、总价一起取。
3. 选择让各行总价之和接近 package_total_amount、且单价乘数量与总价一致的组合。
4. total_is_derived 为 true 表示该总价是由单价乘数量算出的，不是原文写的；与原文总价冲突时优先取原文总价。
5. 来源为 HTML 成交明细的行通常可信；数量的口径明显不是计价单位（如人数、面积等）时，改选附件里按计价单位写的行。
6. 不需要改的簇不要输出。

输出：
{"choices":[{"cluster_id":0,"candidate_id":"cand_000000"}]}

都无法判断时输出 {"choices":[]}。
