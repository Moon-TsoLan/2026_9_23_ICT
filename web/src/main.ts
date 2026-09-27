import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './styles.css'
import ObjectsView from './views/ObjectsView.vue'
import PartiesView from './views/PartiesView.vue'
import ProcessView from './views/ProcessView.vue'
import RelationView from './views/RelationView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/objects' },
    { path: '/relations', component: RelationView },
    { path: '/objects', component: ObjectsView },
    { path: '/parties', component: PartiesView },
    { path: '/process', component: ProcessView },
  ],
})

createApp(App).use(router).mount('#app')
