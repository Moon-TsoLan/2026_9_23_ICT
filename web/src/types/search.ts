import type { Page } from './api'

/** 检索查询条件（全部可选，服务端过滤 + 分页） */
export interface CobSearchQuery {
  kw?: string
  purchaser?: string
  winner?: string
  product_supplier?: string
  project_name?: string
  brand?: string
  category_type?: 'A' | 'B' | 'C' | ''
  source_type?: string
  price_field?: 'unit_price' | 'total_price'
  price_min?: number | null
  price_max?: number | null
  sort?: 'relevance' | 'total_price_desc' | 'total_price_asc' | 'unit_price_desc'
  page?: number
  page_size?: number
}

export interface CobBidder {
  supplier_name: string
  score: number | null
  is_winner: boolean
}

/** 一条标的物视图记录（Project + COB + 中标方 展平） */
export interface CobRecord {
  cob_id: number
  project_id: string
  project_name: string
  package_no: string
  purchaser: string | null
  package_total_amount: number | null
  object_name: string
  category_code: string | null
  category_name: string | null
  category_type: 'A' | 'B' | 'C' | null
  brand: string | null
  product_supplier: string | null
  spec_model: string | null
  unit_price: number | null
  quantity: number | null
  unit: string | null
  total_price: number | null
  winner: { supplier_name: string; score: number | null } | null
  announcement_id: string
  provenance: { source_type: string | null; file_id: string | null }
  /** 详情接口才有：该包全部投标方 */
  bidders?: CobBidder[]
}

export type CobSearchResult = Page<CobRecord>
