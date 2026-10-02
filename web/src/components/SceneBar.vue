<script setup lang="ts">
/**
 * SceneBar —— 顶栏。
 *
 * 场景切换芯片已按《星图交互设计-v3》移除：提问发生在"选中对象之后"，不在顶栏。
 * 顶栏只剩一件事——按全名把选中和镜头交给某个节点。
 * 这样"当前问题"在界面上只有一个来源（右侧面板里的场景按钮），不再有两处真相。
 */
import PartyPicker from '@/components/PartyPicker.vue'
import { ref } from 'vue'

withDefaults(
  defineProps<{ loading?: boolean }>(),
  { loading: false },
)

const emit = defineEmits<{
  /** 按全名定位一个节点：只选中，不提问（§18） */
  (e: 'pick', name: string): void
  (e: 'reset'): void
  (e: 'reframe'): void
}>()

/** 输入框里的半成品字符串：不是共享状态，选完即清 */
const text = ref('')

function onPick(hit: { name: string }) {
  emit('pick', hit.name)
  text.value = ''
}
</script>

<template>
  <div class="scenebar">
    <p class="desc">
      点一颗星，看它能问什么
      <span class="hint">搜索框按全名定位 · 单击选中 · Esc 退出</span>
    </p>
    <div class="row">
      <PartyPicker v-model="text" kind="any" placeholder="输入单位或供应商全名" @select="onPick" />
    </div>
    <button class="btn-ghost" @click="emit('reframe')">重新取景</button>
    <button class="btn-ghost" @click="emit('reset')">回到全景</button>
  </div>
</template>

<style scoped>
.scenebar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  border-bottom: 1px solid var(--line);
  padding: 10px 20px;
}

.desc {
  margin-right: auto;
  color: var(--muted);
  font-size: 13px;
}

.hint {
  margin-left: 10px;
  color: var(--faint);
  font-size: 12px;
}

.row {
  width: 280px;
}
</style>
