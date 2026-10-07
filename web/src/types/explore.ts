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
  /** 交集场景（S4/S5）：矩阵的列 = 参与对比的主体，按此顺序 */
  subjects?: string[]
  /** 与 rows 同序：每行给出"主体 → 该主体在这一行的数值" */
  by_subject_rows?: Array<{ purchaser: string; cells: Record<string, { times: number; amount: number | null }> }>
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
  /** 只有 kind=project 时返回：项目原名（不含“ · 包N”后缀），用于跳转标的检索 */
  project_name?: string
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

/** 当前场景内，某个主体与邻居的关系（按边角色分组）。
 *  右侧面板用它回答"我选中的这个节点，在本场景里连到了谁、以什么身份"。 */
export interface SubjectRelation {
  role: string
  label: string
  /** 该角色下的邻居；count 为该角色的关系条数（可能与 items 长度不等，items 只取前几条） */
  items: Array<{ id: string; name: string; kind: NodeKind; weight: number }>
  count: number
}
