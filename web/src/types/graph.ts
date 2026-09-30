/** 星图契约：节点/边/布局坐标 */
export type NodeKind = 'buyer' | 'project' | 'winner' | 'bidder' | 'vendor'
export type EdgeRole = 'win' | 'bid' | 'supply' | 'buy'

export interface GraphNode {
  id: string
  label: string
  /** 星图上显示的精简名（缺省时由 utils/label 规则推导） */
  labelShort?: string
  kind: NodeKind
  weight: number
}

export interface GraphEdge {
  a: string
  b: string
  role: EdgeRole
  weight: number
}

export interface Placed {
  id: string
  x: number
  y: number
  z: number
  scale: number
}

/**
 * 星图上的展示类别只有三种：采购单位 / 项目 / 供应商。
 * "中标"与"投标"只在某一条边上成立——同一家公司在 A 项目中标、在 B 项目只是陪标，
 * 所以它是关系属性（EdgeRole），不是节点身份。winner/bidder/vendor 在图上都画成"供应商"，
 * 选中一个项目节点时，再用不同的连线把中标/投标/供应区分出来。
 */
export type DisplayKind = 'buyer' | 'project' | 'supplier'

export const DISPLAY_KIND: Record<NodeKind, DisplayKind> = {
  buyer: 'buyer',
  project: 'project',
  winner: 'supplier',
  bidder: 'supplier',
  vendor: 'supplier',
}

export const DISPLAY_KIND_LABEL: Record<DisplayKind, string> = {
  buyer: '采购单位',
  project: '项目',
  supplier: '供应商',
}

export const NODE_KIND_LABEL: Record<NodeKind, string> = {
  buyer: '采购单位',
  project: '项目',
  winner: '中标供应商',
  bidder: '投标参与方',
  vendor: '产品供应商',
}

export const EDGE_ROLE_LABEL: Record<EdgeRole, string> = {
  win: '中标',
  bid: '投标',
  supply: '供应',
  buy: '采购',
}
