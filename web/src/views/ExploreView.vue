<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Crosshair, X } from 'lucide-vue-next'
import { api } from '@/api/client'
import NarrativePanel from '@/components/NarrativePanel.vue'
import SceneBar from '@/components/SceneBar.vue'
import StarMap from '@/components/StarMap.vue'
import type {
  Distribution,
  OverviewResult,
  PartyProfile,
  SceneId,
  SceneResult,
} from '@/types/explore'
import type { DisplayKind, GraphEdge, GraphNode } from '@/types/graph'
import { DISPLAY_KIND, DISPLAY_KIND_LABEL } from '@/types/graph'
import type { LayoutMode } from '@/composables/useGraphLayout'

const router = useRouter()
const route = useRoute()

/* ---- 星图数据（当前显示的图） ---- */
const graphNodes = ref<GraphNode[]>([])
const graphEdges = ref<GraphEdge[]>([])
const highlightIds = ref<string[]>([])
const ranks = ref<Record<string, number>>({})
const focusId = ref<string | null>(null)
/** 图例即过滤器：被关掉的类别不画也不标注 */
const hiddenKinds = ref<DisplayKind[]>([])
/** 排布：disc = 原扁平圆盘，sphere = 球面星团（画面待确认） */
const layoutMode = ref<LayoutMode>('sphere')

/* ---- 面板数据 ---- */
const overview = ref<OverviewResult | null>(null)
const distribution = ref<Distribution | null>(null)
const sceneResult = ref<SceneResult | null>(null)
const sceneLoading = ref(false)
const focusProfile = ref<PartyProfile | null>(null)
const focusLoading = ref(false)

/* ---- 状态条计时与标注密度 ---- */
const serverMs = ref<number | null>(null)
const renderMs = ref<number | null>(null)
const sampled = ref(false)
const labelsShown = ref(0)
const labelsCand = ref(0)

/* ---- 悬停卡片：全名只在悬停/聚焦时出现，星图上只放短名 ---- */
const hover = ref<{ id: string; x: number; y: number } | null>(null)
const starMap = ref<InstanceType<typeof StarMap> | null>(null)
const sceneBar = ref<{ apply: (s: SceneId, names: string[]) => void } | null>(null)

const nodeById = computed(() => new Map(graphNodes.value.map((n) => [n.id, n])))
const hoverNode = computed(() => (hover.value ? nodeById.value.get(hover.value.id) ?? null : null))
const focusNode_ = computed(() => (focusId.value ? nodeById.value.get(focusId.value) ?? null : null))
const rankOf = (id: string) => ranks.value[id]

/** 档案取不到时（如 vendor: 名称不在 supplier 表里），用当前图的数据兜一份速览 */
const focusLocal = computed(() => {
  const n = focusNode_.value
  if (!n) return null
  let deg = 0
  const roles = new Set<string>()
  for (const e of graphEdges.value) {
    if (e.a === n.id || e.b === n.id) {
      deg++
      roles.add(e.role)
    }
  }
  const lines: string[] = ['本场景内 ' + deg + ' 条关系']
  if (n.weight > 1) lines.push('权重 ' + n.weight)
  const r = ranks.value[n.id]
  if (r) lines.push('排名第 ' + r)
  return { id: n.id, name: n.label, role: DISPLAY_KIND_LABEL[DISPLAY_KIND[n.kind]], lines }
})

/** 各角色在图里的数量，供图例显示与过滤 */
const kindCounts = computed(() => {
  const m = { buyer: 0, project: 0, supplier: 0 } as Record<DisplayKind, number>
  for (const n of graphNodes.value) m[DISPLAY_KIND[n.kind]]++
  return m
})
const LEGEND: DisplayKind[] = ['buyer', 'project', 'supplier']

/** 交集为空：场景查出来没有一条关系 */
const emptyResult = computed(
  () => !!sceneResult.value && sceneResult.value.graph.edges.length === 0 && sceneResult.value.narrative.ranking.length === 0,
)

/** 接力查询：从当前聚焦的主体直接跳进下一个场景，五问连成一条动线 */
const relay = computed(() => {
  const n = focusNode_.value
  if (!n) return []
  const name = n.label
  if (n.kind === 'buyer')
    return [
      { scene: 'S1' as SceneId, text: '看它的长期合作供应商' },
      { scene: 'S2' as SceneId, text: '看它的高频投标圈子' },
    ]
  if (n.kind !== 'project' && n.kind !== 'buyer')
    return [
      { scene: 'S3' as SceneId, text: '查它的同场竞标对手' },
      { scene: 'S4' as SceneId, text: '拿它找交集采购单位' },
      { scene: 'S5' as SceneId, text: '拿它找交集项目' },
    ]
  return []
})

onMounted(async () => {
  window.addEventListener('keydown', onKey)
  try {
    const res = await api.overview()
    overview.value = res
    sampled.value = res.meta.sampled
    serverMs.value = res.elapsed_ms
    graphNodes.value = res.graph.nodes
    graphEdges.value = res.graph.edges
    const focus = route.query.focus
    if (typeof focus === 'string' && focus) void focusNode(focus, true)
  } catch {
    /* 面板会显示空态 */
  }
  try {
    distribution.value = await api.distribution()
  } catch {
    /* 分布图非关键路径 */
  }
})

onUnmounted(() => window.removeEventListener('keydown', onKey))

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    focusId.value = null
    focusProfile.value = null
    hover.value = null
  }
}

/** 场景查询：叙事面板先渲染（纯 DOM），星图在下一拍再补。
 *  注意不能用 requestIdleCallback：星图的 rAF 循环会持续吃帧预算，
 *  软渲染/低端机上 idle 回调可能永远排不上，导致图不更新。 */
async function runScene(scene: SceneId, subjects: string[]) {
  sceneLoading.value = true
  focusId.value = null
  focusProfile.value = null
  hover.value = null
  sampled.value = false
  hiddenKinds.value = []
  try {
    const res = await api.sceneQuery(scene, subjects, 5)
    sceneResult.value = res
    serverMs.value = res.elapsed_ms
    highlightIds.value = res.graph.highlight_ids
    ranks.value = Object.fromEntries(res.narrative.ranking.map((r) => [r.id, r.rank]))
    await nextTick() // 面板数字先上屏
    graphNodes.value = res.graph.nodes
    graphEdges.value = res.graph.edges
    // 搜索即定位：把镜头交给用户刚查的那个主体
    const subj = res.graph.nodes.find((n) => n.kind !== 'project' && subjects.includes(n.label))
    if (subj) {
      focusId.value = subj.id
      void loadProfile(subj)
    }
  } catch {
    sceneResult.value = null
  } finally {
    sceneLoading.value = false
  }
}

function resetAll() {
  sceneResult.value = null
  focusId.value = null
  focusProfile.value = null
  highlightIds.value = []
  ranks.value = {}
  hiddenKinds.value = []
  if (overview.value) {
    graphNodes.value = overview.value.graph.nodes
    graphEdges.value = overview.value.graph.edges
    serverMs.value = overview.value.elapsed_ms
    sampled.value = overview.value.meta.sampled
  }
  void nextTick(() => starMap.value?.frame())
}

function clearFocus() {
  focusId.value = null
  focusProfile.value = null
}

function toggleKind(k: DisplayKind) {
  hiddenKinds.value = hiddenKinds.value.includes(k)
    ? hiddenKinds.value.filter((x) => x !== k)
    : [...hiddenKinds.value, k]
}

/** 主体类节点顺带取档案速览 */
async function loadProfile(n: GraphNode) {
  if (n.kind === 'project' || n.id.startsWith('proj:')) {
    focusProfile.value = null
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

async function focusNode(id: string | null, fly = false) {
  focusId.value = id
  focusProfile.value = null
  if (!id) return
  const n = nodeById.value.get(id)
  if (n) await loadProfile(n)
  if (fly) await flyToNode(id)
}

/** 星图重建是异步的：等它真的有这个节点再飞，最多重试 3 次 */
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

function onSelect(id: string | null) {
  void focusNode(id)
}

/** 榜单行点击：图上定位（飞过去 + 聚焦 + 排名已在标签前缀里） */
function onRankSelect(id: string) {
  focusId.value = id
  const n = nodeById.value.get(id)
  if (n) void loadProfile(n)
  void flyToNode(id)
}

function onHover(id: string | null, pt: { x: number; y: number } | null) {
  hover.value = id && pt ? { id, x: pt.x, y: pt.y } : null
}

function doRelay(scene: SceneId, name: string) {
  sceneBar.value?.apply(scene, [name])
}

function openParty(id: string) {
  void router.push(`/party/${encodeURIComponent(id)}`)
}

function onReady(ms: number) {
  renderMs.value = ms
}

function onLabels(shown: number, cand: number) {
  labelsShown.value = shown
  labelsCand.value = cand
}

</script>

<template>
  <div class="explore">
    <SceneBar
      ref="sceneBar"
      :loading="sceneLoading"
      @query="runScene"
      @reset="resetAll"
      @reframe="starMap?.frame()"
    />

    <div class="body">
      <div class="sky">
        <StarMap
          ref="starMap"
          :nodes="graphNodes"
          :edges="graphEdges"
          :focus-id="focusId"
          :highlight-ids="highlightIds"
          :ranks="ranks"
          :dim-others="true"
          :hidden-kinds="hiddenKinds"
          :layout-mode="layoutMode"
          @select="onSelect"
          @hover="onHover"
          @ready="onReady"
          @labels="onLabels"
        />
        <div class="vignette" />

        <!-- 交集为空：明确告诉用户发生了什么、下一步做什么 -->
        <div v-if="emptyResult" class="notice">
          <b>这张图里没有交集关系</b>
          <span>所选主体之间没有共同项目，试试换一家中标供应商，或减少同时对比的家数</span>
        </div>

        <!-- 悬停卡片：全名只在这里出现 -->
        <div
          v-if="hover && hoverNode"
          class="hovercard"
          :style="{ left: Math.min(hover.x + 14, 9999) + 'px', top: hover.y + 14 + 'px' }"
        >
          <p class="role">{{ DISPLAY_KIND_LABEL[DISPLAY_KIND[hoverNode.kind]] }}<span v-if="rankOf(hoverNode.id)"> · 第 {{ rankOf(hoverNode.id) }} 位</span></p>
          <p class="name">{{ hoverNode.label }}</p>
          <p class="tip">单击聚焦它的关系邻域</p>
        </div>

        <p class="hint">拖拽旋转 · 滚轮远近 · 悬停看全名 · 单击聚焦 · Esc 取消</p>

        <!-- 排布开关：现场对比扁平圆盘与球面星团 -->
        <div class="modesw" role="group" aria-label="排布方式">
          <button :class="{ on: layoutMode === 'disc' }" @click="layoutMode = 'disc'">平面</button>
          <button :class="{ on: layoutMode === 'sphere' }" @click="layoutMode = 'sphere'">立体</button>
        </div>

        <!-- 图例即过滤器：点一下把这类星体从天上抹掉 -->
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
        :result="sceneResult"
        :loading="sceneLoading"
        :overview="overview"
        :distribution="distribution"
        :focus-profile="focusProfile"
        :focus-local="focusLocal"
        :focus-loading="focusLoading"
        :focus-kind="focusNode_?.kind ?? null"
        @select-node="onRankSelect"
        @open-party="openParty"
        @clear-focus="clearFocus"
      />
    </div>

    <footer class="statusbar">
      <span class="num">{{ graphNodes.length }}</span> 节点 ·
      <span class="num">{{ graphEdges.length }}</span> 关系 ·
      标注 <span class="num">{{ labelsShown }}</span>/<span class="num">{{ labelsCand }}</span>
      <span v-if="sampled" class="text-faint">（高频主体采样展示）</span>
      <span class="spacer" />
      <span v-if="focusNode_" class="relays">
        接力：
        <button v-for="r in relay" :key="r.scene" class="relay" @click="doRelay(r.scene, focusNode_!.label)">
          {{ r.text }}
        </button>
      </span>
      <span v-if="serverMs !== null">数据 <b class="num">{{ serverMs }} ms</b></span>
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
}

.notice b {
  color: #ffce94;
  font-weight: 500;
}

.notice span {
  color: rgba(246, 239, 230, 0.6);
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

.modesw {
  position: absolute;
  top: 14px;
  right: 14px;
  display: flex;
  border: 1px solid rgba(246, 239, 230, 0.16);
  border-radius: 99px;
  background: rgba(10, 9, 8, 0.6);
  overflow: hidden;
  backdrop-filter: blur(6px);
}

.modesw button {
  padding: 4px 12px;
  color: rgba(246, 239, 230, 0.55);
  font-size: 11px;
  letter-spacing: 0.06em;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}

.modesw button.on {
  background: rgba(255, 200, 97, 0.16);
  color: #ffc861;
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
  flex-wrap: wrap;
  gap: 6px 8px;
}

.lg {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid transparent;
  border-radius: 99px;
  padding: 3px 9px 3px 7px;
  color: rgba(246, 239, 230, 0.7);
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
  opacity: 0.34;
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
  background: radial-gradient(circle, #ffd484 0%, rgba(255, 200, 97, 0.4) 40%, transparent 74%);
}

.mark.supplier {
  width: 8px;
  height: 8px;
  background: radial-gradient(circle, #ffffff 0%, rgba(238, 243, 251, 0.5) 40%, transparent 72%);
}

.statusbar {
  display: flex;
  height: 34px;
  flex-shrink: 0;
  align-items: center;
  border-top: 1px solid rgba(246, 239, 230, 0.08);
  padding: 0 20px;
  color: rgba(246, 239, 230, 0.55);
  font-size: 12px;
}

.statusbar .spacer {
  flex: 1;
}

.statusbar b {
  color: #ffce94;
  font-weight: 500;
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
</style>
