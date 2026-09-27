<script setup lang="ts">
import { onUnmounted, ref } from 'vue'

const steps = [
  { title: '读入公告', detail: 'HTML 标讯进入队列' },
  { title: '拆解附件', detail: 'PDF、DOCX、XLSX' },
  { title: '抽取标的', detail: '七个字段一次对齐' },
  { title: '识别主体', detail: '采购单位与投标方' },
  { title: '连上关系', detail: '合作、竞标、供应' },
]

const cursor = ref(steps.length)
const playing = ref(false)
let timer = 0

const counts = [
  { k: '项目', v: '14' },
  { k: '标的物', v: '8' },
  { k: '主体', v: '15' },
  { k: '关系', v: '36' },
]

const files = [
  { kind: 'HTML', name: 't20260812_27119664.html' },
  { kind: 'XLSX', name: '报价明细.xlsx' },
  { kind: 'PDF', name: '评分表.pdf' },
]

function play() {
  window.clearInterval(timer)
  playing.value = true
  cursor.value = 0
  timer = window.setInterval(() => {
    cursor.value += 1
    if (cursor.value >= steps.length) {
      window.clearInterval(timer)
      playing.value = false
    }
  }, 700)
}

onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div class="h-full overflow-auto px-6 py-7 lg:px-10">
    <div class="flex flex-wrap items-end justify-between gap-6">
      <div>
        <p class="text-[11px] tracking-[0.18em] text-muted">任务一之前</p>
        <h1 class="mt-2 font-serif text-[40px] leading-none font-semibold">从公告到关系</h1>
        <p class="mt-4 max-w-xl text-[14px] leading-7 text-muted">样例已经在库里。走完五步，标的和主体写定，关系页再按这些结果重画。</p>
      </div>
      <button
        class="h-11 bg-ink px-5 text-[14px] text-white transition-colors duration-200 hover:bg-[#3f3f46] disabled:opacity-50"
        :disabled="playing"
        @click="play"
      >
        {{ playing ? '处理中' : '播放处理' }}
      </button>
    </div>

    <div class="mt-8 grid grid-cols-2 gap-px bg-line md:grid-cols-4">
      <div v-for="c in counts" :key="c.k" class="bg-white px-4 py-4">
        <div class="font-serif text-[34px] leading-none">{{ c.v }}</div>
        <div class="mt-2 text-[12px] text-muted">{{ c.k }}</div>
      </div>
    </div>

    <div class="relative mt-12">
      <div class="absolute top-5 right-[10%] left-[10%] hidden h-px bg-line md:block" />
      <div
        class="absolute top-5 left-[10%] hidden h-px bg-ink transition-[width] duration-700 ease-out md:block"
        :style="{ width: `${(Math.min(cursor, steps.length - 1) / (steps.length - 1)) * 80}%` }"
      />
      <ol class="relative grid grid-cols-1 gap-6 md:grid-cols-5 md:gap-3">
        <li v-for="(step, i) in steps" :key="step.title" class="md:text-center">
          <span
            class="relative z-10 grid h-10 w-10 place-items-center border text-[13px] md:mx-auto"
            :class="i < cursor ? 'border-ink bg-ink text-white' : i === cursor && playing ? 'border-accent bg-white text-ink' : 'border-line bg-white text-muted'"
          >
            {{ i + 1 }}
          </span>
          <div class="mt-3 text-[14px]">{{ step.title }}</div>
          <div class="mt-1 text-[12px] text-muted">{{ step.detail }}</div>
        </li>
      </ol>
    </div>

    <div class="mt-8 flex flex-wrap items-center justify-between gap-4">
      <p class="text-[13px] text-muted">关系不在这一步生成，它读的是上面抽出来的标的和主体。</p>
      <RouterLink
        v-if="!playing"
        to="/objects"
        class="text-[13px] underline decoration-accent decoration-2 underline-offset-[5px]"
      >
        查看提取出的标的
      </RouterLink>
    </div>

    <div class="mt-10 flex flex-wrap gap-4">
      <article v-for="file in files" :key="file.name" class="w-56 border border-line bg-white px-4 py-4">
        <div class="h-0.5 w-8 bg-ink" />
        <div class="mt-4 text-[11px] tracking-[0.14em] text-muted">{{ file.kind }}</div>
        <div class="mt-2 font-serif text-[16px]">{{ file.name }}</div>
      </article>
    </div>
  </div>
</template>
