/** 星图力导布局 —— 从原 universe.ts 的 placeStars 移植并泛化。
 *
 * 保留：原始力常数（斥力 26/d²、弹簧 rest/k、home 牵引、质量、分离步）。
 * 泛化：不再依赖硬编码 buyer 座位；任意场景图（无 buyer / 无 project）都可排。
 * 策略：在"原始单位"下布局（25 节点时视觉最佳），再按 sqrt(n/25) 整体放大，
 *       保持相对密度与原视觉一致；StarMap 按同一比例适配雾/星尘/相机。
 */
import type { GraphEdge, GraphNode, NodeKind, Placed } from '@/types/graph'

type V3 = { x: number; y: number; z: number }

export const homeEye = { x: 0.78, y: 0.5, z: 0.9 }

export function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967295
}

const v = (x = 0, y = 0, z = 0): V3 => ({ x, y, z })
const add = (a: V3, b: V3): V3 => ({ x: a.x + b.x, y: a.y + b.y, z: a.z + b.z })
const sub = (a: V3, b: V3): V3 => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z })
const mul = (a: V3, s: number): V3 => ({ x: a.x * s, y: a.y * s, z: a.z * s })
const len = (a: V3) => Math.hypot(a.x, a.y, a.z)
const norm = (a: V3): V3 => mul(a, 1 / (len(a) || 1))
const cross = (a: V3, b: V3): V3 => ({
  x: a.y * b.z - a.z * b.y,
  y: a.z * b.x - a.x * b.z,
  z: a.x * b.y - a.y * b.x,
})

const REST: Record<string, { rest: number; k: number }> = {
  buy: { rest: 8.6, k: 0.04 },
  supply: { rest: 5, k: 0.05 },
  win: { rest: 6.4, k: 0.05 },
  bid: { rest: 6.4, k: 0.05 },
}

/** 黄金角散射（Phyllotaxis）：均匀散开中心节点，替代原版硬编码座位。
 *  深度抖动 ±14（对齐原版座位的 z 跨度），避免"摊大饼"的扁平感。 */
function scatter(ids: string[], spacing: number, face: (x: number, y: number, d: number) => V3): Map<string, V3> {
  const homes = new Map<string, V3>()
  const golden = Math.PI * (3 - Math.sqrt(5))
  ids.forEach((id, i) => {
    const angle = i * golden + 0.4
    const radius = i === 0 ? 0 : spacing * Math.sqrt(i)
    homes.set(id, face(Math.cos(angle) * radius, Math.sin(angle) * radius * 0.9, (hash(id + 'd') - 0.5) * 28))
  })
  return homes
}

/** 排布模式：disc = 原来的圆盘座位 + 深度抖动；sphere = 簇心铺满球面、簇内球壳 */
export type LayoutMode = 'disc' | 'sphere'

export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[], mode: LayoutMode = 'disc'): Placed[] {
  const n = nodes.length
  if (!n) return []

  const byId = new Map(nodes.map((b) => [b.id, b]))
  const neighbors = new Map<string, Set<string>>()
  const projLinksOf = new Map<string, Set<string>>() // 任意节点 → 相连的 project 节点
  const buyerOf = new Map<string, string>() // project → buyer
  const degree = new Map<string, number>()
  const addEdge = (a: string, b: string) => {
    if (!byId.has(a) || !byId.has(b)) return
    neighbors.get(a)!.add(b)
    neighbors.get(b)!.add(a)
    degree.set(a, (degree.get(a) ?? 0) + 1)
    degree.set(b, (degree.get(b) ?? 0) + 1)
    if (byId.get(a)!.kind === 'project') projLinksOf.get(b)!.add(a)
    if (byId.get(b)!.kind === 'project') projLinksOf.get(a)!.add(b)
  }
  for (const b of nodes) {
    neighbors.set(b.id, new Set())
    projLinksOf.set(b.id, new Set())
    degree.set(b.id, 0)
  }
  for (const e of edges) {
    addEdge(e.a, e.b)
    if (e.role === 'buy') {
      const [proj, buyer] = byId.get(e.a)?.kind === 'project' ? [e.a, e.b] : [e.b, e.a]
      if (byId.get(proj)?.kind === 'project' && byId.get(buyer)?.kind === 'buyer') buyerOf.set(proj, buyer)
    }
  }

  /** 星体大小：原版公式（buyer 恒 6.6；project 按度数；其余 base + sqrt(项目连接数)） */
  function starScale(id: string): number {
    const b = byId.get(id)!
    if (b.kind === 'buyer') return 6.6
    if (b.kind === 'project') return 1.85 + Math.sqrt(Math.max(degree.get(id) ?? 1, 1)) * 1.15
    const links = projLinksOf.get(id)!.size || (degree.get(id) ?? 0)
    const base = b.kind === 'vendor' ? 0.95 : b.kind === 'bidder' ? 1.1 : 1.25
    return base + Math.sqrt(Math.max(links, 1)) * 1.55
  }

  const eye = norm(homeEye)
  const right = norm(cross(eye, { x: 0, y: 1, z: 0 }))
  const up = norm(cross(right, eye))
  const face = (x: number, y: number, depth: number) => add(add(mul(right, x), mul(up, y)), mul(eye, depth))

  /** Fibonacci 球：把 count 个点均匀铺在球面上，深度由结构本身产生 */
  const fibSphere = (count: number, radius: number, flatten = 0.86): V3[] => {
    const pts: V3[] = []
    const golden = Math.PI * (3 - Math.sqrt(5))
    for (let i = 0; i < count; i++) {
      const y = count === 1 ? 0 : 1 - (i / (count - 1)) * 2
      const ring = Math.sqrt(Math.max(0, 1 - y * y))
      const th = golden * i
      pts.push({ x: Math.cos(th) * ring * radius, y: y * radius * flatten, z: Math.sin(th) * ring * radius })
    }
    return pts
  }
  /** 每个星团一个随机朝向，避免所有盘面共面（那正是"扁平"的来源） */
  const rotOf = (seed: string) => {
    const a = hash(seed + 'a') * Math.PI * 2
    const bb = hash(seed + 'b') * Math.PI * 2
    const ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(bb), sb = Math.sin(bb)
    return (p2: V3): V3 => {
      const y = p2.y * ca - p2.z * sa
      const z = p2.y * sa + p2.z * ca
      return { x: p2.x * cb - y * sb, y: p2.x * sb + y * cb, z }
    }
  }
  const shellHomes = (ids: string[], radius: number, seed: string) => {
    const rot = rotOf(seed)
    const pts = fibSphere(ids.length, radius)
    const m = new Map<string, V3>()
    ids.forEach((id, i) => m.set(id, rot(pts[i] ?? { x: 0, y: 0, z: 0 })))
    return m
  }
  const randDir = (seed: string, radius: number): V3 => {
    const u = hash(seed) * 2 - 1
    const th = hash(seed + 't') * Math.PI * 2
    const ring = Math.sqrt(Math.max(0, 1 - u * u))
    return { x: Math.cos(th) * ring * radius, y: u * radius * 0.86, z: Math.sin(th) * ring * radius }
  }

  const home = new Map<string, V3>()
  const pos = new Map<string, V3>()
  const vel = new Map<string, V3>()
  const setHome = (id: string, p: V3) => {
    home.set(id, p)
    pos.set(id, { ...p })
    vel.set(id, v())
  }

  // ① 中心层：buyer 优先；没有 buyer 时以 supplier 层为中心（S3/S5 场景图）
  const buyers = nodes.filter((b) => b.kind === 'buyer').map((b) => b.id)
  const orgs = nodes.filter((b) => b.kind !== 'buyer' && b.kind !== 'project')
  const centers = buyers.length ? buyers : orgs.map((b) => b.id)
  if (mode === 'sphere') {
    const pts = fibSphere(centers.length, 30 * Math.sqrt(Math.max(centers.length, 1)) * 0.62)
    centers.forEach((id, i) => setHome(id, pts[i] ?? v()))
  } else {
    const centerHomes = scatter(centers, 30, face)
    for (const id of centers) setHome(id, centerHomes.get(id)!)
  }

  // ② 项目层：有 buyer 的绕 buyer 转；没有的绕相连中心点的质心
  const projects = nodes.filter((b) => b.kind === 'project')
  const ringOf = new Map<string, number>()
  const byBuyer = new Map<string, string[]>()
  for (const p of projects) {
    const buyer = buyerOf.get(p.id)
    if (buyer) {
      const list = byBuyer.get(buyer) ?? []
      list.push(p.id)
      byBuyer.set(buyer, list)
    }
  }
  for (const [buyer, list] of byBuyer) {
    const c = home.get(buyer)!
    const rad = list.length > 1 ? (mode === 'sphere' ? 16 * Math.sqrt(list.length / 4) + 6 : 16) : 9
    if (mode === 'sphere') {
      const shell = shellHomes(list, rad, buyer)
      for (const pid of list) {
        setHome(pid, add(c, shell.get(pid)!))
        ringOf.set(pid, rad)
      }
    } else {
      list.forEach((pid, i) => {
        const even = (i / Math.max(list.length, 1)) * Math.PI * 2 + 0.4
        const local = face(Math.cos(even) * rad, Math.sin(even) * rad * 0.78, (hash(pid) - 0.5) * 10)
        setHome(pid, add(c, local))
        ringOf.set(pid, rad)
      })
    }
  }
  for (const p of projects) {
    if (home.has(p.id)) continue
    const linked = [...neighbors.get(p.id)!].filter((id) => home.has(id)).map((id) => home.get(id)!)
    const c = linked.length
      ? mul(linked.reduce((a, x) => add(a, x), v()), 1 / linked.length)
      : v()
    const local =
      mode === 'sphere' ? randDir(p.id, 12) : face(Math.cos(hash(p.id) * Math.PI * 2) * 12, Math.sin(hash(p.id) * Math.PI * 2) * 9.4, (hash(p.id + 'z') - 0.5) * 8)
    setHome(p.id, add(c, local))
  }

  // ③ 其余主体：多个项目取质心，单个项目做径向偏移（原版逻辑）
  for (const org of orgs) {
    if (home.has(org.id)) continue
    const centers2 = [...projLinksOf.get(org.id)!].map((id) => home.get(id)).filter(Boolean) as V3[]
    let p: V3
    if (centers2.length <= 1) {
      const c = centers2[0] ?? v()
      const rad = 9 + hash(org.id + 'r') * 2
      p = add(
        c,
        mode === 'sphere'
          ? randDir(org.id, rad)
          : face(Math.cos(hash(org.id) * Math.PI * 2) * rad, Math.sin(hash(org.id) * Math.PI * 2) * rad * 0.8, (hash(org.id + 'z') - 0.5) * 8),
      )
    } else {
      p = mul(centers2.reduce((a, x) => add(a, x), v()), 1 / centers2.length)
      p = add(p, mode === 'sphere' ? randDir(org.id + 'm', 5) : mul(up, (hash(org.id) - 0.5) * 6))
    }
    setHome(org.id, p)
  }

  // ④ 力导（原版常数；步数按规模自适应）。
  // 热循环用 TypedArray 标量计算（无对象分配、无 Map 查询），536 节点从 ~2s 降到百毫秒级。
  const ids = nodes.map((b) => b.id)
  const idxOf = new Map(ids.map((id, i) => [id, i]))
  const KIND_CODE: Record<NodeKind, number> = { buyer: 0, project: 1, winner: 2, bidder: 3, vendor: 4 }
  const kindArr = new Uint8Array(n)
  const px = new Float64Array(n)
  const py = new Float64Array(n)
  const pz = new Float64Array(n)
  const vx = new Float64Array(n)
  const vy = new Float64Array(n)
  const vz = new Float64Array(n)
  const hx = new Float64Array(n)
  const hy = new Float64Array(n)
  const hz = new Float64Array(n)
  const massArr = new Float64Array(n)
  const pullArr = new Float64Array(n)
  for (let i = 0; i < n; i++) {
    const b = nodes[i]!
    kindArr[i] = KIND_CODE[b.kind]
    const p = pos.get(b.id)!
    px[i] = p.x
    py[i] = p.y
    pz[i] = p.z
    const hm = home.get(b.id)!
    hx[i] = hm.x
    hy[i] = hm.y
    hz[i] = hm.z
    massArr[i] = b.kind === 'buyer' ? 9 : b.kind === 'project' ? 3.4 : 1
    pullArr[i] = b.kind === 'buyer' ? 0.22 : b.kind === 'project' ? 0.1 : 0.02
  }
  const spA: number[] = []
  const spB: number[] = []
  const spRest: number[] = []
  const spK: number[] = []
  for (const e of edges) {
    const ia = idxOf.get(e.a)
    const ib = idxOf.get(e.b)
    if (ia === undefined || ib === undefined) continue
    const r = REST[e.role] ?? REST.bid!
    spA.push(ia)
    spB.push(ib)
    spRest.push(r.rest)
    spK.push(r.k)
  }

  const fx = new Float64Array(n)
  const fy = new Float64Array(n)
  const fz = new Float64Array(n)
  const iters = n <= 120 ? 180 : n <= 320 ? 120 : 70
  for (let step = 0; step < iters; step++) {
    fx.fill(0)
    fy.fill(0)
    fz.fill(0)

    for (let i = 0; i < n; i++) {
      const ki = kindArr[i]!
      for (let j = i + 1; j < n; j++) {
        let dx = px[j]! - px[i]!
        let dy = py[j]! - py[i]!
        let dz = pz[j]! - pz[i]!
        const dist = Math.max(1.4, Math.hypot(dx, dy, dz))
        let push = 26 / (dist * dist)
        const kj = kindArr[j]!
        if (ki === 1 && kj === 1) push *= 1.7
        if (ki === 0 || kj === 0) push *= 0.28
        push /= dist // 归一化方向
        dx *= push
        dy *= push
        dz *= push
        fx[i]! -= dx
        fy[i]! -= dy
        fz[i]! -= dz
        fx[j]! += dx
        fy[j]! += dy
        fz[j]! += dz
      }
    }

    for (let s = 0; s < spA.length; s++) {
      const ia = spA[s]!
      const ib = spB[s]!
      let dx = px[ib]! - px[ia]!
      let dy = py[ib]! - py[ia]!
      let dz = pz[ib]! - pz[ia]!
      const dist = Math.max(0.6, Math.hypot(dx, dy, dz))
      const f = ((dist - spRest[s]!) * spK[s]!) / dist
      dx *= f
      dy *= f
      dz *= f
      fx[ia]! += dx
      fy[ia]! += dy
      fz[ia]! += dz
      fx[ib]! -= dx
      fy[ib]! -= dy
      fz[ib]! -= dz
    }

    for (let i = 0; i < n; i++) {
      fx[i]! += (hx[i]! - px[i]!) * pullArr[i]!
      fy[i]! += (hy[i]! - py[i]!) * pullArr[i]!
      fz[i]! += (hz[i]! - pz[i]!) * pullArr[i]!
      const m = 1 / massArr[i]!
      vx[i] = (vx[i]! + fx[i]! * m) * 0.6
      vy[i] = (vy[i]! + fy[i]! * m) * 0.6
      vz[i] = (vz[i]! + fz[i]! * m) * 0.6
      px[i]! += vx[i]!
      py[i]! += vy[i]!
      pz[i]! += vz[i]!
    }
  }

  // ⑤ 分离步（原版）：非 buyer 对最小间距
  const scaleArr = new Float64Array(n)
  for (let i = 0; i < n; i++) scaleArr[i] = starScale(ids[i]!)
  const sepIters = n <= 120 ? 36 : 20
  for (let step = 0; step < sepIters; step++) {
    for (let i = 0; i < n; i++) {
      if (kindArr[i] === 0) continue
      for (let j = i + 1; j < n; j++) {
        if (kindArr[j] === 0) continue
        let dx = px[j]! - px[i]!
        let dy = py[j]! - py[i]!
        let dz = pz[j]! - pz[i]!
        const dist = Math.hypot(dx, dy, dz) || 0.001
        const minSep = 3.8 + (scaleArr[i]! + scaleArr[j]!) * 0.12
        if (dist < minSep) {
          const f = ((minSep - dist) * 0.45) / dist
          dx *= f
          dy *= f
          dz *= f
          px[i]! -= dx
          py[i]! -= dy
          pz[i]! -= dz
          px[j]! += dx
          py[j]! += dy
          pz[j]! += dz
        }
      }
    }
  }

  // 写回 pos（供后续质心归零）
  for (let i = 0; i < n; i++) {
    pos.set(ids[i]!, { x: px[i]!, y: py[i]!, z: pz[i]! })
  }

  // ⑥ 质心归零 + 整体放大（保持原视觉密度）
  let cx = 0
  let cy = 0
  let cz = 0
  for (const id of ids) {
    const p = pos.get(id)!
    cx += p.x
    cy += p.y
    cz += p.z
  }
  cx /= n
  cy /= n
  cz /= n

  // 位置与尺寸解耦：位置放得更开（/20），星体尺寸收一点（/30），
  // 图越大间距相对越松，避免"紧密"感。
  const F_POS = Math.max(1, Math.sqrt(n / 20))
  const F_SIZE = Math.max(1, Math.sqrt(n / 30))
  return nodes.map((b) => {
    const p = pos.get(b.id)!
    return {
      id: b.id,
      x: (p.x - cx) * F_POS,
      y: (p.y - cy) * F_POS,
      z: (p.z - cz) * F_POS,
      scale: starScale(b.id) * F_SIZE,
    }
  })
}

/** 布局相关的视觉比例：StarMap 用它等比适配雾密度 / 星尘范围 / 相机距离 */
export function visualScale(placed: Placed[]): number {
  let maxR = 8
  for (const p of placed) maxR = Math.max(maxR, Math.hypot(p.x, p.y, p.z))
  return maxR / 40
}

export type { NodeKind }
