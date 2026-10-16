<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api/client'
import { sessionChanged, useAuth } from '@/composables/useAuth'

const route = useRoute()
const { sessionError } = useAuth()
const registering = ref(false)
const username = ref(''), password = ref(''), confirmation = ref('')
const busy = ref(false), error = ref('')

function switchMode() { registering.value = !registering.value; error.value = ''; confirmation.value = '' }
async function submit() {
  if (busy.value) return
  error.value = ''
  if (registering.value && password.value !== confirmation.value) { error.value = '两次输入的密码不一致'; return }
  busy.value = true
  try {
    const method = registering.value ? api.register : api.login
    await method(username.value.trim(), password.value)
    sessionChanged()
    const next = String(route.query.next ?? '')
    // A full navigation clears graph/session caches before another account can see them.
    window.location.replace(next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') && !next.startsWith('/login') ? next : '/ingest')
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '操作失败，请重试' }
  finally { busy.value = false }
}
</script>

<template>
  <main class="login-page" data-domain="paper">
    <div class="login-intro">
      <p class="login-eyebrow">采购公告 · 实体与关系</p>
      <h1>标络</h1>
      <p class="login-subtitle">从采购信息，发现关联。</p>
      <div class="login-lines" aria-hidden="true"><i /><i /><i /><b /><b /><b /></div>
      <p class="login-caption">公告提取 / 标的检索 / 关系探索</p>
    </div>
    <section class="login-card">
      <p class="login-eyebrow">{{ registering ? '建立你的数据空间' : '欢迎回来' }}</p>
      <h2>{{ registering ? '注册账号' : '登录标络' }}</h2>
      <p class="login-help">使用账号管理和查看你的采购数据。</p>
      <form @submit.prevent="submit">
        <label for="username">用户名</label>
        <input id="username" v-model="username" name="username" required minlength="3" maxlength="32" autocomplete="username" placeholder="请输入用户名" :disabled="busy" />
        <label for="password">密码</label>
        <input id="password" v-model="password" name="password" type="password" required minlength="6" maxlength="128" :autocomplete="registering ? 'new-password' : 'current-password'" placeholder="请输入密码" :disabled="busy" />
        <template v-if="registering">
          <label for="confirmation">确认密码</label>
          <input id="confirmation" v-model="confirmation" type="password" required maxlength="128" autocomplete="new-password" placeholder="再次输入密码" :disabled="busy" />
        </template>
        <p v-if="error || sessionError" class="login-error" role="alert">{{ error || sessionError }}</p>
        <button class="login-submit" type="submit" :disabled="busy">{{ busy ? '请稍候…' : registering ? '注册并进入' : '登录' }}</button>
      </form>
      <p class="login-switch">{{ registering ? '已有账号？' : '还没有账号？' }}<button type="button" :disabled="busy" @click="switchMode">{{ registering ? '去登录' : '注册账号' }}</button></p>
    </section>
  </main>
</template>

<style scoped>
.login-page{min-height:100%;display:grid;grid-template-columns:1fr 420px;align-items:center;gap:10vw;padding:8vh 12vw;background:var(--paper);color:var(--ink)}
.login-eyebrow{font-size:12px;letter-spacing:.15em;color:var(--muted)}
.login-intro h1{font-family:var(--font-serif);font-size:clamp(64px,8vw,112px);line-height:1.3;letter-spacing:-.07em;margin:24px 0}
.login-subtitle{font-family:var(--font-serif);font-size:22px}.login-caption{font-size:12px;color:var(--muted);letter-spacing:.08em;margin-top:30px}
.login-lines{position:relative;width:260px;height:100px;margin-top:40px}.login-lines i{position:absolute;left:12px;top:48px;width:180px;height:1px;background:var(--line);transform-origin:left}.login-lines i:nth-child(1){transform:rotate(-15deg)}.login-lines i:nth-child(3){transform:rotate(15deg)}
.login-lines b{position:absolute;top:43px;left:5px;width:11px;height:11px;border:1px solid var(--ink);border-radius:50%;background:var(--paper)}.login-lines b:nth-last-child(2){left:181px;top:0}.login-lines b:last-child{left:181px;top:90px}
.login-card{border:1px solid var(--line);background:var(--paper);padding:36px;border-radius:4px;box-shadow:0 12px 50px #00000005}.login-card h2{font-family:var(--font-serif);font-size:28px;margin:12px 0}.login-help{font-size:13px;line-height:1.8;color:var(--muted);margin-bottom:28px}
form{display:flex;flex-direction:column}label{font-size:13px;margin:0 0 8px}input{height:44px;border:1px solid var(--line);background:transparent;border-radius:3px;padding:0 12px;font-size:14px;margin-bottom:20px;color:var(--ink)}input:focus{outline:1px solid var(--ink);outline-offset:1px}
.login-submit{height:44px;background:var(--ink);color:var(--paper);border:0;border-radius:3px;margin-top:8px;font-size:14px;cursor:pointer}button:disabled{opacity:.6;cursor:wait}.login-error{font-size:13px;color:#b03c2f;margin:0 0 12px;line-height:1.6}.login-switch{margin-top:24px;font-size:13px;color:var(--muted)}.login-switch button{border:0;background:none;color:var(--ink);margin-left:6px;text-decoration:underline;cursor:pointer}
@media(max-width:850px){.login-page{grid-template-columns:minmax(0,420px);justify-content:center;gap:24px;padding:32px 20px}.login-intro h1{font-size:48px;margin:8px 0}.login-lines,.login-caption{display:none}.login-subtitle{font-size:18px}.login-card{padding:28px}}
</style>
