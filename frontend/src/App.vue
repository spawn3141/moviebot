<script setup>
import { ui } from './store'
import ListPicker from './components/ListPicker.vue'
import TitleDetail from './components/TitleDetail.vue'
</script>

<template>
  <header class="topbar">
    <div class="topbar-inner">
      <RouterLink to="/" class="brand">
        <img src="/favicon.svg" alt="" width="22" height="22" /> moviebot
      </RouterLink>
      <nav>
        <RouterLink to="/">Entdecken</RouterLink>
        <RouterLink to="/neu">Neu</RouterLink>
        <RouterLink to="/listen">Listen</RouterLink>
        <RouterLink to="/einstellungen">Einstellungen</RouterLink>
        <RouterLink to="/hilfe">Hilfe</RouterLink>
      </nav>
    </div>
  </header>

  <main class="page">
    <RouterView />
  </main>

  <footer class="footer">
    Film- und Seriendaten: <a href="https://www.themoviedb.org" target="_blank" rel="noopener">TMDB</a>
    · Streaming-Verfügbarkeit: <a href="https://www.justwatch.com" target="_blank" rel="noopener">JustWatch</a>.
    Dieses Produkt nutzt die TMDB-API, ist aber nicht von TMDB unterstützt oder zertifiziert.
  </footer>

  <TitleDetail v-if="ui.detailId" :id="ui.detailId" @close="ui.detailId = null" />
  <ListPicker v-if="ui.pickerTitle" :item="ui.pickerTitle" @close="ui.pickerTitle = null" />

  <Transition name="toast">
    <div v-if="ui.toast" class="toast" role="status">
      <span>{{ ui.toast.message }}</span>
      <button v-if="ui.toast.undo" type="button" @click="ui.toast.undo">Rückgängig</button>
      <button v-if="ui.toast.action" type="button" @click="ui.toast.action.run(); ui.toast = null">
        {{ ui.toast.action.label }}
      </button>
    </div>
  </Transition>
</template>
