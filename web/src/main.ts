import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './styles/index.css'
import { useAuth } from './composables/useAuth'

// 全部路由级懒加载：three 只随 /explore 进包，首屏（/ingest）不背星图
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('@/views/LoginView.vue') },
    { path: '/', redirect: '/ingest' },
    { path: '/ingest', component: () => import('@/views/IngestView.vue') },
    { path: '/search', component: () => import('@/views/SearchView.vue') },
    { path: '/explore', component: () => import('@/views/ExploreView.vue') },
    { path: '/party/:id', component: () => import('@/views/PartyView.vue') },
  ],
})

router.beforeEach(async to => {
  const user = await useAuth().loadSession()
  if (!user && to.path !== '/login') return { path: '/login', query: { next: to.fullPath } }
  if (user && to.path === '/login') return '/ingest'
})
window.addEventListener('ict:unauthorized', () => {
  if (location.pathname !== '/login') location.replace('/login')
})
window.addEventListener('storage', event => {
  if (event.key === 'ict:session-change') location.replace('/login')
})
window.addEventListener('pageshow', event => {
  if (event.persisted) location.reload()
})

createApp(App).use(router).mount('#app')
