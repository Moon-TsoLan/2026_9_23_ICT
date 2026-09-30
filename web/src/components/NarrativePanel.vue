<script setup lang="ts">
import { ArrowRight, Users, X } from 'lucide-vue-next'
import BarsChart from '@/components/BarsChart.vue'
import EmptyState from '@/components/EmptyState.vue'
import MetricCard from '@/components/MetricCard.vue'
import RankList from '@/components/RankList.vue'
import type { Distribution, OverviewResult, PartyProfile, SceneResult } from '@/types/explore'
import { DISPLAY_KIND, DISPLAY_KIND_LABEL, NODE_KIND_LABEL } from '@/types/graph'
import type { NodeKind } from '@/types/graph'
import { fmtMetric } from '@/utils/format'

defineProps<{
  result: SceneResult | null
  loading: boolean
  overview: OverviewResult | null
  distribution: Distribution | null
  focusProfile: PartyProfile | null
  focusLoading: boolean
  /** 当前聚焦节点的角色，用于"接力查询"文案 */
  focusKind: NodeKind | null
  /** 取不到档案时（如 vendor: 名称不在 supplier 表内），用当前图的数据兜一份速览 */
  focusLocal: { id: string; name: string; role: string; lines: string[] } | null
}>()

const emit = defineEmits<{
  (e: 'select-node', id: string): void
  (e: 'open-party', id: string): void
  (e: 'clear-focus'): void
}>()

const CATEGORY_LABEL: Record<string, string> = { A: 'A · 货物', B: 'B · 工程', C: 'C · 服务' }

function tableVal(v: string | number | null, key: string): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'number') return fmtMetric(key, v)
  return v
}
</script>

<template>
  <aside class="panel">
    <!-- 场景查询结果 -->
    <template v-if="loading">
      <div class="pad">
        <div class="skeleton h-6 w-3/4" />
        <div class="mt-4 grid grid-cols-3 gap-2">
          <div class="skeleton h-16" />
          <div class="skeleton h-16" />
          <div class="skeleton h-16" />
        </div>
        <div class="skeleton mt-6 h-4 w-full" />
        <div class="skeleton mt-2 h-4 w-5/6" />
        <div class="skeleton mt-2 h-4 w-4/6" />
      </div>
    </template>

    <template v-else-if="result">
      <div class="pad head">
        <p class="kicker">任务二 · {{ result.scene }} 场景</p>
        <h2 class="h-serif">{{ result.narrative.title }}</h2>
        <div class="stats">
          <MetricCard v-for="s in result.narrative.stats" :key="s.k" :k="s.k" :v="s.v" />
        </div>
      </div>
      <div class="scroll">
        <section v-if="focusProfile || focusLoading || focusLocal" class="fs">
          <div class="fs-head">
            <span class="fs-role">{{ focusProfile ? DISPLAY_KIND_LABEL[DISPLAY_KIND[focusProfile.kind]] : focusLocal ? focusLocal.role : '加载中' }}</span>
            <button class="fs-x" @click="emit('clear-focus')"><X :size="12" />取消聚焦</button>
          </div>
          <p v-if="focusLoading" class="fs-hint">正在取这个节点的速览…</p>
          <template v-else-if="focusProfile">
            <p class="fs-name">{{ focusProfile.name }}</p>
            <p class="fs-stats">
              <span v-for="st in focusProfile.stats.slice(0, 3)" :key="st.k">
                <b class="num">{{ st.v }}</b> {{ st.k }}
              </span>
            </p>
            <button class="btn-ghost fs-open" @click="emit('open-party', focusProfile.id)">
              完整档案 <ArrowRight :size="12" />
            </button>
          </template>
          <template v-else-if="focusLocal">
            <p class="fs-name">{{ focusLocal.name }}</p>
            <p class="fs-stats">
              <span v-for="l in focusLocal.lines" :key="l">{{ l }}</span>
            </p>
          </template>
        </section>

        <section v-if="!result.narrative.ranking.length">
          <p class="guide">
            这次查询没有命中任何关系：所选主体之间不存在共同项目或共同投标。
            换一个主体，或减少同时对比的家数再试。
          </p>
        </section>

        <section v-if="result.narrative.ranking.length">
          <h3>{{ result.scene === 'S5' ? '金额最高项目' : 'TOP 榜' }}</h3>
          <RankList :rows="result.narrative.ranking" @select="(id) => emit('select-node', id)" />
        </section>

        <section v-if="result.narrative.combos?.length">
          <h3>协同投标组合</h3>
          <div class="combos">
            <div v-for="c in result.narrative.combos" :key="c.a + c.b" class="combo">
              <Users :size="13" class="shrink-0 text-muted" />
              <span class="names">{{ c.a }} × {{ c.b }}</span>
              <span class="num count">{{ c.count }} 次</span>
            </div>
          </div>
        </section>

        <section v-if="result.narrative.table">
          <h3>明细（{{ result.narrative.table.rows.length }} 行）</h3>
          <table class="mini">
            <thead>
              <tr>
                <th v-for="col in result.narrative.table.columns" :key="col.key" :class="col.align === 'right' ? 'r' : 'l'">
                  {{ col.label }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in result.narrative.table.rows.slice(0, 10)" :key="i">
                <td v-for="col in result.narrative.table!.columns" :key="col.key" :class="[col.align === 'right' ? 'r num' : 'l']">
                  {{ tableVal(row[col.key] ?? null, col.key) }}
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="result.narrative.table.rows.length > 10" class="more">
            仅展示前 10 行，完整 {{ result.narrative.table.rows.length }} 行可去检索页筛选
          </p>
        </section>
      </div>
    </template>

    <!-- 节点聚焦：主体速览 -->
    <template v-else-if="focusProfile || focusLoading">
      <div v-if="focusLoading" class="pad">
        <div class="skeleton h-6 w-2/3" />
        <div class="skeleton mt-4 h-4 w-full" />
        <div class="skeleton mt-2 h-4 w-4/6" />
      </div>
      <template v-else-if="focusProfile">
        <div class="pad head">
          <p class="kicker">{{ DISPLAY_KIND_LABEL[DISPLAY_KIND[focusProfile.kind]] }}</p>
          <h2 class="h-serif">{{ focusProfile.name }}</h2>
          <div class="stats">
            <MetricCard v-for="s in focusProfile.stats" :key="s.k" :k="s.k" :v="s.v" />
          </div>
        </div>
        <div class="scroll">
          <section v-if="focusProfile.facts.length">
            <h3>关键事实</h3>
            <ul class="facts">
              <li v-for="f in focusProfile.facts" :key="f">{{ f }}</li>
            </ul>
          </section>
          <div class="acts">
            <button class="btn-ghost open" @click="emit('open-party', focusProfile.id)">
              查看完整档案
              <ArrowRight :size="13" />
            </button>
            <button class="btn-ghost open" @click="emit('clear-focus')">
              <X :size="13" />
              取消聚焦
            </button>
          </div>
        </div>
      </template>
    </template>

    <!-- 总览：数据规模 + 分布 + 指引 -->
    <template v-else>
      <div class="pad head">
        <p class="kicker">任务二 · 关系总览</p>
        <h2 class="h-serif">采招关系宇宙</h2>
        <div v-if="overview" class="stats">
          <MetricCard k="采购单位" :v="String(overview.meta.purchasers)" />
          <MetricCard k="项目" :v="String(overview.meta.projects)" />
          <MetricCard k="投标主体" :v="String(overview.meta.suppliers_total)" />
        </div>
        <div v-else class="stats">
          <div class="skeleton h-16" />
          <div class="skeleton h-16" />
          <div class="skeleton h-16" />
        </div>
      </div>
      <div class="scroll">
        <section>
          <h3>五大场景</h3>
          <p class="guide">
            顶部切换五个场景：S1/S2 从采购单位看合作与投标圈子，S3 从中标供应商看同场对手，
            S4/S5 把多家供应商放一起找交集。星图上只标短名，悬停看全名，单击聚焦它的关系邻域，
            底部图例可以按角色过滤，状态条上的"接力"能拿着当前主体直接查下一个场景。
          </p>
        </section>
        <section v-if="distribution">
          <h3>品目类别分布（按金额）</h3>
          <BarsChart
            :rows="distribution.categories.map((c) => ({ ...c, key: CATEGORY_LABEL[c.key] ?? c.key }))"
            metric="amount"
          />
        </section>
        <section v-if="distribution">
          <h3>高频品牌 TOP 10</h3>
          <BarsChart :rows="distribution.brands" metric="count" />
        </section>
        <section v-if="distribution">
          <h3>中标次数 TOP 10</h3>
          <BarsChart :rows="distribution.winners" metric="count" />
        </section>
        <EmptyState v-if="!overview" title="星图加载中" hint="正在获取总览数据" />
      </div>
    </template>
  </aside>
</template>

<style scoped>
.acts {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.fs {
  border: 1px solid rgba(255, 206, 148, 0.26);
  border-radius: var(--r-md);
  background: color-mix(in srgb, var(--ink) 4%, transparent);
  padding: 10px 12px;
}

.fs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.fs-role {
  color: var(--accent);
  font-size: 10px;
  letter-spacing: 0.1em;
}

.fs-x {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--muted);
  font-size: 11px;
}

.fs-x:hover {
  color: var(--ink);
}

.fs-name {
  margin: 5px 0 0;
  font-family: "Noto Serif SC", serif;
  font-size: 14px;
  line-height: 1.45;
}

.fs-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 11px;
}

.fs-stats b {
  color: var(--ink);
  font-weight: 500;
  margin-right: 3px;
}

.fs-open {
  margin-top: 8px;
  font-size: 11px;
}

.fs-hint {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.panel {
  display: flex;
  width: 380px;
  flex-shrink: 0;
  flex-direction: column;
  border-left: 1px solid var(--line);
  background: color-mix(in srgb, var(--surface) 72%, transparent);
  backdrop-filter: blur(14px);
}

.pad {
  padding: 18px 20px 12px;
}

.head h2 {
  margin-top: 8px;
  font-size: 19px;
  line-height: 1.4;
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 14px;
}

.scroll {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 4px 12px 20px;
}

section {
  margin-top: 16px;
  padding: 0 8px;
}

h3 {
  margin-bottom: 8px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.14em;
}

.guide {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.8;
}

.combos {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.combo {
  display: flex;
  align-items: center;
  gap: 8px;
  border-radius: var(--r-sm);
  padding: 6px 8px;
  font-size: 12px;
}

.combo .names {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.combo .count {
  flex-shrink: 0;
  color: var(--accent);
}

.mini {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.mini th {
  border-bottom: 1px solid var(--line);
  padding: 5px 6px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
}

.mini td {
  border-bottom: 1px solid var(--line);
  padding: 5px 6px;
}

.mini td.l {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.l {
  text-align: left;
}

.r {
  text-align: right;
}

.more {
  margin-top: 6px;
  color: var(--faint);
  font-size: 11px;
}

.facts {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.facts li {
  border-left: 2px solid var(--accent);
  padding-left: 10px;
  font-size: 13px;
  line-height: 1.6;
}

.open {
  margin: 16px 8px 0;
}

@media (max-width: 960px) {
  .panel {
    display: none;
  }
}
</style>
