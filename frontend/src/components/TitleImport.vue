<script setup>
import { ref, watch } from 'vue'
import { api } from '../api'
import { showToast, ui } from '../store'

const query = ref('')
const results = ref([])
const loading = ref(false)
const error = ref('')
const busyId = ref(null)

let timer
watch(query, (value) => {
  clearTimeout(timer)
  if (value.trim().length < 2) {
    results.value = []
    return
  }
  timer = setTimeout(search, 400)
})

async function search() {
  loading.value = true
  error.value = ''
  try {
    results.value = await api.search(query.value.trim())
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function add(entry) {
  busyId.value = entry.tmdb_id
  try {
    const title = await api.importTitle(entry.media_type, entry.tmdb_id)
    Object.assign(entry, { title_id: title.id, manual: true })
    showToast(`„${title.title}“ hinzugefügt`, null,
      { label: 'Ansehen', run: () => (ui.detailId = title.id) })
  } catch (e) {
    showToast(e.message)
  } finally {
    busyId.value = null
  }
}
</script>

<template>
  <div class="import">
    <input v-model="query" type="search" placeholder="Titel bei TMDB suchen, z. B. „Matrix“ …"
           aria-label="Titel suchen" />
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="loading" class="muted">Sucht …</p>
    <p v-else-if="query.trim().length >= 2 && !results.length" class="muted">Nichts gefunden.</p>

    <ul v-if="results.length" class="results">
      <li v-for="entry in results" :key="`${entry.media_type}-${entry.tmdb_id}`">
        <img v-if="entry.poster_url" :src="entry.poster_url" :alt="entry.title" loading="lazy" />
        <div v-else class="no-poster" />
        <div class="text">
          <strong>{{ entry.title }}</strong>
          <span class="meta">
            {{ entry.media_type === 'tv' ? 'Serie' : 'Film' }}<template v-if="entry.year"> · {{ entry.year }}</template>
          </span>
          <span v-if="entry.overview" class="overview">{{ entry.overview }}</span>
        </div>
        <button v-if="entry.manual" type="button" @click="ui.detailId = entry.title_id">Ansehen</button>
        <span v-else-if="entry.title_id" class="muted known">schon im Katalog</span>
        <button v-else type="button" :disabled="busyId === entry.tmdb_id" @click="add(entry)">
          Hinzufügen
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.import input { width: 100%; }
.results {
  list-style: none; margin: 12px 0 0; padding: 0 4px 0 0; display: grid; gap: 8px;
  max-height: 60vh; overflow-y: auto;
}
.results li {
  display: grid; grid-template-columns: 46px 1fr auto; gap: 12px; align-items: center;
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px;
}
.results img, .no-poster { width: 46px; aspect-ratio: 2 / 3; object-fit: cover; border-radius: 6px; }
.no-poster { background: var(--panel-2); }
.text { min-width: 0; display: grid; gap: 2px; }
.meta { color: var(--muted); font-size: .82rem; }
.overview { color: var(--text-soft); font-size: .82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.known { font-size: .85rem; }
.muted { color: var(--muted); }
.error { color: var(--danger); }
</style>
