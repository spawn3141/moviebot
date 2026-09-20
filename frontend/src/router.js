import { createRouter, createWebHistory } from 'vue-router'
import DiscoverView from './views/DiscoverView.vue'
import NewView from './views/NewView.vue'
import HelpView from './views/HelpView.vue'
import SettingsView from './views/SettingsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'discover', component: DiscoverView },
    { path: '/neu', name: 'new', component: NewView },
    { path: '/einstellungen', name: 'settings', component: SettingsView },
    { path: '/hilfe', name: 'help', component: HelpView },
  ],
})
