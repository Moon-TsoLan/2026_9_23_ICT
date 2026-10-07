/**
 * 「资料库有没有更新」的探测。
 *
 * 记住本页加载数据时的版本（/api/health 的 data_version），之后每 10 秒静默拉一次；
 * 版本变了就把 `stale` 置真，并给出新增条数。刷新完成后调 `ack()` 复位。
 *
 * 三条纪律：
 *   1 只提示，绝不自动刷新（用户可能正在看某个结果）；
 *   2 页面隐藏时不轮询；
 *   3 探测失败静默忽略，不能因为探测坏了打断用户。
 */
import { computed, onUnmounted, ref } from 'vue'
import { api } from '@/api/client'

const POLL_MS = 10_000

export function useDataVersion() {
  const stale = ref(false)
  const newCount = ref(0)
  const dismissed = ref(false)
  let baseline: string | null = null
  let baselineCount = 0
  let timer = 0

  /** 提示条是否该出现：有更新、且用户没手动关掉 */
  const show = computed(() => stale.value && !dismissed.value)

  async function tick() {
    if (document.visibilityState === 'visible') {
      try {
        const health = await api.health()
        if (baseline === null) {
          baseline = health.data_version
          baselineCount = health.announcements
        } else if (health.data_version !== baseline) {
          stale.value = true
          newCount.value = Math.max(0, health.announcements - baselineCount)
        }
      } catch {
        /* 探测失败：下一次再说 */
      }
    }
    timer = window.setTimeout(tick, POLL_MS)
  }

  /** 本页数据加载完成后调用：把当前版本记为基线 */
  async function mark() {
    try {
      const health = await api.health()
      baseline = health.data_version
      baselineCount = health.announcements
      stale.value = false
      newCount.value = 0
      dismissed.value = false
    } catch {
      /* 没基线也不影响使用 */
    }
  }

  /** 刷新完成后调用：收起提示并把基线推到最新 */
  function ack() {
    void mark()
  }

  function dismiss() {
    dismissed.value = true
  }

  function start() {
    timer = window.setTimeout(tick, POLL_MS)
  }

  onUnmounted(() => window.clearTimeout(timer))

  return { stale, newCount, show, mark, ack, dismiss, start }
}
