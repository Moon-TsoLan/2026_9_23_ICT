<script setup lang="ts">
/**
 * 「资料库已更新」提示条（检索页 / 探索页共用）。
 *
 * mode=append：有新数据，点「刷新」按不打断用户的方式拉一次；
 * mode=rebuild：新增超出底图尺度，点「重建」才会重排（会跳画面，所以要用户明确点）。
 * 组件只负责显示与发事件，怎么刷新由页面决定。
 */
defineProps<{
  mode: 'append' | 'rebuild'
  newCount: number
}>()

const emit = defineEmits<{ run: []; dismiss: [] }>()
</script>

<template>
  <div class="notice" :class="{ heavy: mode === 'rebuild' }">
    <span class="dot" />
    <p v-if="mode === 'append'">
      资料库已更新<template v-if="newCount > 0">（新增 {{ newCount }} 则）</template>
    </p>
    <p v-else>更新较多，建议重建底图</p>
    <button class="btn-ghost" @click="emit('run')">
      {{ mode === 'append' ? '刷新' : '重建' }}
    </button>
    <button class="x" aria-label="关闭提示" @click="emit('dismiss')">✕</button>
  </div>
</template>

<style scoped>
.notice {
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--line);
  background: color-mix(in srgb, var(--info) 7%, transparent);
  padding: 7px 16px;
  font-size: 13px;
  color: var(--ink);
}

.notice.heavy {
  background: color-mix(in srgb, var(--warn) 10%, transparent);
}

.dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 99px;
  background: var(--info);
}

.heavy .dot {
  background: var(--warn);
}

.notice p {
  flex: 1;
}

.x {
  color: var(--faint);
  font-size: 12px;
}

.x:hover {
  color: var(--ink);
}
</style>
