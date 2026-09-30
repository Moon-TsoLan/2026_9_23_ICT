<script setup lang="ts">
import { CloudUpload, FileClock, Workflow } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import EmptyState from '@/components/EmptyState.vue'
import RunPipeline from '@/components/RunPipeline.vue'
import type { AnnouncementItem, RunState } from '@/types/ingest'

const items = ref<AnnouncementItem[]>([])
const loading = ref(true)
const errorMsg = ref('')

const expandedId = ref<string | null>(null)
const run = ref<RunState | null>(null)
const runLoading = ref(false)
const runError = ref('')

const uploadMsg = ref('')
const dragging = ref(false)

onMounted(async () => {
  try {
    const res = await api.announcements()
    items.value = res.items
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '公告列表加载失败'
  } finally {
    loading.value = false
  }
})

async function toggleRun(aid: string) {
  if (expandedId.value === aid) {
    expandedId.value = null
    return
  }
  expandedId.value = aid
  run.value = null
  runError.value = ''
  runLoading.value = true
  try {
    run.value = await api.runState(aid)
  } catch (e) {
    runError.value = e instanceof Error ? e.message : '没有处理记录'
  } finally {
    runLoading.value = false
  }
}

/** 上传：真实调用后端；演示环境返回 501，把原因如实展示 */
async function onFiles(files: FileList | null) {
  if (!files?.length) return
  uploadMsg.value = ''
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  try {
    const res = await fetch('/api/ingest/upload', { method: 'POST', body: fd })
    const body = (await res.json().catch(() => null)) as { detail?: string } | null
    uploadMsg.value = body?.detail ?? `上传失败（${res.status}）`
  } catch {
    uploadMsg.value = '无法连接后端服务'
  }
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
      <p class="total">已入库 <b class="num">{{ items.length }}</b> 篇公告</p>
    </header>

    <div
      class="dropzone"
      :class="{ dragging }"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop.prevent="((dragging = false), onFiles($event.dataTransfer?.files ?? null))"
    >
      <CloudUpload :size="20" class="text-faint" />
      <p>拖入一组数据（1 个公告 HTML + 同名附件 zip，可批量）</p>
      <label class="btn-ghost">
        选择文件
        <input type="file" multiple accept=".html,.zip" class="hidden" @change="onFiles(($event.target as HTMLInputElement).files)" />
      </label>
      <p v-if="uploadMsg" class="upload-msg">{{ uploadMsg }}</p>
    </div>

    <div class="card list">
      <div v-if="loading" class="p-4">
        <div v-for="i in 6" :key="i" class="skeleton mb-3 h-5" :style="{ width: `${92 - i * 7}%` }" />
      </div>
      <EmptyState v-else-if="errorMsg" :title="errorMsg" hint="确认后端服务已启动（uvicorn :8000）" />
      <EmptyState v-else-if="!items.length" title="库里还没有公告" />
      <table v-else>
        <thead>
          <tr>
            <th class="l">公告</th>
            <th class="l">数据来源</th>
            <th class="r">项目</th>
            <th class="r">标的物</th>
            <th class="l">入库时间</th>
            <th class="r">流水线</th>
          </tr>
        </thead>
        <tbody v-for="a in items" :key="a.announcement_id">
          <tr :class="{ on: expandedId === a.announcement_id }" @click="toggleRun(a.announcement_id)">
            <td class="l">
              <span class="aid num">{{ a.announcement_id }}</span>
              <span class="atitle">{{ a.title }}</span>
            </td>
            <td class="l">
              <span class="tag" :class="a.synthetic ? 'syn' : 'real'">{{ a.synthetic ? '合成演示' : '黄金真实' }}</span>
              <span v-if="a.review_required" class="tag warn">需复核</span>
            </td>
            <td class="r num">{{ a.projects }}</td>
            <td class="r num">{{ a.cobs }}</td>
            <td class="l num text-muted">{{ fmtTime(a.created_at) }}</td>
            <td class="r">
              <Workflow :size="14" class="inline text-muted" />
            </td>
          </tr>
          <tr v-if="expandedId === a.announcement_id" class="run-row">
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

.upload-msg {
  width: 100%;
  color: var(--warn);
  font-size: 12px;
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

.tag.syn {
  background: color-mix(in srgb, var(--info) 10%, transparent);
  color: var(--info);
}

.tag.real {
  background: color-mix(in srgb, var(--ok) 10%, transparent);
  color: var(--ok);
}

.tag.warn {
  margin-left: 4px;
  background: color-mix(in srgb, var(--warn) 10%, transparent);
  color: var(--warn);
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
