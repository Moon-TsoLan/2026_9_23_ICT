<script setup lang="ts">
/**
 * ExploreView（任务二 · 探索）—— 四态状态机
 *
 * 模式是唯一真相（docs/星图交互设计-v3.md §17–18）：
 *   overview  总览：全亮，无选中
 *   picked    S0 选中：只亮选中点，邻居也暗；面板给出"能问什么"
 *   scene     场景态：S1/S2/S3 的结果，锁定阅读
 *   picking   多选态：S4/S5 凑家中，供应商池亮
 *   compared  多选结果态
 *
 * 三条铁律：
 *   1 「选中」和「提问」是两个动作，任何入口都不合并 —— 搜索框只选中，不提问。
 *   2 场景态/结果态锁定：图上的点不再改变选中；要换对象必须先退出。
 *   3 每个非总览状态都有一条一屏内可见的退出路径。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { ApiError } from '@/types/api'
import NarrativePanel from '@/components/NarrativePanel.vue'
import SceneBar from '@/components/SceneBar.vue'
import StarMap from '@/components/StarMap.vue'
import UpdateNotice from '@/components/UpdateNotice.vue'
import { useDataVersion } from '@/composables/useDataVersion'
import type {
  Distribution,
  OverviewResult,
  PartyHit,
  PartyProfile,
  SceneId,
  SceneResult,
  SubjectRelation,
} from '@/types/explore'
import type { DisplayKind, GraphEdge, GraphNode } from '@/types/graph'
import { DISPLAY_KIND, DISPLAY_KIND_LABEL } from '@/types/graph'
import type { LayoutMode } from '@/composables/useGraphLayout'

const router = useRouter()
const route = useRoute()

type Mode = 'overview' | 'picked' | 'scene' | 'picking' | 'compared' | 's6'

/**
 * 有新结果入库时只提示、不自动刷新。点「刷新」默认**增量追加**（老节点坐标由
 * placeStars 的缓存钉住，画面不跳）；只有新增超出底图尺度时才改成提示「重建」
 * —— 重建会走 loadOverview()，那是全量力导重排，必须用户明确点。
 */
const APPEND_MAX_NEW = 10
const APPEND_MAX_TOTAL = 80

const { newCount, show: noticeShow, mark, ack, dismiss, start } = useDataVersion()
const noticeMode = ref<'append' | 'rebuild'>('append')

/* ======== 1. 底图：只有一张，场景只叠加不替换 ======== */
const baseNodes = ref<GraphNode[]>([])
const baseEdges = ref<GraphEdge[]>([])
const knownNodeIds = new Set<string>()
const graphNodes = computed(() => baseNodes.value)
const graphEdges = computed(() => baseEdges.value)
const nodeById = computed(() => new Map(graphNodes.value.map((n) => [n.id, n])))

/** 场景可能带进底图没有的节点；位置由 placeStars 的缓存保证老节点不动 */
const knownEdgeKeys = new Set<string>()
const edgeKey = (e: GraphEdge) => e.a + '>' + e.b + ':' + e.role

/** 只按 id 去重追加。老节点坐标由 placeStars 的缓存钉住，不会因追加而漂移。 */
function absorbSceneGraph(nodes: GraphNode[], edges: GraphEdge[]) {
  const extraN = nodes.filter((n) => !knownNodeIds.has(n.id))
  const extraE = edges.filter((e) => !knownEdgeKeys.has(edgeKey(e)))
  if (!extraN.length && !extraE.length) return
  for (const n of extraN) knownNodeIds.add(n.id)
  for (const e of extraE) knownEdgeKeys.add(edgeKey(e))
  if (extraN.length) baseNodes.value = [...baseNodes.value, ...extraN]
  if (extraE.length) baseEdges.value = [...baseEdges.value, ...extraE]
}

/* ======== 2. 唯一状态 ======== */
const mode = ref<Mode>('overview')
const focusId = ref<string | null>(null)
const scene = ref<SceneId>('S1')
const result = ref<SceneResult | null>(null)
/** 对比篮（S4/S5）：只装供应商，上限 4 家，界面上写明 */
const subjects = ref<string[]>([])
/** 单选场景的主体名字：与对比篮分开存，否则篮子会被采购单位污染，S4 拿 buyer 名去查中标商 */
const sceneSubject = ref('')
const MULTI_MAX = 4

const sceneLoading = ref(false)
const sceneError = ref('')
const tip = ref('')
const hover = ref<{ id: string; x: number; y: number } | null>(null)
const focusProfile = ref<PartyProfile | null>(null)
const focusLoading = ref(false)

const overview = ref<OverviewResult | null>(null)
const distribution = ref<Distribution | null>(null)
const overviewError = ref(false)
const overviewLoading = ref(false)
const distributionError = ref(false)
const serverMs = ref<number | null>(null)
const renderMs = ref<number | null>(null)
const sampled = ref(false)
const labelsShown = ref(0)
const labelsCand = ref(0)
const hiddenKinds = ref<DisplayKind[]>([])
const layoutMode = ref<LayoutMode>('sphere')

const starMap = ref<InstanceType<typeof StarMap> | null>(null)

/* ======== 3. 派生 ======== */
const displayMode = computed<'overview' | 'picked' | 'scene' | 'pool' | 's6'>(() =>
  mode.value === 'overview'
    ? 'overview'
    : mode.value === 'picked'
      ? 'picked'
      : mode.value === 'picking'
        ? 'pool'
        : mode.value === 'compared'
          ? 'compared'
          : mode.value === 's6'
            ? 's6'
            : 'scene',
)
const locked = computed(() => mode.value === 'scene' || mode.value === 'compared' || mode.value === 's6')
const hasResult = computed(() => !!result.value && (mode.value === 'scene' || mode.value === 'compared'))
const sceneGraph = computed(() => (hasResult.value ? result.value!.graph : null))
/**
 * S6 · 项目详情。
 * 它不是后端场景，全部由图数据现算：点项目节点即默认在问
 * "这个项目是谁采购的、谁投了标、谁中了标"，所以也不需要金环——
 * 三种角色由三种线色直接表达（采购冰蓝 / 中标金 / 投标冷灰，供货铜）。
 */
const focusProject = computed(() => (mode.value === 's6' ? focusNode_.value : null))
const s6Edges = computed<GraphEdge[]>(() => {
  const p = focusProject.value
  if (!p) return []
  const seen = new Set<string>()
  const out: GraphEdge[] = []
  for (const e of baseEdges.value) {
    if (e.a !== p.id && e.b !== p.id) continue
    const k = e.a + '>' + e.b + ':' + e.role
    if (seen.has(k)) continue
    seen.add(k)
    out.push(e)
  }
  return out
})
const s6Ids = computed(() => {
  const p = focusProject.value
  if (!p) return []
  const ids = new Set<string>([p.id])
  for (const e of s6Edges.value) {
    ids.add(e.a)
    ids.add(e.b)
  }
  return [...ids]
})
const sceneIds = computed(() =>
  mode.value === 's6' ? s6Ids.value : sceneGraph.value?.nodes.map((n) => n.id) ?? [],
)
const sceneEdges = computed(() => (mode.value === 's6' ? s6Edges.value : sceneGraph.value?.edges ?? []))
const highlightIds = computed(() => sceneGraph.value?.highlight_ids ?? [])
const ranks = computed<Record<string, number>>(() =>
  Object.fromEntries((hasResult.value ? result.value!.narrative.ranking : []).map((r) => [r.id, r.rank])),
)

const focusNode_ = computed(() => (focusId.value ? nodeById.value.get(focusId.value) ?? null : null))
const hoverNode = computed(() => (hover.value ? nodeById.value.get(hover.value.id) ?? null : null))
const subjectCount = computed(() => subjects.value.length)
/** 对比主体的节点 id（顺序即配色顺序）：星图的边、圆环、面板矩阵列头共用这一份 */
const subjectIds = computed(() => {
  const byLabel = new Map(graphNodes.value.map((n) => [n.label, n.id]))
  return subjects.value.map((name) => byLabel.get(name)).filter(Boolean) as string[]
})

const kindCounts = computed(() => {
  const m = { buyer: 0, project: 0, supplier: 0 } as Record<DisplayKind, number>
  for (const n of graphNodes.value) m[DISPLAY_KIND[n.kind]]++
  return m
})
const LEGEND: DisplayKind[] = ['buyer', 'project', 'supplier']

const emptyResult = computed(
  () =>
    hasResult.value &&
    result.value!.graph.edges.length === 0 &&
    result.value!.narrative.ranking.length === 0,
)

/** 中标判定：图数据里 kind==='winner' 就表示"至少中过一次标"。
 *  之前是异步查档案反推，会给 vendor 节点白打一次 404；现在同步、没有中间态。 */
const hasWinOf = (n: GraphNode) => n.kind === 'winner'


/** 面板上的场景按钮：由节点类型 + 中标记录决定，不给就说明原因 */
const subjectActions = computed<Array<{ scene: SceneId; label: string }>>(() => {
  const n = focusNode_.value
  if (!n || mode.value !== 'picked') return []
  if (n.kind === 'buyer')
    return [
      { scene: 'S1', label: '查它的长期合作供应商（中标/产品）' },
      { scene: 'S2', label: '查它的高频投标主体与协同组合' },
    ]
  if (n.kind === 'project') return []
  if (!hasWinOf(n)) return []
  return [
    { scene: 'S3', label: '查它的高频共同竞标主体' },
    { scene: 'S4', label: '选多家，查共同合作的采购单位' },
    { scene: 'S5', label: '选多家，查共同竞标的项目' },
  ]
})

const subjectNote = computed(() => {
  const n = focusNode_.value
  if (!n || mode.value !== 'picked') return ''
  if (n.kind === 'project') return '项目是证据，不是查询主体。'
  if (n.kind === 'buyer') return ''
  return '它没有任何中标记录。S3/S4/S5 都以中标供应商为主体，所以这里没有问题可问。'
})

/** 顶栏一句话状态语（不变量 11） */
const stateLine = computed(() => {
  const n = focusNode_.value
  if (mode.value === 's6') {
    const g = subjectRelations.value
    const pick = (r: string) => g.find((x) => x.role === r)?.count ?? 0
    return 'S6 · 项目 ' + (n?.label ?? '') + ' · 采购 ' + pick('buy') + ' · 投标 ' + pick('bid') + ' · 中标 ' + pick('win')
  }
  if (mode.value === 'overview') return '未提问 · 选中一个节点开始'
  if (mode.value === 'picked') return 'S0 · 已选中 ' + (n?.label ?? '')
  if (mode.value === 'picking') return 'S4/S5 · 对比篮 ' + subjectCount.value + '/' + MULTI_MAX
  if (mode.value === 'compared') {
    const hit = result.value!.narrative.ranking.length
    return result.value!.scene + ' · 对比 ' + subjects.value.length + ' 家 · 交集 ' + hit + ' 项' + (hit ? '' : '（没有共同对象）')
  }
  return result.value!.scene + ' · 主体 ' + (sceneSubject.value || n?.label || '') + ' · 答案 ' + result.value!.narrative.ranking.length + ' 项'
})

/** 当前选中点的关系摘要：没有场景结果时回落到总览边（否则 S0 面板是空的） */
const subjectRelations = ref<SubjectRelation[]>([])
const REL_ROLE_LABEL: Record<string, string> = { win: '中标', buy: '采购', supply: '供应', bid: '投标' }

function collectSubjectRelations() {
  const fid = focusId.value
  if (!fid || mode.value === 'compared') {
    // 交集场景下"某一家在图里连到谁"不是答案，列出来反而会被当成答案读
    subjectRelations.value = []
    relTotal.value = 0
    return
  }
  // 场景态用场景边；S0 用底图边 —— 这样"只选中不提问"也有内容可看
  const g = sceneGraph.value
  const edges = g ? g.edges : baseEdges.value
  const nodes = g ? g.nodes : baseNodes.value
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const groups = new Map<string, SubjectRelation>()
  let total = 0
  for (const e of edges) {
    const otherId = e.a === fid ? e.b : e.b === fid ? e.a : null
    if (!otherId) continue
    total++
    const other = byId.get(otherId)
    if (!other) continue
    let grp = groups.get(e.role)
    if (!grp) {
      grp = { role: e.role, label: REL_ROLE_LABEL[e.role] ?? e.role, items: [], count: 0 }
      groups.set(e.role, grp)
    }
    grp.count++
    if (grp.items.length < 8) grp.items.push({ id: other.id, name: other.label, kind: other.kind, weight: e.weight })
  }
  subjectRelations.value = [...groups.values()].sort((a, b) => b.count - a.count)
  relTotal.value = total
}
const relTotal = ref(0)

const focusLocal = computed(() => {
  const n = focusNode_.value
  if (!n) return null
  const lines = [relTotal.value + ' 条关系']
  if (n.weight > 1) lines.push('权重 ' + n.weight)
  const r = ranks.value[n.id]
  if (r) lines.push('排名第 ' + r)
  return { id: n.id, name: n.label, role: DISPLAY_KIND_LABEL[DISPLAY_KIND[n.kind]], lines }
})

const subjectItems = computed(() => {
  const byLabel = new Map(graphNodes.value.map((n) => [n.label, n]))
  return subjects.value.map((name) => {
    const n = byLabel.get(name)
    return { id: n?.id ?? 'sup:' + name, name, kind: n?.kind ?? ('winner' as const) }
  })
})

/* ======== 4. 转移 ======== */

/** 选中一个节点 = 进入 S0。上一个问题当场作废（不残留、不展示） */
function selectNode(id: string | null, fly = false) {
  if (!id) return exitToOverview()
  // 项目节点没有"能问的场景"（它不是主体），点它本身就是一个问题 → 直接进 S6
  mode.value = nodeById.value.get(id)?.kind === 'project' ? 's6' : 'picked'
  focusId.value = id
  result.value = null
  sceneError.value = ''
  tip.value = ''
  hover.value = null
  const n = nodeById.value.get(id)
  if (n) {
    void loadProfile(n)
  }
  if (fly) void flyToNode(id)
}

/** 提问。S4/S5 先进凑家态，不直接查 */
async function askScene(target: SceneId) {
  const n = focusNode_.value
  if (!n) return
  if (target === 'S4' || target === 'S5') {
    mode.value = 'picking'
    scene.value = target
    const keep = subjects.value.filter((name) => {
      const m = graphNodes.value.find((x) => x.label === name)
      return !m || (m.kind !== 'buyer' && m.kind !== 'project')
    })
    if (!keep.includes(n.label)) keep.push(n.label)
    subjects.value = keep.slice(0, MULTI_MAX)
    tip.value = '再点一颗供应商星（或从顶栏搜索）凑够 2 家就能查交集'
    return
  }
  await runScene(target, [n.label])
}

async function runScene(target: SceneId, names: string[], reframe = true) {
  const list = names.map((s) => s.trim()).filter(Boolean)
  if (!list.length) return
  sceneLoading.value = true
  sceneError.value = ''
  hover.value = null
  tip.value = ''
  try {
    const res = await api.sceneQuery(target, list, 5)
    result.value = res
    scene.value = target
    sceneSubject.value = list[0] ?? ''
    mode.value = target === 'S4' || target === 'S5' ? 'compared' : 'scene'
    serverMs.value = res.elapsed_ms
    absorbSceneGraph(res.graph.nodes, res.graph.edges)
    const subj = res.graph.nodes.find((n) => n.kind !== 'project' && list.includes(n.label))
    if (subj) {
      focusId.value = subj.id
      void loadProfile(subj)
    }
    if (reframe) {
      await nextTick()
      starMap.value?.frameTo(res.graph.nodes.map((n) => n.id))
    }
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 0
    sceneError.value =
      status === 422 || status === 404
        ? '这个主体在当前场景下查不出结果，换一家或改用别的场景'
        : `数据服务异常${status ? '（HTTP ' + status + '）' : '（无法连接）'}，请确认后端已启动（uvicorn :8000）`
  } finally {
    sceneLoading.value = false
  }
}

/** 退出问题：星图与面板一起回总览 */
function exitToOverview() {
  mode.value = 'overview'
  focusId.value = null
  result.value = null
  focusProfile.value = null
  hover.value = null
  tip.value = ''
  sceneError.value = ''
  subjectRelations.value = []
  relTotal.value = 0
  subjects.value = []
  sceneSubject.value = ''
  void nextTick(() => starMap.value?.frame())
}

/** 多选态：点一颗供应商星加入集合 */
function addToBasket(id: string) {
  const n = nodeById.value.get(id)
  if (!n) return
  if (n.kind === 'project' || n.kind === 'buyer') {
    tip.value = '交集只能拿供应商比：项目与采购单位不能作为主体'
    return
  }
  if (!hasWinOf(n)) {
    tip.value = n.label + ' 没有中标记录：S4/S5 以中标供应商为主体，先不放进对比篮'
    return
  }
  if (subjects.value.includes(n.label)) {
    subjects.value = subjects.value.filter((x) => x !== n.label)
    return
  }
  if (subjects.value.length >= MULTI_MAX) {
    tip.value = '最多同时比 ' + MULTI_MAX + ' 家：先移掉一家再加'
    return
  }
  subjects.value = [...subjects.value, n.label]
  if (mode.value === 'compared') {
    // 改了集合就掉结果与连线，直到重新按查询（§17 第 5 条）
    result.value = null
    mode.value = 'picking'
  }
  tip.value =
    subjects.value.length >= 2
      ? n.label + ' 已加入，可以查交集了'
      : n.label + ' 已加入，再多选一家就能查交集'
}

function removeSubject(name: string) {
  subjects.value = subjects.value.filter((x) => x !== name)
  if (mode.value === 'compared') {
    result.value = null
    mode.value = 'picking'
  }
  if (subjects.value.length < 2) tip.value = '至少留 2 家才能比交集'
}

function clearSubjects() {
  subjects.value = []
  result.value = null
  // 留在凑家态：清空是"重挑"，不是"不问了"
  if (mode.value === 'compared') mode.value = 'picking'
}

function onBlocked(id: string) {
  const n = nodeById.value.get(id)
  tip.value = '这是 ' + (result.value?.scene ?? scene.value) + ' 的结果，点不动别的星。' + (n ? '要问 ' + n.label + '，先退出。' : '按 Esc 退出。')
}

function onSelect(id: string | null) {
  // 点空白不再退出：多选态下找节点很容易误点空白，一次误点就丢掉整篮太贵。
  // 退出只走两个明确出口：面板/锁定条上的退出按钮，或 Esc。
  if (id === null) return
  if (mode.value === 'picking' || mode.value === 'compared') return addToBasket(id)
  selectNode(id)
}

function onRankSelect(id: string) {
  // 榜单行 = 把镜头交给这个答案，但不改选中、不换问题（场景态是锁定阅读）
  void flyToNode(id)
}

/** 搜索选中一个节点。项目节点的图标签是截断后的“项目名 · 包N”，
 *  与候选里的全名不同，所以先按 id 匹配、再退回按名字匹配。 */
function onSearchPick(hit: PartyHit) {
  const n = nodeById.value.get(hit.id) ?? graphNodes.value.find((x) => x.label === hit.name)
  if (!n) {
    tip.value = '底图里还没有「' + hit.name + '」这个节点，它可能不在高频采样内'
    return
  }
  // 多选态/结果态：搜索等于把这家加进对比篮；其余状态：只是选中（§18）
  if (mode.value === 'picking' || mode.value === 'compared') {
    if (!subjects.value.includes(n.label)) addToBasket(n.id)
    void flyToNode(n.id)
    return
  }
  selectNode(n.id, true)
}

function onReady(ms: number) {
  renderMs.value = ms
}
function onLabels(shown: number, cand: number) {
  labelsShown.value = shown
  labelsCand.value = cand
}
function onHover(id: string | null, pt: { x: number; y: number } | null) {
  hover.value = id && pt ? { id, x: pt.x, y: pt.y } : null
}
function toggleKind(k: DisplayKind) {
  hiddenKinds.value = hiddenKinds.value.includes(k)
    ? hiddenKinds.value.filter((x) => x !== k)
    : [...hiddenKinds.value, k]
}
function reframeScene() {
  const ids = sceneIds.value
  if (ids.length) starMap.value?.frameTo(ids)
  else starMap.value?.frame()
}
function openParty(id: string) {
  void router.push(`/party/${encodeURIComponent(id)}`)
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') exitToOverview()
}

/* ======== 5. 加载 ======== */
async function loadOverview() {
  overviewLoading.value = true
  try {
    const res = await api.overview()
    overview.value = res
    sampled.value = res.meta.sampled
    serverMs.value = res.elapsed_ms
    baseNodes.value = res.graph.nodes
    baseEdges.value = res.graph.edges
    knownNodeIds.clear()
    for (const n of res.graph.nodes) knownNodeIds.add(n.id)
    knownEdgeKeys.clear()
    for (const e of res.graph.edges) knownEdgeKeys.add(edgeKey(e))
    overviewError.value = false
    void nextTick(() => starMap.value?.frame(false))
  } catch {
    overviewError.value = true
  } finally {
    overviewLoading.value = false
  }
}
async function loadProfile(n: GraphNode) {
  if (n.kind === 'project' || n.id.startsWith('proj:')) {
    focusProfile.value = null
    focusLoading.value = false
    return
  }
  focusLoading.value = true
  try {
    focusProfile.value = await api.partyProfile(n.id)
  } catch {
    focusProfile.value = null
  } finally {
    focusLoading.value = false
  }
}
async function flyToNode(id: string) {
  for (let i = 0; i < 3; i++) {
    await nextTick()
    if (starMap.value?.hasNode?.(id)) {
      starMap.value.flyTo(id)
      return
    }
    await new Promise((r) => setTimeout(r, 260))
  }
  starMap.value?.flyTo(id)
}

onMounted(async () => {
  window.addEventListener('keydown', onKey)
  await loadOverview()
  if (!overviewError.value) {
    const f = route.query.focus
    if (typeof f === 'string' && f && nodeById.value.has(f)) selectNode(f, true)
  }
  try {
    distribution.value = await api.distribution()
    distributionError.value = false
  } catch {
    distributionError.value = true
  }
  void mark()
  start()
})
onUnmounted(() => window.removeEventListener('keydown', onKey))

/** 提示条上的按钮：默认增量追加；超限时改成重建 */
async function onNoticeRun() {
  if (noticeMode.value === 'rebuild') {
    await rebuildOverview()
    return
  }
  try {
    const res = await api.overview()
    const added = res.graph.nodes.filter((n) => !knownNodeIds.has(n.id)).length
    if (added > APPEND_MAX_NEW || graphNodes.value.length + added > APPEND_MAX_TOTAL) {
      // 超出底图尺度：不追加，改成提示重建（会重排，所以要用户明确点）
      noticeMode.value = 'rebuild'
      return
    }
    absorbSceneGraph(res.graph.nodes, res.graph.edges)
    overview.value = res
    sampled.value = res.meta.sampled
    serverMs.value = res.elapsed_ms
    await refreshDistribution()
    noticeMode.value = 'append'
    ack()
  } catch {
    /* 刷新失败就留着提示，下次再点 */
  }
}

/** 重建底图：重新采样 + 重新排布（画面会跳，只在用户点「重建」时发生） */
async function rebuildOverview() {
  await loadOverview()
  await refreshDistribution()
  noticeMode.value = 'append'
  ack()
}

async function refreshDistribution() {
  try {
    distribution.value = await api.distribution()
    distributionError.value = false
  } catch {
    distributionError.value = true
  }
}

watch([focusId, sceneGraph, mode], () => collectSubjectRelations(), { immediate: true })
</script>

<template>
  <div class="explore">
    <SceneBar :loading="sceneLoading" @pick="onSearchPick" @reset="exitToOverview" @reframe="reframeScene" />
    <UpdateNotice
      v-if="noticeShow"
      :mode="noticeMode"
      :new-count="newCount"
      @run="onNoticeRun"
      @dismiss="dismiss"
    />

    <div class="body">
      <div class="sky">
        <StarMap
          ref="starMap"
          :nodes="graphNodes"
          :edges="graphEdges"
          :focus-id="focusId"
          :highlight-ids="highlightIds"
          :scene-ids="sceneIds"
          :scene-edges="sceneEdges"
          :subject-ids="subjectIds"
          :ranks="ranks"
          :dim-others="true"
          :hidden-kinds="hiddenKinds"
          :layout-mode="layoutMode"
          :display-mode="displayMode"
          :locked="locked"
          @select="onSelect"
          @basket="addToBasket"
          @blocked="onBlocked"
          @hover="onHover"
          @ready="onReady"
          @labels="onLabels"
        />
        <div class="vignette" />

        <!-- 锁定阅读的出口：一屏内必须看得见（不变量 13） -->
        <div v-if="locked" class="lockbar">
          <span>{{ mode === 's6' ? '项目详情' : (result?.scene ?? '') + ' 结果' }} · 星图已锁定，点别的星无效</span>
          <button @click="exitToOverview">退出这个问题</button>
        </div>

        <div v-if="overviewError" class="notice err" :class="{ lowered: locked }">
          <b>数据服务未连接</b>
          <span>无法获取关系总览，请确认后端已启动（uvicorn :8000）与数据库可用。</span>
          <button class="retry" :disabled="overviewLoading" @click="loadOverview">
            {{ overviewLoading ? '重试中…' : '重试' }}
          </button>
        </div>
        <div v-else-if="sceneError" class="notice err" :class="{ lowered: locked }">
          <b>查询失败</b>
          <span>{{ sceneError }}</span>
          <button class="retry" @click="exitToOverview">退出这个问题</button>
        </div>
        <div v-else-if="emptyResult" class="notice" :class="{ lowered: locked }">
          <b>这几家没有交集</b>
          <span>去掉一家试试，或换一家中标记录更多的供应商</span>
        </div>
        <div v-else-if="tip" class="notice" :class="{ lowered: locked }">
          <span>{{ tip }}</span>
        </div>

        <div
          v-if="hover && hoverNode"
          class="hovercard"
          :style="{ left: hover.x + 14 + 'px', top: hover.y + 14 + 'px' }"
        >
          <p class="role">{{ DISPLAY_KIND_LABEL[DISPLAY_KIND[hoverNode.kind]] }}</p>
          <p class="name">{{ hoverNode.label }}</p>
          <p v-if="locked" class="tip">场景结果中 · 退出后才能选它</p>
          <p v-else-if="mode === 'picking' || mode === 'compared'" class="tip">单击加入/移出对比篮</p>
          <p v-else class="tip">单击选中它，看能问什么</p>
        </div>

        <p class="hint">
          <template v-if="mode === 'picking'">点供应商星加入对比篮 · 凑够 2 家按查询</template>
          <template v-else-if="mode === 'compared'">可旋转缩放 · 改对比篮需先退出 · Esc 退出</template>
          <template v-else-if="locked">可旋转缩放 · Esc 退出这个问题</template>
          <template v-else>拖拽旋转 · 滚轮远近 · 单击选中 · Esc 回总览</template>
        </p>

        <div class="legend">
          <button
            v-for="k in LEGEND"
            :key="k"
            class="lg"
            :class="{ off: hiddenKinds.includes(k) }"
            :aria-pressed="!hiddenKinds.includes(k)"
            :disabled="!kindCounts[k]"
            @click="toggleKind(k)"
          >
            <i class="mark" :class="k" />
            {{ DISPLAY_KIND_LABEL[k] }}
            <span class="num">{{ kindCounts[k] ?? 0 }}</span>
          </button>
        </div>
      </div>

      <NarrativePanel
        :mode="mode"
        :result="hasResult ? result : null"
        :loading="sceneLoading"
        :overview="overview"
        :distribution="distribution"
        :focus-profile="focusProfile"
        :focus-loading="focusLoading"
        :focus-local="focusLocal"
        :subject-relations="subjectRelations"
        :subject-actions="subjectActions"
        :subject-note="subjectNote"
        :subjects="subjectItems"
        :multi-max="MULTI_MAX"
        :overview-error="overviewError"
        :distribution-error="distributionError"
        @select-node="onRankSelect"
        @open-party="openParty"
        @ask="askScene"
        @exit="exitToOverview"
        @run-subjects="runScene(scene, subjects)"
        @remove-subject="removeSubject"
        @clear-subjects="clearSubjects"
        @retry-overview="loadOverview"
      />
    </div>

    <footer class="statusbar">
      <span class="state">{{ stateLine }}</span>
      <span class="spacer" />
      <span class="num">{{ graphNodes.length }}</span> 节点 ·
      <span class="num">{{ graphEdges.length }}</span> 关系 ·
      标注 <span class="num">{{ labelsShown }}</span>/<span class="num">{{ labelsCand }}</span>
      <span v-if="sampled" class="text-faint">（高频主体采样）</span>
      <span v-if="serverMs !== null" class="ml-3">数据 <b class="num">{{ serverMs }} ms</b></span>
      <span v-if="renderMs !== null" class="ml-3">星图 <b class="num">{{ renderMs }} ms</b></span>
    </footer>
  </div>
</template>

<style scoped>
.explore {
  display: flex;
  height: 100%;
  flex-direction: column;
  background: #0c0b0a;
  color: #f6efe6;
}

.body {
  display: flex;
  min-height: 0;
  flex: 1;
}

.sky {
  position: relative;
  min-width: 0;
  /* 高度下限：场景栏/查询条件变高时，星图不该被无限压缩
     （画布高 H 同时进入标签屏幕半径与墨迹预算两个公式） */
  min-height: 300px;
  flex: 1;
  overflow: hidden;
  user-select: none;
}

.vignette {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(ellipse at 40% 48%, transparent 42%, rgba(6, 5, 4, 0.5) 100%);
}

.lockbar {
  position: absolute;
  top: 14px;
  left: 50%;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid rgba(255, 200, 97, 0.3);
  border-radius: 99px;
  background: rgba(10, 9, 8, 0.82);
  padding: 5px 6px 5px 14px;
  transform: translateX(-50%);
  font-size: 12px;
  color: rgba(246, 239, 230, 0.72);
  backdrop-filter: blur(6px);
}

.lockbar button {
  border: 1px solid rgba(255, 200, 97, 0.4);
  border-radius: 99px;
  padding: 3px 11px;
  color: #ffc861;
  font-size: 11px;
  transition: background var(--dur-fast) var(--ease);
}

.lockbar button:hover {
  background: rgba(255, 200, 97, 0.14);
}

.statusbar .state {
  color: rgba(246, 239, 230, 0.8);
  letter-spacing: 0.02em;
}

.notice.lowered {
  top: 58px;
}

.notice {
  position: absolute;
  top: 22px;
  left: 50%;
  display: flex;
  max-width: 460px;
  transform: translateX(-50%);
  flex-direction: column;
  border: 1px solid rgba(255, 206, 148, 0.28);
  border-radius: 6px;
  background: rgba(12, 11, 10, 0.82);
  padding: 10px 14px;
  gap: 4px;
  font-size: 12px;
  backdrop-filter: blur(6px);
  pointer-events: none;
}

.notice b {
  color: #ffce94;
  font-weight: 500;
}

.notice span {
  color: rgba(246, 239, 230, 0.6);
}

.notice.err {
  border-color: rgba(255, 138, 118, 0.45);
  background: rgba(28, 12, 10, 0.88);
}

.notice.err b {
  color: #ff8a76;
}

.notice .retry {
  align-self: flex-start;
  margin-top: 6px;
  border: 1px solid rgba(255, 138, 118, 0.45);
  border-radius: 4px;
  padding: 3px 10px;
  color: #ff8a76;
  font-size: 11px;
  letter-spacing: 0.06em;
  pointer-events: auto;
  transition:
    background var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease);
}

.notice .retry:hover:not(:disabled) {
  border-color: #ff8a76;
  background: rgba(255, 138, 118, 0.12);
}

.notice .retry:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.hovercard {
  position: absolute;
  z-index: 6;
  max-width: 320px;
  border: 1px solid rgba(246, 239, 230, 0.14);
  border-radius: 5px;
  background: rgba(10, 9, 8, 0.9);
  padding: 8px 10px;
  pointer-events: none;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(8px);
}

.hovercard .role {
  margin: 0;
  color: #ffce94;
  font-size: 10px;
  letter-spacing: 0.1em;
}

.hovercard .name {
  margin: 3px 0 0;
  font-family: "Noto Serif SC", serif;
  font-size: 13px;
  line-height: 1.45;
  color: #f6efe6;
}

.hovercard .tip {
  margin: 5px 0 0;
  color: rgba(246, 239, 230, 0.4);
  font-size: 10px;
}

.hint {
  position: absolute;
  bottom: 46px;
  left: 22px;
  margin: 0;
  color: rgba(246, 239, 230, 0.42);
  font-size: 12px;
  letter-spacing: 0.08em;
  pointer-events: none;
}

.legend {
  position: absolute;
  bottom: 14px;
  left: 20px;
  display: flex;
  gap: 14px;
}

.lg {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 3px 6px;
  color: rgba(246, 239, 230, 0.62);
  font-size: 12px;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease),
    opacity var(--dur-fast) var(--ease);
}

.lg:hover {
  border-color: rgba(246, 239, 230, 0.16);
  background: rgba(246, 239, 230, 0.05);
  color: #f6efe6;
}

.lg.off {
  border-style: dashed;
  opacity: 0.42;
  text-decoration: line-through;
}

.lg.off .mark {
  filter: grayscale(1);
  opacity: 0.4;
}

.lg.off .num {
  text-decoration: line-through;
}

.lg:disabled {
  opacity: 0.22;
  cursor: default;
}

.lg .num {
  color: rgba(246, 239, 230, 0.42);
  font-size: 11px;
}

.mark {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 99px;
}

.mark.buyer {
  width: 16px;
  height: 16px;
  background: radial-gradient(circle, #54c4ff 0%, rgba(84, 196, 255, 0.45) 34%, rgba(84, 196, 255, 0.12) 62%, transparent 78%);
}

.mark.project {
  width: 13px;
  height: 13px;
  background: radial-gradient(circle, #ffc861 0%, rgba(255, 200, 97, 0.45) 36%, rgba(255, 200, 97, 0.12) 64%, transparent 80%);
}

.mark.supplier {
  width: 9px;
  height: 9px;
  background: radial-gradient(circle, #eef3fb 0%, rgba(238, 243, 251, 0.5) 40%, transparent 80%);
}

.statusbar {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  height: 34px;
  border-top: 1px solid var(--line);
  padding: 0 20px;
  color: rgba(246, 239, 230, 0.5);
  font-size: 11px;
}

.statusbar .spacer {
  flex: 1;
}

.statusbar .num {
  color: rgba(246, 239, 230, 0.78);
}

.relays {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 14px;
  color: rgba(246, 239, 230, 0.4);
}

.relay {
  border: 1px solid rgba(246, 239, 230, 0.14);
  border-radius: 4px;
  padding: 2px 8px;
  color: rgba(246, 239, 230, 0.78);
  font-size: 11px;
  transition:
    border-color var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}

.relay:hover {
  border-color: #ffce94;
  color: #ffce94;
}

.ml-3 {
  margin-left: 12px;
}

.statusbar .warn {
  color: #ff8a76;
  font-weight: 500;
}

.statusbar .filtered {
  color: #ffce94;
  opacity: 0.85;
}
</style>
