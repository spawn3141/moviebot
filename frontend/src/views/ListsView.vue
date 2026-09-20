<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { currentLists, currentState, lists, loadLists, showToast } from '../store'
import TitleCard from '../components/TitleCard.vue'

const SORTS = [
  ['list_added', 'Zuletzt gemerkt'],
  ['popularity', 'Beliebt'],
  ['rating', 'Beste Bewertung'],
  ['newest', 'Neueste zuerst'],
  ['title', 'Titel A–Z'],
]

const route = useRoute()
const router = useRouter()

const selectedId = ref(Number(route.query.id) || null)
const items = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const sort = ref('list_added')
const onlyAvailable = ref(false)
const showSeen = ref(true)
const newName = ref('')
const renaming = ref(false)
const renameValue = ref('')

const manualLists = computed(() => lists.items.filter((l) => l.kind === 'manual'))
const selected = computed(() => lists.items.find((l) => l.id === selectedId.value) ?? null)
// titles removed from this list in this session disappear right away
const visible = computed(() => items.value.filter((i) => currentLists(i).includes(selectedId.value)))

async function load() {
  if (!selectedId.value) return
  loading.value = true
  error.value = ''
  try {
    const data = await api.titles({
      list_id: selectedId.value, sort: sort.value, page_size: 200,
      only_available: onlyAvailable.value || undefined,
      show_seen: showSeen.value, show_not_interested: showSeen.value,
    })
    items.value = data.items
    total.value = data.total
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function select(id) {
  selectedId.value = id
  renaming.value = false
  router.replace({ query: { id } })
}

async function createList() {
  const name = newName.value.trim()
  if (!name) return
  try {
    const created = await api.createList(name)
    newName.value = ''
    await loadLists(true)
    select(created.id)
  } catch (e) {
    showToast(e.message)
  }
}

async function rename() {
  try {
    await api.updateList(selectedId.value, { name: renameValue.value })
    renaming.value = false
    await loadLists(true)
  } catch (e) {
    showToast(e.message)
  }
}

async function makeDefault() {
  await api.updateList(selectedId.value, { is_default: true }).catch((e) => showToast(e.message))
  await loadLists(true)
}

async function remove() {
  if (!window.confirm(`Liste „${selected.value.name}“ wirklich löschen? Die Titel selbst bleiben erhalten.`)) return
  try {
    await api.deleteList(selectedId.value)
    await loadLists(true)
    select(manualLists.value[0]?.id ?? null)
  } catch (e) {
    showToast(e.message)
  }
}

watch([selectedId, sort, onlyAvailable, showSeen], load)

onMounted(async () => {
  await loadLists(true)
  if (!selectedId.value || !manualLists.value.some((l) => l.id === selectedId.value)) {
    select(manualLists.value[0]?.id ?? null)
  }
  load()
})
</script>

<template>
  <div>
    <div class="head">
      <h1>Listen</h1>
      <form class="new" @submit.prevent="createList">
        <input v-model="newName" type="text" placeholder="Neue Liste …" maxlength="60" />
        <button type="submit" :disabled="!newName.trim()">Anlegen</button>
      </form>
    </div>

    <div class="tabs">
      <button v-for="l in manualLists" :key="l.id" type="button" class="tab"
              :class="{ on: l.id === selectedId }" @click="select(l.id)">
        {{ l.name }} <span class="count">{{ l.count }}</span>
        <span v-if="l.is_default" class="star" title="Standardliste">★</span>
      </button>
    </div>

    <template v-if="selected">
      <div class="bar">
        <template v-if="renaming">
          <form class="rename" @submit.prevent="rename">
            <input v-model="renameValue" type="text" maxlength="60" />
            <button type="submit">Speichern</button>
            <button type="button" @click="renaming = false">Abbrechen</button>
          </form>
        </template>
        <template v-else>
          <select v-model="sort" aria-label="Sortierung">
            <option v-for="[value, label] in SORTS" :key="value" :value="value">{{ label }}</option>
          </select>
          <label class="check"><input v-model="onlyAvailable" type="checkbox" /> nur verfügbare</label>
          <label class="check"><input v-model="showSeen" type="checkbox" /> gesehene zeigen</label>
          <span class="spacer" />
          <button type="button" @click="renaming = true; renameValue = selected.name">Umbenennen</button>
          <button v-if="!selected.is_default" type="button" @click="makeDefault">Als Standard</button>
          <button v-if="!selected.is_default" type="button" class="danger" @click="remove">Löschen</button>
        </template>
      </div>

      <p v-if="error" class="error">Fehler: {{ error }}</p>
      <p v-else-if="loading && !items.length" class="muted">Lädt …</p>
      <div v-else-if="!visible.length" class="empty">
        <p>Diese Liste ist leer.</p>
        <p class="muted">
          Merke Titel über das ☆ auf einer Kachel oder in der Detailansicht.
          <RouterLink to="/">Zum Stöbern</RouterLink>
        </p>
      </div>

      <div class="grid">
        <TitleCard v-for="item in visible" :key="item.id" :item="item" :list-id="selectedId"
                   :badge="item.available_on.length ? '' : 'nicht verfügbar'" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.head { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
h1 { font-size: 1.4rem; margin: 0; }
.new { display: flex; gap: 8px; }
.new input { background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0; }
.tab { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; }
.tab.on { background: var(--accent-2); border-color: var(--accent-2); color: #fff; }
.count { color: var(--muted); font-size: .8rem; }
.tab.on .count { color: rgba(255, 255, 255, .8); }
.star { color: var(--accent); }
.bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 14px; }
.rename { display: flex; gap: 8px; flex: 1; }
.rename input { flex: 1; max-width: 280px; background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.check { display: inline-flex; align-items: center; gap: 6px; color: var(--text-soft); }
.spacer { flex: 1; }
.danger { color: var(--danger); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(165px, 1fr)); gap: 14px; }
.empty { padding: 40px 0; text-align: center; }
.muted { color: var(--muted); }
.error { color: var(--danger); }
@media (max-width: 600px) {
  .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
  .spacer { display: none; }
}
</style>
