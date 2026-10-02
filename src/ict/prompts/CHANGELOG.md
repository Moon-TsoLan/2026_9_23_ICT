# 提示词变更记录

依据：`doc/提示词编写规范.md`（v1）。
旧版本存档：`src/ict/prompts/_archive/`。
文件名保持不变（代码按 `prompt_version` 精确读取）。

## 2026-09-29

- 重写全部 8 个提示词为合规版本（同名覆盖），旧版存档于 `_archive/v1/`。
- 主要改动：
  1. 删除所有真实公告原文示例（原 `announcement` / `html-tables` / `html-candidates` / `attachment-extract` / `repair-package` 中的评测集原句）。
  2. 删除与代码重复的确定性规则（如包号改写"第3包→3"，均由代码归一）。
  3. 补全未定义字段（`expected_fields` 取契约正式字段键）。
  4. 统一结构：任务 → 输入 → 字段/枚举 → 判断准则 → 输出 → 末尾出口。
  5. 每条提示词结尾加"无法判断 → null/unknown/空"出口。

### 修复（同日，回归修复，非补丁）

首轮重写后有 2 处回归，按"字段定义 + 出口"的通用写法修复，未加特例规则：

- `html-tables-v1`：删掉"用 cob_detail，不要用 other"的推动性措辞，改为按表的**列内容**判定角色，并明确"没有能映射到 object_name 的列就不是 cob_detail"。修复 t20260401_26346106 键值表被误判导致标的被丢。
- `attachment-extract-v1` / `html-candidates-v1`：删掉"有编码就写入 category_code"的推动性措辞，改为"category_code/category_name 是政府采购品目分类的编码与名称，无法确认是品目编码时不填"。修复 t20260508_26523336 内部编号被写入 category_code 导致的归一化失败。

### 修复二（同日，包号格式，非补丁）

第二轮重跑发现 t20260202_26139731 的候选 `package_no` 被写成"第3包"，而**候选层代码不做包号值归一**，导致候选被判"无法确定包号"丢弃。原因是首轮重写误信"包号归代码已做"而删除了格式要求。

- `html-tables-v1` / `resolve-packages-v1` / `triage-files-v1` / `locate-pages-v1` / `html-candidates-v1` / `attachment-extract-v1`：在这 6 个输出包号的提示词里恢复一句通用格式要求："只写编号本身，例如 3、A，不要写成'第3包''包3'这类形式。"（通用格式规则，非特例）
- 同步修正 `doc/提示词编写规范.md` §1：明确代码只归一字段键名，不归一包号值。

### 复核（同日，补回被误删的通用规则）

与存档 v1 逐条比对，发现重写时误删了三条通用（非特例）约束，予以补回：

- `html-candidates-v1`：输入已给出 package_no / package_scope 时沿用该编号。
- `triage-files-v1`：plans 里已有的包号必须原样沿用。
- `locate-pages-v1`：只输出 known_packages 内的编号或 announcement、unknown；页上写了别的包号也不要照抄。

未恢复 v1 中的真实公告示例（属污染，应删除）。v1 的"品目号第一段是包号"规则仍只保留在 `resolve-packages-v1`，不在 `html-tables-v1` 重复（契约规定该步由代码补包号）。
