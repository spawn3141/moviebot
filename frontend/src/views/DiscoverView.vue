<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { currentState, loadSavedFilters, savedFilters, showToast } from '../store'
import SavedFilterMenu from '../components/SavedFilterMenu.vue'
import TitleCard from '../components/TitleCard.vue'

const PAGE_SIZE = 48
const SORTS = [
  ['popularity', 'Beliebt'],
  ['rating', 'Beste Bewertung'],
  ['newest', 'Neueste zuerst'],
  ['added', 'Zuletzt dazugekommen'],
  ['title', 'Titel A–Z'],
]
const AGE_OPTIONS = [['', 'Alle'], ['0', 'bis 0 Jahre'], ['6', 'bis 6 Jahre'], ['12', 'bis 12 Jahre'], ['16', 'bis 16 Jahre']]
const NEW_OPTIONS = [['', 'Alle'], ['7', 'Neu: 7 Tage'], ['14', 'Neu: 14 Tage'], ['30', 'Neu: 30 Tage']]
const DEFAULTS = {
  media_type: '', genre: [], services: [], year_from: '', year_to: '', q: '',
  sort: 'popularity', new_days: '', show_seen: false, max_age: '', include_unrated: false,
}

const route = useRoute()
const router = useRouter()

// Filters live in the URL, so reloading or bookmarking keeps them.
const asArray = (v) => (v == null ? [] : Array.isArray(v) ? v : [v])
const f = reactive({
  ...DEFAULTS,
  ...Object.fromEntries(Object.entries(route.query).filter(([k]) => k in DEFAULTS)),
  genre: asArray(route.query.genre),
  services: asArray(route.query.services),
  show_seen: route.query.show_seen === 'true',
  include_unrated: route.query.include_unrated === 'true',
})
const search = ref(f.q)

const items = ref([])
const total = ref(0)
const searchedIn = ref([])
const page = ref(1)
const loading = ref(false)
const error = ref('')
const genres = ref([])
const services = ref([])
const showFilters = ref(false)
const status = ref(null)
const activeFilterId = ref(null)   // saved filter currently applied

const paidServices = computed(() => services.value.filter((s) => !s.free))
const freeServices = computed(() => services.value.filter((s) => s.free))
const serviceNames = computed(() => Object.fromEntries(services.value.map((s) => [s.key, s.name])))
const activeFilterCount = computed(() => {
  // the service selection is a filter too – except "Alle", which restricts nothing
  const services = f.services.includes('all') ? 0 : Math.max(f.services.length, 1)
  return ['media_type', 'year_from', 'year_to', 'new_days', 'max_age'].filter((k) => f[k] !== '').length
    + f.genre.length + services + (f.show_seen ? 1 : 0)
})

/** "in Prime Video, WOW" – but not a wall of 19 names. */
const searchedInText = computed(() => {
  const names = searchedIn.value.map((k) => serviceNames.value[k] ?? k)
  if (!names.length) return ''
  if (searchedIn.value.length >= services.value.length && services.value.length) return 'in allen Diensten'
  if (names.length > 3) return `in ${names.slice(0, 3).join(', ')} und ${names.length - 3} weiteren`
  return `in ${names.join(', ')}`
})

// Cards marked as seen / not interested disappear right away (undo via the toast).
const visible = computed(() =>
  items.value.filter((i) => {
    const s = currentState(i).status
    return s === 'unseen' || (s === 'seen' && f.show_seen)
  }))

let saveTimer
function saveFilters() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    api.updateSettings({ discover_filters: { ...f } }).catch(() => {})
  }, 600)
}

let requestId = 0
async function load(reset = true) {
  const id = ++requestId
  const nextPage = reset ? 1 : page.value + 1
  loading.value = true
  error.value = ''
  try {
    const params = { ...f, page: nextPage, page_size: PAGE_SIZE }
    if (f.max_age === '') params.include_unrated = false
    const data = await api.titles(params)
    if (id !== requestId) return // a newer request is on its way
    items.value = reset ? data.items : [...items.value, ...data.items]
    total.value = data.total
    searchedIn.value = data.services
    page.value = nextPage
  } catch (e) {
    if (id === requestId) error.value = e.message
  } finally {
    if (id === requestId) loading.value = false
  }
}

async function loadGenres() {
  genres.value = await api.genres({ media_type: f.media_type })
}

watch(f, () => {
  const query = Object.fromEntries(
    Object.entries(f).filter(([k, v]) => (Array.isArray(v) ? v.length : v !== DEFAULTS[k])))
  router.replace({ query })
  saveFilters()
  load()
}, { deep: true })

watch(() => f.media_type, loadGenres)

let searchTimer
watch(search, (value) => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => (f.q = value.trim()), 300)
})

const activeFilter = computed(() =>
  savedFilters.items.find((x) => x.id === activeFilterId.value) ?? null)

/** Has the user changed anything since applying the saved filter? */
const filterChanged = computed(() => {
  if (!activeFilter.value) return false
  return JSON.stringify(normalize(currentFilters())) !== JSON.stringify(normalize(activeFilter.value.filters))
})

function normalize(filters) {
  const withServices = { services: ['mine'], ...filters }  // "mine" is the implicit default
  return Object.fromEntries(Object.entries(withServices)
    .filter(([k, v]) => k !== 'sort' && v !== '' && v !== false && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => [k, Array.isArray(v) ? [...v].sort() : String(v)])
    .sort(([a], [b]) => a.localeCompare(b)))
}

function applyFilter(entry) {
  activeFilterId.value = entry.id
  // deep copy: otherwise editing the filter here would also change the stored one in memory
  const stored = JSON.parse(JSON.stringify(entry.filters))
  Object.assign(f, { ...DEFAULTS, genre: [], ...stored, services: servicesFromSaved(stored.services) })
  search.value = f.q
}

/** Everything that defines *which* titles are shown (not how they are sorted/paged). */
function currentFilters() {
  const keys = ['media_type', 'genre', 'services', 'year_from', 'year_to', 'q', 'max_age',
                'include_unrated', 'new_days']
  const filters = Object.fromEntries(
    keys.map((k) => [k, f[k]]).filter(([, v]) => (Array.isArray(v) ? v.length : v !== '' && v !== false)))
  // empty selection means "meine Dienste"; saved filters store that explicitly
  return { ...filters, services: f.services.length ? f.services : ['mine'], sort: f.sort }
}

// Worth saving / worth calling "unsaved": anything that actually narrows the view.
// "Alle Dienste" does not, and "Meine Dienste" is the app's default rather than a setup.
const canSave = computed(() => Object.entries(currentFilters()).some(([key, value]) =>
  key !== 'sort' && !(key === 'services' && value.includes('all'))))

/** A saved filter stores "mine"; in the UI that is the empty selection. */
function servicesFromSaved(services) {
  return !services || services.includes('mine') ? [] : services
}



function toggleService(key) {
  // picking a single service replaces "Alle"
  if (f.services.includes('all')) f.services = [key]
  else toggleIn(f.services, key)
}

function toggleIn(list, value) {
  const i = list.indexOf(value)
  if (i === -1) list.push(value)
  else list.splice(i, 1)
}

onBeforeUnmount(() => clearTimeout(saveTimer))

function reset() {
  // "keine Filter" means no restriction at all – including the service selection
  Object.assign(f, { ...DEFAULTS, genre: [], services: ['all'] })
  search.value = ''
  activeFilterId.value = null
}

onMounted(async () => {
  // A link with filters wins; otherwise restore what was used last.
  let restored = false
  if (!Object.keys(route.query).length) {
    const settings = await api.settings().catch(() => null)
    if (settings?.discover_filters) {
      Object.assign(f, { ...DEFAULTS, ...settings.discover_filters })
      search.value = f.q
      restored = true // the watcher on `f` loads and updates the URL
    }
  }
  if (!restored) load()
  loadGenres()
  services.value = await api.services()
  status.value = await api.status()
  await loadSavedFilters(true)
})
</script>

<template>
  <div class="discover">
    <div class="toolbar">
      <input v-model="search" type="search" class="search" placeholder="Titel suchen …" aria-label="Titel suchen" />
      <div class="segmented" role="group" aria-label="Film oder Serie">
        <button type="button" :class="{ on: f.media_type === '' }" @click="f.media_type = ''">Alles</button>
        <button type="button" :class="{ on: f.media_type === 'movie' }" @click="f.media_type = 'movie'">Filme</button>
        <button type="button" :class="{ on: f.media_type === 'tv' }" @click="f.media_type = 'tv'">Serien</button>
      </div>
      <select v-model="f.sort" aria-label="Sortierung">
        <option v-for="[value, label] in SORTS" :key="value" :value="value">{{ label }}</option>
      </select>
      <select v-model="f.new_days" aria-label="Nur Neuzugänge">
        <option v-for="[value, label] in NEW_OPTIONS" :key="value" :value="value">{{ label }}</option>
      </select>
      <button type="button" class="filter-toggle" :class="{ on: showFilters }" @click="showFilters = !showFilters">
        Filter<span v-if="activeFilterCount" class="count">{{ activeFilterCount }}</span>
      </button>
      <SavedFilterMenu :filters="currentFilters()" :active-id="activeFilterId" :changed="filterChanged"
                       :service-names="serviceNames" :can-save="canSave"
                       @apply="applyFilter" @clear="reset"
                       @saved="(id) => (activeFilterId = id)" />
    </div>

    <div v-show="showFilters" class="filters">
      <div class="filter-row">
        <span class="label">Genre</span>
        <div class="chips">
          <button v-for="g in genres" :key="g.name" type="button" class="chip"
                  :class="{ on: f.genre.includes(g.name) }" @click="toggleIn(f.genre, g.name)">
            {{ g.name }}
          </button>
        </div>
      </div>

      <div class="filter-row">
        <span class="label">Dienste</span>
        <div class="chips">
          <button type="button" class="chip" :class="{ on: f.services.includes('all') }"
                  title="Alle verfolgten Dienste, auch ohne Abo" @click="f.services = ['all']">
            Alle
          </button>
          <button type="button" class="chip" :class="{ on: !f.services.length }" @click="f.services = []">
            Meine Dienste
          </button>
          <button v-for="s in paidServices" :key="s.key" type="button" class="chip"
                  :class="{ on: f.services.includes(s.key), mine: s.subscribed }"
                  @click="toggleService(s.key)">{{ s.name }}</button>
          <button v-for="s in freeServices" :key="s.key" type="button" class="chip free"
                  :class="{ on: f.services.includes(s.key) }"
                  @click="toggleService(s.key)">{{ s.name }}</button>
        </div>
      </div>

      <div class="filter-row inline">
        <label>
          <span class="label">Altersfreigabe</span>
          <select v-model="f.max_age" aria-label="Altersfreigabe">
            <option v-for="[value, label] in AGE_OPTIONS" :key="value" :value="value">{{ label }}</option>
          </select>
        </label>
        <label v-if="f.max_age !== ''" class="check">
          <input v-model="f.include_unrated" type="checkbox" /> Titel ohne Angabe anzeigen
        </label>
      </div>

      <div class="filter-row inline">
        <label>
          <span class="label">Jahr von</span>
          <input v-model.lazy="f.year_from" type="number" inputmode="numeric" placeholder="z. B. 2025" />
        </label>
        <label>
          <span class="label">bis</span>
          <input v-model.lazy="f.year_to" type="number" inputmode="numeric" placeholder="z. B. 2026" />
        </label>
        <label class="check">
          <input v-model="f.show_seen" type="checkbox" /> Gesehene anzeigen
        </label>
        <button type="button" class="link" @click="reset">Alle Filter zurücksetzen</button>
      </div>
    </div>

    <p v-if="activeFilter && filterChanged" class="editing">
      <span>„{{ activeFilter.name }}“ wurde geändert.</span>
      <button type="button" class="link" @click="applyFilter(activeFilter)">Änderung verwerfen</button>
    </p>

    <p class="summary">
      <template v-if="!loading || items.length">
        {{ total }} Titel {{ searchedInText }}
      </template>
    </p>

    <p v-if="f.max_age !== '' && status && status.titles_ratings_checked < status.titles" class="hint">
      Altersfreigaben sind bisher erst für {{ status.titles_ratings_checked }} von {{ status.titles }}
      Titeln abgefragt (bekannt bei {{ status.titles_with_age_rating }}). Der Rest kommt mit den
      nächsten Abgleichen oder einem Nachlauf. * = aus US-Freigabe geschätzt.
    </p>
    <p v-else-if="f.max_age !== ''" class="hint">* = aus US-Freigabe geschätzt</p>

    <p v-if="error" class="error">Fehler: {{ error }}</p>
    <div v-else-if="!loading && !searchedIn.length" class="empty">
      Du hast noch keine Abos ausgewählt.
      <RouterLink to="/einstellungen">Zu den Einstellungen</RouterLink>
    </div>
    <div v-else-if="!loading && !visible.length" class="empty">
      <template v-if="f.new_days && status && !status.has_comparison">
        Noch keine Neuzugänge: Bisher gab es nur den ersten Abgleich. Sie ergeben sich ab dem
        nächsten Abgleich.
      </template>
      <template v-else>
        Nichts gefunden. <button type="button" class="link" @click="reset">Filter zurücksetzen</button>
      </template>
    </div>

    <div class="grid">
      <TitleCard v-for="item in visible" :key="item.id" :item="item" />
    </div>

    <div class="more">
      <button v-if="items.length < total" type="button" :disabled="loading" @click="load(false)">
        {{ loading ? 'Lädt …' : `Mehr laden (${total - items.length} weitere)` }}
      </button>
      <span v-else-if="loading" class="muted">Lädt …</span>
    </div>
  </div>
</template>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.search { flex: 1 1 220px; }
.segmented { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.segmented button { background: var(--panel); border: 0; color: var(--text-soft); padding: 8px 12px; cursor: pointer; }
.segmented button + button { border-left: 1px solid var(--border); }
.segmented button.on { background: var(--accent-2); color: #fff; }
.filter-toggle { display: inline-flex; align-items: center; gap: 6px; }
.filter-toggle.on { border-color: var(--accent-2); }
.count { background: var(--accent-2); color: #fff; border-radius: 999px; font-size: .72rem; padding: 1px 7px; }
.filters {
  margin-top: 10px; padding: 14px; background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius); display: grid; gap: 14px;
}
.filter-row { display: grid; gap: 6px; }
.filter-row.inline { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
.filter-row.inline input[type='number'] { width: 110px; }
.label { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); display: block; margin-bottom: 4px; }
.check { display: inline-flex; align-items: center; gap: 6px; padding-bottom: 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  background: var(--panel-2); border: 1px solid var(--border); color: var(--text-soft);
  padding: 5px 11px; border-radius: 999px; cursor: pointer; font-size: .85rem;
}
.chip.mine { border-color: var(--border-strong); color: var(--text); }
.chip.free { border-style: dashed; }
.chip.on { background: var(--accent-2); border-color: var(--accent-2); color: #fff; }
.summary { color: var(--muted); font-size: .9rem; margin: 14px 0 10px; min-height: 1.2em; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 14px; }
.more { display: flex; justify-content: center; margin: 24px 0 8px; }
.empty { padding: 40px 0; text-align: center; color: var(--muted); }
.error { color: var(--danger); }
.hint { color: var(--muted); font-size: .85rem; margin: -4px 0 12px; }
.editing { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 12px 0 0;
  padding: 8px 12px; border-radius: 8px; background: var(--panel-2);
  border: 1px solid var(--border-strong); font-size: .9rem; }
.muted { color: var(--muted); }
@media (max-width: 600px) {
  .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
  .toolbar select { flex: 1 1 40%; }
}
</style>
