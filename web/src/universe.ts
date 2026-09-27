export type Kind = 'buyer' | 'project' | 'winner' | 'bidder' | 'vendor'

export interface Body {
  id: string
  label: string
  kind: Kind
  note: string
  buyer?: string
}

export interface Link {
  a: string
  b: string
  role: 'win' | 'bid' | 'supply'
}

export type EdgeRole = 'win' | 'bid' | 'supply' | 'buy'

export interface StoryEdge {
  a: string
  b: string
  label?: string
  role?: EdgeRole
}

export type RelationMode = 'rival' | 'coop'

export interface RelationModeInfo {
  id: RelationMode
  name: string
}

export interface RowItem {
  id: string
  name: string
  meta: string
  value: string
}

export interface GroupItem {
  id: string
  title: string
  meta: string
  parties: RowItem[]
}

export interface Placed {
  id: string
  x: number
  y: number
  z: number
  scale: number
}

export interface Focus {
  ids: Set<string>
  bands: boolean
  edges: StoryEdge[]
}

type V3 = { x: number; y: number; z: number }

const bodies: Body[] = [
  { id: 'zeng', label: '增城医院', kind: 'buyer', note: '样例里项目最多的采购单位' },
  { id: 'zhe', label: '浙人医', kind: 'buyer', note: '东软、联通数智都有成交' },
  { id: 'nan', label: '南山政数', kind: 'buyer', note: '数据中台与医共体专网' },
  { id: 'edu', label: '高新教育', kind: 'buyer', note: '教育云桌面' },

  { id: 'p1', label: '增城二期', kind: 'project', buyer: 'zeng', note: '信息化集成，联通数智中标' },
  { id: 'p2', label: '门诊系统', kind: 'project', buyer: 'zeng', note: '卫宁健康中标' },
  { id: 'p3', label: '机房改造', kind: 'project', buyer: 'zeng', note: '东软集团中标' },
  { id: 'p4', label: '临床集成', kind: 'project', buyer: 'zhe', note: '东软主成交，联通数智也有成交' },
  { id: 'p5', label: '数据中台', kind: 'project', buyer: 'nan', note: '中国软件中标，联通数智参与' },
  { id: 'p6', label: '医共体专网', kind: 'project', buyer: 'nan', note: '联通数智中标，中国软件参与' },
  { id: 'p7', label: '检验协同', kind: 'project', buyer: 'nan', note: '联通数智中标，中国软件参与' },
  { id: 'p8', label: '教育云', kind: 'project', buyer: 'edu', note: '中科曙光中标' },

  { id: 'lt', label: '联通数智', kind: 'winner', note: '跨过的项目最多，所以这颗星最大' },
  { id: 'ns', label: '东软集团', kind: 'winner', note: '增城机房与浙人医临床' },
  { id: 'wn', label: '卫宁健康', kind: 'winner', note: '门诊电子病历' },
  { id: 'css', label: '中国软件', kind: 'winner', note: '与联通数智有三个同场项目' },
  { id: 'sg', label: '中科曙光', kind: 'winner', note: '教育云桌面' },
  { id: 'dx', label: '广东电信', kind: 'bidder', note: '增城项目多次出现，未中标' },
  { id: 'yy', label: '广州云奕', kind: 'bidder', note: '常与广东电信同场' },
  { id: 'hw', label: '华为', kind: 'vendor', note: '集成平台、防火墙，不作为投标方' },
  { id: 'h3', label: '新华三', kind: 'vendor', note: '医疗专网交换机' },
]

const links: Link[] = [
  { a: 'p1', b: 'lt', role: 'win' },
  { a: 'p1', b: 'dx', role: 'bid' },
  { a: 'p1', b: 'yy', role: 'bid' },
  { a: 'p1', b: 'ns', role: 'bid' },
  { a: 'p1', b: 'hw', role: 'supply' },
  { a: 'p1', b: 'h3', role: 'supply' },
  { a: 'p2', b: 'wn', role: 'win' },
  { a: 'p2', b: 'lt', role: 'bid' },
  { a: 'p2', b: 'dx', role: 'bid' },
  { a: 'p2', b: 'yy', role: 'bid' },
  { a: 'p3', b: 'ns', role: 'win' },
  { a: 'p4', b: 'ns', role: 'win' },
  { a: 'p4', b: 'lt', role: 'win' },
  { a: 'p5', b: 'css', role: 'win' },
  { a: 'p5', b: 'lt', role: 'bid' },
  { a: 'p6', b: 'lt', role: 'win' },
  { a: 'p6', b: 'css', role: 'bid' },
  { a: 'p7', b: 'lt', role: 'win' },
  { a: 'p7', b: 'css', role: 'bid' },
  { a: 'p8', b: 'sg', role: 'win' },
]

export const relations: RelationModeInfo[] = [
  { id: 'rival', name: '竞争关系' },
  { id: 'coop', name: '合作关系' },
]

const byId = new Map(bodies.map((b) => [b.id, b]))

export function bodyOf(id: string) {
  return byId.get(id)
}

export const kindName: Record<Kind, string> = {
  buyer: '采购单位',
  project: '项目',
  winner: '中标供应商',
  bidder: '投标参与方',
  vendor: '产品供应商',
}

export function hash(s: string) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967295
}

function v(x = 0, y = 0, z = 0): V3 {
  return { x, y, z }
}
function add(a: V3, b: V3): V3 {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z }
}
function sub(a: V3, b: V3): V3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }
}
function mul(a: V3, s: number): V3 {
  return { x: a.x * s, y: a.y * s, z: a.z * s }
}
function len(a: V3) {
  return Math.hypot(a.x, a.y, a.z)
}
function norm(a: V3): V3 {
  const l = len(a) || 1
  return mul(a, 1 / l)
}
function cross(a: V3, b: V3): V3 {
  return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }
}

export const homeEye = { x: 0.78, y: 0.5, z: 0.9 }

function projectsOfOrg(id: string) {
  const ids = new Set<string>()
  for (const l of links) {
    const other = l.a === id ? l.b : l.b === id ? l.a : null
    if (other && byId.get(other)?.kind === 'project') ids.add(other)
  }
  return [...ids]
}

function starScale(id: string) {
  const b = byId.get(id)!
  if (b.kind === 'buyer') return 6.6
  if (b.kind === 'project') {
    const n = links.filter((l) => (l.a === id || l.b === id) && (l.role === 'win' || l.role === 'bid')).length
    return 1.85 + Math.sqrt(Math.max(n, 1)) * 1.15
  }
  const n = projectsOfOrg(id).length
  const base = b.kind === 'vendor' ? 0.95 : b.kind === 'bidder' ? 1.1 : 1.25
  return base + Math.sqrt(Math.max(n, 1)) * 1.55
}

const edgeWord: Record<EdgeRole, string> = { win: '中标', bid: '参与', supply: '供应', buy: '采购' }
const rivalRoles: Link['role'][] = ['win', 'bid']
const coopRoles: Link['role'][] = ['win', 'supply']

function partner(link: Link, id: string) {
  if (link.a === id) return link.b
  if (link.b === id) return link.a
  return null
}

function rolesOf(mode: RelationMode) {
  return mode === 'rival' ? rivalRoles : coopRoles
}

function projectParties(projectId: string, mode: RelationMode) {
  return links.filter((l) => partner(l, projectId) && rolesOf(mode).includes(l.role))
}

function orgProjects(id: string, mode: RelationMode) {
  const found: { projectId: string; role: Link['role'] }[] = []
  for (const l of links) {
    const other = partner(l, id)
    if (!other || byId.get(other)?.kind !== 'project') continue
    if (!rolesOf(mode).includes(l.role)) continue
    found.push({ projectId: other, role: l.role })
  }
  return found
}

function pushParty(ids: Set<string>, edges: StoryEdge[], projectId: string, other: string, role: EdgeRole) {
  ids.add(other)
  edges.push({ a: projectId, b: other, label: edgeWord[role], role })
}

export function idleEdges(mode: RelationMode): StoryEdge[] {
  const edges: StoryEdge[] = []
  for (const l of links) {
    if (!rolesOf(mode).includes(l.role)) continue
    edges.push({ a: l.a, b: l.b })
  }
  if (mode === 'coop') {
    for (const body of bodies) {
      if (body.kind === 'project' && body.buyer) edges.push({ a: body.id, b: body.buyer })
    }
  }
  return edges
}

export function focusOf(id: string, mode: RelationMode): Focus {
  const body = byId.get(id)
  const ids = new Set<string>([id])
  const edges: StoryEdge[] = []
  if (!body) return { ids, bands: false, edges }

  const attach = (projectId: string, except: string) => {
    ids.add(projectId)
    if (mode === 'coop') {
      const buyer = byId.get(projectId)?.buyer
      if (buyer && buyer !== except) pushParty(ids, edges, projectId, buyer, 'buy')
    }
    for (const l of projectParties(projectId, mode)) {
      const other = partner(l, projectId)!
      if (other === except) continue
      pushParty(ids, edges, projectId, other, l.role)
    }
  }

  if (body.kind === 'project') {
    attach(id, '')
    return { ids, bands: true, edges }
  }

  if (body.kind === 'buyer') {
    for (const project of bodies.filter((b) => b.buyer === id)) {
      pushParty(ids, edges, project.id, id, 'buy')
      attach(project.id, id)
    }
    return { ids, bands: true, edges }
  }

  for (const item of orgProjects(id, mode)) {
    edges.push({ a: id, b: item.projectId, label: edgeWord[item.role], role: item.role })
    attach(item.projectId, id)
  }
  return { ids, bands: true, edges }
}

export function roster(mode: RelationMode): RowItem[] {
  const rows: RowItem[] = []
  for (const body of bodies) {
    if (body.kind === 'buyer' || body.kind === 'project') continue
    const deals = orgProjects(body.id, mode)
    if (mode === 'rival') {
      if (body.kind === 'vendor' || !deals.length) continue
      const wins = deals.filter((d) => d.role === 'win').length
      rows.push({
        id: body.id,
        name: body.label,
        meta: `参与 ${deals.length} 个项目`,
        value: wins ? `中标 ${wins}` : '未中标',
      })
    } else if (deals.length) {
      const wins = deals.filter((d) => d.role === 'win').length
      rows.push({
        id: body.id,
        name: body.label,
        meta: body.kind === 'vendor' ? `供应 ${deals.length} 个项目` : `成交 ${wins} 个项目`,
        value: body.kind === 'vendor' ? '产品供应' : '中标供应商',
      })
    }
  }
  return rows
}

export function selectionGroups(id: string, mode: RelationMode): GroupItem[] {
  const body = byId.get(id)
  if (!body) return []

  const partiesOf = (projectId: string, except: string): RowItem[] => {
    const rows: RowItem[] = []
    if (mode === 'coop') {
      const buyerId = byId.get(projectId)?.buyer
      if (buyerId && buyerId !== except) {
        const buyer = byId.get(buyerId)!
        rows.push({ id: buyer.id, name: buyer.label, meta: '采购单位', value: '采购' })
      }
    }
    for (const l of projectParties(projectId, mode)) {
      const other = partner(l, projectId)!
      if (other === except) continue
      const who = byId.get(other)!
      rows.push({ id: who.id, name: who.label, meta: kindName[who.kind], value: edgeWord[l.role] })
    }
    return rows
  }

  if (body.kind === 'project') {
    return [
      {
        id: body.id,
        title: body.label,
        meta: mode === 'rival' ? '同场投标方' : '成交与供应',
        parties: partiesOf(body.id, ''),
      },
    ]
  }

  const projectIds =
    body.kind === 'buyer'
      ? bodies.filter((b) => b.buyer === id).map((b) => b.id)
      : orgProjects(id, mode).map((item) => item.projectId)

  return projectIds.map((projectId) => {
    const project = byId.get(projectId)!
    const mine = orgProjects(id, mode).find((item) => item.projectId === projectId)?.role
    return {
      id: projectId,
      title: project.label,
      meta: mine ? edgeWord[mine] : '其下项目',
      parties: partiesOf(projectId, id),
    }
  })
}

export function selectionStats(id: string | null, mode: RelationMode) {
  if (!id) {
    const people = roster(mode)
    const projects = new Set(
      links
        .filter((l) => rolesOf(mode).includes(l.role))
        .map((l) => (byId.get(l.a)?.kind === 'project' ? l.a : l.b)),
    )
    const ties = links.filter((l) => rolesOf(mode).includes(l.role)).length
    return mode === 'rival'
      ? [
          { k: '投标主体', v: String(people.length) },
          { k: '竞标项目', v: String(projects.size) },
          { k: '投标联系', v: String(ties) },
        ]
      : [
          { k: '合作主体', v: String(people.length) },
          { k: '成交项目', v: String(projects.size) },
          { k: '合作联系', v: String(ties) },
        ]
  }
  const groups = selectionGroups(id, mode)
  const body = byId.get(id)
  const others = new Set(groups.flatMap((g) => g.parties.map((p) => p.id)))
  const ownWins = groups.filter((g) => g.meta === '中标').length
  const winLinks =
    body?.kind === 'winner' || body?.kind === 'bidder'
      ? ownWins
      : groups.reduce((n, g) => n + g.parties.filter((p) => p.value === '中标').length, 0)
  return mode === 'rival'
    ? [
        { k: '参与项目', v: String(groups.length) },
        { k: '同场对手', v: String(others.size) },
        { k: '中标', v: String(winLinks) },
      ]
    : [
        { k: '相关项目', v: String(groups.length) },
        { k: '合作方', v: String(others.size) },
        { k: '中标', v: String(winLinks) },
      ]
}

export function selectionTitle(id: string | null, mode: RelationMode) {
  if (!id) {
    return mode === 'rival'
      ? '点一家供应商，展开它参与的项目，以及每个项目还有谁在竞标'
      : '点一家供应商，展开它成交的项目，以及每个项目的采购单位和供应方'
  }
  const body = byId.get(id)
  if (!body) return ''
  return mode === 'rival'
    ? `${body.label}参与的项目，以及各项目的其他投标方`
    : `${body.label}相关的项目，以及各项目的合作方`
}

export function placeStars(): Placed[] {
  const home = new Map<string, V3>()
  const pos = new Map<string, V3>()
  const vel = new Map<string, V3>()
  const buyers = bodies.filter((b) => b.kind === 'buyer')
  const eye = norm(homeEye)
  const right = norm(cross(eye, { x: 0, y: 1, z: 0 }))
  const up = norm(cross(right, eye))
  const face = (x: number, y: number, depth: number) => add(add(mul(right, x), mul(up, y)), mul(eye, depth))

  const seats: Record<string, V3> = {
    zeng: face(-22, 2, 0),
    nan: face(8, 20, 16),
    zhe: face(24, -16, -4),
    edu: face(-34, 22, 18),
  }
  for (const b of buyers) {
    const p = seats[b.id] ?? v()
    home.set(b.id, p)
    pos.set(b.id, { ...p })
    vel.set(b.id, v())
  }

  for (const buyer of buyers) {
    const ps = bodies.filter((b) => b.buyer === buyer.id)
    const c = home.get(buyer.id)!
    const rad = ps.length > 1 ? 16 : 8
    ps.forEach((p, i) => {
      const even = (i / Math.max(ps.length, 1)) * Math.PI * 2 + 0.4
      const local = face(Math.cos(even) * rad, Math.sin(even) * rad * 0.78, (hash(p.id) - 0.5) * 5)
      const q = add(c, local)
      home.set(p.id, q)
      pos.set(p.id, { ...q })
      vel.set(p.id, v())
    })
  }

  for (const org of bodies.filter((b) => b.kind !== 'buyer' && b.kind !== 'project')) {
    const centers = projectsOfOrg(org.id).map((id) => home.get(id)!)
    let q: V3
    if (centers.length <= 1) {
      const c = centers[0] ?? v()
      const ang = hash(org.id) * Math.PI * 2
      const rad = 9 + hash(org.id + 'r') * 2
      const local = face(Math.cos(ang) * rad, Math.sin(ang) * rad * 0.8, (hash(org.id + 'z') - 0.5) * 4)
      q = add(c, local)
    } else {
      q = mul(centers.reduce((a, p) => add(a, p), v()), 1 / centers.length)
      q = add(q, mul(up, (hash(org.id) - 0.5) * 6))
    }
    home.set(org.id, q)
    pos.set(org.id, { ...q })
    vel.set(org.id, v())
  }

  const ids = bodies.map((b) => b.id)
  const kindOf = new Map(bodies.map((b) => [b.id, b.kind]))
  const springs: { a: string; b: string; rest: number; k: number }[] = []
  for (const b of bodies) {
    if (b.kind === 'project' && b.buyer) springs.push({ a: b.buyer, b: b.id, rest: 8.6, k: 0.04 })
  }
  for (const l of links) springs.push({ a: l.a, b: l.b, rest: l.role === 'supply' ? 5 : 6.4, k: 0.05 })

  for (let step = 0; step < 180; step++) {
    const force = new Map<string, V3>(ids.map((id) => [id, v()]))
    const pushF = (id: string, f: V3) => force.set(id, add(force.get(id)!, f))

    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = ids[i]!
        const b = ids[j]!
        const dlt = sub(pos.get(b)!, pos.get(a)!)
        const dist = Math.max(1.4, len(dlt))
        const ka = kindOf.get(a)
        const kb = kindOf.get(b)
        let push = 26 / (dist * dist)
        if (ka === 'project' && kb === 'project') push *= 1.7
        if (ka === 'buyer' || kb === 'buyer') push *= 0.28
        const f = mul(norm(dlt), push)
        pushF(a, mul(f, -1))
        pushF(b, f)
      }
    }

    for (const s of springs) {
      const dlt = sub(pos.get(s.b)!, pos.get(s.a)!)
      const dist = Math.max(0.6, len(dlt))
      const f = mul(norm(dlt), (dist - s.rest) * s.k)
      pushF(s.a, f)
      pushF(s.b, mul(f, -1))
    }

    for (const id of ids) {
      const k = kindOf.get(id)
      const mass = k === 'buyer' ? 9 : k === 'project' ? 3.4 : 1
      const homePull = k === 'buyer' ? 0.22 : k === 'project' ? 0.1 : 0.02
      pushF(id, mul(sub(home.get(id)!, pos.get(id)!), homePull))
      const nv = mul(add(vel.get(id)!, mul(force.get(id)!, 1 / mass)), 0.6)
      vel.set(id, nv)
      pos.set(id, add(pos.get(id)!, nv))
    }
  }

  for (let step = 0; step < 36; step++) {
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = ids[i]!
        const b = ids[j]!
        if (kindOf.get(a) === 'buyer' || kindOf.get(b) === 'buyer') continue
        const dlt = sub(pos.get(b)!, pos.get(a)!)
        const dist = len(dlt) || 0.001
        const minSep = 3.8 + (starScale(a) + starScale(b)) * 0.12
        if (dist < minSep) {
          const push = mul(norm(dlt), (minSep - dist) * 0.45)
          pos.set(a, sub(pos.get(a)!, push))
          pos.set(b, add(pos.get(b)!, push))
        }
      }
    }
  }

  let cx = 0
  let cy = 0
  let cz = 0
  for (const id of ids) {
    const p = pos.get(id)!
    cx += p.x
    cy += p.y
    cz += p.z
  }
  cx /= ids.length
  cy /= ids.length
  cz /= ids.length

  return bodies.map((b) => {
    const p = pos.get(b.id)!
    return { id: b.id, x: p.x - cx, y: p.y - cy, z: p.z - cz, scale: starScale(b.id) }
  })
}

export { bodies }
