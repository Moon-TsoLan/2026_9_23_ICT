/** API 薄封装：只管 HTTP，不含业务逻辑。baseURL 走 vite proxy（/api → :8000）。 */
import { ApiError } from '@/types/api'
import type { Health, Page } from '@/types/api'
import type { CobRecord, CobSearchQuery } from '@/types/search'
import type {
  Distribution,
  OverviewResult,
  PartyHit,
  PartyProfile,
  SceneResult,
} from '@/types/explore'
import type { SceneId } from '@/types/explore'
import type { AnnouncementItem, RunState } from '@/types/ingest'
import type { IngestJob, PrecheckResult, UploadOutcome } from '@/types/ingest'
import type { IngestRecords } from '@/types/ingest'

const BASE = '/api'
const TIMEOUT = 15_000

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT)
  try {
    const res = await fetch(BASE + path, { ...init, signal: ctrl.signal })
    if (!res.ok) {
      let detail = res.statusText
      let raw: unknown = undefined
      try {
        const body = (await res.json()) as { detail?: unknown }
        raw = body.detail
        if (typeof body.detail === 'string') detail = body.detail
        else if (body.detail && typeof body.detail === 'object') {
          detail = String((body.detail as { message?: string }).message ?? detail)
        }
      } catch {
        /* 非 JSON 错误体，用 statusText */
      }
      throw new ApiError(res.status, detail, raw)
    }
    return (await res.json()) as T
  } finally {
    clearTimeout(timer)
  }
}

function qs(params: Record<string, string | number | null | undefined>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

function searchParams(query: CobSearchQuery): Record<string, string | number | null | undefined> {
  return {
    kw: query.kw,
    purchaser: query.purchaser,
    winner: query.winner,
    product_supplier: query.product_supplier,
    project_name: query.project_name,
    brand: query.brand,
    category_type: query.category_type,
    source_type: query.source_type,
    price_field: query.price_field,
    price_min: query.price_min,
    price_max: query.price_max,
    sort: query.sort,
    page: query.page,
    page_size: query.page_size,
  }
}

export const api = {
  /** 健康检查：announcements 用来算"新增几则"，data_version 用来判断变没变 */
  health: () => http<Health>('/health'),

  searchObjects: (query: CobSearchQuery) =>
    http<Page<CobRecord>>(`/objects/search${qs(searchParams(query))}`),

  objectDetail: (cobId: number) => http<CobRecord>(`/objects/${cobId}`),

  /** CSV 导出直接走浏览器下载，不需要经过 fetch */
  exportUrl: (query: CobSearchQuery) => `${BASE}/objects/export${qs(searchParams(query))}`,

  sceneQuery: (scene: SceneId, subjects: string[], limit = 5) =>
    http<SceneResult>(`/scenes/${scene}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subjects, limit }),
    }),

  overview: () => http<OverviewResult>('/graph/overview'),

  parties: (kw: string) => http<{ items: PartyHit[] }>(`/parties${qs({ q: kw })}`),

  partyProfile: (id: string) => http<PartyProfile>(`/parties/${encodeURIComponent(id)}`),

  distribution: () => http<Distribution>('/stats/distribution'),

  announcements: () => http<{ total: number; items: AnnouncementItem[] }>('/announcements'),

  /** 01 页那张表：一行一则公告（等待中/处理中/已完成/失败），服务端分页 */
  ingestRecords: (page: number, pageSize: number) =>
    http<IngestRecords>(`/ingest/records${qs({ page, page_size: pageSize })}`),

  runState: (announcementId: string) => http<RunState>(`/runs/${announcementId}`),

  /** ---- 上传 → 建任务（见 doc/全链路改造计划.md §4） ---- */

  /** 只把文件名发给后端配对；不落盘。配对规则只有后端一份。 */
  precheck: (filenames: string[]) =>
    http<PrecheckResult>('/ingest/precheck', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames }),
    }),

  createJob: (items: Array<{ announcement_id: string; has_zip: boolean }>, overwrite: string[]) =>
    http<IngestJob>('/ingest/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items, overwrite }),
    }),

  startJob: (jobId: string) =>
    http<IngestJob>(`/ingest/jobs/${encodeURIComponent(jobId)}/start`, { method: 'POST' }),

  /** 删掉一条还没开始跑的任务记录（放弃上传时用）。 */
  deleteJob: (jobId: string) =>
    http<{ deleted: string }>(`/ingest/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' }),

  /**
   * 单文件上传（逐文件、一个请求一个文件，同名由服务端跳过）。
   *
   * 这里用 XHR 而不是 fetch：只有 XMLHttpRequest 能拿到上传进度（fetch 没有 upload 进度事件）。
   * 大文件不设总超时，靠字节推进；连接失败由 onerror 兜。
   */
  uploadJobFile: (jobId: string, file: File, onProgress?: (sent: number, total: number) => void) =>
    new Promise<UploadOutcome>((resolve, reject) => {
      const form = new FormData()
      form.append('file', file, file.name)
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${BASE}/ingest/jobs/${encodeURIComponent(jobId)}/files`)
      xhr.timeout = 0
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(event.loaded, event.total)
      }
      xhr.onload = () => {
        let body: { detail?: unknown } | UploadOutcome | null = null
        try {
          body = JSON.parse(xhr.responseText) as { detail?: unknown }
        } catch {
          body = null
        }
        if (xhr.status >= 200 && xhr.status < 300 && body) {
          resolve(body as UploadOutcome)
          return
        }
        const detail = (body as { detail?: unknown } | null)?.detail
        const message =
          typeof detail === 'string'
            ? detail
            : ((detail as { message?: string } | undefined)?.message ?? xhr.statusText)
        reject(new ApiError(xhr.status, message || '上传失败', detail))
      }
      xhr.onerror = () => reject(new ApiError(0, '无法连接后端服务'))
      xhr.ontimeout = () => reject(new ApiError(0, '上传超时'))
      xhr.send(form)
    }),
}
