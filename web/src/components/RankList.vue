<script setup lang="ts">
import { computed } from 'vue'
import type { SceneRankRow } from '@/types/explore'
import { fmtMetric } from '@/utils/format'

const props = defineProps<{
  rows: SceneRankRow[]
  /** 条形按哪个指标画；默认第一个数值型指标 */
  metricKey?: string
}>()

const emit = defineEmits<{ (e: 'select', id: string): void }>()

const barKey = computed(() => {
  if (props.metricKey) return props.metricKey
  const first = props.rows[0]
  if (!first) return ''
  return Object.keys(first.metrics).find((k) => typeof first.metrics[k] === 'number') ?? ''
})

const maxVal = computed(() =>
  Math.max(1, ...props.rows.map((r) => Number(r.metrics[barKey.value]) || 0)),
)

const otherKeys = computed(() => {
  const first = props.rows[0]
  return first ? Object.keys(first.metrics).filter((k) => k !== barKey.value) : []
})
</script>

<template>
  <ol class="rank">
    <li v-for="row in rows" :key="row.id">
      <button class="row" @click="emit('select', row.id)">
        <span class="no h-serif">{{ row.rank }}</span>
        <span class="body">
          <span class="name">{{ row.name }}</span>
          <span class="bar">
            <i :style="{ width: `${(Number(row.metrics[barKey]) / maxVal) * 100}%` }" />
          </span>
        </span>
        <span class="vals">
          <span class="main num">{{ fmtMetric(barKey, row.metrics[barKey] ?? 0) }}</span>
          <span v-for="k in otherKeys" :key="k" class="sub num">{{ k }} {{ fmtMetric(k, row.metrics[k] ?? 0) }}</span>
        </span>
      </button>
    </li>
  </ol>
</template>

<style scoped>
.rank {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.row {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  border-radius: var(--r-sm);
  padding: 7px 8px;
  text-align: left;
  transition: background var(--dur-fast) var(--ease);
}

.row:hover {
  background: color-mix(in srgb, var(--ink) 6%, transparent);
}

.no {
  width: 18px;
  flex-shrink: 0;
  color: var(--accent);
  font-size: 13px;
}

.body {
  min-width: 0;
  flex: 1;
}

.name {
  display: block;
  overflow: hidden;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar {
  display: block;
  height: 3px;
  margin-top: 5px;
  border-radius: 2px;
  background: color-mix(in srgb, var(--ink) 10%, transparent);
}

.bar i {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: var(--accent);
  transition: width var(--dur-slow) var(--ease);
}

.vals {
  flex-shrink: 0;
  text-align: right;
}

.main {
  display: block;
  font-size: 13px;
}

.sub {
  display: block;
  margin-top: 1px;
  color: var(--muted);
  font-size: 11px;
}
</style>
