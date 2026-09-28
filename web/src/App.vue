<script setup lang="ts">
import { Search } from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import type { PartyHit } from '@/types/explore'
import { NODE_KIND_LABEL } from '@/types/graph'

const route = useRoute()
const router = useRouter()

const nav = [
  { to: '/ingest', label: '采集', no: '01' },
  { to: '/search', label: '检索', no: '02' },
  { to: '/explore', label: '探索', no: '03' },
]

/** 双域主题：/explore 宇宙域（深），其余记录域（浅） */
const domain = computed(() => (route.path.startsWith('/explore') ? 'void' : 'paper'))

// ---------- 命令面板 ----------
const open = ref(false)
const q = ref('')
const parties = ref<PartyHit[]>([])
const searching = ref(false)
let debounceTimer = 0

const pages = [
  { label: '数据接入 · 采集', to: '/ingest' },
  { label: '标的检索', to: '/search' },
  { label: '关系探索', to: '/explore' },
]

const pageHits = computed(() => {
  const s = q.value.trim()
  return pages.filter((p) => !s || p.label.includes(s))
})

watch(q, (kw) => {
  window.clearTimeout(debounceTimer)
  const s = kw.trim()
  if (!s) {
    parties.value = []
    return
  }
  debounceTimer = window.setTimeout(async () => {
    searching.value = true
    try {
      const res = await api.parties(s)
      parties.value = res.items
    } catch {
      parties.value = []
    } finally {
      searching.value = false
    }
  }, 250)
})

function go(to: string) {
  open.value = false
  q.value = ''
  void router.push(to)
}

function goParty(hit: PartyHit) {
  go(`/party/${encodeURIComponent(hit.id)}`)
}

function onKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    open.value = !open.value
    q.value = ''
    parties.value = []
  } else if (e.key === 'Escape') {
    open.value = false
  }
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="shell" :data-domain="domain">
    <header class="topbar">
      <RouterLink to="/ingest" class="brand">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
          <rect x="1.25" y="1.25" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.4" />
          <circle cx="14.5" cy="14.5" r="6" fill="currentColor" />
        </svg>
        <div>
          标络
          <small>采招实体关系分析平台</small>
        </div>
      </RouterLink>
      <nav class="nav" aria-label="主导航">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to" :class="{ on: route.path.startsWith(item.to) }">
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
        placeholder="搜索页面 / 采购单位 / 供应商"
        aria-label="搜索页面或主体"
      />
      <div class="max-h-[46vh] overflow-auto">
        <button v-for="item in pageHits" :key="item.to" @click="go(item.to)">
          <span class="w-10 shrink-0 text-[11px] text-faint">页面</span>
          {{ item.label }}
        </button>
        <button v-for="hit in parties" :key="hit.id" @click="goParty(hit)">
          <span class="w-10 shrink-0 text-[11px] text-faint">{{ NODE_KIND_LABEL[hit.kind] }}</span>
          {{ hit.name }}
        </button>
        <p v-if="q.trim() && !searching && parties.length === 0" class="px-4 py-5 text-[13px] text-muted">
          没有匹配的主体
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  flex-direction: column;
  background: var(--paper);
  color: var(--ink);
  transition: background var(--dur-slow) var(--ease);
}

.topbar {
  display: flex;
  height: 60px;
  flex-shrink: 0;
  align-items: center;
  gap: 28px;
  padding: 0 24px;
  border-bottom: 1px solid var(--line);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--ink);
  font-family: var(--font-serif);
  font-weight: 600;
  letter-spacing: -0.03em;
  text-decoration: none;
}

.brand small {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-family: var(--font-sans);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.14em;
}

.nav {
  display: flex;
  gap: 4px;
}

.nav a {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-right: 18px;
  border-bottom: 2px solid transparent;
  padding: 8px 0;
  color: var(--muted);
  font-size: 14px;
  text-decoration: none;
  transition:
    color var(--dur-base) var(--ease),
    border-color var(--dur-base) var(--ease);
}

.nav a em {
  color: var(--faint);
  font-size: 10px;
  font-style: normal;
  letter-spacing: 0.08em;
}

.nav a:hover {
  color: var(--ink);
}

.nav a.on {
  color: var(--ink);
  border-bottom-color: var(--ink);
}

.nav a.on em {
  color: var(--accent);
}

.search-btn {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: var(--r-sm);
  padding: 0 12px;
  color: var(--muted);
  font-size: 13px;
  transition:
    border-color var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}

.search-btn:hover {
  border-color: var(--ink);
  color: var(--ink);
}

.search-btn kbd {
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 1px 5px;
  font-size: 10px;
}

.viewport {
  position: relative;
  min-height: 0;
  flex: 1;
  overflow: hidden;
}

.page-root {
  height: 100%;
}

.sheet-enter-active {
  transition: opacity var(--dur-slow) var(--ease);
}

.sheet-enter-from {
  opacity: 0;
}

.palette {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(9, 9, 11, 0.32);
}

.palette-card {
  width: min(560px, calc(100% - 32px));
  margin: 14vh auto 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.28);
}

.palette-card input {
  width: 100%;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  padding: 14px 16px;
  font-size: 14px;
  outline: none;
}

.palette-card button {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  text-align: left;
  font-size: 13px;
  transition: background var(--dur-fast) var(--ease);
}

.palette-card button:hover {
  background: color-mix(in srgb, var(--ink) 5%, transparent);
}

@media (prefers-reduced-motion: reduce) {
  .sheet-enter-active,
  .shell {
    transition: none;
  }

  .sheet-enter-from {
    opacity: 1;
  }
}
</style>
