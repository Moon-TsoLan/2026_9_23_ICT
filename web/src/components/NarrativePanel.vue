<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight, Users, X } from 'lucide-vue-next'
import BarsChart from '@/components/BarsChart.vue'
import EmptyState from '@/components/EmptyState.vue'
import MetricCard from '@/components/MetricCard.vue'
import RankList from '@/components/RankList.vue'
import type {
  Distribution,
  OverviewResult,
  PartyProfile,
  SceneId,
  SceneResult,
  SubjectRelation,
} from '@/types/explore'
import { DISPLAY_KIND, DISPLAY_KIND_LABEL, NODE_KIND_LABEL } from '@/types/graph'
import type { NodeKind } from '@/types/graph'
import { fmtMetric, fmtYuanShort } from '@/utils/format'
import { orgShort } from '@/utils/label'
import { SLOT_CSS } from '@/constants/slots'

const props = defineProps<{
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
  /** 总览/分布加载失败：必须与"仍在加载"区分，否则会永远显示"星图加载中" */
  overviewError?: boolean
  distributionError?: boolean
  /** 当前场景标识；为 S4/S5 时面板显示"查询条件（对比篮）" */
  sceneKind?: SceneId | null
  /** 对比篮：与场景解耦，切单选场景也不清空 */
  subjects?: Array<{ id: string; name: string; kind: NodeKind }>
  /** 当前场景内，聚焦主体与邻居的关系（按角色分组） */
  subjectRelations?: SubjectRelation[]
  /** 面板模式 = 状态机的模式，面板自己不猜 */
  mode?: 'overview' | 'picked' | 'scene' | 'picking' | 'compared' | 's6'
  /** S0 选中态给出的场景按钮（由节点类型与中标记录决定，可能为空） */
  subjectActions?: Array<{ scene: SceneId; label: string }>
  /** 没有按钮时，说明为什么没有 */
  subjectNote?: string
  /** 对比篮上限 */
  multiMax?: number
}>()

const emit = defineEmits<{
  (e: 'select-node', id: string): void
  (e: 'open-party', id: string): void
  (e: 'retry-overview'): void
  (e: 'remove-subject', name: string): void
  (e: 'clear-subjects'): void
  (e: 'run-subjects'): void
  (e: 'ask', scene: SceneId): void
  (e: 'exit'): void
}>()

const panelMode = computed(() => {
  const m = props.mode ?? (props.result ? 'scene' : 'overview')
  return m === 'scene' || m === 'compared' ? 'data' : m === 'picking' ? 'picking' : m === 'picked' || m === 's6' ? 'picked' : 'overview'
})

/** S0 的关系构成摘要：一行说清"它有多少条什么关系"，但不在图上展开 */
const relSummary = computed(() => (props.subjectRelations ?? []).map((g) => g.label + ' ' + g.count).join(' · '))

/** S6 的读法顺序：先问"谁采购的"，再问"谁中了标"，最后才是"谁陪了标" */
const S6_ORDER = ['buy', 'win', 'supply', 'bid']
const s6Groups = computed(() => {
  const gs = props.subjectRelations ?? []
  return [...gs].sort((a, b) => S6_ORDER.indexOf(a.role) - S6_ORDER.indexOf(b.role))
})

/** 交集场景的列头：主体全名太长，矩阵里用短名，完整名放 title */
function colName(name: string) {
  return orgShort(name) || name
}

/** S4 矩阵：行=共同对象，列=参与对比的各家，格子=这一家在该对象上的次数/金额 */
const matrix = computed(() => {
  const t = props.result?.narrative.table
  if (!t?.subjects?.length || !t.by_subject_rows?.length) return null
  const totals = new Map((t.rows ?? []).map((r) => [String(r.purchaser ?? ''), String(r.coop_times ?? '')]))
  return {
    cols: t.subjects,
    rows: t.by_subject_rows.map((r) => ({
      name: r.purchaser,
      cells: r.cells,
      total: totals.get(r.purchaser) ?? '',
    })),
  }
})

/** S5 补充：共同项目里各自中了几次标（后端表格已带"中标方"列，前端直接数） */
const winShare = computed(() => {
  const t = props.result?.narrative.table
  const subs = props.subjects?.map((x) => x.name) ?? []
  if (!t?.rows?.length || subs.length < 2) return []
  const col = t.columns.find((c) => /中标|winner/i.test(c.key + c.label))
  if (!col) return []
  return subs.map((name) => ({
    name,
    wins: t.rows!.filter((r) => String(r[col.key] ?? '').includes(name)).length,
  }))
})

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
    <template v-if="loading && panelMode === 'data'">
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

    <template v-else-if="panelMode === 'data' && result">
      <div class="pad head">
        <p class="kicker">任务二 · {{ result.scene }} 场景</p>
        <h2 class="h-serif">{{ result.narrative.title }}</h2>
        <div class="stats">
          <MetricCard v-for="s in result.narrative.stats" :key="s.k" :k="s.k" :v="s.v" />
        </div>
      </div>
      <div class="scroll">
        <!-- 当前场景内，聚焦主体与邻居的关系：这是"选中一个节点后看它的数据"的主答案 -->
        <section v-if="mode !== 'compared' && (focusProfile || focusLocal || subjectRelations?.length)" class="fs">
          <div class="fs-head">
            <span class="fs-role">{{ focusProfile ? DISPLAY_KIND_LABEL[DISPLAY_KIND[focusProfile.kind]] : focusLocal ? focusLocal.role : '加载中' }}</span>
          </div>
          <p v-if="focusLoading" class="fs-hint">正在取这个节点的速览…</p>
          <template v-else-if="focusProfile || focusLocal">
            <p class="fs-name">{{ focusProfile?.name ?? focusLocal?.name }}</p>
            <p class="fs-stats">
              <span v-for="st in (focusProfile?.stats ?? []).slice(0, 3)" :key="st.k">
                <b class="num">{{ st.v }}</b> {{ st.k }}
              </span>
              <span v-for="l in (focusProfile ? [] : (focusLocal?.lines ?? []))" :key="l">{{ l }}</span>
            </p>
          </template>

          <!-- 本场景内的关系：按边角色分组，点了能跳到邻居 -->
          <div v-if="subjectRelations?.length" class="srel">
            <p class="srel-title">本场景内的关系</p>
            <div v-for="g in subjectRelations" :key="g.role" class="srel-group">
              <p class="srel-role">{{ g.label }}<b class="num">{{ g.count }}</b></p>
              <button
                v-for="it in g.items"
                :key="it.id"
                class="srel-item"
                :title="it.name"
                @click="emit('select-node', it.id)"
              >
                <span class="srel-name">{{ it.name }}</span>
                <span v-if="it.weight > 1" class="num srel-w">×{{ it.weight }}</span>
              </button>
              <p v-if="g.count > g.items.length" class="srel-more">等共 {{ g.count }} 条</p>
            </div>
          </div>
          <p v-else class="fs-hint">这个主体在当前场景里没有连到其它节点。</p>

          <div class="acts">
            <button v-if="focusProfile" class="btn-ghost open" @click="emit('open-party', focusProfile.id)">
              完整档案 <ArrowRight :size="12" />
            </button>
          </div>
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

        <!-- 交集场景：问题问的是"分别跟他们各多少"，所以必须按主体拆列 -->
        <section v-if="matrix">
          <h3>分别与他们各合作了多少</h3>
          <table class="mini mtx">
            <thead>
              <tr>
                <th class="l">采购单位</th>
                <th v-for="(c, ci) in matrix.cols" :key="c" class="r" :title="c">
                  <i class="slotdot" :style="{ background: SLOT_CSS[ci % SLOT_CSS.length] }" />{{ colName(c) }}
                </th>
                <th class="r">合计</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in matrix.rows" :key="row.name">
                <td class="l">{{ row.name }}</td>
                <td v-for="(c, ci) in matrix.cols" :key="c" class="r num" :style="{ borderColor: SLOT_CSS[ci % SLOT_CSS.length] }">
                  {{ row.cells[c]?.times ?? 0 }} 次
                  <em>{{ fmtYuanShort(row.cells[c]?.amount ?? null) }}</em>
                </td>
                <td class="r num">{{ row.total }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <p v-if="winShare.length" class="bs-hint">
          共同项目里各自中标：
          <span v-for="(w, i) in winShare" :key="w.name">{{ colName(w.name) }} {{ w.wins }} 个<template v-if="i &lt; winShare.length - 1"> · </template></span>
        </p>

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

        <!-- 交集场景已经用矩阵按主体拆列了，合计明细就不再重复一遍 -->
        <section v-if="result.narrative.table && !matrix">
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

    <!-- S0 选中态：基本信息 + "可以问它什么"。这是整个交互的枢纽 -->
    <template v-else-if="panelMode === 'picked'">
      <div class="pad head">
        <p class="kicker">{{ mode === 's6' ? 'S6 · 项目详情' : 'S0 · 已选中' }}</p>
        <h2 class="h-serif">{{ focusLoading ? '正在取档案…' : focusProfile?.name ?? focusLocal?.name ?? '—' }}</h2>
        <p class="role-line">
          {{ focusLocal?.role ?? '节点' }}
          <span v-if="relSummary"> · {{ relSummary }}</span>
        </p>
        <div v-if="focusProfile" class="stats">
          <MetricCard v-for="st in focusProfile.stats.slice(0, 3)" :key="st.k" :k="st.k" :v="st.v" />
        </div>
      </div>
      <div class="scroll">
        <section v-if="subjectActions?.length">
          <h3>可以问它什么</h3>
          <div class="asks">
            <button v-for="a in subjectActions" :key="a.scene" class="ask" @click="emit('ask', a.scene)">
              <em>{{ a.scene }}</em>
              <span>{{ a.label }}</span>
            </button>
          </div>
          <p class="bs-hint">星图此刻只亮着它自己——关系要选一个问题才展开。</p>
        </section>
        <p v-else-if="subjectNote" class="note">{{ subjectNote }}</p>

        <!-- S6 的答案就是这三组名字：谁采购、谁中标、谁投标 -->
        <section v-if="mode === 's6' && s6Groups.length">
          <div v-for="g in s6Groups" :key="g.role" class="srel-group">
            <p class="srel-role">{{ g.label }}<b class="num">{{ g.count }}</b></p>
            <button
              v-for="it in g.items"
              :key="it.id"
              class="srel-item"
              :title="it.name"
              @click="emit('select-node', it.id)"
            >
              <span class="srel-name">{{ it.name }}</span>
              <span v-if="it.weight > 1" class="num srel-w">×{{ it.weight }}</span>
            </button>
            <p v-if="g.count > g.items.length" class="srel-more">等共 {{ g.count }} 家</p>
          </div>
          <p class="bs-hint">点名字可以把镜头移过去；点星图上的别的星无效，需先退出。</p>
        </section>

        <section v-if="focusProfile?.facts.length">
          <h3>关键事实</h3>
          <ul class="facts">
            <li v-for="f in focusProfile.facts.slice(0, 4)" :key="f">{{ f }}</li>
          </ul>
        </section>
        <div class="acts">
          <button v-if="focusProfile" class="btn-ghost open" @click="emit('open-party', focusProfile.id)">
            查看完整档案 <ArrowRight :size="13" />
          </button>
          <button class="btn-ghost open" @click="emit('exit')"><X :size="13" />回总览</button>
        </div>
      </div>
    </template>

    <!-- 多选态：凑家 -->
    <template v-else-if="panelMode === 'picking'">
      <div class="pad head">
        <p class="kicker">任务二 · S4 / S5 交集</p>
        <h2 class="h-serif">挑两家以上一起比</h2>
        <div class="stats">
          <MetricCard k="已选" :v="String(subjects?.length ?? 0)" />
          <MetricCard k="还需要" :v="String(Math.max(0, 2 - (subjects?.length ?? 0)))" />
          <MetricCard k="上限" :v="String(multiMax ?? 4)" />
        </div>
      </div>
      <div class="scroll">
        <p class="guide">
          星图现在只亮着供应商。点一颗加进对比篮，再点一次移出；凑够 2 家后按下面的"查询交集"。
          篮子里的星图保持不动，你可以一边看位置一边挑。
        </p>
      </div>
    </template>

    <!-- 总览 -->
    <template v-else-if="panelMode === 'overview'">
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
            先点一颗星选中它，右侧会给出"可以问它什么"：采购单位能问 S1 长期合作、S2 高频投标；
            中标供应商能问 S3 同场对手、S4/S5 与别家的交集。星图上只标短名，悬停看全名，
            底部图例可以按角色过滤。按 Esc 随时回总览。
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
        <div v-if="overviewError" class="err-box">
          <b>数据服务未连接</b>
          <span>无法获取关系总览。请确认后端已启动（uvicorn :8000）与数据库可用。</span>
          <button class="btn-ghost" @click="emit('retry-overview')">重试</button>
        </div>
        <EmptyState v-else-if="!overview" title="星图加载中" hint="正在获取总览数据" />
        <p v-if="distributionError && overview" class="sub-err">分布统计加载失败，图表暂不可用。</p>
      </div>
    </template>

    <!-- 查询条件（对比篮）常驻区块：只要有已选就显示（哪怕当前是单选场景），
         否则用户切一下场景会以为"已选被清空了"。非 S4/S5 时说明它何时才生效。 -->
    <section v-if="mode === 'picking' || mode === 'compared'" class="basket-sec">
      <div class="bs-head">
        <h3>查询条件<b class="num">{{ subjects?.length ?? 0 }}</b></h3>
        <span>
          <button v-if="(subjects?.length ?? 0) >= 2" class="bs-run" :disabled="loading" @click="emit('run-subjects')">查询交集</button>
          <button v-if="subjects?.length" class="bs-clear" @click="emit('clear-subjects')">清空</button>
        </span>
      </div>
      <div v-if="subjects?.length" class="bs-list">
        <span v-for="s in subjects" :key="s.id" class="chip">
          {{ s.name }}
          <button aria-label="移除" @click="emit('remove-subject', s.name)"><X :size="12" /></button>
        </span>
      </div>
      <p v-else class="bs-hint">点星图上亮着的供应商加进篮子，凑够 2 家就能查交集。</p>
      <p v-if="subjects?.length === 1" class="bs-hint">已选 1 家，再多选一家就能查交集。</p>
      <p v-if="(subjects?.length ?? 0) >= (multiMax ?? 4)" class="bs-hint">已到上限 {{ multiMax ?? 4 }} 家，先移掉一家再加。</p>
    </section>
  </aside>
</template>

<style scoped>
.mtx th,
.mtx td {
  vertical-align: top;
}

.mtx td em {
  display: block;
  color: var(--muted);
  font-size: 10px;
  font-style: normal;
}

.slotdot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 99px;
  vertical-align: middle;
}

.mtx td {
  border-top: 1px solid transparent;
}

.mtx th {
  max-width: 76px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.asks {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ask {
  display: flex;
  align-items: baseline;
  gap: 9px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: 9px 11px;
  text-align: left;
  color: var(--ink);
  font-size: 13px;
  transition:
    border-color var(--dur-fast) var(--ease),
    background var(--dur-fast) var(--ease);
}

.ask:hover {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}

.ask em {
  color: var(--accent);
  font-family: Inter, sans-serif;
  font-size: 10px;
  font-style: normal;
  letter-spacing: 0.08em;
}

.role-line {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.note {
  border-left: 2px solid var(--accent);
  margin: 0;
  padding-left: 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.bs-run:disabled {
  opacity: 0.5;
}
/* 本场景内的关系：面板承载"选中节点的场景关系数据"的主区块 */
.srel {
  margin-top: 10px;
  border-top: 1px solid rgba(246, 239, 230, 0.08);
  padding-top: 10px;
}

.srel-title {
  margin: 0 0 8px;
  color: rgba(246, 239, 230, 0.5);
  font-size: 11px;
  letter-spacing: 0.1em;
}

.srel-group + .srel-group {
  margin-top: 10px;
}

.srel-role {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0 0 4px;
  color: #ffce94;
  font-size: 11px;
}

.srel-role .num {
  color: rgba(246, 239, 230, 0.45);
  font-size: 11px;
}

.srel-item {
  display: flex;
  width: 100%;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  border-radius: 4px;
  padding: 3px 6px;
  color: rgba(246, 239, 230, 0.86);
  text-align: left;
  font-size: 12px;
  transition: background var(--dur-fast) var(--ease);
}

.srel-item:hover {
  background: rgba(246, 239, 230, 0.06);
}

.srel-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.srel-w {
  flex-shrink: 0;
  color: rgba(246, 239, 230, 0.4);
  font-size: 11px;
}

.srel-more {
  margin: 2px 0 0 6px;
  color: rgba(246, 239, 230, 0.35);
  font-size: 11px;
}

/* 查询条件（对比篮）：从顶部场景栏迁入 */
.basket-sec {
  border-top: 1px solid rgba(246, 239, 230, 0.1);
  border-bottom: 1px solid rgba(246, 239, 230, 0.1);
  padding: 10px 16px;
}

.bs-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.bs-head h3 {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 0;
}

.bs-head h3 .num {
  color: #ffce94;
}

.bs-run,
.bs-clear {
  margin-left: 8px;
  border: 1px solid rgba(246, 239, 230, 0.16);
  border-radius: 4px;
  padding: 2px 8px;
  color: rgba(246, 239, 230, 0.72);
  font-size: 11px;
}

.bs-run {
  border-color: color-mix(in srgb, var(--accent, #ffc861) 45%, transparent);
  color: #ffce94;
}

.bs-run:hover,
.bs-clear:hover {
  border-color: #ffce94;
  color: #ffce94;
}

.bs-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.bs-list .chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--accent, #ffc861) 30%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--accent, #ffc861) 10%, transparent);
  padding: 2px 6px;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}

.bs-list .chip button {
  display: inline-flex;
  flex-shrink: 0;
  color: rgba(246, 239, 230, 0.55);
}

.bs-list .chip button:hover {
  color: #ff8a76;
}

.bs-hint {
  margin: 8px 0 0;
  color: rgba(246, 239, 230, 0.45);
  font-size: 11px;
  line-height: 1.6;
}
.err-box {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  margin: 16px;
  border: 1px solid rgba(255, 138, 118, 0.4);
  border-radius: 6px;
  background: rgba(255, 138, 118, 0.07);
  padding: 12px 14px;
}

.err-box b {
  color: #ff8a76;
  font-size: 13px;
}

.err-box span {
  color: rgba(246, 239, 230, 0.62);
  font-size: 12px;
  line-height: 1.55;
}

.sub-err {
  margin: 10px 16px 0;
  color: #ff8a76;
  font-size: 11px;
  opacity: 0.85;
}
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

.fs-x-unused {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--muted);
  font-size: 11px;
}

.fs-x-unused:hover {
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
