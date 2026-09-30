<script setup lang="ts">
import { Check, CircleDashed, LoaderCircle, Minus, TriangleAlert, X } from 'lucide-vue-next'
import MetricCard from '@/components/MetricCard.vue'
import type { RunState, RunStep, StepStatus } from '@/types/ingest'
import { FAILURE_LABEL, STEP_LABEL } from '@/types/ingest'

defineProps<{ run: RunState }>()

const STATUS_META: Record<StepStatus, { label: string; cls: string }> = {
  pending: { label: '待处理', cls: 'pending' },
  running: { label: '进行中', cls: 'running' },
  success: { label: '完成', cls: 'success' },
  partial: { label: '部分完成', cls: 'partial' },
  failed: { label: '失败', cls: 'failed' },
  skipped: { label: '跳过', cls: 'skipped' },
}

function stepMs(step: RunStep): string {
  if (!step.started_at || !step.ended_at) return ''
  const ms = new Date(step.ended_at).getTime() - new Date(step.started_at).getTime()
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function failureText(step: RunStep): string {
  const label = step.failure_code ? (FAILURE_LABEL[step.failure_code] ?? step.failure_code) : ''
  return [label, step.failure_message].filter(Boolean).join('：')
}
</script>

<template>
  <div class="pipeline">
    <div class="counts">
      <MetricCard k="项目" :v="String(run.counts.projects)" />
      <MetricCard k="标的物" :v="String(run.counts.final_cobs)" />
      <MetricCard k="主体" :v="String(run.counts.final_subs)" />
      <MetricCard k="候选合并" :v="String(run.counts.normalized_candidates)" />
    </div>
    <ol class="steps">
      <li v-for="(step, i) in run.steps" :key="step.step" :class="STATUS_META[step.status]?.cls">
        <span class="icon">
          <Check v-if="step.status === 'success'" :size="13" />
          <TriangleAlert v-else-if="step.status === 'partial'" :size="13" />
          <X v-else-if="step.status === 'failed'" :size="13" />
          <LoaderCircle v-else-if="step.status === 'running'" :size="13" class="spin" />
          <Minus v-else-if="step.status === 'skipped'" :size="13" />
          <CircleDashed v-else :size="13" />
        </span>
        <span class="no num">{{ String(i + 1).padStart(2, '0') }}</span>
        <span class="name">{{ STEP_LABEL[step.step] ?? step.step }}</span>
        <span class="ms num">{{ stepMs(step) }}</span>
        <span class="status">{{ STATUS_META[step.status]?.label }}</span>
        <p v-if="step.failure_code || step.failure_message" class="fail">{{ failureText(step) }}</p>
      </li>
    </ol>
    <p v-if="run.review_required" class="review">
      <TriangleAlert :size="13" />
      该公告存在需人工复核的字段
    </p>
  </div>
</template>

<style scoped>
.counts {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.steps {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
}

.steps li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--line);
  padding: 7px 4px;
  font-size: 13px;
}

.icon {
  display: grid;
  width: 22px;
  height: 22px;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 99px;
  color: var(--muted);
}

.success .icon {
  border-color: var(--ok);
  color: var(--ok);
}

.partial .icon {
  border-color: var(--warn);
  color: var(--warn);
}

.failed .icon {
  border-color: var(--bad);
  color: var(--bad);
}

.running .icon {
  border-color: var(--accent);
  color: var(--accent);
}

.no {
  color: var(--faint);
  font-size: 11px;
}

.name {
  font-weight: 500;
}

.ms {
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
}

.status {
  width: 60px;
  text-align: right;
  color: var(--muted);
  font-size: 12px;
}

.success .status {
  color: var(--ok);
}

.partial .status {
  color: var(--warn);
}

.failed .status {
  color: var(--bad);
}

.fail {
  width: 100%;
  margin: 2px 0 4px 32px;
  color: var(--warn);
  font-size: 12px;
}

.review {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  color: var(--warn);
  font-size: 12px;
}

.spin {
  animation: rot 0.9s linear infinite;
}

@keyframes rot {
  to {
    transform: rotate(360deg);
  }
}
</style>
