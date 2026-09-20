<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import { describeFilters } from '../format'
import { loadSavedFilters, savedFilters, showToast } from '../store'

const props = defineProps({
  filters: { type: Object, required: true },   // current filters on the page
  activeId: { type: Number, default: null },
  changed: { type: Boolean, default: false },  // current filters differ from the saved one
  serviceNames: { type: Object, default: () => ({}) },
  canSave: { type: Boolean, default: false },
})
const emit = defineEmits(['apply', 'clear', 'saved'])

const open = ref(false)
const busy = ref(false)
const root = ref(null)
const active = computed(() => savedFilters.items.find((x) => x.id === props.activeId) ?? null)
// no saved filter selected but something is set: the current filter is simply unsaved
const unsaved = computed(() => !active.value && props.canSave)
const label = computed(() => {
  if (active.value) return active.value.name
  return unsaved.value ? 'Ungespeicherter Filter' : 'Gespeicherte Filter'
})

function apply(entry) {
  emit('apply', entry)
  open.value = false
}

async function run(action) {
  busy.value = true
  try {
    await action()
    await loadSavedFilters(true)
  } catch (e) {
    showToast(e.message)
  } finally {
    busy.value = false
  }
}

async function overwrite() {
  await run(async () => {
    await api.updateSavedFilter(active.value.id, { filters: props.filters })
    showToast(`Filter „${active.value.name}“ gespeichert`)
  })
  open.value = false
}

async function saveAsNew() {
  const name = window.prompt('Name des Filters:', '')
  if (!name?.trim()) return
  await run(async () => {
    const created = await api.createSavedFilter(name.trim(), props.filters)
    emit('saved', created.id)
    showToast(`Filter „${created.name}“ gespeichert`)
  })
  open.value = false
}

async function rename() {
  const name = window.prompt('Filter umbenennen:', active.value.name)
  if (!name?.trim()) return
  await run(() => api.updateSavedFilter(active.value.id, { name: name.trim() }))
}

async function remove() {
  if (!window.confirm(`Filter „${active.value.name}“ löschen?`)) return
  await run(async () => {
    await api.deleteSavedFilter(active.value.id)
    emit('clear')  // the deleted filter is no longer applied
  })
  open.value = false
}

function onOutside(e) {
  if (open.value && root.value && !root.value.contains(e.target)) open.value = false
}
function onKey(e) {
  if (e.key === 'Escape') open.value = false
}
onMounted(() => {
  loadSavedFilters()
  document.addEventListener('click', onOutside)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onOutside)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="wrap">
    <button type="button" class="trigger" :class="{ on: open || active }"
            :aria-expanded="open" @click="open = !open">
      {{ label }}<span v-if="changed" class="dot" title="geändert">•</span>
      <span class="caret">▾</span>
    </button>

    <div v-if="open" class="menu" role="menu">
      <ul>
        <li>
          <button type="button" class="entry" :class="{ on: !activeId && !unsaved }"
                  @click="emit('clear'); open = false">
            <span class="name">Kein Filter</span>
            <span class="desc">alle Titel, ohne Einschränkung</span>
          </button>
        </li>
        <li v-if="unsaved">
          <div class="entry on current">
            <span class="name">Ungespeicherter Filter</span>
            <span class="desc">{{ describeFilters(filters, serviceNames) || 'eigene Einstellung' }}</span>
          </div>
        </li>
        <li v-for="entry in savedFilters.items" :key="entry.id">
          <button type="button" class="entry" :class="{ on: entry.id === activeId }"
                  @click="apply(entry)">
            <span class="name">{{ entry.name }}</span>
            <span class="count" :title="entry.error || ''">{{ entry.error ? '!' : entry.count }}</span>
            <span class="desc">{{ describeFilters(entry.filters, serviceNames) }}</span>
          </button>
        </li>
      </ul>

      <p v-if="!savedFilters.items.length" class="empty">
        Noch keine gespeicherten Filter gespeichert.
      </p>

      <div class="actions">
        <button v-if="active && changed" type="button" :disabled="busy" @click="overwrite">
          „{{ active.name }}“ überschreiben
        </button>
        <button v-if="canSave" type="button" :disabled="busy" @click="saveAsNew">
          Aktuellen Filter speichern …
        </button>
        <template v-if="active">
          <button type="button" :disabled="busy" @click="rename">Umbenennen</button>
          <button type="button" class="danger" :disabled="busy" @click="remove">Löschen</button>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
.trigger { display: inline-flex; align-items: center; gap: 6px; }
.trigger.on { border-color: var(--accent-2); }
.caret { color: var(--muted); font-size: .8rem; }
.dot { color: var(--accent); }
.menu {
  position: absolute; right: 0; top: calc(100% + 6px); z-index: 30; width: min(340px, 90vw);
  background: var(--panel); border: 1px solid var(--border-strong); border-radius: 10px;
  padding: 8px; box-shadow: 0 12px 30px rgba(0, 0, 0, .45);
}
ul { list-style: none; margin: 0 0 8px; padding: 0; max-height: 50vh; overflow-y: auto; }
.entry {
  display: grid; grid-template-columns: 1fr auto; width: 100%; text-align: left;
  background: none; border: 0; border-radius: 8px; padding: 8px 10px; cursor: pointer;
}
.entry:hover { background: var(--panel-2); }
.entry.on { background: var(--accent-2); color: #fff; }
.entry.current { cursor: default; }
.name { font-weight: 600; }
.count { color: var(--muted); font-size: .8rem; }
.entry.on .count, .entry.on .desc { color: rgba(255, 255, 255, .85); }
.desc { grid-column: 1 / -1; color: var(--muted); font-size: .8rem; margin-top: 2px; }
.actions { display: grid; gap: 4px; border-top: 1px solid var(--border); padding-top: 8px; }
.actions button { text-align: left; background: none; border: 0; padding: 7px 10px; border-radius: 8px; }
.actions button:hover { background: var(--panel-2); }
.actions .danger { color: var(--danger); }
.empty { color: var(--muted); font-size: .85rem; padding: 10px; margin: 0; }
</style>
