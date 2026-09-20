import { createRouter, createWebHistory } from 'vue-router'
import DiscoverView from './views/DiscoverView.vue'
import HelpView from './views/HelpView.vue'
import ListsView from './views/ListsView.vue'
import SettingsView from './views/SettingsView.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'discover', component: DiscoverView },
    { path: '/listen', name: 'lists', component: ListsView },
    { path: '/einstellungen', name: 'settings', component: SettingsView },
    { path: '/hilfe', name: 'help', component: HelpView },
  ],
})
