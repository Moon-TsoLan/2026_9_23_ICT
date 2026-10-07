<script setup lang="ts">
/**
 * 上传 → 配对确认 → 逐文件上传（B4）。
 *
 * 三步：confirm（看配对、勾"确认覆盖"）→ uploading（进度条）→ done（已提交）。
 * 配对规则**完全以后端 /api/ingest/precheck 的返回为准**，前端不自己算，避免两套规则分叉。
 * 上传是逐文件、2 路并发；同名已存在由服务端返回 skipped（等于断点续传）。
 */
import { computed, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { PrecheckResult } from '@/types/ingest'

const props = defineProps<{ modelValue: boolean; files: File[] }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; submitted: [] }>()

type Step = 'confirm' | 'uploading' | 'done'
type Entry = { file: File; state: 'pending' | 'done' | 'skipped' | 'failed'; message?: string }

const LANES = 2
const PREVIEW = 50

const step = ref<Step>('confirm')
const plan = ref<PrecheckResult | null>(null)
const planError = ref('')
const loadingPlan = ref(false)
const confirmed = ref(false)
const showZipOnly = ref(false)
const showInvalid = ref(false)
const showAlready = ref(false)
const entries = ref<Entry[]>([])
const currentName = ref('')
const jobId = ref('')
const errorMsg = ref('')

watch(
  () => [props.modelValue, props.files] as const,
  () => {
    if (props.modelValue && props.files.length) void loadPlan()
  },
)

async function loadPlan() {
  step.value = 'confirm'
  confirmed.value = false
  showZipOnly.value = false
  showInvalid.value = false
  showAlready.value = false
  plan.value = null
  planError.value = ''
  errorMsg.value = ''
  entries.value = []
  currentName.value = ''
  jobId.value = ''
  loadingPlan.value = true
  try {
    plan.value = await api.precheck(props.files.map((file) => file.name))
  } catch (e) {
    planError.value = e instanceof Error ? e.message : '配对失败'
  } finally {
    loadingPlan.value = false
  }
}

const items = computed(() => plan.value?.items ?? [])
const paired = computed(() => items.value.filter((item) => item.has_zip).length)
const htmlOnly = computed(() => items.value.filter((item) => !item.has_zip).length)
const already = computed(() => plan.value?.already_extracted ?? [])
const canSubmit = computed(
  () => items.value.length > 0 && (already.value.length === 0 || confirmed.value),
)

/** 只上传配对成功的文件：每则的 html，以及（若有）它的 zip。 */
function filesToSend(): File[] {
  const slots = new Map<string, { html?: File; zip?: File }>()
  for (const file of props.files) {
    const dot = file.name.lastIndexOf('.')
    const stem = dot > 0 ? file.name.slice(0, dot) : file.name
    const ext = dot > 0 ? file.name.slice(dot).toLowerCase() : ''
    if (ext !== '.html' && ext !== '.zip') continue
    const slot = slots.get(stem) ?? {}
    if (ext === '.html') slot.html = file
    else slot.zip = file
    slots.set(stem, slot)
  }
  const out: File[] = []
  for (const item of items.value) {
    const slot = slots.get(item.announcement_id)
    if (slot?.html) out.push(slot.html)
    if (item.has_zip && slot?.zip) out.push(slot.zip)
  }
  return out
}

const finished = computed(() => entries.value.filter((entry) => entry.state !== 'pending').length)
const failures = computed(() => entries.value.filter((entry) => entry.state === 'failed'))
const skipped = computed(() => entries.value.filter((entry) => entry.state === 'skipped').length)
const percent = computed(() =>
  entries.value.length ? Math.round((finished.value / entries.value.length) * 100) : 0,
)

async function pump() {
  const queue = entries.value.filter((entry) => entry.state === 'pending')
  let cursor = 0
  const lanes = Math.max(1, Math.min(LANES, queue.length))
  const worker = async () => {
    while (cursor < queue.length) {
      const entry = queue[cursor++]
      currentName.value = entry.file.name
      try {
        const out = await api.uploadJobFile(jobId.value, entry.file)
        entry.state = out.skipped ? 'skipped' : 'done'
      } catch (e) {
        entry.state = 'failed'
        entry.message = e instanceof Error ? e.message : '上传失败'
      }
      entries.value = [...entries.value]
    }
  }
  await Promise.all(Array.from({ length: lanes }, worker))
  currentName.value = ''
}

async function submit() {
  if (!canSubmit.value) return
  errorMsg.value = ''
  currentName.value = ''
  step.value = 'uploading'
  entries.value = filesToSend().map((file) => ({ file, state: 'pending' as const }))
  try {
    const job = await api.createJob(
      items.value.map((item) => ({ announcement_id: item.announcement_id, has_zip: item.has_zip })),
      [...already.value],
    )
    jobId.value = job.job_id
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '建任务失败'
    step.value = 'confirm'
    entries.value = []
    return
  }
  await pump()
  await finish()
}

async function finish() {
  if (failures.value.length) return // 还有文件没传完，停在原地等重试
  try {
    await api.startJob(jobId.value)
    step.value = 'done'
    emit('submitted')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '提交失败'
  }
}

async function retry() {
  for (const entry of entries.value) {
    if (entry.state === 'failed') {
      entry.state = 'pending'
      entry.message = undefined
    }
  }
  entries.value = [...entries.value]
  errorMsg.value = ''
  await pump()
  await finish()
}

/** 放弃本次：删掉这个还没开始跑的任务，避免留一条永远不动的 draft。 */
async function discard() {
  try {
    await api.deleteJob(jobId.value)
  } catch {
    /* 删不掉也不拦住用户关闭 */
  }
  emit('update:modelValue', false)
}

function close() {
  if (step.value !== 'uploading') {
    emit('update:modelValue', false)
    return
  }
  if (finished.value < entries.value.length) return // 还没传完，先别关
  void discard() // 传完了但有失败：等同"放弃本次上传"，别留一条不会跑的 draft
}

function preview<T>(list: T[]): T[] {
  return list.slice(0, PREVIEW)
}
</script>

<template>
  <div v-if="modelValue" class="mask" @click.self="close">
    <div class="sheet" role="dialog" aria-modal="true">
      <header class="head">
        <h2>
          {{ step === 'confirm' ? '上传确认' : step === 'uploading' ? '正在上传' : '已提交' }}
        </h2>
        <button class="x" aria-label="关闭" @click="close">✕</button>
      </header>

      <div class="body">
        <div v-if="loadingPlan" class="skeleton h-24" />

        <p v-else-if="planError" class="err">
          配对失败：{{ planError }}
          <button class="btn-ghost" @click="loadPlan">重试</button>
        </p>

        <!-- ① 配对确认 -->
        <template v-else-if="step === 'confirm' && plan">
          <ul class="stats">
            <li>
              <div class="line"><span>配对成功</span><b class="num">{{ paired }}</b><em>对</em></div>
            </li>
            <li>
              <div class="line">
                <span>仅 HTML（无附件，正常）</span><b class="num">{{ htmlOnly }}</b><em>则</em>
              </div>
            </li>
            <li>
              <div class="line">
                <span>孤立 ZIP（将被丢弃）</span>
                <b class="num">{{ plan.zip_only.length }}</b><em>个</em>
                <button v-if="plan.zip_only.length" class="more" @click="showZipOnly = !showZipOnly">
                  {{ showZipOnly ? '收起' : '展开' }}
                </button>
              </div>
              <!-- 清单挂在它自己那一行下面，不能排到别的统计行后面 -->
              <ul v-if="showZipOnly" class="names">
                <li v-for="z in preview(plan.zip_only)" :key="z.filename">{{ z.filename }}</li>
                <li v-if="plan.zip_only.length > PREVIEW" class="faint">等 {{ plan.zip_only.length }} 个</li>
              </ul>
            </li>
            <li>
              <div class="line">
                <span>无法识别</span>
                <b class="num">{{ plan.invalid.length }}</b><em>个</em>
                <button v-if="plan.invalid.length" class="more" @click="showInvalid = !showInvalid">
                  {{ showInvalid ? '收起' : '展开' }}
                </button>
              </div>
              <ul v-if="showInvalid" class="names">
                <li v-for="name in preview(plan.invalid)" :key="name">{{ name }}</li>
                <li v-if="plan.invalid.length > PREVIEW" class="faint">等 {{ plan.invalid.length }} 个</li>
              </ul>
            </li>
          </ul>

          <p v-if="!items.length" class="warn">
            没有可提取的公告：这批文件里没有配到 HTML（只有 ZIP 无法提取）。
          </p>

          <template v-if="already.length">
            <p class="warn">
              ⚠ 以下 {{ already.length }} 则已提取过，重复提取会覆盖：
              <button class="more" @click="showAlready = !showAlready">
                {{ showAlready ? '收起' : '展开' }}
              </button>
            </p>
            <ul v-if="showAlready" class="names">
              <li v-for="id in preview(already)" :key="id" class="num">{{ id }}</li>
              <li v-if="already.length > PREVIEW" class="faint">等 {{ already.length }} 则</li>
            </ul>
            <label class="check">
              <input v-model="confirmed" type="checkbox" />
              我确认覆盖这 {{ already.length }} 则公告
            </label>
          </template>

          <p v-if="errorMsg" class="err">{{ errorMsg }}</p>
        </template>

        <!-- ② 上传中 -->
        <template v-else-if="step === 'uploading'">
          <p class="progress-line">
            正在上传 <b class="num">{{ finished }}/{{ entries.length }}</b>
          </p>
          <div class="bar"><i :style="{ width: percent + '%' }" /></div>
          <p class="sub">
            <span v-if="currentName">当前：{{ currentName }}</span>
            <span v-else>处理中…</span>
          </p>
          <p class="sub">
            跳过（已传过） <b class="num">{{ skipped }}</b> 个 · 失败
            <b class="num">{{ failures.length }}</b> 个
          </p>
          <template v-if="failures.length">
            <ul class="names">
              <li v-for="entry in preview(failures)" :key="entry.file.name">
                {{ entry.file.name }} —— {{ entry.message }}
              </li>
              <li v-if="failures.length > PREVIEW" class="faint">等 {{ failures.length }} 个</li>
            </ul>
          </template>
          <p v-if="errorMsg" class="err">{{ errorMsg }}</p>
        </template>

        <!-- ③ 完成 -->
        <template v-else>
          <p class="done-line">
            已提交 <b class="num">{{ items.length }}</b> 则，正在后台提取。
          </p>
          <p class="sub">进度与结果见下方列表（等待中 / 处理中 / 已完成 / 失败）。</p>
          <p v-if="skipped" class="sub">其中 {{ skipped }} 个文件本来就传过，已跳过。</p>
        </template>
      </div>

      <footer class="foot">
        <template v-if="step === 'confirm'">
          <span v-if="already.length && !confirmed" class="sub">勾选"我确认覆盖"后才能继续</span>
          <span v-else-if="!items.length" class="sub">没有可提取的公告</span>
          <button class="btn-ghost" @click="close">取消</button>
          <button class="btn" :disabled="!canSubmit || loadingPlan" @click="submit">开始上传</button>
        </template>

        <template v-else-if="step === 'uploading'">
          <template v-if="failures.length">
            <button class="btn-ghost" @click="discard">放弃本次上传</button>
            <button class="btn" @click="retry">重试失败项（{{ failures.length }}）</button>
          </template>
          <span v-else class="sub">请勿关闭窗口，正在逐文件上传…</span>
        </template>

        <template v-else>
          <button class="btn" @click="close">关闭</button>
        </template>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(9, 9, 11, 0.32);
}

.sheet {
  display: flex;
  width: min(600px, calc(100% - 32px));
  max-height: 78vh;
  margin: 9vh auto 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  color: var(--ink);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.28);
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--line);
  padding: 12px 16px;
}

.head h2 {
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 600;
}

.x {
  color: var(--faint);
  font-size: 13px;
}

.x:hover {
  color: var(--ink);
}

.body {
  min-height: 120px;
  flex: 1;
  overflow: auto;
  padding: 16px;
}

.foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  border-top: 1px solid var(--line);
  background: color-mix(in srgb, var(--ink) 2%, transparent);
  padding: 10px 16px;
}

.foot .sub {
  margin-right: auto;
}

.stats {
  display: grid;
  gap: 6px;
}

.stats li {
  font-size: 13px;
}

.stats .line {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.stats .line span {
  flex: 1;
  color: var(--muted);
}

.stats .line b {
  font-size: 14px;
}

.stats .line em {
  color: var(--faint);
  font-size: 11px;
  font-style: normal;
}

.more {
  color: var(--info);
  font-size: 12px;
}

.names {
  margin-top: 8px;
  max-height: 160px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 8px 10px;
  font-size: 12px;
  color: var(--muted);
}

.names li + li {
  margin-top: 3px;
}

.faint {
  color: var(--faint);
}

.warn {
  margin-top: 14px;
  border-left: 2px solid var(--warn);
  padding-left: 10px;
  color: var(--warn);
  font-size: 13px;
  line-height: 1.6;
}

.err {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  color: var(--bad);
  font-size: 13px;
}

.check {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 13px;
}

.check input {
  accent-color: var(--accent);
}

.progress-line {
  font-size: 13px;
  color: var(--muted);
}

.sub {
  margin-top: 6px;
  color: var(--muted);
  font-size: 12px;
}

.done-line {
  font-size: 14px;
}

.bar {
  height: 6px;
  margin-top: 8px;
  overflow: hidden;
  border-radius: 99px;
  background: color-mix(in srgb, var(--ink) 8%, transparent);
}

.bar i {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width var(--dur-base) var(--ease);
}

.h-24 {
  height: 96px;
}
</style>
