import type { GraphEdge, GraphNode, StarSnapshot } from '../types/graph'
import type { Distribution, GraphSlice, OverviewResult, PartyHit, SceneId, SceneResult } from '../types/explore'

export type ExploreMode = 'overview' | 'picked' | 'scene' | 'picking' | 'compared' | 's6'
export const edgeKey = (e: GraphEdge) => JSON.stringify([e.a, e.b, e.role])

/** The ambient graph stays immutable; temporary membership is derived from current ownership. */
export function projectGraph(base: { nodes: GraphNode[]; edges: GraphEdge[] }, slices: GraphSlice[],
                             answer?: SceneResult | null, pinned: GraphNode[] = []) {
  const nodes = new Map(base.nodes.map(n => [n.id, n]))
  const edges = new Map(base.edges.map(e => [edgeKey(e), e]))
  for (const graph of [...slices.map(s => s.graph), ...(answer ? [answer.graph] : [])]) {
    for (const n of graph.nodes) if (!nodes.has(n.id)) nodes.set(n.id, n)
    for (const e of graph.edges) edges.set(edgeKey(e), e)
  }
  for (const n of pinned) if (!nodes.has(n.id)) nodes.set(n.id, n)
  return { nodes: [...nodes.values()], edges: [...edges.values()].filter(e => nodes.has(e.a) && nodes.has(e.b)) }
}

export function appendSlice(previous: GraphSlice, page: GraphSlice): GraphSlice {
  if (previous.node.id !== page.node.id || previous.data_version !== page.data_version)
    throw new Error('补载主体或数据版本不一致')
  const graph = projectGraph(previous.graph, [page])
  return { ...page, graph }
}

/** Validate again at commit time, after asynchronous loading. The basket stores identities. */
export function addSubject(basket: PartyHit[], node: GraphNode, max = 4): PartyHit[] {
  if (node.kind !== 'winner') throw new Error('只能选择有中标记录的供应商')
  if (basket.some(n => n.id === node.id)) return basket
  if (basket.length >= max) throw new Error(`最多同时比较${max}家供应商`)
  return [...basket, { id: node.id, name: node.label, kind: node.kind }]
}

export class OperationGate {
  private revision = 0
  private controller = new AbortController()
  begin() { this.controller.abort(); this.controller = new AbortController(); return ++this.revision }
  current(revision: number) { return revision === this.revision && !this.controller.signal.aborted }
  get signal() { return this.controller.signal }
  cancel() { this.controller.abort(); ++this.revision }
}

export interface ExploreSnapshot {
  entryIntent: string
  overview: OverviewResult
  distribution: Distribution | null
  mode: ExploreMode
  focusId: string | null
  subjects: PartyHit[]
  slices: GraphSlice[]
  result: SceneResult | null
  scene: SceneId
  sceneSubject: string
  hiddenKinds: Array<'buyer' | 'project' | 'supplier'>
  layoutMode: 'sphere' | 'disc'
  star: StarSnapshot | null
}

// Memory only: route changes restore the session; a browser reload starts a fresh session.
let saved: ExploreSnapshot | null = null
export function saveExplore(snapshot: ExploreSnapshot) { saved = JSON.parse(JSON.stringify(snapshot)) }
export function restoreExplore() { return saved ? structuredClone(saved) : null }

export function fitDistance(radius: number, verticalFov: number, aspect: number) {
  const vertical = verticalFov * Math.PI / 360
  const horizontal = Math.atan(Math.tan(vertical) * Math.max(aspect, 0.01))
  return radius / Math.sin(Math.min(vertical, horizontal)) * 1.12
}
