<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import { showToast } from '../store'

const services = ref([])
const settings = ref({ include_free: false })
const status = ref(null)
const error = ref('')

const paid = computed(() => services.value.filter((s) => !s.free))
const free = computed(() => services.value.filter((s) => s.free))

async function load() {
  try {
    ;[services.value, settings.value, status.value] = await Promise.all([
      api.services(), api.settings(), api.status(),
    ])
  } catch (e) {
    error.value = e.message
  }
}

async function toggleService(s) {
  try {
    const updated = await api.setSubscribed(s.key, !s.subscribed)
    Object.assign(s, updated)
  } catch (e) {
    showToast(`Konnte nicht speichern: ${e.message}`)
  }
}

async function setSeriesNewest(value) {
  try {
    settings.value = await api.updateSettings({ series_newest: value })
  } catch (e) {
    showToast(`Konnte nicht speichern: ${e.message}`)
  }
}

async function toggleFree() {
  try {
    settings.value = await api.updateSettings({ include_free: !settings.value.include_free })
  } catch (e) {
    showToast(`Konnte nicht speichern: ${e.message}`)
  }
}

let pollTimer
function pollWhileRunning() {
  clearTimeout(pollTimer)
  if (!status.value?.schedule?.running) return
  pollTimer = setTimeout(async () => {
    const wasRunning = status.value.schedule.running
    status.value = await api.status()
    if (wasRunning && !status.value.schedule.running) {
      showToast(status.value.schedule.last_error
        ? `Abgleich mit Fehlern beendet: ${status.value.schedule.last_error}`
        : 'Abgleich fertig')
      services.value = await api.services()
    }
    pollWhileRunning()
  }, 5000)
}

async function startSnapshot() {
  try {
    await api.startSnapshot()
    status.value = await api.status()
    pollWhileRunning()
  } catch (e) {
    showToast(e.message)
  }
}

onBeforeUnmount(() => clearTimeout(pollTimer))

function formatTimestamp(iso) {
  return iso ? new Date(iso).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' }) : 'noch nie'
}

onMounted(async () => {
  await load()
  pollWhileRunning()
})
</script>

<template>
  <div class="settings">
    <h1>Einstellungen</h1>
    <p v-if="error" class="error">Fehler: {{ error }}</p>

    <section>
      <h2>Meine Abos</h2>
      <p class="muted">Gezeigt und empfohlen wird nur, was in diesen Diensten im Abo läuft.</p>
      <ul class="list">
        <li v-for="s in paid" :key="s.key">
          <label class="switch-row">
            <span class="name">{{ s.name }}</span>
            <span class="counts">{{ s.movies }} Filme · {{ s.series }} Serien</span>
            <input type="checkbox" class="switch" :checked="s.subscribed" @change="toggleService(s)" />
          </label>
        </li>
      </ul>
    </section>

    <section>
      <h2>Kostenlose Angebote</h2>
      <ul class="list">
        <li>
          <label class="switch-row">
            <span class="name">Kostenlose Angebote einbeziehen</span>
            <span class="counts">Mediatheken und Dienste mit Werbung – kein Abo nötig</span>
            <input type="checkbox" class="switch" :checked="settings.include_free" @change="toggleFree" />
          </label>
        </li>
      </ul>
      <p class="muted small">{{ free.map((s) => `${s.name} (${s.movies + s.series})`).join(' · ') }}</p>
    </section>

    <section>
      <h2>Sortierung</h2>
      <p class="muted">Was bei „Neueste zuerst“ für eine Serie zählt:</p>
      <ul class="list">
        <li>
          <label class="switch-row">
            <span class="name">Start der neuesten Staffel</span>
            <span class="counts">Serien mit neuer Staffel stehen weit oben</span>
            <input type="radio" name="series-newest" :checked="settings.series_newest === 'season'"
                   @change="setSeriesNewest('season')" />
          </label>
        </li>
        <li>
          <label class="switch-row">
            <span class="name">Start der Serie</span>
            <span class="counts">wie bei Filmen: das ursprüngliche Erscheinungsdatum</span>
            <input type="radio" name="series-newest" :checked="settings.series_newest === 'first'"
                   @change="setSeriesNewest('first')" />
          </label>
        </li>
      </ul>
    </section>

    <section v-if="status">
      <h2>Datenstand</h2>
      <dl class="facts">
        <dt>Automatischer Abgleich</dt>
        <dd v-if="!status.schedule">aus (Server ohne Zeitplan gestartet)</dd>
        <dd v-else-if="!status.schedule.time">aus</dd>
        <dd v-else>täglich um {{ status.schedule.time }} Uhr</dd>
        <template v-if="status.schedule?.next_run">
          <dt>Nächster Abgleich</dt><dd>{{ formatTimestamp(status.schedule.next_run) }}</dd>
        </template>
        <dt>Letzter Abgleich</dt><dd>{{ formatTimestamp(status.last_snapshot) }}</dd>
        <dt>Titel</dt><dd>{{ status.titles }}</dd>
        <dt>mit vollständiger Angebotsliste</dt><dd>{{ status.titles_with_offers }}</dd>
      </dl>
      <p v-if="status.schedule" class="actions">
        <button type="button" :disabled="status.schedule.running" @click="startSnapshot">
          {{ status.schedule.running ? 'Abgleich läuft … (dauert einige Minuten)' : 'Jetzt abgleichen' }}
        </button>
      </p>
      <p v-if="status.schedule?.last_error" class="error">Letzter Abgleich: {{ status.schedule.last_error }}</p>
      <p v-if="status.failed_since_last_snapshot.length" class="error">
        Fehlgeschlagen: {{ status.failed_since_last_snapshot.map((r) => `${r.service}/${r.media_type}`).join(', ') }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.settings { max-width: 680px; }
h1 { font-size: 1.4rem; margin: 0 0 8px; }
h2 { font-size: 1rem; margin: 28px 0 4px; }
.muted { color: var(--muted); margin: 0 0 10px; }
.small { font-size: .85rem; margin-top: 8px; }
.error { color: var(--danger); }
.list { list-style: none; padding: 0; margin: 0; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.list li + li { border-top: 1px solid var(--border); }
.switch-row {
  display: grid; grid-template-columns: 1fr auto; grid-template-areas: 'name switch' 'counts switch';
  align-items: center; gap: 2px 12px; padding: 12px 14px; background: var(--panel); cursor: pointer;
}
.name { grid-area: name; }
.counts { grid-area: counts; font-size: .8rem; color: var(--muted); }
.switch { grid-area: switch; }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 8px 0 0; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; }
.actions { margin-top: 14px; }
</style>
