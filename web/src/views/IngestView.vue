<script setup lang="ts">
import { CloudUpload, FileClock, Trash2, Workflow } from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import RunPipeline from '@/components/RunPipeline.vue'
import UploadDialog from '@/components/UploadDialog.vue'
import { RECORD_STATUS_LABEL, STEP_LABEL } from '@/types/ingest'
import type { IngestRecord, RunState } from '@/types/ingest'

const PAGE_SIZE = 20
const POLL_MS = 3000

const records = ref<IngestRecord[]>([])
const total = ref(0)
const doneTotal = ref(0)
const page = ref(1)
const loading = ref(true)
const errorMsg = ref('')

const expandedId = ref<string | null>(null)
const run = ref<RunState | null>(null)
const runLoading = ref(false)
const runError = ref('')

const dragging = ref(false)
const uploadOpen = ref(false)
const uploadFiles = ref<File[]>([])
/** 待确认丢弃的任务（任务级操作，行只是它的入口） */
const discardTarget = ref<IngestRecord | null>(null)
const actionError = ref('')

const pages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))
/** 进行中数量 = 记录总数 − 已入库数（后端合并时算好的） */
const inflightCount = computed(() => Math.max(0, total.value - doneTotal.value))
const hasInflight = computed(() => inflightCount.value > 0)

let timer = 0

async function load(target = page.value, silent = false) {
  if (!silent) loading.value = true
  try {
    const res = await api.ingestRecords(target, PAGE_SIZE)
    records.value = res.items
    total.value = res.total
    doneTotal.value = res.done_total
    page.value = res.page
    errorMsg.value = ''
    // 展开着的那一行要跟着轮询一起更新，否则列表在走、里面的步骤停在上一次点开的瞬间
    const open = expandedId.value
      ? records.value.find((item) => item.announcement_id === expandedId.value)
      : undefined
    if (open?.run_started) await loadRunState(open.announcement_id, true)
  } catch (e) {
    if (!silent) errorMsg.value = e instanceof Error ? e.message : '列表加载失败'
  } finally {
    loading.value = false
    schedule()
  }
}

/** 还有等待中/处理中才轮询，全部结束就停。静默刷新：不动页码、不收起展开的行。 */
function schedule() {
  window.clearTimeout(timer)
  if (hasInflight.value) timer = window.setTimeout(() => void load(page.value, true), POLL_MS)
}

function goPage(target: number) {
  if (target < 1 || target > pages.value || target === page.value) return
  expandedId.value = null
  void load(target)
}

function pct(record: IngestRecord): number {
  const progress = record.progress
  if (!progress || !progress.total) return 0
  return Math.round((progress.done / progress.total) * 100)
}

function stepText(record: IngestRecord): string {
  const progress = record.progress
  if (!progress) return ''
  const label = progress.current_step
    ? (STEP_LABEL[progress.current_step] ?? progress.current_step)
    : ''
  return `第 ${progress.done}/${progress.total} 步${label ? ' · ' + label : ''}`
}

/** 上传弹窗提交成功后：回第 1 页刷新，立刻能看到"等待中" */
function onSubmitted() {
  void load(1)
}

function askDiscard(record: IngestRecord) {
  actionError.value = ''
  discardTarget.value = record
}

async function doDiscard() {
  const jobId = discardTarget.value?.job_id
  if (!jobId) return discardTarget.value = null
  actionError.value = ''
  try {
    await api.deleteJob(jobId)
    discardTarget.value = null
    await load(page.value, true)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : '丢弃失败'
  }
}

onMounted(() => void load(1))
onUnmounted(() => window.clearTimeout(timer))

async function toggleRun(record: IngestRecord) {
  const aid = record.announcement_id
  if (expandedId.value === aid) {
    expandedId.value = null
    return
  }
  expandedId.value = aid
  run.value = null
  runError.value = ''
  // 等待中的行没有本次的 run_state；磁盘上那份是上一轮留下的，不能拿来当"已完成"展示
  if (!record.run_started) {
    runError.value = '本次提取还没开始（等待工作进程领取）'
    return
  }
  await loadRunState(aid, false)
}

/** 拉某一则的处理步骤。silent=true 用于轮询里的静默刷新：失败就保留上一次的内容 */
async function loadRunState(aid: string, silent: boolean) {
  if (!silent) runLoading.value = true
  try {
    run.value = await api.runState(aid)
    runError.value = ''
  } catch (e) {
    if (!silent) runError.value = e instanceof Error ? e.message : '没有处理记录'
  } finally {
    if (!silent) runLoading.value = false   // 静默刷新别去动"正在加载"的骨架
  }
}

/** 选/拖入文件 → 打开上传弹窗；配对、覆盖确认、逐文件上传都在弹窗里做 */
function onFiles(files: FileList | null) {
  if (!files?.length) return
  uploadFiles.value = Array.from(files)
  uploadOpen.value = true
}

/** 选完立刻清空 input，否则同一批文件再选一次不会触发 change */
function onPickFiles(event: Event) {
  const input = event.target as HTMLInputElement
  onFiles(input.files)
  input.value = ''
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="ingest">
    <header class="head">
      <div>
        <p class="kicker">任务一 · 数据接入</p>
        <h1 class="h-serif">公告采集与抽取</h1>
      </div>
      <p class="total">
        已入库 <b class="num">{{ doneTotal }}</b> 篇公告<template v-if="inflightCount">
          · 进行中 <b class="num">{{ inflightCount }}</b> 则</template
        >
      </p>
    </header>

    <div
      class="dropzone"
      :class="{ dragging }"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop.prevent="((dragging = false), onFiles($event.dataTransfer?.files ?? null))"
    >
      <CloudUpload :size="20" class="text-faint" />
      <p>拖入 HTML 与同名 ZIP（可批量）</p>
      <label class="btn-ghost">
        选择文件
        <input type="file" multiple accept=".html,.zip" class="hidden" @change="onPickFiles" />
      </label>
    </div>

    <UploadDialog v-model="uploadOpen" :files="uploadFiles" @submitted="onSubmitted" />

    <div class="card list">
      <div v-if="loading" class="p-4">
        <div v-for="i in 6" :key="i" class="skeleton mb-3 h-5" :style="{ width: `${92 - i * 7}%` }" />
      </div>
      <EmptyState v-else-if="errorMsg" :title="errorMsg" hint="确认后端服务已启动（uvicorn :8000）" />
      <EmptyState v-else-if="!records.length" title="还没有任何记录" hint="用上面的上传区提交公告，提取进度会实时显示在这里" />
      <table v-else>
        <thead>
          <tr>
            <th class="l">公告</th>
            <th class="l">状态</th>
            <th class="r">项目</th>
            <th class="r">标的物</th>
            <th class="l">更新时间</th>
            <th class="r">操作</th>
          </tr>
        </thead>
        <tbody v-for="r in records" :key="r.announcement_id">
          <tr :class="{ on: expandedId === r.announcement_id }" @click="toggleRun(r)">
            <td class="l">
              <span class="aid num">{{ r.announcement_id }}</span>
              <span class="atitle">{{ r.title }}</span>
            </td>
            <td class="l">
              <span class="st" :class="r.status">{{ RECORD_STATUS_LABEL[r.status] }}</span>
              <template v-if="r.status === 'running'">
                <div v-if="r.progress" class="minibar"><i :style="{ width: pct(r) + '%' }" /></div>
                <span class="step">{{ r.progress ? stepText(r) : '正在处理…' }}</span>
              </template>
              <p v-if="r.status === 'failed' && r.error" class="errline" :title="r.error">
                {{ r.error }}
              </p>
            </td>
            <td class="r num">{{ r.projects ?? '—' }}</td>
            <td class="r num">{{ r.cobs ?? '—' }}</td>
            <td class="l num text-muted">{{ fmtTime(r.updated_at) }}</td>
            <td class="r nowrap">
              <button class="mini" title="展开处理步骤" @click.stop="toggleRun(r)">
                <Workflow :size="13" />
              </button>
              <!-- 丢弃只对"整条任务都还没开跑"出现；它是任务级操作，行只是入口 -->
              <button
                v-if="r.status === 'waiting' && r.job_discardable"
                class="mini danger"
                title="丢弃这个任务（都还没开始提取）"
                @click.stop="askDiscard(r)"
              >
                <Trash2 :size="13" />
                丢弃
              </button>
            </td>
          </tr>
          <tr v-if="expandedId === r.announcement_id" class="run-row">
            <td colspan="6">
              <div v-if="runLoading" class="skeleton h-24" />
              <div v-else-if="runError" class="run-err">
                <FileClock :size="15" />
                {{ runError }}
              </div>
              <RunPipeline v-else-if="run" :run="run" />
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loading && !errorMsg && total > PAGE_SIZE" class="pager">
        <button class="btn-ghost" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <span class="pageno num">第 {{ page }} / {{ pages }} 页 · 共 {{ total }} 则</span>
        <button class="btn-ghost" :disabled="page >= pages" @click="goPage(page + 1)">下一页</button>
      </div>
    </div>

    <!-- 丢弃确认：用居中弹窗，不撑开行内布局 -->
    <div v-if="discardTarget" class="mask" @click.self="discardTarget = null">
      <div class="confirm" role="dialog" aria-modal="true">
        <h3>丢弃这个任务？</h3>
        <p>
          该任务共 <b class="num">{{ discardTarget.job_total ?? 1 }}</b> 则公告，都还没开始提取。
          已上传的原件会一并删除，之后需要重新上传。
        </p>
        <p v-if="actionError" class="err">{{ actionError }}</p>
        <div class="confirm-row">
          <button class="btn-ghost" @click="discardTarget = null">取消</button>
          <button class="btn danger" @click="doDiscard">丢弃</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ingest {
  height: 100%;
  overflow: auto;
  padding: 20px 24px 32px;
}

.head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.head h1 {
  margin-top: 6px;
  font-size: 28px;
}

.total {
  color: var(--muted);
  font-size: 13px;
}

.total b {
  color: var(--ink);
  font-size: 18px;
}

.dropzone {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 16px;
  border: 1px dashed var(--faint);
  border-radius: var(--r-lg);
  padding: 18px 20px;
  color: var(--muted);
  font-size: 13px;
  transition:
    border-color var(--dur-fast) var(--ease),
    background var(--dur-fast) var(--ease);
}

.dropzone.dragging {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}

.dropzone label {
  margin-left: auto;
  cursor: pointer;
}

.list {
  margin-top: 14px;
  overflow: hidden;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th {
  border-bottom: 1px solid var(--ink);
  padding: 9px 14px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.08em;
  white-space: nowrap;
}

td {
  border-bottom: 1px solid var(--line);
  padding: 9px 14px;
}

tbody tr:not(.run-row) {
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease);
}

tbody tr:not(.run-row):hover {
  background: color-mix(in srgb, var(--ink) 4%, transparent);
}

tbody tr.on {
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}

.l {
  text-align: left;
}

.r {
  text-align: right;
}

.aid {
  display: block;
  font-size: 12px;
}

.atitle {
  display: block;
  max-width: 460px;
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 四种用户可见状态；没有"部分完成"这种警告 */
.st {
  display: inline-block;
  border-radius: var(--r-sm);
  padding: 1px 6px;
  font-size: 11px;
  line-height: 1.6;
}

.st.waiting {
  background: color-mix(in srgb, var(--ink) 6%, transparent);
  color: var(--muted);
}

.st.running {
  background: color-mix(in srgb, var(--info) 12%, transparent);
  color: var(--info);
}

.st.done {
  background: color-mix(in srgb, var(--ok) 12%, transparent);
  color: var(--ok);
}

.st.failed {
  background: color-mix(in srgb, var(--bad) 12%, transparent);
  color: var(--bad);
}

.minibar {
  height: 3px;
  margin-top: 5px;
  overflow: hidden;
  border-radius: 99px;
  background: color-mix(in srgb, var(--ink) 8%, transparent);
}

.minibar i {
  display: block;
  height: 100%;
  background: var(--info);
  transition: width var(--dur-base) var(--ease);
}

.step {
  display: block;
  margin-top: 3px;
  color: var(--faint);
  font-size: 11px;
}

.errline {
  margin: 3px 0 0;
  max-width: 260px;
  overflow: hidden;
  color: var(--bad);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  border-top: 1px solid var(--line);
  padding: 10px;
}

.pageno {
  color: var(--muted);
  font-size: 12px;
}

.nowrap {
  white-space: nowrap;
}

/* 行内的小按钮：展开步骤 / 丢弃 */
.mini {
  display: inline-flex;
  height: 24px;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 0 7px;
  color: var(--muted);
  font-size: 11px;
  transition:
    color var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease);
}

.mini:hover {
  border-color: var(--ink);
  color: var(--ink);
}

.mini.danger {
  color: var(--bad);
  border-color: color-mix(in srgb, var(--bad) 35%, transparent);
}

.mini.danger:hover {
  border-color: var(--bad);
  background: color-mix(in srgb, var(--bad) 8%, transparent);
}

.mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(9, 9, 11, 0.32);
}

.confirm {
  width: min(420px, calc(100% - 32px));
  margin: 22vh auto 0;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--surface);
  padding: 18px 20px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.28);
}

.confirm h3 {
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 600;
}

.confirm p {
  margin-top: 8px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.confirm .err {
  color: var(--bad);
}

.confirm-row {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.btn.danger {
  border-color: var(--bad);
  background: var(--bad);
  color: #fff;
}

.run-row td {
  background: color-mix(in srgb, var(--ink) 2%, transparent);
  padding: 14px 20px;
}

.run-err {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 4px;
  color: var(--muted);
  font-size: 13px;
}
</style>
