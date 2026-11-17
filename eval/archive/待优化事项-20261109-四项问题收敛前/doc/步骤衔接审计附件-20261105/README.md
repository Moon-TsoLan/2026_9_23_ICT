# 实际模型调用与提示词索引

由当前生产调用点静态提取，不包含work中的双路实验。

|步骤|有效提示词全文|调用点|接收校验|
|---|---|---|---|
|understand_announcement|[announcement-v4](effective_prompts/announcement-v4.md)|[src/ict/steps/s01_understand.py:194](../../src/ict/steps/s01_understand.py#L194)|_validate_announcement|
|understand_html_tables|[html-tables-v6](effective_prompts/html-tables-v6.md)|[src/ict/steps/s02_html.py:84](../../src/ict/steps/s02_html.py#L84)|lambda item: _validate_table(item, package_nos)|
|extract_html_candidates|[html-candidates-v5](effective_prompts/html-candidates-v5.md)|[src/ict/steps/s02_html.py:195](../../src/ict/steps/s02_html.py#L195)|lambda item: _validate_source_candidates(item, payload['source_rows'])|
|extract_html_candidates|[html-candidates-v5](effective_prompts/html-candidates-v5.md)|[src/ict/steps/s02_html.py:278](../../src/ict/steps/s02_html.py#L278)|lambda item: _validate_source_candidates(item, body_sections)|
|locate_pages|[locate-pages-v5](effective_prompts/locate-pages-v5.md)|[src/ict/steps/s04_attach.py:399](../../src/ict/steps/s04_attach.py#L399)|_validate_pages|
|extract_attachment_candidates|[attachment-extract-v5](effective_prompts/attachment-extract-v5.md)|[src/ict/steps/s04_attach.py:529](../../src/ict/steps/s04_attach.py#L529)|_validate_candidates|
|review_native_continuation|[native-continuation-v1](effective_prompts/native-continuation-v1.md)|[src/ict/steps/s04_attach.py:794](../../src/ict/steps/s04_attach.py#L794)|validate|
|repair_packages|[repair-package-v1](effective_prompts/repair-package-v1.md)|[src/ict/steps/s08b_repair.py:100](../../src/ict/steps/s08b_repair.py#L100)|validate|
|merge_candidates|[merge-objects-v6](effective_prompts/merge-objects-v6.md)|[src/ict/steps/s08_merge.py:567](../../src/ict/steps/s08_merge.py#L567)|validate|
|complete_suppliers|[supplier-completion-v1](effective_prompts/supplier-completion-v1.md)|[src/ict/steps/s09_supplier.py:50](../../src/ict/steps/s09_supplier.py#L50)|validate|
|bind_packages|[package-binding-v4](effective_prompts/package-binding-v4.md)|[src/ict/bindings.py:193](../../src/ict/bindings.py#L193)|validate_body|
|merge_subs|[merge-subs-v2](effective_prompts/merge-subs-v2.md)|[src/ict/sub_merge.py:75](../../src/ict/sub_merge.py#L75)|validate|
|screen_files|[screen-file-v4](effective_prompts/screen-file-v4.md)|[src/ict/parse/screen.py:160](../../src/ict/parse/screen.py#L160)|lambda value: _validate(value) or ('evidence_refs 必须引用给出的 source_segments' if any((not isinstance(ref, str) or ref not in {segment['source_ref'] for segment in peek.segments} for ref in value.get('evidence_refs', []))) else '') or _validate_need_ids(value, needs)|
|screen_images|[screen-page-v4](effective_prompts/screen-page-v4.md)|[src/ict/parse/screen.py:176](../../src/ict/parse/screen.py#L176)|图像通道自行校验|

有效提示词已包含fields.py动态追加定义；prompt_fields.json中的字段列表是提示词声明，不等同HTTP请求JSON Schema或完整输入键校验。

Schema快照见schemas_snapshot.json；源码哈希见source_sha256.json；接收/校验函数全文见接收与变换函数快照.md。