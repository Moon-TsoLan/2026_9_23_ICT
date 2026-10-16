<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { ApiError } from '@/types/api'
import NarrativePanel from '@/components/NarrativePanel.vue'
import SceneBar from '@/components/SceneBar.vue'
import StarMap from '@/components/StarMap.vue'
import UpdateNotice from '@/components/UpdateNotice.vue'
import { useDataVersion } from '@/composables/useDataVersion'
import type { Distribution, GraphSlice, OverviewResult, PartyHit, PartyProfile, SceneId, SceneResult, SubjectRelation } from '@/types/explore'
import type { DisplayKind, GraphNode, StarMapHandle } from '@/types/graph'
import { DISPLAY_KIND, DISPLAY_KIND_LABEL } from '@/types/graph'
import type { LayoutMode } from '@/composables/useGraphLayout'
import { addSubject, appendSlice, OperationGate, projectGraph, restoreExplore, saveExplore } from '@/utils/exploreGraph'
import type { ExploreMode } from '@/utils/exploreGraph'

const router = useRouter()
const route = useRoute()
const saved = restoreExplore()
const entryIntent = () => `${route.query.focus ?? ''}|${window.history.state?.exploreIntent ?? ''}`
const gate = new OperationGate()
let alive = true
let profileSeq = 0
const { newCount, show: noticeShow, mark, ack, dismiss, start } = useDataVersion()
const noticeMode = ref<'append' | 'rebuild'>('rebuild')
const overview = shallowRef<OverviewResult | null>(saved?.overview ?? null)
const baseNodes = shallowRef(saved?.overview.graph.nodes ?? [])
const baseEdges = shallowRef(saved?.overview.graph.edges ?? [])
const slices = ref<GraphSlice[]>(saved?.slices ?? [])
const pendingIds = ref<string[]>([])
const mode = ref<ExploreMode>(saved?.mode ?? 'overview')
const focusId = ref<string | null>(saved?.focusId ?? null)
const scene = ref<SceneId>(saved?.scene ?? 'S1')
const result = ref<SceneResult | null>(saved?.result ?? null)
const subjects = ref<PartyHit[]>(saved?.subjects ?? [])
const sceneSubject = ref(saved?.sceneSubject ?? '')
const MULTI_MAX = 4
const activeIds = computed(() => new Set([...pendingIds.value, ...subjects.value.map(s => s.id), ...(focusId.value ? [focusId.value] : [])]))
const activeSlices = computed(() => slices.value.filter(s => activeIds.value.has(s.node.id)))
const graph = computed(() => projectGraph({ nodes: baseNodes.value, edges: baseEdges.value }, activeSlices.value, result.value,
  subjects.value.map(s => ({ id: s.id, label: s.name, kind: s.kind, weight: 1 }))))
const graphNodes = computed(() => graph.value.nodes)
const graphEdges = computed(() => graph.value.edges)
const nodeById = computed(() => new Map(graphNodes.value.map(n => [n.id, n])))
const focusNode_ = computed(() => focusId.value ? nodeById.value.get(focusId.value) ?? null : null)
const hover = ref<{ id: string; x: number; y: number } | null>(null)
const hoverNode = computed(() => hover.value ? nodeById.value.get(hover.value.id) ?? null : null)
const focusProfile = ref<PartyProfile | null>(null)
const focusLoading = ref(false)
const nodeLoading = ref(false)
const sceneLoading = ref(false)
const sceneError = ref('')
const tip = ref('')
const distribution = ref<Distribution | null>(saved?.distribution ?? null)
const overviewError = ref(false)
const overviewLoading = ref(false)
const distributionError = ref(false)
const serverMs = ref<number | null>(null)
const renderMs = ref<number | null>(null)
const sampled = computed(() => overview.value?.meta.sampled ?? false)
const labelsShown = ref(0)
const labelsCand = ref(0)
const hiddenKinds = ref<DisplayKind[]>(saved?.hiddenKinds ?? [])
const layoutMode = ref<LayoutMode>(saved?.layoutMode ?? 'sphere')
const restoredStar = shallowRef(saved?.star ?? null)
const starMap = ref<StarMapHandle | null>(null)
const version = computed(() => overview.value?.meta.data_version ?? '')
const focusSlice = computed(() => slices.value.find(s => s.node.id === focusId.value))
const displayMode = computed(() => mode.value === 'picking' ? 'pool' as const : mode.value)
const locked = computed(() => ['scene', 'compared', 's6'].includes(mode.value))
const hasResult = computed(() => !!result.value && ['scene', 'compared'].includes(mode.value))
const sceneGraph = computed(() => hasResult.value ? result.value!.graph : mode.value === 's6' ? focusSlice.value?.graph ?? null : null)
const sceneIds = computed(() => sceneGraph.value?.nodes.map(n => n.id) ?? [])
const sceneEdges = computed(() => sceneGraph.value?.edges ?? [])
const highlightIds = computed(() => hasResult.value ? result.value!.graph.highlight_ids : [])
const ranks = computed(() => Object.fromEntries((hasResult.value ? result.value!.narrative.ranking : []).map(r => [r.id, r.rank])))
const subjectIds = computed(() => subjects.value.map(s => s.id))
const subjectItems = computed(() => subjects.value)
const subjectCount = computed(() => subjects.value.length)
const LEGEND: DisplayKind[] = ['buyer', 'project', 'supplier']
const kindCounts = computed(() => {
  const counts: Record<DisplayKind, number> = { buyer: 0, project: 0, supplier: 0 }
  for (const n of graphNodes.value) counts[DISPLAY_KIND[n.kind]]++
  return counts
})
const emptyResult = computed(() => hasResult.value && !result.value!.graph.edges.length && !result.value!.narrative.ranking.length)
const subjectActions = computed<Array<{ scene: SceneId; label: string }>>(() => {
  const n = focusNode_.value
  if (!n || mode.value !== 'picked') return []
  if (n.kind === 'buyer') return [{ scene: 'S1', label: '查它的长期合作供应商（中标/产品）' }, { scene: 'S2', label: '查它的高频投标主体与协同组合' }]
  if (n.kind !== 'winner') return []
  return [{ scene: 'S3', label: '查它的高频共同竞标主体' }, { scene: 'S4', label: '选多家，查共同合作的采购单位' }, { scene: 'S5', label: '选多家，查共同竞标的项目' }]
})
const subjectNote = computed(() => focusNode_.value && mode.value === 'picked' && !subjectActions.value.length
  ? '该主体没有中标记录，不能作为S3/S4/S5的查询主体；可以查看档案与已加载关系。' : '')
const stateLine = computed(() => {
  if (nodeLoading.value) return '正在补载目标，完成后定位'
  if (mode.value === 'overview') return '未提问 · 选中一个节点开始'
  if (mode.value === 'picking') return `S4/S5 · 对比篮 ${subjectCount.value}/${MULTI_MAX}`
  if (mode.value === 'compared') return `${scene.value} · 对比 ${subjectCount.value} 家 · 交集 ${result.value?.narrative.ranking.length ?? 0} 项`
  return `${mode.value === 'picked' ? 'S0 · 已选中' : mode.value === 's6' ? 'S6 · 项目' : scene.value} ${focusNode_.value?.label ?? ''}`
})
const subjectRelations = computed<SubjectRelation[]>(() => {
  if (!focusId.value || mode.value === 'compared') return []
  const g = sceneGraph.value ?? focusSlice.value?.graph
  if (!g) return []
  const byId = new Map(g.nodes.map(n => [n.id, n]))
  const groups = new Map<string, SubjectRelation>()
  const labels: Record<string, string> = { win: '中标', bid: '投标', supply: '供应', buy: '采购' }
  for (const e of g.edges) {
    const id = e.a === focusId.value ? e.b : e.b === focusId.value ? e.a : null
    const node = id ? byId.get(id) : null
    if (!node) continue
    const group = groups.get(e.role) ?? { role: e.role, label: labels[e.role] ?? e.role, items: [], count: 0 }
    group.count++
    if (group.items.length < 8) group.items.push({ id: node.id, name: node.label, kind: node.kind, weight: e.weight })
    groups.set(e.role, group)
  }
  return [...groups.values()]
})
const focusLocal = computed(() => focusNode_.value ? { id: focusNode_.value.id, name: focusNode_.value.label,
  role: DISPLAY_KIND_LABEL[DISPLAY_KIND[focusNode_.value.kind]], lines: [subjectRelations.value.reduce((n, g) => n + g.count, 0) + ' 条已加载关系'] } : null)
const coverage = computed(() => {
  if (['scene', 'compared', 'overview'].includes(mode.value)) return ''
  const p = focusSlice.value?.pagination
  return p ? `关联对象已加载 ${Math.min(p.offset + p.limit, p.total)} / ${p.total}；右侧关系仅统计已加载部分` : ''
})
function prune() { slices.value = slices.value.filter(s => activeIds.value.has(s.node.id)) }
function unhide(node: GraphNode) { hiddenKinds.value = hiddenKinds.value.filter(k => k !== DISPLAY_KIND[node.kind]) }
function fail(error: unknown) {
  if (error instanceof ApiError && error.status === 409) sceneError.value = '资料库已更新。请点击刷新星图后再继续，当前问题暂时保留。'
  else sceneError.value = error instanceof Error ? error.message : '加载失败，请重试'
}
async function loadProfile(node: GraphNode) {
  const seq = ++profileSeq
  focusProfile.value = null
  focusLoading.value = false
  if (node.kind === 'project') return
  focusLoading.value = true
  try { const profile = await api.partyProfile(node.id); if (alive && seq === profileSeq) focusProfile.value = profile }
  catch { /* 图中主体与已加载关系仍可查看 */ }
  finally { if (alive && seq === profileSeq) focusLoading.value = false }
}
async function ensureNode(id: string, revision: number) {
  let slice = slices.value.find(s => s.node.id === id && s.data_version === version.value)
  if (!slice) slice = await api.neighborhood(id, version.value, 0, gate.signal)
  if (!gate.current(revision) || !alive) return null
  if (slice.data_version !== version.value) throw new Error('数据版本已改变，请刷新星图')
  pendingIds.value = [slice.node.id]
  slices.value = [...slices.value.filter(s => s.node.id !== slice!.node.id), slice]
  await nextTick()
  if (!starMap.value) throw new Error('星图尚未就绪')
  await starMap.value.waitForNodes([slice.node.id])
  return gate.current(revision) && alive ? slice.node : null
}
async function selectIdentity(id: string, basket = false, external = false) {
  if (locked.value && !external) { tip.value = '当前问题已锁定，请先退出，再更换主体'; return }
  const revision = gate.begin()
  nodeLoading.value = true
  sceneLoading.value = false
  sceneError.value = ''
  try {
    const node = await ensureNode(id, revision)
    if (!node) return
    if (basket) {
      subjects.value = addSubject(subjects.value, node, MULTI_MAX)
      focusId.value = node.id
      mode.value = 'picking'
      scene.value = scene.value === 'S5' ? 'S5' : 'S4'
    } else {
      subjects.value = []
      focusId.value = node.id
      mode.value = node.kind === 'project' ? 's6' : 'picked'
      void loadProfile(node)
    }
    result.value = null
    unhide(node)
    tip.value = basket ? `${node.label} 已加入对比篮` : ''
    await nextTick()
    starMap.value?.flyTo(node.id)
  } catch (error) { if (gate.current(revision) && alive) fail(error) }
  finally {
    if (gate.current(revision) && alive) { nodeLoading.value = false; pendingIds.value = []; prune() }
  }
}
function addToBasket(id: string) {
  if (locked.value) { tip.value = '请先退出当前问题，再调整对比主体'; return }
  if (subjects.value.some(s => s.id === id)) return removeSubject(id)
  void selectIdentity(id, true)
}
function removeSubject(id: string) {
  gate.cancel(); nodeLoading.value = false; sceneLoading.value = false; pendingIds.value = []
  subjects.value = subjects.value.filter(s => s.id !== id)
  if (focusId.value === id) focusId.value = subjects.value[0]?.id ?? null
  result.value = null; mode.value = 'picking'; prune()
}
function clearSubjects() { for (const s of [...subjects.value]) removeSubject(s.id) }
async function askScene(target: SceneId) {
  const node = focusNode_.value
  if (!node) return
  if (target === 'S4' || target === 'S5') {
    subjects.value = addSubject(subjects.value, node, MULTI_MAX)
    scene.value = target; mode.value = 'picking'; result.value = null
    tip.value = '搜索或点选其他中标供应商；加入成功后可查看全部已选'
    return
  }
  await runScene(target)
}
async function runScene(target: SceneId) {
  const multi = target === 'S4' || target === 'S5'
  if (multi && subjects.value.length < 2) { tip.value = '至少选择两家中标供应商'; return }
  const names = multi ? subjects.value.map(s => s.name) : [focusNode_.value?.label ?? '']
  const revision = gate.begin()
  sceneLoading.value = true; nodeLoading.value = false; pendingIds.value = []; sceneError.value = ''; tip.value = ''
  try {
    await starMap.value?.waitForNodes(multi ? subjectIds.value : focusId.value ? [focusId.value] : [])
    if (!gate.current(revision)) return
    const answer = await api.sceneQuery(target, names, 5, version.value, gate.signal)
    if (!gate.current(revision) || !alive) return
    if (answer.data_version !== version.value) throw new Error('数据版本已改变，请刷新星图')
    if (multi) answer.graph.nodes = projectGraph(answer.graph, [], null,
      subjects.value.map(s => ({ id: s.id, label: s.name, kind: s.kind, weight: 1 }))).nodes
    result.value = answer; scene.value = target; sceneSubject.value = names[0] ?? ''
    mode.value = multi ? 'compared' : 'scene'
    serverMs.value = answer.elapsed_ms
    await nextTick()
    await starMap.value?.waitForNodes(answer.graph.nodes.map(n => n.id))
    if (gate.current(revision)) starMap.value?.frameTo(answer.graph.nodes.map(n => n.id))
  } catch (error) { if (gate.current(revision) && alive) fail(error) }
  finally { if (gate.current(revision) && alive) sceneLoading.value = false }
}
function exitToOverview() {
  gate.cancel(); ++profileSeq; nodeLoading.value = false; sceneLoading.value = false; focusLoading.value = false
  mode.value = 'overview'; focusId.value = null; result.value = null; focusProfile.value = null
  hover.value = null; tip.value = ''; sceneError.value = ''; subjects.value = []; slices.value = []; pendingIds.value = []
  void nextTick(() => starMap.value?.frame())
}
function onSelect(id: string | null) {
  if (!id) return
  if (mode.value === 'picking') return addToBasket(id)
  if (locked.value) return onBlocked(id)
  void selectIdentity(id)
}
function onSearchPick(hit: PartyHit) { void selectIdentity(hit.id, mode.value === 'picking') }
function onBlocked(id: string) { tip.value = '当前问题已锁定，先退出后再选择' + (nodeById.value.get(id)?.label ?? '') }
function onRankSelect(id: string) {
  if (nodeById.value.has(id)) starMap.value?.flyTo(id)
  else if (!locked.value) void selectIdentity(id)
}
function onReady(ms: number) { renderMs.value = ms }
function onLabels(shown: number, cand: number) { labelsShown.value = shown; labelsCand.value = cand }
function onHover(id: string | null, pt: { x: number; y: number } | null) { hover.value = id && pt ? { id, ...pt } : null }
function toggleKind(kind: DisplayKind) { hiddenKinds.value = hiddenKinds.value.includes(kind) ? hiddenKinds.value.filter(k => k !== kind) : [...hiddenKinds.value, kind] }
function reframeScene() {
  const ids = mode.value === 'picking' ? subjectIds.value : sceneIds.value
  if (ids.length) starMap.value?.frameTo(ids); else starMap.value?.frame()
}
function openParty(id: string) { void router.push(`/party/${encodeURIComponent(id)}`) }
function onKey(e: KeyboardEvent) { if (e.key === 'Escape') exitToOverview() }
async function loadMore() {
  const current = focusSlice.value
  if (!current?.pagination.has_more || nodeLoading.value || sceneLoading.value) return
  const revision = gate.begin(); nodeLoading.value = true; sceneError.value = ''
  try {
    const page = await api.neighborhood(current.node.id, version.value,
      current.pagination.offset + current.pagination.limit, gate.signal)
    if (!gate.current(revision) || !alive) return
    const merged = appendSlice(current, page)
    slices.value = slices.value.map(s => s.node.id === merged.node.id ? merged : s)
    await nextTick()
    await starMap.value?.waitForNodes(merged.graph.nodes.map(n => n.id))
  } catch (error) { if (gate.current(revision) && alive) fail(error) }
  finally { if (gate.current(revision) && alive) nodeLoading.value = false }
}
async function refreshDistribution() {
  try { const data = await api.distribution(); if (alive) distribution.value = data }
  catch { if (alive) distributionError.value = true }
}
async function loadOverview() {
  const revision = gate.begin(); overviewLoading.value = true
  try {
    const data = await api.overview()
    if (!gate.current(revision) || !alive) return
    exitToOverview()
    restoredStar.value = null; overview.value = data
    baseNodes.value = data.graph.nodes; baseEdges.value = data.graph.edges
    serverMs.value = data.elapsed_ms; overviewError.value = false
    await mark(data.meta.data_version)
    await nextTick(); starMap.value?.frame(false)
    return true
  } catch (error) { if (gate.current(revision) && alive) { overviewError.value = true; fail(error) } }
  finally { if (alive) overviewLoading.value = false }
}
async function onNoticeRun() { if (await loadOverview()) { await refreshDistribution(); ack(version.value) } }
let mounted = false
async function routeFocus(value: unknown) {
  if (typeof value === 'string' && value) await selectIdentity(value, false, true)
}
onMounted(async () => {
  window.addEventListener('keydown', onKey)
  if (!overview.value) await loadOverview()
  if (!alive) return
  mounted = true
  if (typeof route.query.focus === 'string' && saved?.entryIntent !== entryIntent()) await routeFocus(route.query.focus)
  else if (focusNode_.value) void loadProfile(focusNode_.value)
  if (!distribution.value) void refreshDistribution()
  await mark(version.value)
  if (alive) start()
})
watch(() => route.query.focus, value => { if (mounted) void routeFocus(value) })
onBeforeUnmount(() => {
  alive = false; gate.cancel(); ++profileSeq
  window.removeEventListener('keydown', onKey)
  if (overview.value) saveExplore({ entryIntent: entryIntent(), overview: overview.value, distribution: distribution.value,
    mode: mode.value, focusId: focusId.value, subjects: subjects.value, slices: activeSlices.value,
    result: result.value, scene: scene.value, sceneSubject: sceneSubject.value,
    hiddenKinds: hiddenKinds.value, layoutMode: layoutMode.value, star: starMap.value?.snapshot() ?? null })
})
</script>

<template>
  <div class="explore">
    <SceneBar :loading="sceneLoading || nodeLoading" @pick="onSearchPick" @reset="exitToOverview" @reframe="reframeScene" />
    <div v-if="coverage || mode === 'picking'" class="coverage-bar">
      <span v-if="coverage">{{ coverage }}</span>
      <button v-if="focusSlice?.pagination.has_more" class="btn-ghost" :disabled="nodeLoading || sceneLoading" @click="loadMore">加载更多关系</button>
      <button v-if="mode === 'picking'" class="btn-ghost" @click="reframeScene">查看全部已选</button>
      <button v-if="sceneError.includes('资料库已更新')" class="btn-ghost" @click="onNoticeRun">刷新星图</button>
    </div>
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
          :ambient-nodes="baseNodes"
          :ambient-edges="baseEdges"
          :restored="restoredStar"
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
          <template v-else-if="mode === 'compared'">可旋转缩放 · 修改对比篮会清除当前结果 · Esc 退出</template>
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
        :focus-kind="focusNode_?.kind ?? null"
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
        @run-subjects="runScene(scene)"
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
      <span v-if="sampled" class="text-faint">（底图高频采样）</span>
      <span v-if="serverMs !== null" class="ml-3">数据 <b class="num">{{ serverMs }} ms</b></span>
      <span v-if="renderMs !== null" class="ml-3">星图 <b class="num">{{ renderMs }} ms</b></span>
    </footer>
  </div>
</template>

<style scoped>
.coverage-bar { display: flex; gap: 12px; align-items: center; padding: 6px 20px; color: var(--muted); font-size: 12px; flex-wrap: wrap; }

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
