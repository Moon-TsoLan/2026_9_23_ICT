# 服务器解析服务

版面检测放服务器（CPU 够，PP-DocLayoutV3 很小），识别仍打 6006 的 vLLM。转换、取页范围、合页都在服务里做完；客户端只发文件和页号、拿回结构，两边都不落盘。

## 三步

```bash
scp -r deploy root@HOST:/root/deploy
ssh root@HOST
bash /root/deploy/install_parse.sh        # 装 CPU 版；版面也想上 GPU：DEVICE=gpu bash ...

set -a; . /opt/ict-parse/parse.env; set +a
cd /root/deploy
python3 probe_paddlex.py --file '/root/投标（响应）报价明细表([2]).pdf' --pages 1-6 --out /root/probe_report.json

python3 -m uvicorn parse_service:app --host 0.0.0.0 --port 6008
curl -s localhost:6008/health
```

探针文件不是随便挑的：`投标（响应）报价明细表([2]).pdf` 的第 2 到 6 页是同一张大表跨页，正好验 `restructure_pages` 有没有真把表接上。同一页本地 pymupdf 抽出的是 `8,89\n0.00\n00\n元`，顺带能对比字段保真度。

## 探针要回传什么

`probe_report.json` 里这几处决定服务代码怎么钉死，这些运行时对象在本机看不见：

| 字段 | 决定 |
|---|---|
| `pages[].json_view.res_keys`、`first_blocks[].keys` | `page_blocks()` 取块路径 |
| `pages[].markdown_keys` / `markdown_head` | `page_markdown()` 用哪个方法、md 里表格是什么形式 |
| `pages[].seconds`、`init_seconds` | 每则公告的页预算与并发档位 |
| `restructure` / `restructure_error` | 跨页表交给官方，还是我们兜底合并 |

## 资源档位

| 项 | 实测 / 预算 | 说明 |
|---|---|---|
| vLLM 识别 | 并发 8→2.06 页/s，16→2.98，32→3.37 页/s | 16 最划算，再高只是排队 |
| PP-DocLayoutV3 版面 | CPU 约 50～150 ms/页 | 单进程就够喂满 16 并发的识别；CPU 只占一半有余量 |
| LibreOffice | 转换时 CPU 峰值，秒级到十几秒 | 每次转换独立 `-env:UserInstallation`，避免多进程抢同一把 profile 锁 |
| 服务并发 | 客户端 `ICT_PARSE_MAX_INFLIGHT=3` × 服务端 `start_parse.sh --workers 4` | 两端相乘，见 `doc/全链路改造计划.md` §10.2 的实测表；每个 worker 进程另有 `ICT_PARSE_FILE_WORKERS` 与 `ICT_PARSE_MAX_CONCURRENCY` |

## 暴露方式

推荐只开隧道：`ssh -L 6008:127.0.0.1:6008 root@HOST`，客户端打 `http://127.0.0.1:6008`。vLLM 走实例内回环 `127.0.0.1:6006`，不必再暴露公网。若一定要走平台映射对外，先 `export ICT_PARSE_TOKEN=<随机串>`，客户端每次带 `x_parse_token`（/parse 表单字段）或 `token`（/parse/base64 的 JSON 字段）。

## 回滚

装的东西只在 pip 环境、`/opt/ict-parse`、`/root/deploy` 里；停掉 6008 进程就回到现状。直连 6006 用 `OCR:` / `Table Recognition:` 的退路已实测，数据在 `work/vl_probe_20261002/`。

## 已知待办

- 权重：脚本会检查 `PaddleOCR-VL-1.6` 与 `PP-DocLayoutV3`，缺了首次运行联网下，或 scp 本机 `D:\PaddleOCR-VL-5060\models\official_models\` 那份。
- `page_blocks()` / `page_markdown()` 现在是防御式双写（dict 与对象都试），探针回来后按实测字段收敛成一条。
- xlsx 不转 PDF：单元格里是数据，转 PDF 会被分页截列、丢结构，仍走 openpyxl 原生读；只有 doc/docx/wps 走转换。
