<script setup lang="ts">
import { LoaderCircle, Play, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import PartyPicker from '@/components/PartyPicker.vue'
import type { SceneId } from '@/types/explore'

interface SceneMeta {
  name: string
  desc: string
  subjectKind: 'buyer' | 'supplier'
  multi: boolean
  hint: string
}

const SCENES: Record<SceneId, SceneMeta> = {
  S1: { name: '长期合作', desc: '采购单位 → 合作的中标/产品供应商', subjectKind: 'buyer', multi: false, hint: '选 1 个采购单位' },
  S2: { name: '高频投标', desc: '采购单位 → 高频投标主体与协同组合', subjectKind: 'buyer', multi: false, hint: '选 1 个采购单位' },
  S3: { name: '共同竞标', desc: '中标供应商 → 高频共同竞标主体', subjectKind: 'supplier', multi: false, hint: '选 1 个中标供应商' },
  S4: { name: '交集采购', desc: '多中标商 → 共同合作的采购单位', subjectKind: 'supplier', multi: true, hint: '选 2 个以上中标供应商' },
  S5: { name: '交集项目', desc: '多中标商 → 共同竞标的项目', subjectKind: 'supplier', multi: true, hint: '选 2 个以上中标供应商' },
}

const props = defineProps<{ loading?: boolean }>()

const emit = defineEmits<{
  (e: 'query', scene: SceneId, subjects: string[]): void
  (e: 'reset'): void
  (e: 'reframe'): void
  (e: 'scene-change', scene: SceneId): void
}>()

const scene = ref<SceneId>('S1')
const single = ref('')
const multiList = ref<string[]>([])
const multiInput = ref('')

const meta = computed(() => SCENES[scene.value])
const sceneIds = Object.keys(SCENES) as SceneId[]

/** 接力只带过来一家时，明确说还差几家，而不是点了没反应 */
const notice = ref('')

const canQuery = computed(() =>
  meta.value.multi ? multiList.value.length >= 2 : single.value.trim().length > 0,
)

function pickScene(id: SceneId) {
  scene.value = id
  
  // 切场景不是"换页"，而是对当前对象换个问法：交给上层决定要不要立刻查
  emit('scene-change', id)
}

function addMulti(name: string) {
  const s = name.trim()
  if (s && !multiList.value.includes(s)) multiList.value.push(s)
  multiInput.value = ''
}

function removeMulti(name: string) {
  multiList.value = multiList.value.filter((x) => x !== name)
}

function run() {
  if (!canQuery.value || props.loading) return
  emit('query', scene.value, meta.value.multi ? [...multiList.value] : [single.value.trim()])
}

/** 供星图"接力查询"调用：切场景 + 填主体 + 立刻查 */
function apply(next: SceneId, names: string[]) {
  scene.value = next
  notice.value = ''
  if (SCENES[next].multi) {
    multiList.value = [...new Set(names)]
    multiInput.value = ''
    if (multiList.value.length < 2) {
      const have = multiList.value[0] ?? ''
      notice.value = have + ' 已填入，' + next + ' 需要至少 2 家中标供应商，再加一家后点查询'
      return
    }
  } else {
    single.value = names[0] ?? ''
  }
  if (canQuery.value) emit('query', next, SCENES[next].multi ? [...multiList.value] : [single.value])
}

/** 对比篮：星图 Shift+点往这里加主体 */
function addSubject(name: string): boolean {
  if (multiList.value.includes(name)) return false
  multiList.value.push(name)
  notice.value = ''
  return true
}

function subjects(): string[] {
  return [...multiList.value]
}

defineExpose({ apply, addSubject, subjects })
</script>

<template>
  <div class="scenebar">
    <div class="chips" role="tablist" aria-label="五大业务场景">
      <button
        v-for="id in sceneIds"
        :key="id"
        class="scene-chip"
        :class="{ on: scene === id }"
        role="tab"
        :aria-selected="scene === id"
        :title="SCENES[id].desc"
        @click="pickScene(id)"
      >
        <em>{{ id }}</em>
        {{ SCENES[id].name }}
      </button>
    </div>
    <div class="controls">
      <p class="desc">
        {{ meta.desc }}<span class="hint">{{ meta.hint }}</span>
        <span v-if="notice" class="warn">{{ notice }}</span>
      </p>
      <div v-if="!meta.multi" class="row">
        <PartyPicker
          v-model="single"
          :kind="meta.subjectKind === 'supplier' ? 'winner' : 'buyer'"
          :placeholder="meta.subjectKind === 'buyer' ? '输入采购单位名称' : '输入中标供应商名称'"
          @select="run"
          @update:model-value="() => {}"
        />
      </div>
      <div v-else class="row multi">
        <span v-for="name in multiList" :key="name" class="chip">
          {{ name }}
          <button aria-label="移除" @click="removeMulti(name)"><X :size="12" /></button>
        </span>
        <PartyPicker
          v-model="multiInput"
          kind="winner"
          :placeholder="multiList.length ? '继续添加中标供应商' : '输入中标供应商名称'"
          @select="(hit) => addMulti(hit.name)"
        />
      </div>
      <button class="btn run" :disabled="!canQuery || loading" @click="run">
        <LoaderCircle v-if="loading" :size="14" class="spin" />
        <Play v-else :size="14" />
        查询
      </button>
      <button class="btn-ghost" :disabled="!loading && false" @click="emit('reframe')">重新取景</button>
      <button class="btn-ghost" @click="emit('reset')">回到全景</button>
    </div>
  </div>
</template>

<style scoped>
.scenebar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 20px;
  border-bottom: 1px solid var(--line);
  padding: 10px 20px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.scene-chip {
  display: flex;
  align-items: baseline;
  gap: 6px;
  border-bottom: 1px solid transparent;
  padding: 6px 0;
  color: var(--muted);
  font-size: 13px;
  transition:
    color var(--dur-base) var(--ease),
    border-color var(--dur-base) var(--ease);
}

.scene-chip em {
  color: var(--faint);
  font-size: 10px;
  font-style: normal;
  letter-spacing: 0.08em;
}

.scene-chip:hover {
  color: var(--ink);
}

.scene-chip.on {
  border-bottom-color: var(--accent);
  color: var(--ink);
}

.scene-chip.on em {
  color: var(--accent);
}

.controls {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 320px;
}

.desc {
  margin-right: auto;
  color: var(--muted);
  font-size: 12px;
}

.hint {
  margin-left: 8px;
  color: var(--faint);
}

.warn {
  margin-left: 10px;
  color: var(--warm, #ffce94);
  font-size: 11px;
}

.row {
  width: 260px;
}

.multi {
  display: flex;
  width: auto;
  max-width: 460px;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.multi .chip {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border-color: color-mix(in srgb, var(--accent) 30%, transparent);
}

.multi .chip button {
  display: inline-flex;
  color: var(--muted);
}

.multi :deep(.picker) {
  width: 200px;
}

.run {
  height: 34px;
}

.spin {
  animation: rot 0.9s linear infinite;
}

@keyframes rot {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
</style>
