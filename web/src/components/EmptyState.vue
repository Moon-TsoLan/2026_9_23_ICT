<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

withDefaults(defineProps<{ title: string; hint?: string }>(), { hint: '' })
</script>

<template>
  <div class="empty">
    <Inbox :size="22" class="icon" />
    <p class="title">{{ title }}</p>
    <p v-if="hint" class="hint">{{ hint }}</p>
    <slot />
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 48px 16px;
  text-align: center;
}

.icon {
  color: var(--faint);
}

.title {
  font-size: 13px;
  color: var(--muted);
}

.hint {
  font-size: 12px;
  color: var(--faint);
}
</style>
