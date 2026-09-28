<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
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
import type { GraphEdge, GraphNode } from '@/types/graph'

const router = useRouter()
const route = useRoute()

/* ---- 星图数据（当前显示的图） ---- */
const graphNodes = ref<GraphNode[]>([])
const graphEdges = ref<GraphEdge[]>([])
const highlightIds = ref<string[]>([])
const ranks = ref<Record<string, number>>({})
const focusId = ref<string | null>(null)

/* ---- 面板数据 ---- */
const overview = ref<OverviewResult | null>(null)
const distribution = ref<Distribution | null>(null)
const sceneResult = ref<SceneResult | null>(null)
const sceneLoading = ref(false)
const focusProfile = ref<PartyProfile | null>(null)
const focusLoading = ref(false)

/* ---- 状态条计时 ---- */
const serverMs = ref<number | null>(null)
const renderMs = ref<number | null>(null)
const sampled = ref(false)

onMounted(async () => {
  try {
    const res = await api.overview()
    overview.value = res
    sampled.value = res.meta.sampled
    serverMs.value = res.elapsed_ms
    graphNodes.value = res.graph.nodes
    graphEdges.value = res.graph.edges
    // 支持 /explore?focus=<id> 从主体档案跳入
    const focus = route.query.focus
    if (typeof focus === 'string' && focus) void focusNode(focus)
  } catch {
    /* 面板会显示空态 */
  }
  try {
    distribution.value = await api.distribution()
  } catch {
    /* 分布图非关键路径 */
  }
})

/** 场景查询：叙事面板先渲染（纯 DOM），星图在下一拍再补。
 *  注意不能用 requestIdleCallback：星图的 rAF 循环会持续吃帧预算，
 *  软渲染/低端机上 idle 回调可能永远排不上，导致图不更新。 */
async function runScene(scene: SceneId, subjects: string[]) {
  sceneLoading.value = true
  focusId.value = null
  focusProfile.value = null
  sampled.value = false
  try {
    const res = await api.sceneQuery(scene, subjects, 5)
    sceneResult.value = res
    serverMs.value = res.elapsed_ms
    highlightIds.value = res.graph.highlight_ids
    ranks.value = Object.fromEntries(res.narrative.ranking.map((r) => [r.id, r.rank]))
    await nextTick() // 面板数字先上屏
    graphNodes.value = res.graph.nodes
    graphEdges.value = res.graph.edges
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
  if (overview.value) {
    graphNodes.value = overview.value.graph.nodes
    graphEdges.value = overview.value.graph.edges
    serverMs.value = overview.value.elapsed_ms
    sampled.value = overview.value.meta.sampled
  }
}

/** 点节点 / 点榜单 → 聚焦；主体类节点顺带取档案速览 */
async function focusNode(id: string | null) {
  focusId.value = id
  focusProfile.value = null
  if (!id) return
  if (id.startsWith('sup:') || id.startsWith('buyer:')) {
    focusLoading.value = true
    try {
      focusProfile.value = await api.partyProfile(id)
    } catch {
      focusProfile.value = null
    } finally {
      focusLoading.value = false
    }
  }
}

function onSelect(id: string | null) {
  // 点空白处取消聚焦；点节点在场景结果内聚焦，否则进入主体速览
  void focusNode(id)
}

function onRankSelect(id: string) {
  // 榜单行点击：图上定位（聚焦 + 脉冲已有排名徽标）
  focusId.value = id
}

function openParty(id: string) {
  void router.push(`/party/${encodeURIComponent(id)}`)
}

function onReady(ms: number) {
  renderMs.value = ms
}
</script>

<template>
  <div class="explore">
    <SceneBar :loading="sceneLoading" @query="runScene" @reset="resetAll" />

    <div class="body">
      <div class="sky">
        <StarMap
          :nodes="graphNodes"
          :edges="graphEdges"
          :focus-id="focusId"
          :highlight-ids="highlightIds"
          :ranks="ranks"
          :dim-others="true"
          @select="onSelect"
          @ready="onReady"
        />
        <div class="vignette" />
        <p class="hint">拖拽旋转 · 滚轮远近 · 点节点聚焦</p>
        <div class="legend">
          <span><i class="mark buyer" />采购单位</span>
          <span><i class="mark project" />项目</span>
          <span><i class="mark winner" />中标供应商</span>
          <span><i class="mark bidder" />投标参与方</span>
          <span><i class="mark vendor" />产品供应商</span>
        </div>
      </div>

      <NarrativePanel
        :result="sceneResult"
        :loading="sceneLoading"
        :overview="overview"
        :distribution="distribution"
        :focus-profile="focusProfile"
        :focus-loading="focusLoading"
        @select-node="onRankSelect"
        @open-party="openParty"
      />
    </div>

    <footer class="statusbar">
      <span class="num">{{ graphNodes.length }}</span> 节点 ·
      <span class="num">{{ graphEdges.length }}</span> 关系
      <span v-if="sampled" class="text-faint">（高频主体采样展示）</span>
      <span class="spacer" />
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

.hint {
  position: absolute;
  bottom: 44px;
  left: 22px;
  margin: 0;
  color: rgba(246, 239, 230, 0.42);
  font-size: 12px;
  letter-spacing: 0.08em;
  pointer-events: none;
}

.legend {
  position: absolute;
  bottom: 16px;
  left: 22px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: rgba(246, 239, 230, 0.62);
  font-size: 12px;
  pointer-events: none;
}

.legend span {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mark {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 99px;
  background: radial-gradient(circle, rgba(255, 246, 236, 0.9) 0%, rgba(255, 220, 186, 0.25) 26%, transparent 72%);
}

.mark.buyer {
  width: 16px;
  height: 16px;
  background: radial-gradient(circle, rgba(255, 236, 214, 0.28) 0%, rgba(255, 220, 186, 0.08) 46%, transparent 76%);
}

.mark.project {
  width: 15px;
  height: 15px;
  background: radial-gradient(circle, rgba(240, 208, 176, 0.72) 0%, rgba(240, 208, 176, 0.28) 46%, transparent 76%);
}

.mark.bidder {
  opacity: 0.7;
}

.mark.vendor {
  background: radial-gradient(circle, rgba(240, 210, 170, 0.8) 0%, rgba(210, 170, 120, 0.2) 30%, transparent 72%);
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

.ml-3 {
  margin-left: 12px;
}
</style>
