// Small shared UI state: open detail dialog, toast, and user state changes
// (so a rating given in the dialog updates the card in the list, too).
import { reactive } from 'vue'
import { api } from './api'

export const ui = reactive({ detailId: null, toast: null })

// title id -> { status, rating } after a change in this session
export const userState = reactive({})

let toastTimer
export function showToast(message, undo = null) {
  ui.toast = { message, undo }
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
