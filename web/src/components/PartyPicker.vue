<script setup lang="ts">
import { X } from 'lucide-vue-next'
import { ref, watch } from 'vue'
import { api } from '@/api/client'
import type { PartyHit } from '@/types/explore'
import { NODE_KIND_LABEL } from '@/types/graph'

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholder?: string
    /** 限定候选类型：buyer=采购单位；supplier=供应商（winner/bidder）；any=全部 */
    kind?: 'buyer' | 'supplier' | 'any'
  }>(),
  { placeholder: '输入名称检索', kind: 'any' },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'select', hit: PartyHit): void
}>()

const text = ref(props.modelValue)
const hits = ref<PartyHit[]>([])
const openList = ref(false)
const loading = ref(false)
let timer = 0
let suppressSearch = false

watch(
  () => props.modelValue,
  (v) => {
    if (v !== text.value) {
      suppressSearch = true // 外部赋值不触发搜索/弹层
      text.value = v
    }
  },
)

watch(text, (kw) => {
  window.clearTimeout(timer)
  if (suppressSearch) {
    suppressSearch = false
    return
  }
  const s = kw.trim()
  emit('update:modelValue', kw)
  if (!s) {
    hits.value = []
    openList.value = false
    return
  }
  timer = window.setTimeout(async () => {
    loading.value = true
    try {
      const res = await api.parties(s)
      hits.value = res.items.filter((h) =>
        props.kind === 'any' ? true : props.kind === 'buyer' ? h.kind === 'buyer' : h.kind !== 'buyer',
      )
      openList.value = true
    } catch {
      hits.value = []
    } finally {
      loading.value = false
    }
  }, 250)
})

function pick(hit: PartyHit) {
  text.value = hit.name
  emit('update:modelValue', hit.name)
  emit('select', hit)
  openList.value = false
}

function clear() {
  text.value = ''
  emit('update:modelValue', '')
  hits.value = []
  openList.value = false
}

function onBlur() {
  // 延迟收起，让点击候选先生效
  window.setTimeout(() => (openList.value = false), 160)
}
</script>

<template>
  <div class="picker">
    <input
      v-model="text"
      class="input w-full"
      :placeholder="placeholder"
      @focus="hits.length && (openList = true)"
      @blur="onBlur"
      @keydown.escape="openList = false"
    />
    <button v-if="text" class="clear" aria-label="清除" @mousedown.prevent="clear">
      <X :size="13" />
    </button>
    <div v-if="openList && hits.length" class="drop">
      <button v-for="hit in hits" :key="hit.id" @mousedown.prevent="pick(hit)">
        <span class="kind">{{ NODE_KIND_LABEL[hit.kind] }}</span>
        <span class="truncate">{{ hit.name }}</span>
      </button>
    </div>
    <div v-else-if="openList && !loading && text.trim()" class="drop">
      <p class="px-3 py-3 text-[12px] text-muted">没有匹配的主体</p>
    </div>
  </div>
</template>

<style scoped>
.picker {
  position: relative;
}

.clear {
  position: absolute;
  top: 50%;
  right: 8px;
  transform: translateY(-50%);
  color: var(--faint);
}

.clear:hover {
  color: var(--ink);
}

.drop {
  position: absolute;
  z-index: 30;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  overflow: auto;
  max-height: 260px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.16);
}

.drop button {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  text-align: left;
  font-size: 13px;
  transition: background var(--dur-fast) var(--ease);
}

.drop button:hover {
  background: color-mix(in srgb, var(--ink) 5%, transparent);
}

.kind {
  flex-shrink: 0;
  color: var(--faint);
  font-size: 11px;
}
</style>
