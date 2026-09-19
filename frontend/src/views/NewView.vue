<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { formatDate, relativeDay } from '../format'
import TitleCard from '../components/TitleCard.vue'

const days = ref(14)
const events = ref([])
const status = ref(null)
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    events.value = await api.newEvents({ days: days.value })
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function badge(e) {
  if (e.event === 'new_season') return `Staffel ${e.season_number}`
  const name = e.title.available_on.find((a) => a.service === e.service)?.name ?? e.service
  return e.event === 'readded' ? `Wieder bei ${name}` : `Neu bei ${name}`
}

// [{ date, events: [...] }], newest day first
const byDay = computed(() => {
  const groups = new Map()
  for (const e of events.value) {
    if (!groups.has(e.date)) groups.set(e.date, [])
    groups.get(e.date).push(e)
  }
  return [...groups.entries()].map(([date, list]) => ({ date, events: list }))
})

watch(days, load)
onMounted(async () => {
  load()
  status.value = await api.status()
})
</script>

<template>
  <div>
    <div class="head">
      <h1>Neu in deinen Diensten</h1>
      <select v-model.number="days" aria-label="Zeitraum">
        <option :value="7">letzte 7 Tage</option>
        <option :value="14">letzte 14 Tage</option>
        <option :value="30">letzte 30 Tage</option>
        <option :value="90">letzte 90 Tage</option>
      </select>
    </div>

    <p v-if="error" class="error">Fehler: {{ error }}</p>
    <p v-else-if="loading && !events.length" class="muted">Lädt …</p>
    <div v-else-if="!events.length" class="empty">
      <template v-if="status && !status.has_comparison">
        <p>Noch keine Neuzugänge erkennbar.</p>
        <p class="muted">
          Bisher gab es nur den ersten Abgleich<template v-if="status.first_snapshot">
          am {{ formatDate(status.first_snapshot) }}</template>. Neuzugänge ergeben sich aus dem
          Vergleich mit dem nächsten Abgleich<template v-if="status.schedule?.next_run">
          ({{ formatDate(status.schedule.next_run) }})</template>.
        </p>
      </template>
      <p v-else>In diesem Zeitraum ist nichts Neues dazugekommen.</p>
    </div>

    <section v-for="group in byDay" :key="group.date">
      <h2>{{ relativeDay(group.date) }}</h2>
      <div class="grid">
        <TitleCard v-for="e in group.events" :key="`${e.event}-${e.service}-${e.title.id}-${e.season_number}`"
                   :item="e.title" :badge="badge(e)" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.head { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
h1 { font-size: 1.4rem; margin: 0; }
h2 { font-size: 1rem; color: var(--text-soft); margin: 26px 0 10px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 14px; }
.empty { padding: 40px 0; text-align: center; }
.muted { color: var(--muted); }
.error { color: var(--danger); }
@media (max-width: 600px) {
  .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
}
</style>
