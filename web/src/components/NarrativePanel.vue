<script setup lang="ts">
import { ArrowRight, Users } from 'lucide-vue-next'
import BarsChart from '@/components/BarsChart.vue'
import EmptyState from '@/components/EmptyState.vue'
import MetricCard from '@/components/MetricCard.vue'
import RankList from '@/components/RankList.vue'
import type { Distribution, OverviewResult, PartyProfile, SceneResult } from '@/types/explore'
import { NODE_KIND_LABEL } from '@/types/graph'
import { fmtMetric } from '@/utils/format'

defineProps<{
  result: SceneResult | null
  loading: boolean
  overview: OverviewResult | null
  distribution: Distribution | null
  focusProfile: PartyProfile | null
  focusLoading: boolean
}>()

const emit = defineEmits<{
  (e: 'select-node', id: string): void
  (e: 'open-party', id: string): void
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
          <p class="kicker">{{ NODE_KIND_LABEL[focusProfile.kind] }}</p>
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
          <button class="btn-ghost open" @click="emit('open-party', focusProfile.id)">
            查看完整档案
            <ArrowRight :size="13" />
          </button>
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
            顶部切换场景模板：S1/S2 从采购单位看合作与投标圈子，S3 从中标供应商看同场对手，
            S4/S5 把多家供应商放一起找交集。点星图上的节点可聚焦它的关系邻域。
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
