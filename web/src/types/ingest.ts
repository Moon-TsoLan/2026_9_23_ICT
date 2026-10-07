/** 数据接入契约：公告列表 + 处理流水线 */
export interface AnnouncementItem {
  announcement_id: string
  title: string
  announcement_type: string | null
  run_status: string | null
  review_required: boolean
  created_at: string | null
  /** true = 合成演示数据；false = 黄金真实抽取结果 */
  synthetic: boolean
  projects: number
  cobs: number
}

export type RunStatus = 'pending' | 'running' | 'success' | 'partial' | 'failed'
export type StepStatus = 'pending' | 'running' | 'success' | 'partial' | 'failed' | 'skipped'

/** ---- 上传 → 建任务：与 server/ingest.py 的接口对齐 ---- */

export interface PrecheckItem {
  announcement_id: string
  has_html: boolean
  has_zip: boolean
}

export interface PrecheckResult {
  items: PrecheckItem[]
  html_only: string[]
  zip_only: Array<{ announcement_id: string; filename: string }>
  invalid: string[]
  already_extracted: string[]
}

export type JobStatus = 'draft' | 'queued' | 'running' | 'done' | 'failed'
export type JobItemStatus = 'waiting' | 'running' | 'done' | 'failed'

export interface JobItem {
  announcement_id: string
  has_zip: boolean
  overwrite: boolean
  status: JobItemStatus
  current_step: string | null
  error: string | null
  /** 提取进度（读自 run_state.json；只在跑过之后有） */
  progress?: { done: number; total: number; current_step: string | null; status: string } | null
}

export interface IngestJob {
  job_id: string
  tenant_id: string
  created_at: string
  updated_at: string
  status: JobStatus
  items: JobItem[]
}

export interface UploadOutcome {
  announcement_id: string
  kind: 'html' | 'zip'
  /** 同名已存在：服务端直接跳过（等于断点续传） */
  skipped: boolean
  size: number
}

/** 01 页那张表的一行：一则公告，四种用户可见状态 */
export type RecordStatus = 'waiting' | 'running' | 'done' | 'failed'

export interface IngestRecord {
  announcement_id: string
  title: string
  status: RecordStatus
  job_id: string | null
  /** 该任务一共几则公告（丢弃时提示用） */
  job_total: number | null
  /** 整条任务都还没开跑 → 允许丢弃 */
  job_discardable: boolean
  error: string | null
  /** 磁盘上那份 run_state 是否属于本次尝试（重新上传后，旧记录还在，不能拿来展示） */
  run_started: boolean
  progress: { done: number; total: number; current_step: string | null } | null
  projects: number | null
  cobs: number | null
  updated_at: string | null
}

export interface IngestRecords {
  total: number
  done_total: number
  page: number
  page_size: number
  items: IngestRecord[]
}

export const RECORD_STATUS_LABEL: Record<RecordStatus, string> = {
  waiting: '等待中',
  running: '处理中',
  done: '已完成',
  failed: '失败',
}

export interface RunStep {
  step: string
  status: StepStatus
  output_file: string | null
  started_at: string | null
  ended_at: string | null
  failure_code: string | null
  failure_message: string | null
}

export interface RunState {
  run_id: string
  announcement_id: string
  created_at: string
  updated_at: string
  status: RunStatus
  current_step: string
  steps: RunStep[]
  counts: {
    projects: number
    files: number
    selected_files: number
    selected_pages: number
    html_candidates: number
    attachment_candidates: number
    normalized_candidates: number
    final_cobs: number
    final_subs: number
  }
  review_required: boolean
}

/** 步骤中文名（与后端 steps[].step 对齐） */
export const STEP_LABEL: Record<string, string> = {
  understand_announcement: '读入公告',
  understand_html_tables: '理解表格',
  extract_html_candidates: '抽候选',
  plan_project_gaps: '规划缺口',
  triage_files: '筛附件',
  parse_pages: '解析页面',
  locate_pages: '定页',
  extract_attachment_candidates: '抽附件',
  normalize_candidates: '规范化',
  merge_candidates: '合并去重',
  repair_packages: '检查修复',
  persist_and_report: '入库',
}

export const FAILURE_LABEL: Record<string, string> = {
  no_attachment: '公告没有附件',
  attachment_index_miss: '附件索引缺失',
  unsupported_image: '图片文件暂不支持',
  scanned_or_low_text_pdf: '附件疑似扫描件',
  encrypted_pdf: 'PDF 加密无法读取',
  watermark_interference: '水印干扰导致内容不可读',
  document_parse_failed: '文档解析失败',
  package_structure_unclear: '无法判断包结构',
  llm_schema_invalid: '模型输出格式不合法',
  llm_call_failed: '模型调用失败',
  no_text_extractable: '没有可提取文本',
  no_candidate_extracted: '未抽到任何候选',
  field_normalization_failed: '字段规范化失败',
  merge_conflict_unresolved: '合并冲突未解决',
  page_context_truncated: '页面超出上限未抽取',
  consistency_check_failed: '一致性检查未通过',
  unexpected_error: '未归类异常',
}
