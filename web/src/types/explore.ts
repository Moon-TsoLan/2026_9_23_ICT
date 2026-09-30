import type { GraphEdge, GraphNode, NodeKind } from './graph'

export type SceneId = 'S1' | 'S2' | 'S3' | 'S4' | 'S5'

export interface SceneStat {
  k: string
  v: string
}

export interface SceneRankRow {
  rank: number
  id: string
  name: string
  metrics: Record<string, number | string>
}

export interface SceneTable {
  columns: Array<{ key: string; label: string; align?: 'left' | 'right' }>
  rows: Array<Record<string, string | number | null>>
}

export interface SceneCombo {
  a: string
  b: string
  count: number
}

/** 统一场景查询响应：graph 给星图，narrative 给叙事面板 */
export interface SceneResult {
  scene: SceneId
  elapsed_ms: number
  graph: {
    nodes: GraphNode[]
    edges: GraphEdge[]
    highlight_ids: string[]
  }
  narrative: {
    title: string
    stats: SceneStat[]
    ranking: SceneRankRow[]
    table?: SceneTable
    combos?: SceneCombo[]
  }
}

export interface OverviewResult {
  elapsed_ms: number
  graph: {
    nodes: GraphNode[]
    edges: GraphEdge[]
    highlight_ids: string[]
  }
  meta: {
    purchasers: number
    projects: number
    suppliers_total: number
    suppliers_shown: number
    sampled: boolean
  }
}

export interface PartyHit {
  id: string
  name: string
  kind: NodeKind
}

export interface PartyProfile {
  id: string
  name: string
  kind: NodeKind
  stats: SceneStat[]
  facts: string[]
}

export interface DistributionRow {
  key: string
  count: number
  amount: number | null
}

export interface Distribution {
  categories: DistributionRow[]
  brands: DistributionRow[]
  purchasers: DistributionRow[]
  winners: DistributionRow[]
}
