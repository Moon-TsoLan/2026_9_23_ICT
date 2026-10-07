<script setup lang="ts">
import { Download, RotateCcw, SearchX } from 'lucide-vue-next'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api/client'
import CobDetail from '@/components/CobDetail.vue'
import EmptyState from '@/components/EmptyState.vue'
import PartyPicker from '@/components/PartyPicker.vue'
import UpdateNotice from '@/components/UpdateNotice.vue'
import { useDataVersion } from '@/composables/useDataVersion'
import type { CobRecord, CobSearchQuery } from '@/types/search'
import type { RequestState } from '@/types/api'
import { fmtQty, fmtYuan, orDash } from '@/utils/format'

const PAGE_SIZE = 20

const route = useRoute()

const query = reactive<CobSearchQuery>({
  kw: '',
  purchaser: '',
  winner: '',
  brand: '',
  category_type: '',
  price_field: 'total_price',
  price_min: null,
  price_max: null,
  sort: 'relevance',
  page: 1,
  page_size: PAGE_SIZE,
})

const rows = ref<CobRecord[]>([])
const total = ref(0)
const state = ref<RequestState>('idle')
const errorMsg = ref('')

const selectedId = ref<number | null>(null)
const detail = ref<CobRecord | null>(null)
const detailLoading = ref(false)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

/** 有新结果入库时只给提示；点了「刷新」才按当前筛选与页码重查 */
const { newCount, show: noticeShow, mark, ack, dismiss, start } = useDataVersion()

/** 已生效的非默认条件（chip 展示用） */
const chips = computed(() => {
  const list: Array<{ key: string; label: string }> = []
  if (query.purchaser) list.push({ key: 'purchaser', label: `采购：${query.purchaser}` })
  if (query.winner) list.push({ key: 'winner', label: `中标：${query.winner}` })
  if (query.brand) list.push({ key: 'brand', label: `品牌：${query.brand}` })
  if (query.category_type) {
    const label = { A: '货物', B: '工程', C: '服务' }[query.category_type] ?? query.category_type
    list.push({ key: 'category_type', label: `品目：${label}` })
  }
  if (query.price_min != null || query.price_max != null) {
    const field = query.price_field === 'unit_price' ? '单价' : '总价'
    list.push({ key: 'price', label: `${field} ${query.price_min ?? 0} ~ ${query.price_max ?? '∞'}` })
  }
  return list
})

let seq = 0

async function load() {
  const my = ++seq
  state.value = 'loading'
  errorMsg.value = ''
  try {
    const res = await api.searchObjects({ ...query })
    if (my !== seq) return
    rows.value = res.items
    total.value = res.total
    state.value = 'success'
    if (res.items.length && !res.items.some((r) => r.cob_id === selectedId.value)) {
      void select(res.items[0]!.cob_id)
    }
    if (!res.items.length) {
      selectedId.value = null
      detail.value = null
    }
  } catch (e) {
    if (my !== seq) return
    state.value = 'error'
    errorMsg.value = e instanceof Error ? e.message : '请求失败'
  }
}

async function select(cobId: number) {
  selectedId.value = cobId
  detailLoading.value = true
  try {
    detail.value = await api.objectDetail(cobId)
  } catch {
    detail.value = rows.value.find((r) => r.cob_id === cobId) ?? null
  } finally {
    detailLoading.value = false
  }
}

function applyFilters() {
  query.page = 1
  void load()
}

function resetFilters() {
  Object.assign(query, {
    kw: '', purchaser: '', winner: '', brand: '', category_type: '',
    price_field: 'total_price', price_min: null, price_max: null, sort: 'relevance', page: 1,
  })
  void load()
}

function removeChip(key: string) {
  if (key === 'price') {
    query.price_min = null
    query.price_max = null
  } else {
    ;(query as Record<string, unknown>)[key] = ''
  }
  applyFilters()
}

function gotoPage(p: number) {
  if (p < 1 || p > pageCount.value) return
  query.page = p
  void load()
}

const exportHref = computed(() => api.exportUrl({ ...query }))

// 关键词防抖：只触发关键词，不动其他条件
let kwTimer = 0
watch(
  () => query.kw,
  () => {
    window.clearTimeout(kwTimer)
    kwTimer = window.setTimeout(applyFilters, 300)
  },
)

/** 关键词高亮（拆分渲染，无 v-html，天然防注入） */
function highlight(text: string | null): Array<{ t: string; hit: boolean }> {
  const t = text ?? ''
  const kw = query.kw?.trim()
  if (!kw || !t.includes(kw)) return [{ t, hit: false }]
  const parts: Array<{ t: string; hit: boolean }> = []
  let rest = t
  while (rest.includes(kw)) {
    const i = rest.indexOf(kw)
    if (i > 0) parts.push({ t: rest.slice(0, i), hit: false })
    parts.push({ t: kw, hit: true })
    rest = rest.slice(i + kw.length)
  }
  if (rest) parts.push({ t: rest, hit: false })
  return parts
}

onMounted(() => {
  // 支持从其他页带条件跳入：/search?kw= / ?purchaser= / ?winner=
  const { kw, purchaser, winner } = route.query
  if (typeof kw === 'string') query.kw = kw
  if (typeof purchaser === 'string') query.purchaser = purchaser
  if (typeof winner === 'string') query.winner = winner
  void load().then(() => {
    void mark()
    start()
  })
})

/** 提示条上的「刷新」：保留筛选与页码，重查一次 */
async function onNoticeRun() {
  await load()
  ack()
}
</script>

<template>
  <div class="search-page">
    <section class="main">
      <UpdateNotice
        v-if="noticeShow"
        mode="append"
        :new-count="newCount"
        @run="onNoticeRun"
        @dismiss="dismiss"
      />
      <header class="head">
        <div>
          <p class="kicker">任务一 · 提取结果</p>
          <h1 class="h-serif">标的检索</h1>
        </div>
        <a class="btn-ghost" :href="exportHref" download>
          <Download :size="14" />
          导出 CSV
        </a>
      </header>

      <div class="filters card">
        <div class="grid">
          <input v-model="query.kw" class="input col-span-2" placeholder="关键词：名称 / 项目 / 品牌 / 规格 / 主体" aria-label="关键词" />
          <PartyPicker v-model="query.purchaser" kind="buyer" placeholder="采购单位" @select="applyFilters" @update:model-value="applyFilters" />
          <PartyPicker v-model="query.winner" kind="supplier" placeholder="中标供应商" @select="applyFilters" @update:model-value="applyFilters" />
          <input v-model="query.brand" class="input" placeholder="品牌" @keydown.enter="applyFilters" />
          <select v-model="query.category_type" class="input" aria-label="品目类别" @change="applyFilters">
            <option value="">品目类别</option>
            <option value="A">A · 货物</option>
            <option value="B">B · 工程</option>
            <option value="C">C · 服务</option>
          </select>
          <select v-model="query.sort" class="input" aria-label="排序" @change="applyFilters">
            <option value="relevance">按总价排序</option>
            <option value="total_price_asc">总价从低到高</option>
            <option value="unit_price_desc">单价从高到低</option>
          </select>
          <div class="price">
            <select v-model="query.price_field" class="input w-[86px]" aria-label="金额口径">
              <option value="total_price">总价</option>
              <option value="unit_price">单价</option>
            </select>
            <input v-model.number="query.price_min" class="input" type="number" min="0" placeholder="最低" @keydown.enter="applyFilters" />
            <span class="text-faint">—</span>
            <input v-model.number="query.price_max" class="input" type="number" min="0" placeholder="最高" @keydown.enter="applyFilters" />
          </div>
          <div class="flex gap-2">
            <button class="btn" @click="applyFilters">查询</button>
            <button class="btn-ghost" @click="resetFilters">
              <RotateCcw :size="13" />
              重置
            </button>
          </div>
        </div>
        <div v-if="chips.length" class="chips">
          <span v-for="c in chips" :key="c.key" class="chip">
            {{ c.label }}
            <button class="text-faint hover:text-ink" aria-label="移除条件" @click="removeChip(c.key)">×</button>
          </span>
        </div>
      </div>

      <div class="result-meta">
        <span class="num">{{ total.toLocaleString() }}</span> 条标的
        <span v-if="state === 'loading'" class="text-faint">· 查询中…</span>
      </div>

      <div v-if="state === 'error'" class="card err">
        <SearchX :size="18" />
        <span>{{ errorMsg }}</span>
        <button class="btn-ghost" @click="load">重试</button>
      </div>

      <div v-else class="table-wrap card">
        <table>
          <thead>
            <tr>
              <th class="left">标的物名称</th>
              <th class="left">项目 / 采购单位</th>
              <th class="left">品目</th>
              <th class="left">品牌 / 规格</th>
              <th class="right">单价</th>
              <th class="right">数量</th>
              <th class="right">总价</th>
              <th class="left">中标供应商</th>
            </tr>
          </thead>
          <tbody v-if="state === 'loading' && !rows.length">
            <tr v-for="i in 8" :key="i">
              <td colspan="8"><div class="skeleton h-4" :style="{ width: `${88 - i * 6}%` }" /></td>
            </tr>
          </tbody>
            <tbody v-else>
            <tr
              v-for="row in rows"
              :key="row.cob_id"
              :class="{ on: selectedId === row.cob_id }"
              @click="select(row.cob_id)"
            >
              <td class="left name">
                <template v-for="(part, i) in highlight(row.object_name)" :key="i">
                  <mark v-if="part.hit">{{ part.t }}</mark>
                  <template v-else>{{ part.t }}</template>
                </template>
              </td>
              <td class="left">
                <span class="block max-w-[240px] truncate">{{ row.project_name }} · 包{{ row.package_no }}</span>
                <span class="block max-w-[240px] truncate text-muted">{{ orDash(row.purchaser) }}</span>
              </td>
              <td class="left">{{ orDash(row.category_name) }}</td>
              <td class="left">
                <span class="block max-w-[140px] truncate">{{ orDash(row.brand) }}</span>
                <span class="block max-w-[140px] truncate text-muted">{{ orDash(row.spec_model) }}</span>
              </td>
              <td class="right num">{{ fmtYuan(row.unit_price) }}</td>
              <td class="right num">{{ fmtQty(row.quantity) }}{{ row.unit ?? '' }}</td>
              <td class="right num font-medium">{{ fmtYuan(row.total_price) }}</td>
              <td class="left">
                <span class="block max-w-[180px] truncate">{{ orDash(row.winner?.supplier_name) }}</span>
              </td>
            </tr>
            <tr v-if="!rows.length && state === 'success'">
              <td colspan="8">
                <EmptyState title="没有符合条件的标的" hint="试试放宽筛选条件或换个关键词" />
              </td>
            </tr>
          </tbody>
        </table>
        <footer class="pager">
          <span class="text-muted">第 <b class="num">{{ query.page }}</b> / {{ pageCount }} 页</span>
          <div class="flex gap-2">
            <button class="btn-ghost" :disabled="(query.page ?? 1) <= 1" @click="gotoPage((query.page ?? 1) - 1)">上一页</button>
            <button class="btn-ghost" :disabled="(query.page ?? 1) >= pageCount" @click="gotoPage((query.page ?? 1) + 1)">下一页</button>
          </div>
        </footer>
      </div>
    </section>

    <aside class="detail card">
      <CobDetail v-if="detail || detailLoading" :record="detail" :loading="detailLoading" />
      <EmptyState v-else title="点一行看全量信息" hint="项目、标的、中标方、溯源一卡讲清" />
    </aside>
  </div>
</template>

<style scoped>
.search-page {
  display: flex;
  height: 100%;
  gap: 20px;
  overflow: hidden;
  padding: 20px 24px;
}

.main {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.head h1 {
  margin-top: 6px;
  font-size: 28px;
}

.filters {
  margin-top: 14px;
  padding: 12px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.col-span-2 {
  grid-column: span 2;
}

.price {
  display: flex;
  align-items: center;
  gap: 6px;
}

.price .input:not(.w-\[86px\]) {
  width: 100%;
  min-width: 0;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.result-meta {
  margin: 12px 2px 8px;
  color: var(--muted);
  font-size: 12px;
}

.err {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px;
  color: var(--bad);
  font-size: 13px;
}

.table-wrap {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.table-wrap {
  overflow: auto;
}

thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  border-bottom: 1px solid var(--ink);
  background: var(--surface);
  padding: 8px 10px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.08em;
  white-space: nowrap;
}

td {
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
  vertical-align: top;
}

tbody tr {
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease);
}

tbody tr:hover {
  background: color-mix(in srgb, var(--ink) 4%, transparent);
}

tbody tr.on {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
}

.left {
  text-align: left;
}

.right {
  text-align: right;
}

mark {
  border-radius: 2px;
  background: color-mix(in srgb, var(--accent) 22%, transparent);
  color: inherit;
  padding: 0 1px;
}

.text-muted {
  color: var(--muted);
  font-size: 12px;
}

.pager {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--line);
  padding: 8px 12px;
  font-size: 12px;
}

.detail {
  width: 360px;
  flex-shrink: 0;
  overflow: auto;
}

@media (max-width: 1100px) {
  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .detail {
    display: none;
  }
}
</style>
