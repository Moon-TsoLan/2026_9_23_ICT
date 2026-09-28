<script setup lang="ts">
import { computed } from 'vue'
import type { DistributionRow } from '@/types/explore'
import { fmtYuanShort } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    rows: DistributionRow[]
    metric?: 'count' | 'amount'
    limit?: number
  }>(),
  { metric: 'count', limit: 10 },
)

const shown = computed(() => props.rows.slice(0, props.limit))
const maxVal = computed(() =>
  Math.max(1, ...shown.value.map((r) => (props.metric === 'count' ? r.count : (r.amount ?? 0)))),
)

function val(r: DistributionRow): number {
  return props.metric === 'count' ? r.count : (r.amount ?? 0)
}

function label(r: DistributionRow): string {
  return props.metric === 'count' ? String(r.count) : fmtYuanShort(r.amount)
}
</script>

<template>
  <div class="bars">
    <div v-for="r in shown" :key="r.key" class="line">
      <span class="key" :title="r.key">{{ r.key }}</span>
      <span class="track">
        <i :style="{ width: `${(val(r) / maxVal) * 100}%` }" />
      </span>
      <span class="val num">{{ label(r) }}</span>
    </div>
  </div>
</template>

<style scoped>
.bars {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.key {
  width: 128px;
  flex-shrink: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track {
  height: 6px;
  min-width: 0;
  flex: 1;
  border-radius: 3px;
  background: color-mix(in srgb, var(--ink) 8%, transparent);
}

.track i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
  opacity: 0.85;
}

.val {
  width: 64px;
  flex-shrink: 0;
  text-align: right;
  font-size: 12px;
}
</style>
