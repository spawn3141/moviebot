// Small shared UI state: open detail dialog, toast, and user state changes
// (so a rating given in the dialog updates the card in the list, too).
import { reactive } from 'vue'
import { api } from './api'

export const ui = reactive({ detailId: null, toast: null, pickerTitle: null })

// watchlists, loaded once and refreshed after changes
export const lists = reactive({ items: [], loaded: false })

export async function loadLists(force = false) {
  if (lists.loaded && !force) return lists.items
  lists.items = await api.lists()
  lists.loaded = true
  return lists.items
}

export function defaultList() {
  return lists.items.find((l) => l.is_default) ?? lists.items[0] ?? null
}

// title id -> list ids, for changes made in this session
export const titleLists = reactive({})

export function currentLists(item) {
  return titleLists[item.id] ?? item.in_lists ?? []
}

export async function setTitleLists(item, listIds) {
  try {
    titleLists[item.id] = await api.setTitleLists(item.id, listIds)
    await loadLists(true) // counts changed
    return titleLists[item.id]
  } catch (e) {
    showToast(`Speichern fehlgeschlagen: ${e.message}`)
    return currentLists(item)
  }
}

/** Quick button: add to / remove from one list (the default one, or the one being viewed). */
export async function toggleList(item, listId = null) {
  await loadLists()
  const target = listId ?? defaultList()?.id
  if (!target) return
  const before = currentLists(item)
  const on = before.includes(target)
  const after = await setTitleLists(item, on ? before.filter((id) => id !== target) : [...before, target])
  const name = lists.items.find((l) => l.id === target)?.name ?? 'Liste'
  showToast(
    on ? `„${item.title}“ aus „${name}“ entfernt` : `„${item.title}“ in „${name}“ gemerkt`,
    null,
    after.length || !on ? { label: 'Listen wählen', run: () => (ui.pickerTitle = item) } : null,
  )
}

// title id -> { status, rating } after a change in this session
export const userState = reactive({})

let toastTimer
export function showToast(message, undo = null, action = null) {
  ui.toast = { message, undo, action }
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (ui.toast = null), 6000)
}

export function currentState(item) {
  return userState[item.id] ?? item.user
}

function describe(title, state, changes) {
  if ('rating' in changes && state.rating) return `„${title}“ mit ${state.rating} ★ bewertet`
  if (state.status === 'seen') return `„${title}“ als gesehen markiert`
  if (state.status === 'not_interested') return `„${title}“ ausgeblendet`
  return null
}

export async function updateUserState(item, changes) {
  const before = { ...currentState(item) }
  try {
    const after = await api.setState(item.id, changes)
    userState[item.id] = after
    const message = describe(item.title, after, changes)
    if (message) {
      showToast(message, async () => {
        userState[item.id] = await api.setState(item.id, before)
        ui.toast = null
      })
    }
  } catch (e) {
    showToast(`Speichern fehlgeschlagen: ${e.message}`)
  }
}
