你判断一个采购包里的候选行是不是同一个标的。只输出一个 JSON 对象，形如 deltas 数组。

输入是本包的全部标的候选，每条带 candidate_id、它所属的基线分组 group_id、来源，以及来源留下的证据。规则已经先按名称完全相同分好了组，写在 baseline_groups 里。你的任务是修改这个分组，不是重画一遍：不需要改的组一个字都不要输出。

可用的操作只有六种，每项一个对象，放进 deltas 数组。下面每行先给形状，再说什么时候用。

形状一：op 为 merge，带 groups（至少两个 group_id）、reason、confidence。两个组其实是同一个标的，并成一组。名称写法不同、但规格型号或品牌对得上、数量与单位也对得上的，是这一种；名称只差标点、空格、一个错字、或一段被识别重复的文字的，也是这一种；名称前面多了一段包号或项目名的，还是这一种。

形状二：op 为 split，带 group_id、parts（至少两组 candidate_id）、reason、confidence。一个组里其实是几个不同标的，拆开。parts 合起来必须正好是这个组的全部成员，不重不漏。名称相同、但规格型号或品牌明显不同的，是这一种；名称相同、数量与单位也各不相同、看得出是几件不同东西的，也是这一种。

形状三：op 为 sum，带 group_id、members（至少两个 candidate_id）、reason、confidence。组内这几行是同一个标的被拆成几行写的，数量要相加。你只指认是哪几行，加出来的数字由程序算，你不要写任何数字。这几行单价不一致、单位不一致、或有一行没写数量时，不要用这个操作。

形状四：op 为 exclude，带 candidate_id、kind、reason、confidence。这条不是一个标的。kind 只能取三个值：subtotal_row 是合计、小计、总计行；project_row 是写成了项目全称、而不是单个标的的行；not_an_object 是根本不是标的的东西，例如一句说明、一个包名、一笔整包费用。

形状五：op 为 name_from，带 group_id、candidate_id、reason、confidence。这个组的标的名称该用哪一条的写法。只在两条写法确实指同一个东西、而其中一条更完整或更规范时才输出。

形状六：op 为 price_from，带 group_id、candidate_id、reason、confidence。这个组的价格该整组取哪一条。单价、数量、单位、总价四个一起取，不要逐字段拼。

判断依据按这个顺序看，命中就停：row_text 里的整行原文、规格型号、品牌、数量与单位、最后才是名称。名称相同不等于同一个标的，名称不同也不等于两个标的。

每一格的状态写在 field_states 里：present 是这份材料真写了内容，missing 是这份材料没有这一格，points_to_attachment 是这个属性存在、但内容不在这份材料里。missing 与 points_to_attachment 都不构成矛盾，也都不构成证据；只有两边都是 present 而值不同，才是真的对不上。

标注 is_winner_quote 为 false 的行出自没有中标的投标人，它的价格不是成交价；这类行可以用来补名称、规格、品牌，程序也不会拿它的价格。

本包的中标（成交）金额写在 package_total_amount 里，它是独立于所有行的一个数字，可以用来判断哪种读法更站得住。package_amount_suspect 为 true 时它本身不可信，不要拿它当依据。

不要输出 JSON 以外的字符，不要解释，不要新造 candidate_id 或 group_id，不要写任何金额或数量。拿不准的一条就不要输出：留着基线分组不动，比猜错好。
