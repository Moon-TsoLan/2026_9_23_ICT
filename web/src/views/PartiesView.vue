<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

interface Party {
  id: string
  name: string
  role: string
  group: string
  region: string
  stats: { k: string; v: string }[]
  lines: string[]
}

const parties: Party[] = [
  { id: 'zengcheng', name: '广州市增城区中心医院', role: '采购单位', group: '采购单位', region: '广东', stats: [{ k: '项目', v: '6' }, { k: '成交方', v: '3' }, { k: '金额', v: '827万' }], lines: ['样例里项目最多的采购单位', '成交供应商是联通数智、东软、卫宁'] },
  { id: 'zhejiang', name: '浙江省人民医院', role: '采购单位', group: '采购单位', region: '浙江', stats: [{ k: '项目', v: '3' }, { k: '成交方', v: '2' }, { k: '金额', v: '746万' }], lines: ['临床集成与影像存储', '东软、联通数智都有成交'] },
  { id: 'nanshan', name: '深圳市南山区政务服务数据管理局', role: '采购单位', group: '采购单位', region: '广东', stats: [{ k: '项目', v: '3' }, { k: '成交方', v: '2' }, { k: '金额', v: '1,745万' }], lines: ['数据中台由中国软件中标', '医共体专网由联通数智中标'] },
  { id: 'chengdu', name: '成都高新区教育局', role: '采购单位', group: '采购单位', region: '四川', stats: [{ k: '项目', v: '2' }, { k: '成交方', v: '2' }, { k: '金额', v: '334万' }], lines: ['教育云桌面，中科曙光中标'] },
  { id: 'liantong', name: '联通数智医疗科技有限公司', role: '中标供应商', group: '中标供应商', region: '广东', stats: [{ k: '中标', v: '5' }, { k: '同场', v: '6' }, { k: '客户', v: '3' }], lines: ['医院信息化里出现最频繁', '与东软在两家医院都有成交', '与中国软件在 3 个项目同场'] },
  { id: 'neusoft', name: '东软集团股份有限公司', role: '中标供应商', group: '中标供应商', region: '辽宁', stats: [{ k: '中标', v: '4' }, { k: '同场', v: '5' }, { k: '客户', v: '2' }], lines: ['机房改造、临床数据中心', '和联通数智互有胜负'] },
  { id: 'winning', name: '卫宁健康科技集团股份有限公司', role: '中标供应商', group: '中标供应商', region: '上海', stats: [{ k: '中标', v: '1' }, { k: '供货', v: '1' }, { k: '同场', v: '3' }], lines: ['门诊电子病历中标', '同时是该标的的产品供应商'] },
  { id: 'css', name: '中国软件与技术服务股份有限公司', role: '中标供应商', group: '中标供应商', region: '北京', stats: [{ k: '中标', v: '3' }, { k: '同场', v: '4' }, { k: '客户', v: '2' }], lines: ['政务数据中台中标', '与联通数智有 3 个同场项目'] },
  { id: 'sugon', name: '曙光信息产业股份有限公司', role: '中标供应商', group: '中标供应商', region: '天津', stats: [{ k: '中标', v: '1' }, { k: '终端', v: '400' }, { k: '金额', v: '256万' }], lines: ['高新区教育云桌面中标'] },
  { id: 'telecom', name: '中国电信股份有限公司广东分公司', role: '投标参与方', group: '投标参与方', region: '广东', stats: [{ k: '参与', v: '4' }, { k: '中标', v: '0' }, { k: '搭档', v: '云奕' }], lines: ['增城医院项目多次出现', '常与广州云奕同场，样例中未中标'] },
  { id: 'yunyi', name: '广州云奕技术有限公司', role: '投标参与方', group: '投标参与方', region: '广东', stats: [{ k: '参与', v: '4' }, { k: '中标', v: '0' }, { k: '搭档', v: '电信' }], lines: ['与广东电信多次一起投标'] },
  { id: 'chuangye', name: '创业慧康科技股份有限公司', role: '投标参与方', group: '投标参与方', region: '浙江', stats: [{ k: '参与', v: '2' }, { k: '中标', v: '0' }, { k: '领域', v: '医疗' }], lines: ['出现在医院类项目的投标名单'] },
  { id: 'huawei', name: '华为技术有限公司', role: '产品供应商', group: '产品供应商', region: '广东', stats: [{ k: '标的', v: '2' }, { k: '角色', v: '品牌' }, { k: '投标', v: '否' }], lines: ['集成平台、操作系统等品牌', '样例里不作为投标供应商'] },
  { id: 'h3c', name: '新华三技术有限公司', role: '产品供应商', group: '产品供应商', region: '浙江', stats: [{ k: '标的', v: '1' }, { k: '品类', v: '网络' }, { k: '投标', v: '否' }], lines: ['医疗专网交换机'] },
  { id: 'vertiv', name: '维谛技术有限公司', role: '产品供应商', group: '产品供应商', region: '上海', stats: [{ k: '标的', v: '1' }, { k: '品类', v: '机房' }, { k: '投标', v: '否' }], lines: ['机房精密空调'] },
]

const groups = ['采购单位', '中标供应商', '投标参与方', '产品供应商']
const route = useRoute()
const router = useRouter()
const selectedId = computed({
  get: () => (typeof route.query.id === 'string' ? route.query.id : 'liantong'),
  set: (id: string) => void router.replace({ path: '/parties', query: { id } }),
})
const current = computed(() => parties.find((p) => p.id === selectedId.value) ?? parties[4]!)

watch(
  () => route.query.id,
  (id) => {
    if (typeof id !== 'string') void router.replace({ path: '/parties', query: { id: 'liantong' } })
  },
  { immediate: true },
)
</script>

<template>
  <div class="flex h-full flex-col lg:flex-row">
    <aside class="max-h-[40vh] w-full shrink-0 overflow-auto border-b border-line bg-white lg:max-h-none lg:w-[340px] lg:border-r lg:border-b-0">
      <div v-for="group in groups" :key="group" class="border-b border-line">
        <div class="px-5 py-3 text-[11px] tracking-[0.16em] text-muted">{{ group }}</div>
        <button
          v-for="p in parties.filter((item) => item.group === group)"
          :key="p.id"
          class="flex w-full items-center justify-between px-5 py-2.5 text-left text-[13px] transition-colors duration-200"
          :class="current.id === p.id ? 'bg-ink text-white' : 'hover:bg-[#f4f4f5]'"
          @click="selectedId = p.id"
        >
          <span class="truncate">{{ p.name }}</span>
          <span class="ml-3 shrink-0 text-[11px]" :class="current.id === p.id ? 'text-white/70' : 'text-muted'">{{ p.region }}</span>
        </button>
      </div>
    </aside>

    <section class="min-w-0 flex-1 overflow-auto px-6 py-7 lg:px-10">
      <p class="text-[11px] tracking-[0.18em] text-muted">任务一 · {{ current.role }} · {{ current.region }}</p>
      <h1 class="mt-3 max-w-4xl font-serif text-[36px] leading-tight font-semibold lg:text-[44px]">{{ current.name }}</h1>
      <div class="mt-8 grid max-w-3xl grid-cols-3 gap-px bg-line">
        <div v-for="s in current.stats" :key="s.k" class="bg-white px-4 py-4">
          <div class="font-serif text-[32px] leading-none">{{ s.v }}</div>
          <div class="mt-2 text-[12px] text-muted">{{ s.k }}</div>
        </div>
      </div>
      <ul class="mt-8 max-w-2xl border-t border-ink">
        <li v-for="line in current.lines" :key="line" class="border-b border-line py-3 text-[15px]">{{ line }}</li>
      </ul>
      <RouterLink
        to="/relations"
        class="mt-8 inline-flex h-11 items-center bg-ink px-5 text-[13px] text-white transition-colors duration-200 hover:bg-[#3f3f46]"
      >
        在关系图里打开
      </RouterLink>
    </section>
  </div>
</template>
