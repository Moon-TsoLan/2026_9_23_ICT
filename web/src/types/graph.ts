/** 星图契约：节点/边/布局坐标 */
export type NodeKind = 'buyer' | 'project' | 'winner' | 'bidder' | 'vendor'
export type EdgeRole = 'win' | 'bid' | 'supply' | 'buy'

export interface GraphNode {
  id: string
  label: string
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
