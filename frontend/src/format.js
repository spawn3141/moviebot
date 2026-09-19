export const MONETIZATION = {
  flatrate: 'Im Abo',
  free: 'Kostenlos',
  ads: 'Kostenlos mit Werbung',
  rent: 'Leihen',
  buy: 'Kaufen',
}

export const TV_STATUS = {
  'Returning Series': 'läuft',
  Ended: 'beendet',
  Canceled: 'abgesetzt',
  'In Production': 'in Produktion',
  Planned: 'geplant',
  Pilot: 'Pilot',
}

export function formatDate(iso, withWeekday = false) {
  if (!iso) return ''
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`)
  return d.toLocaleDateString('de-DE', {
    ...(withWeekday ? { weekday: 'short' } : {}),
    day: '2-digit', month: '2-digit', year: 'numeric',
  })
}

export function relativeDay(iso) {
  const today = new Date()
  const d = new Date(`${iso}T12:00:00`)
  const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000)
  if (days === 0) return 'Heute'
  if (days === 1) return 'Gestern'
  return formatDate(iso, true)
}

export function durationLabel(item) {
  if (item.media_type === 'tv') {
    const seasons = item.number_of_seasons
    const parts = []
    if (seasons) parts.push(`${seasons} ${seasons === 1 ? 'Staffel' : 'Staffeln'}`)
    if (item.runtime) parts.push(`${item.runtime} Min./Folge`)
    return parts.join(' · ')
  }
  if (!item.runtime) return ''
  const h = Math.floor(item.runtime / 60)
  const m = item.runtime % 60
  return h ? `${h} Std. ${m} Min.` : `${m} Min.`
}
