<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { relativeDay, summarizeChanges } from '../format'
import { ui } from '../store'

const DAYS = 7
const days = ref(null)
const error = ref('')

const GROUPS = [
  { key: 'added', label: 'Neu' },
  { key: 'readded', label: 'Wieder da' },
  { key: 'new_seasons', label: 'Neue Staffeln' },
  { key: 'removed', label: 'Bei einem Dienst weggefallen' },
]
const visible = computed(() => (days.value ?? []).map((day) => ({
  date: day.date,
  summary: summarizeChanges({ added: day.added.length, readded: day.readded.length,
                              new_seasons: day.new_seasons.length, removed: day.removed.length }),
  groups: GROUPS.map((g) => ({ ...g, items: day[g.key] })).filter((g) => g.items.length),
})))

/** "Film · 2025 · Prime Video", "Serie · 2025 · Staffel 3" or
 *  "Film · 2025 · nicht mehr bei WOW · weiter bei Prime Video" */
function info(item, group) {
  const parts = [item.media_type === 'tv' ? 'Serie' : 'Film']
  if (item.year) parts.push(item.year)
  if (item.seasons.length) parts.push(`Staffel ${item.seasons.join(', ')}`)
  if (group === 'removed') {
    parts.push(`nicht mehr bei ${item.services.join(', ')}`)
    parts.push(item.still_on.length ? `weiter bei ${item.still_on.join(', ')}` : 'bei keinem Dienst mehr im Abo oder kostenlos')
  } else if (item.services.length) parts.push(item.services.join(', '))
  if (item.seen) parts.push('gesehen')
  return parts.join(' · ')
}

onMounted(async () => {
  try {
    days.value = await api.snapshotChanges(DAYS)
  } catch (e) {
    error.value = e.message
  }
})
</script>

<template>
  <div class="new">
    <h1>Neu</h1>
    <p class="muted">
      Was die Abgleiche der letzten {{ DAYS }} Tage geändert haben – in allen Diensten, nicht nur in deinen Abos.
    </p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-else-if="!days" class="muted">Lädt …</p>
    <p v-else-if="!visible.length" class="muted">In den letzten {{ DAYS }} Tagen hat sich nichts geändert.</p>

    <details v-for="(day, index) in visible" :key="day.date" :open="index === 0">
      <summary>{{ relativeDay(day.date) }} <span class="muted">– {{ day.summary }}</span></summary>
      <template v-for="group in day.groups" :key="group.key">
        <h2>{{ group.label }} <span class="muted">({{ group.items.length }})</span></h2>
        <ul class="list">
          <li v-for="item in group.items" :key="item.id">
            <button type="button" class="row" @click="ui.detailId = item.id">
              <img v-if="item.poster_url" :src="item.poster_url" alt="" loading="lazy">
              <span v-else class="no-poster" />
              <span class="name">{{ item.title }}</span>
              <span class="info">{{ info(item, group.key) }}</span>
            </button>
          </li>
        </ul>
      </template>
    </details>
  </div>
</template>

<style scoped>
.new { max-width: 680px; }
h1 { font-size: 1.4rem; margin: 0 0 8px; }
h2 { font-size: .9rem; margin: 12px 0 6px; }
.muted { color: var(--muted); }
p.muted { margin: 0 0 14px; }
.error { color: var(--danger); }
details + details { margin-top: 10px; }
summary { cursor: pointer; padding: 4px 0; font-weight: 600; }
summary .muted { font-weight: 400; }
.list { list-style: none; padding: 0; margin: 0; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.list li + li { border-top: 1px solid var(--border); }
.row {
  display: grid; grid-template-columns: 34px 1fr; grid-template-areas: 'poster name' 'poster info';
  align-items: center; gap: 0 12px; width: 100%; padding: 6px 14px; border: 0; border-radius: 0;
  text-align: left; cursor: pointer;
}
.row:hover { background: var(--panel-2); }
.row img, .no-poster { grid-area: poster; width: 34px; height: 51px; border-radius: 4px; object-fit: cover; }
.no-poster { background: var(--panel-2); }
.name { grid-area: name; align-self: end; }
.info { grid-area: info; align-self: start; font-size: .8rem; color: var(--muted); }
</style>
