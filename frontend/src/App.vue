<script setup>
import { ui } from './store'
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

  <Transition name="toast">
    <div v-if="ui.toast" class="toast" role="status">
      <span>{{ ui.toast.message }}</span>
      <button v-if="ui.toast.undo" type="button" @click="ui.toast.undo">Rückgängig</button>
    </div>
  </Transition>
</template>
