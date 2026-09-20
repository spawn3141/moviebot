<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { currentLists, lists, loadLists, setTitleLists, showToast } from '../store'
import { api } from '../api'

const props = defineProps({ item: { type: Object, required: true } })
const emit = defineEmits(['close'])

const newName = ref('')
const busy = ref(false)
const selected = computed(() => currentLists(props.item))
const manualLists = computed(() => lists.items)

async function toggle(listId) {
  const ids = selected.value.includes(listId)
    ? selected.value.filter((id) => id !== listId)
    : [...selected.value, listId]
  await setTitleLists(props.item, ids)
}

async function createAndAdd() {
  const name = newName.value.trim()
  if (!name || busy.value) return
  busy.value = true
  try {
    const created = await api.createList(name)
    await loadLists(true)
    await setTitleLists(props.item, [...selected.value, created.id])
    newName.value = ''
  } catch (e) {
    showToast(e.message)
  } finally {
    busy.value = false
  }
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  loadLists()
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="picker" role="dialog" aria-modal="true" aria-label="Listen wählen">
      <h3>„{{ item.title }}“ merken</h3>
      <ul>
        <li v-for="l in manualLists" :key="l.id">
          <label>
            <input type="checkbox" :checked="selected.includes(l.id)" @change="toggle(l.id)" />
            <span class="name">{{ l.name }}</span>
            <span class="count">{{ l.count }}</span>
          </label>
        </li>
      </ul>
      <form class="new" @submit.prevent="createAndAdd">
        <input v-model="newName" type="text" placeholder="Neue Liste …" maxlength="60" />
        <button type="submit" :disabled="!newName.trim() || busy">Anlegen</button>
      </form>
      <button type="button" class="done" @click="emit('close')">Fertig</button>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed; inset: 0; z-index: 70; background: rgba(5, 6, 9, .6);
  display: grid; place-items: center; padding: 16px;
}
.picker {
  width: min(380px, 100%); background: var(--panel); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px;
}
h3 { margin: 0 0 12px; font-size: 1rem; }
ul { list-style: none; margin: 0 0 12px; padding: 0; max-height: 45vh; overflow-y: auto; }
li + li { margin-top: 2px; }
label {
  display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px;
  cursor: pointer; background: var(--panel-2);
}
.name { flex: 1; }
.count { color: var(--muted); font-size: .85rem; }
.new { display: flex; gap: 8px; }
.new input { flex: 1; background: var(--panel-2); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.done { width: 100%; margin-top: 12px; }
</style>
