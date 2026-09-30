<script setup lang="ts">
defineProps<{ k: string; v: string }>()
</script>

<template>
  <div class="metric">
    <div class="v h-serif">{{ v }}</div>
    <div class="k">{{ k }}</div>
  </div>
</template>

<style scoped>
.metric {
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface);
  padding: 10px 12px 9px;
}

.v {
  font-size: 20px;
  line-height: 1.2;
}

.k {
  margin-top: 3px;
  color: var(--muted);
  font-size: 11px;
}
</style>
