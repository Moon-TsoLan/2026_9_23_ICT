<script setup lang="ts">
import { Orbit, Table2 } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import MetricCard from '@/components/MetricCard.vue'
import PartyPicker from '@/components/PartyPicker.vue'
import type { PartyProfile } from '@/types/explore'
import { DISPLAY_KIND, DISPLAY_KIND_LABEL } from '@/types/graph'

const route = useRoute()
const router = useRouter()

const profile = ref<PartyProfile | null>(null)
const state = ref<'loading' | 'success' | 'error'>('loading')
const errorMsg = ref('')
const pickerText = ref('')

const partyId = computed(() => {
  const raw = route.params.id
  return typeof raw === 'string' ? raw : ''
})

watch(
  partyId,
  async (id) => {
    if (!id) return
    state.value = 'loading'
    profile.value = null
    try {
      profile.value = await api.partyProfile(id)
      pickerText.value = profile.value.name
      state.value = 'success'
    } catch (e) {
      errorMsg.value = e instanceof Error ? e.message : '加载失败'
      state.value = 'error'
    }
  },
  { immediate: true },
)

function goExplore() {
  void router.push({ path: '/explore', query: { focus: partyId.value } })
}

function goSearch() {
  const name = profile.value?.name
  if (!name) return
  const query =
    profile.value?.kind === 'buyer' ? { purchaser: name } : { winner: name }
  void router.push({ path: '/search', query })
}
</script>

<template>
  <div class="party">
    <header class="head">
      <div class="picker-wrap">
        <PartyPicker
          v-model="pickerText"
          placeholder="切换主体：输入采购单位 / 供应商名称"
          @select="(hit) => router.push(`/party/${encodeURIComponent(hit.id)}`)"
        />
      </div>
    </header>

    <div v-if="state === 'loading'" class="body">
      <div class="skeleton h-9 w-1/2" />
      <div class="mt-8 grid max-w-3xl grid-cols-4 gap-2">
        <div v-for="i in 4" :key="i" class="skeleton h-20" />
      </div>
    </div>

    <EmptyState v-else-if="state === 'error'" :title="errorMsg" hint="换 ⌘K 或上方搜索框找其他主体" />

    <div v-else-if="profile" class="body">
      <p class="kicker">主体档案 · {{ DISPLAY_KIND_LABEL[DISPLAY_KIND[profile.kind]] }}</p>
      <h1 class="h-serif">{{ profile.name }}</h1>

      <div class="stats">
        <MetricCard v-for="s in profile.stats" :key="s.k" :k="s.k" :v="s.v" />
      </div>

      <section v-if="profile.facts.length">
        <h2>关键事实</h2>
        <ul>
          <li v-for="f in profile.facts" :key="f">{{ f }}</li>
        </ul>
      </section>

      <div class="actions">
        <button class="btn" @click="goExplore">
          <Orbit :size="14" />
          在星图中查看
        </button>
        <button class="btn-ghost" @click="goSearch">
          <Table2 :size="14" />
          查看相关标的
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.party {
  height: 100%;
  overflow: auto;
  padding: 20px 24px 32px;
}

.picker-wrap {
  width: min(420px, 100%);
}

.body {
  margin-top: 24px;
  max-width: 860px;
}

.body h1 {
  margin-top: 8px;
  font-size: 34px;
  line-height: 1.3;
}

.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-top: 24px;
  max-width: 640px;
}

section {
  margin-top: 28px;
}

h2 {
  border-bottom: 1px solid var(--line);
  padding-bottom: 8px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.14em;
}

ul {
  margin-top: 10px;
}

li {
  border-left: 2px solid var(--accent);
  margin-top: 8px;
  padding-left: 12px;
  font-size: 14px;
  line-height: 1.7;
}

.actions {
  display: flex;
  gap: 10px;
  margin-top: 32px;
}
</style>
