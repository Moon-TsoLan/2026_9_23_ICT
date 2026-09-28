/** API 薄封装：只管 HTTP，不含业务逻辑。baseURL 走 vite proxy（/api → :8000）。 */
import { ApiError } from '@/types/api'
import type { Page } from '@/types/api'
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

const BASE = '/api'
const TIMEOUT = 15_000

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT)
  try {
    const res = await fetch(BASE + path, { ...init, signal: ctrl.signal })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = (await res.json()) as { detail?: string }
        if (body.detail) detail = body.detail
      } catch {
        /* 非 JSON 错误体，用 statusText */
      }
      throw new ApiError(res.status, detail)
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

  runState: (announcementId: string) => http<RunState>(`/runs/${announcementId}`),
}
