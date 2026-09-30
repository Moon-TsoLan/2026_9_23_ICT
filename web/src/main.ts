import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './styles/index.css'

// 全部路由级懒加载：three 只随 /explore 进包，首屏（/ingest）不背星图
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/ingest' },
    { path: '/ingest', component: () => import('@/views/IngestView.vue') },
    { path: '/search', component: () => import('@/views/SearchView.vue') },
    { path: '/explore', component: () => import('@/views/ExploreView.vue') },
    { path: '/party/:id', component: () => import('@/views/PartyView.vue') },
  ],
})

createApp(App).use(router).mount('#app')
