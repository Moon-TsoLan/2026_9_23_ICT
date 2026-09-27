<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const open = ref(false)
const q = ref('')

const nav = [
  { to: '/process', label: '处理', no: '01' },
  { to: '/objects', label: '标的', no: '02' },
  { to: '/parties', label: '主体', no: '03' },
  { to: '/relations', label: '关系', no: '04' },
]

const jumps = [
  { label: '数据处理', hint: '页面', to: '/process' },
  { label: '标的检索', hint: '页面', to: '/objects' },
  { label: '主体档案', hint: '页面', to: '/parties' },
  { label: '关系场景', hint: '页面', to: '/relations' },
  { label: '联通数智医疗科技有限公司', hint: '主体', to: '/parties?id=liantong' },
  { label: '广州市增城区中心医院', hint: '主体', to: '/parties?id=zengcheng' },
  { label: '中国软件与技术服务股份有限公司', hint: '主体', to: '/parties?id=css' },
  { label: '华为技术有限公司', hint: '主体', to: '/parties?id=huawei' },
]

const hits = computed(() => {
  const s = q.value.trim()
  return jumps.filter((item) => !s || item.label.includes(s))
})

function go(to: string) {
  open.value = false
  q.value = ''
  void router.push(to)
}

function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    open.value = !open.value
    q.value = ''
  } else if (e.key === 'Escape') open.value = false
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
          <rect x="1.25" y="1.25" width="12" height="12" fill="none" stroke="#18181b" stroke-width="1.4" />
          <circle cx="14.5" cy="14.5" r="6" fill="#18181b" />
        </svg>
        <div>
          标络
          <small>任务一提取 · 任务二关系</small>
        </div>
      </div>
      <nav class="nav" aria-label="任务顺序">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to" :class="{ on: route.path === item.to }">
          <em>{{ item.no }}</em>
          {{ item.label }}
        </RouterLink>
      </nav>
      <button class="search-btn" @click="open = true">
        <Search :size="14" />
        <span class="hidden md:inline">搜索页面或主体</span>
        <kbd class="hidden sm:inline">Ctrl K</kbd>
      </button>
    </header>
    <main class="viewport">
      <RouterView v-slot="{ Component }">
        <Transition name="sheet">
          <component :is="Component" :key="route.path" class="page-root" />
        </Transition>
      </RouterView>
    </main>
  </div>

  <div v-if="open" class="palette" @click.self="open = false">
    <div class="palette-card">
      <input
        v-model="q"
        autofocus
        placeholder="搜索页面或主体"
        aria-label="搜索页面或主体"
        @keydown.enter="hits[0] && go(hits[0].to)"
      />
      <button v-for="item in hits" :key="item.to" @click="go(item.to)">
        <span class="w-10 shrink-0 text-[11px] text-muted">{{ item.hint }}</span>
        {{ item.label }}
      </button>
      <p v-if="hits.length === 0" class="px-4 py-6 text-sm text-muted">没有匹配</p>
    </div>
  </div>
</template>
