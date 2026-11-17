"""Offline comparison with file-level facts in 附件重设计跨案例核对-20261116.md."""
from pathlib import Path
import json
from ict.config import ATTACHMENTS_ROOT
from ict.file_routing import read_file, classify

# Fixed before running the classifier. Image-only evidence is not supplied to pass one.
CASES = [
    ("t20260612_26745069", "附件.pdf", "needs_ocr"),
    ("t20260807_27093354", "*声明*.pdf", "needs_ocr"),
    ("t20260703_26870660", "包(1)_报价表.pdf", "reference"),
    ("t20260703_26870660", "包1供应商评审情况表.pdf", "direct"),
    ("t20260403_26361883", "中小企业声明函.pdf", "direct"),
    ("t20260305_26231838", "*声明*.pdf", "direct"),
    ("t20260602_26667599", "包1供应商评审情况表.pdf", "direct"),
    ("t20260812_27123683", "报价明细表.pdf", "direct"),
    ("t20260812_27123683", "开标一览表.pdf", "direct"),
    ("t20260812_27123683", "*第2轮报价.pdf", "direct"),
    ("t20260511_26545535", "招标文件正文.pdf", "reference"),
    ("t20260401_26347563", "G26041招标文件.pdf", "reference"),
    ("t20260608_26707010", "1包其他厨卫用具-招标文件.pdf", "reference"),
    ("t20260810_27107304", "报价明细表及分项报价表.pdf", "direct"),
    ("t20260807_27091873", "*报价明细附件*.pdf", "direct"),
    ("t20260601_26666947", "分项报价表.pdf", "direct"),
    ("t20260804_27071642", "*项目(3).xlsx", "reference"),
    ("t20260212_26174259", "*报价*.pdf", "unavailable"),
    ("t20260609_26719458", "专家费支付表.png", "needs_ocr"),
]

# Additional documents inspected separately after fixing the first 19 expectations.
ADDITIONAL = [
    ("t20260303_26213769", "声明函.docx", "irrelevant"),
    ("t20260303_26213769", "残疾人福利性单位声明函.pdf", "irrelevant"),
    ("t20260303_26213769", "法定代表人或主要负责人身份证明书.docx", "irrelevant"),
    ("t20260303_26213769", "监狱企业的证明文件.docx", "irrelevant"),
    ("t20260812_27123683", "专家报酬支付表.pdf", "needs_ocr"),
    ("t20260812_27123683", "中小企业声明函.pdf", "direct"),
    ("t20260602_26667599", "*四川均盛达*.pdf", "direct"),
    ("t20260806_27082104", "*公诚管理*.pdf", "direct"),
]


def compare(cases=CASES):
    results = []
    for case, pattern, expected in cases:
        paths = list((ATTACHMENTS_ROOT / case).rglob(pattern))
        if len(paths) != 1:
            raise ValueError((case, pattern, paths))
        result = classify(read_file(paths[0]))
        results.append({"case": case, "file": paths[0].name, "expected": expected, **result})
    return results

if __name__ == "__main__":
    print(json.dumps({"development": compare(), "additional": compare(ADDITIONAL)}, ensure_ascii=False, indent=2))

