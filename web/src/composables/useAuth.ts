import { ref } from 'vue'
import { api } from '@/api/client'
import type { AccountUser } from '@/api/client'
import { ApiError } from '@/types/api'

const user = ref<AccountUser | null>(null)
const sessionError = ref('')
let pending: Promise<AccountUser | null> | null = null
let checked = false

async function loadSession() {
  if (checked) return user.value
  if (pending) return pending
  pending = api.me().then(result => {
    user.value = result.user; checked = true; sessionError.value = ''; return result.user
  }).catch((error: unknown) => {
    if (!(error instanceof ApiError && error.status === 401)) sessionError.value = '暂时无法连接账号服务，请稍后重试'
    return null
  }).finally(() => { pending = null })
  return pending
}

export function sessionChanged() {
  try { localStorage.setItem('ict:session-change', `${Date.now()}:${Math.random()}`) } catch { /* cookie remains authoritative */ }
}

export function useAuth() { return { user, loadSession, sessionError } }
