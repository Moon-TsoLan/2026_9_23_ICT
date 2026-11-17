"""Readable page lists from the independently authored labels and frozen routing join."""
from collections import defaultdict
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work/attachment-fieldscope-review-20261109"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def clean(value):
    return str(value or "").replace("|", "／").replace("\n", " ")


def link(path, label):
    return "[" + clean(label) + "](<" + Path(path).resolve().as_posix() + ">)"


def ratio(n, d):
    return f"{n}/{d}（{n/d:.1%}）" if d else "—"


def spans(numbers):
    numbers = sorted(set(numbers))
    groups = []
    for number in numbers:
        if not groups or number != groups[-1][-1] + 1:
            groups.append([number])
        else:
            groups[-1].append(number)
    return "、".join(str(g[0]) if len(g) == 1 else f"{g[0]}–{g[-1]}" for g in groups)


CAUSE = {"rule_name_ignore": "硬忽略", "parse_page_budget": "解析预算外", "extraction_page_budget": "抽取页预算", "extraction_context_budget": "抽取上下文预算", "skip": "模型跳过", "failed": "接口失败", "not_executed": "暂停前未执行", "reading_mode_wrong": "读取方式错误"}


def main():
    summary, rows = read(OUT / "summary.json"), read(OUT / "comparison_rows.json")
    source_resolution = read(OUT / "source_resolution.json")
    reports = OUT / "case_reports"
    reports.mkdir(exist_ok=True)
    all_targets = [r for r in rows if r["review"] and r["review"]["target"] is True]
    write_rows = []
    for aid, case in summary["by_case"].items():
        part = [r for r in rows if r["announcement_id"] == aid]
        targets = [r for r in part if r["review"] and r["review"]["target"] is True]
        uncertain = [r for r in part if r["review"] and r["review"]["target"] == "uncertain"]
        lines = [f"# {aid} 页入口复核", "", clean(case["project_name"]), "",
                 f"源单位{case['source_units']}，已复核{case['reviewed']}，待复核{case['pending']}；确认应读{case['targets']}（物理页{case['targets_physical']}、逻辑片段{case['targets_logical']}），方式{case['expected_reading']}；正确入口覆盖{case['covered']}；不确定{case['uncertain']}。", "",
                 "所有标签相对固定公告快照；不同附件同值不关闭任务。旧动作保持冻结；本页入口为既有排程回放结果，实际OCR/抽取服务未运行。", "", "## 应读单位与实际入口", "",
                 "| 文件及原文 | 坐标 | 应读方式 | 支持字段 | 上轮入口／未进入原因 | 原文依据 |", "|---|---|---|---|---|---|"]
        for r in targets:
            label = r["review"]
            coordinate = ("物理页" if r["physical_page"] else "逻辑片段") + str(r["unit_no"])
            actual = ("OCR" if r["old_action"] == "ocr" else "原生提取") if r["entered"] else CAUSE.get(r["old_loss"], r["old_loss"])
            if r["wrong_native_only"]: actual += "（方式错误）"
            if r["unnecessary_ocr"]: actual += "（原生已足够）"
            lines.append("| " + " | ".join([link(r["source_path"], r["file_name"]), coordinate, label["reading"], clean("、".join(label["fields"])), clean(actual), clean(label["reason"])]) + " |")
        if not targets: lines.append("| — | — | — | — | — | 尚无确认目标；源不可读与不确定单位另列 |")
        lines += ["", "## 已确认的多读", "", "| 文件 | 旧入口方式 | 坐标与页号／片段号 | 数量 |", "|---|---|---|---|"]
        groups = defaultdict(list)
        for r in part:
            if r["overread"]:
                groups[(r["file_key"], r["file_name"], r["source_path"], r["old_action"], r["physical_page"])].append(r["unit_no"])
        for (key, name, path, action, physical), numbers in groups.items():
            lines.append(f"| {link(path, name)} `{key}` | {action} | {'物理页' if physical else '逻辑片段'} {spans(numbers)} | {len(numbers)} |")
        if not groups: lines.append("| — | — | — | 0 |")
        if uncertain:
            lines += ["", "## 不确定单位", ""]
            for r in uncertain:
                lines.append(f"- `{r['unit_key']}` {clean(r['file_name'])}，{clean(r['review']['reason'])}")
        errors = [e for e in summary["source_errors"] if e["announcement_id"] == aid]
        if errors:
            lines += ["", "## 无法展开的源文件", ""]
            for error in errors: lines.append(f"- {clean(error['file_name'])}：{clean(error['error'])}")
        source_issues = [e for e in source_resolution["source_issues"] if e["announcement_id"] == aid]
        if source_issues:
            lines += ["", "## 原始内容可用性核对", ""]
            for issue in source_issues:
                lines.append(f"- {clean(issue['file_name'])}：{clean(issue['reviewed_source_status'])}")
        conflicts = [r for r in targets if r["review"].get("context_conflict")]
        if conflicts:
            lines += ["", "## 公告快照的归属冲突", "",
                      "本案固定公告快照的采购单元与供应商/金额关系存在冲突。应读标签保留纠正归属所需的直接证据；不把该问题记为最终字段尚未实现。原快照保持不改。"]
        lines += ["", "逐单位完整标签、图像和旧动作见上级comparison_rows.json及units目录。"]
        (reports / (aid + ".md")).write_text("\n".join(lines) + "\n", encoding="utf-8")
        write_rows.append([aid, case["project_name"], case["targets"], case["expected_reading"].get("ocr", 0), case["expected_reading"].get("native", 0), case["covered"], case["misses"], case["overread"], case["uncertain"], case["pending"]])
    (OUT / "target_pages.json").write_text(json.dumps(all_targets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    page_list = ["# 应读页与读取方式逐页清单", "", "相对固定公告快照判定；每行一个真实物理页/原生片段，页号是附件物理页号，不能当成页面印刷的页码。原文及图像供逐项核对。", "",
                 "| 案例 | 文件 | 坐标 | 应读方式 | 上轮入口/损失 | 支持字段 | 原文图像 |", "|---|---|---|---|---|---|---|"]
    for r in all_targets:
        actual = r["old_action"] if r["entered"] else CAUSE.get(r["old_loss"], r["old_loss"])
        page_list.append("| " + " | ".join([r["announcement_id"], link(r["source_path"],r["file_name"]),
                         ("物理页" if r["physical_page"] else "逻辑片段")+str(r["unit_no"]), r["review"]["reading"],
                         clean(actual), clean("、".join(r["review"]["fields"])), link(r["image"],"图") if r["image"] else "—"]) + " |")
    (OUT / "target_pages.md").write_text("\n".join(page_list)+"\n",encoding="utf-8")
    all_source = summary["all_source"]
    eligibility = summary["old_read_budget"]
    lines = ["# 38例附件应读页与旧入口对照", "", "2026-11-09。按用户确认的最终字段口径复审，以目标进入正确OCR/原生入口为本阶段成功。", "",
             "状态：" + ("源单位标签已齐，结果见下表。" if summary["complete_page_review"] else "复审或结构核对尚未完成，下列为进行中数据，不作最终验收。"), "",
             "[任务目标与字段口径](../doc/任务目标与最终字段口径.md)已写入AGENTS/README新会话入口。Luna辅助原文复审与主agent复核单列留痕，未经用户独立确认的标签不称人工金标；所有不确定单位单列，旧请求/回答及页预算不改。", "",
             link(OUT / "target_pages.md", "138个应读页及读取方式总清单") + "；详细字段依据和全部多读页分布见下方逐例索引。", "",
             "## 范围", "",
             f"- 冻结38例、314份源文件，完整可展开内容{all_source['source_units']}单位：2898物理页、183原生逻辑片段；旧检查1840单位之外增加1241页。", 
             f"- 已复核{all_source['reviewed']}，待复核{all_source['pending']}，不确定{all_source['uncertain']}；另有{len(summary['source_errors'])}份文件无法展开。不可读源与不确定原件不能当作无目标。", 
             "- 38例中32例有可读采购附件正文；4例源文件是验证码/访问限制HTML，另2例仅有平台维护页。还有1例的部分报价附件为维护页。6例完全缺采购正文的案例不作为零漏读的成功案例。RAR案可列出两个成员，现有PDF已复审；其中一个成员流哈希一致，另一个因RAR校验失败未能完整验证。", 
             "- 当前标签下，旧1840单位以外新增的1241物理页未发现确认的最终字段增量；原先抽样的135个解析预算外单位亦无确认目标。源内容缺失的文件没有可核验的召回分母。", 
             "- 原生全文直接抽取是现行已批准路线，原生无用输入与图像目检误判分别报告。", "",
             "## 最核心对照", "", "| 项目 | 数量 |", "|---|---:|",
             f"| 确认应OCR的页 | {all_source['expected_reading'].get('ocr',0)} |",
             f"| 确认应原生读取的页／片段 | {all_source['expected_reading'].get('native',0)} |",
             f"| 确认应读合计 | {all_source['targets']} |",
             f"| 上轮正确入口覆盖 | {ratio(all_source['covered'],all_source['targets'])} |",
             f"| 解析预算内且未硬忽略的应读单位覆盖 | {ratio(eligibility['covered'],eligibility['targets'])} |",
             f"| 上轮预算前选择OCR／原生 | {summary['old_selected_before_extraction_budget'].get('ocr',0)}／{summary['old_selected_before_extraction_budget'].get('extract',0)} |",
             f"| 上轮预算后OCR／原生入口 | {summary['actual_old_entry']['ocr']}／{summary['actual_old_entry']['extract']} |",
             f"| 无目标仍进入OCR／原生 | {all_source['overread'].get('ocr',0)}／{all_source['overread'].get('extract',0)} |",
             f"| 应OCR却仅进入原生 | {all_source['wrong_native_only']} |",
             f"| 原生已足够但被送OCR | {all_source['unnecessary_ocr']} |", "",
             "上轮真实图像目检动作数：" + clean(summary["old_image_gate_actions"]) + "。其中failed包含暂停前未执行与接口失败，见损失分项。这里的入口来自冻结真实决定及既有零网络生产调度回放，实际OCR和附件抽取服务均未执行；新增第三方API/OCR调用0。", "",
             f"原生入口1146单位细分为{summary['actual_old_entry_coordinates'].get('extract_physical',0)}物理页和{summary['actual_old_entry_coordinates'].get('extract_logical',0)}逻辑片段；OCR入口67个均为物理页。本次138个确认目标均为物理页；逻辑片段仍保留在多读及全量审查范围内。", "",
             "每页相对固定公告独立判断，同一缺失字段可以由多份附件支持；不因另一文件已含同值而关闭本页。页损失数不等于最终业务字段丢失数。", "",
             f"无目标的原生输入为{all_source['overread_coordinates'].get('extract_physical',0)}物理页和{all_source['overread_coordinates'].get('extract_logical',0)}逻辑片段；另有3个源为维护页的逻辑片段进入原生入口，单列为不确定，不充当负例。", "",
             "## 单独评价图像页目检", "",
             f"227个图像目检任务中，212个取得有效动作，8个接口失败，7个在暂停前未执行。有效动作涉及的确认目标，预算前按正确读取方式放行{ratio(summary['image_gate_completed']['selected_covered_before_extraction_budget'],summary['image_gate_completed']['targets'])}；未放行目标与硬忽略、预算损失分开。", "",
             "| 复审应读方式／目标性 | 上轮动作 | 单位数 |", "|---|---|---:|"]
    for pair, count in summary["image_gate_all"]["decision_matrix"].items():
        expected, actual = pair.split(" -> ")
        expected = {"False":"无目标", "native":"应原生", "ocr":"应OCR", "uncertain":"不确定"}.get(expected, expected)
        lines.append(f"| {expected} | {actual} | {count} |")
    lines += ["", "false表示相对固定公告无最终字段增量；uncertain表示源不可用或仍待确认。原生完整直接抽取不经过该图像页价值判断，原生多读不能全归因为目检误判。", "",
              "图像目检有效动作选择104页（69 OCR、35原生），其中确认目标41页、无目标63页；预算后留下102页，确认目标仍为41页、无目标61页。OCR实际入口67页中26页有目标、41页无目标。", "",
             "## 目标未进入的原因", "", "| 原因 | 单位数 |", "|---|---:|"]
    for cause, count in all_source["misses"].items(): lines.append(f"| {CAUSE.get(cause,cause)} | {count} |")
    lines += ["", "## 两处模型漏判与重点案例", "",
              "- 东莞第二批实训室电脑更新（t20260604_26688741）：本国产品声明第3页明确列出无线AP及实训家具制造商，原生文本完整。旧目检称“仅重复已知产品与供应商”，选择skip，漏掉product_supplier增量。", 
              "- 北京安定镇（t20260309_26250037）：分项报价第6页原生署名明确该报价属于北京凤舞星河。固定公告快照存在包/供应商/金额映射冲突；旧目检认为落款重复已知供应商，跳过了必要归属证据。此例的上下文冲突另记，不据此称所有投标主体都是新增。", 
              "- 德州生命线（t20260602_26668587）：应读5页均进入正确入口：开标记录2页OCR、两份报价各1页原生、供应商相关证明第2页原生（服务提供方证据）。旧入口另含38页无目标OCR和57个无目标原生单位；历史案例、人员证书及一般服务方案按最终字段口径排除。完整文件、坐标和旧动作见逐例页单。", ""]
    lines += ["", "## 逐例索引", "", "| 案例 | 应读 | 应OCR | 应原生 | 已覆盖 | 漏读原因 | 多读OCR／原生 | 不确定／待复核 |", "|---|---:|---:|---:|---:|---|---|---|"]
    for aid, name, target, ocr, native, covered, miss, over, uncertain, pending in write_rows:
        label = f"{aid} {name}"
        case_link = link(reports / (aid + ".md"), label)
        lines.append(f"| {case_link} | {target} | {ocr} | {native} | {covered} | {clean('；'.join(CAUSE.get(k,k)+str(v) for k,v in miss.items()) or '—')} | {over.get('ocr',0)}／{over.get('extract',0)} | {uncertain}／{pending} |")
    lines += ["", "## 复算与审计", "",
              "- 清单、快照及分配：work/attachment-fieldscope-review-20261109/manifest.json；固定公告context与每个真实页/片段单列存储。", 
              "- 原始辅助标签：reviews/luna_group1.jsonl、luna_group2.jsonl、luna_group3.jsonl；主agent原文复核root_primary.jsonl；有争议时另写root_adjudications.jsonl，保留原判。", 
              "- 追加协同标签luna_group3_assist.jsonl共195单位；组1自有385、组2共1247、组3原组1150、主agent自有104，合计3081。", 
              "- 读取方式专审：group1_corrections.json修正6页原生误标为OCR，并撤去2个无增量签章页；mode_audit.json独立复核其余29个含图/native目标，纠正1页。主agent另逐页否定把整段服务技术要求当目标的标签。", 
              "- 原始文件SHA256逐份核对314份，1,415,301,625字节无变化；109份生产实现/提示词与上轮冻结哈希一致，38份评审公告快照在补全已有身份字段后保持固定。详见source_integrity.json与integrity.json。", 
              "- 完整逐页对照comparison_rows.json，所有目标target_pages.json，统计summary.json；每例应读页、方式、入口和多读页见case_reports。", 
              "- 准备脚本eval/prepare_fieldscope_review_20261109.py；核对脚本eval/summarize_fieldscope_review_20261109.py；报告脚本eval/report_fieldscope_review_20261109.py。脚本只读取/分片/关联/统计，不用内容词表生成业务标签。", 
              "- 不将1241个旧窗口外页自动并入生产，不改预算与硬忽略；不以Luna的新判断冒充上轮DeepSeek的实际决定。"]
    (ROOT / "eval/38例附件最终字段口径复审-20261109.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
