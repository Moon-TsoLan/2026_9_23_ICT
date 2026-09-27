<script setup lang="ts">
import { computed, ref, watch } from 'vue'

interface Lot {
  id: string
  name: string
  cat: string
  brand: string
  spec: string
  price: number
  qty: number
  total: number
  project: string
  winner: string
}

const lots: Lot[] = [
  { id: '1', name: '医院信息化集成平台', cat: '服务器', brand: '华为', spec: 'FusionCube 1000 超融合', price: 1280000, qty: 2, total: 2560000, project: '增城医院二期信息化', winner: '联通数智' },
  { id: '2', name: '医疗专网交换机', cat: '网络设备', brand: '新华三', spec: 'S6800-54QF', price: 86000, qty: 12, total: 1032000, project: '增城医院二期信息化', winner: '联通数智' },
  { id: '3', name: '三年驻场运维', cat: '运行维护', brand: '—', spec: '工作日驻场 2 人', price: 360000, qty: 1, total: 360000, project: '增城医院二期信息化', winner: '联通数智' },
  { id: '4', name: '门诊电子病历', cat: '应用软件', brand: '卫宁健康', spec: 'WiNEX EMR V6', price: 960000, qty: 1, total: 960000, project: '增城医院门诊系统', winner: '卫宁健康' },
  { id: '5', name: '机房精密空调', cat: '专用设备', brand: '维谛', spec: 'Liebert PEX 4 台', price: 74000, qty: 4, total: 296000, project: '增城医院机房改造', winner: '东软集团' },
  { id: '6', name: '临床数据中心', cat: '应用软件', brand: '东软', spec: 'RealOne CDR', price: 1800000, qty: 1, total: 1800000, project: '浙人医临床集成平台', winner: '东软集团' },
  { id: '7', name: '政务数据中台许可', cat: '应用软件', brand: '中国软件', spec: '中软数据中台 V3', price: 1600000, qty: 1, total: 1600000, project: '南山政数局数据中台', winner: '中国软件' },
  { id: '8', name: '教育云桌面终端', cat: '终端设备', brand: '中科曙光', spec: 'W330-G40', price: 6400, qty: 400, total: 2560000, project: '高新区教育云桌面', winner: '中科曙光' },
]

const q = ref('')
const cat = ref('全部')
const openId = ref(lots[0]!.id)
const cats = ['全部', ...new Set(lots.map((l) => l.cat))]
const yuan = (n: number) => n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const rows = computed(() => {
  const s = q.value.trim()
  return lots.filter((l) => {
    const hit = !s || [l.name, l.brand, l.spec, l.cat, l.project, l.winner].some((v) => v.includes(s))
    return hit && (cat.value === '全部' || l.cat === cat.value)
  })
})
const current = computed(() => lots.find((l) => l.id === openId.value) ?? null)
watch(rows, (list) => {
  if (!list.some((l) => l.id === openId.value)) openId.value = list[0]?.id ?? ''
})
</script>

<template>
  <div class="flex h-full flex-col gap-6 overflow-auto px-6 py-6 lg:flex-row lg:overflow-hidden lg:px-8">
    <section class="flex min-h-0 min-w-0 flex-1 flex-col">
      <header>
        <p class="text-[11px] tracking-[0.18em] text-muted">任务一 · 提取结果</p>
        <div class="mt-2 flex flex-wrap items-end justify-between gap-3">
          <h1 class="font-serif text-[40px] leading-none font-semibold">标的</h1>
          <RouterLink to="/relations" class="text-[13px] underline decoration-accent decoration-2 underline-offset-[5px]">
            用这批结果看关系
          </RouterLink>
        </div>
        <div class="mt-5 flex flex-wrap items-center gap-2">
          <input
            v-model="q"
            placeholder="搜名称、品牌、规格、项目"
            aria-label="搜索标的"
            class="h-10 w-72 border border-line bg-white px-3 text-[13px] outline-none placeholder:text-muted focus:border-ink"
          />
          <button
            v-for="c in cats"
            :key="c"
            class="h-8 px-3 text-[12px] transition-colors duration-200"
            :class="cat === c ? 'bg-ink text-white' : 'border border-line bg-white text-muted hover:border-ink hover:text-ink'"
            @click="cat = c"
          >
            {{ c }}
          </button>
          <span class="ml-auto text-[12px] text-muted">{{ rows.length }} 条</span>
        </div>
      </header>

      <div class="mt-4 min-h-0 flex-1 overflow-auto border-t border-ink">
        <button
          v-for="(row, i) in rows"
          :key="row.id"
          class="flex w-full items-center gap-4 border-b border-line px-2 py-3 text-left transition-colors duration-200"
          :class="openId === row.id ? 'bg-ink text-white' : 'hover:bg-[#f4f4f5]'"
          @click="openId = row.id"
        >
          <span class="w-8 font-serif text-[13px]" :class="openId === row.id ? 'text-white/70' : 'text-muted'">0{{ i + 1 }}</span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-[15px]">{{ row.name }}</span>
            <span class="block truncate text-[12px]" :class="openId === row.id ? 'text-white/70' : 'text-muted'">{{ row.project }} · {{ row.brand }}</span>
          </span>
          <span class="hidden text-[12px] md:block" :class="openId === row.id ? 'text-white/80' : 'text-muted'">{{ row.cat }}</span>
          <span class="w-36 text-right font-serif text-[16px]">{{ yuan(row.total) }}</span>
        </button>
        <p v-if="rows.length === 0" class="py-16 text-center text-muted">没有符合的标的</p>
      </div>
    </section>

    <aside v-if="current" class="w-full shrink-0 border border-ink bg-white lg:w-[340px] lg:self-start">
      <div class="h-[3px] bg-accent" />
      <div class="px-5 py-5">
        <div class="text-[11px] tracking-[0.16em] text-muted">摘录</div>
        <h2 class="mt-2 font-serif text-[26px] leading-snug">{{ current.name }}</h2>
        <p class="mt-1 text-[13px] text-muted">{{ current.project }}</p>
        <dl class="mt-5 space-y-3 text-[13px]">
          <div
            v-for="[k, v] in [
              ['品目', current.cat],
              ['品牌', current.brand],
              ['规格', current.spec],
              ['单价', yuan(current.price)],
              ['数量', String(current.qty)],
              ['总价', yuan(current.total)],
              ['中标', current.winner],
            ]"
            :key="k"
            class="flex justify-between gap-4 border-b border-line pb-2"
          >
            <dt class="text-muted">{{ k }}</dt>
            <dd class="text-right">{{ v }}</dd>
          </div>
        </dl>
      </div>
    </aside>
  </div>
</template>
