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

// "FSK 12" for German ratings, "ab 12*" for ones converted from the US rating
export function ageLabel(item) {
  if (item.age_rating == null) return null
  if (item.age_rating_source === 'fsk') {
    return { short: `FSK ${item.age_rating}`, long: `FSK ${item.age_rating}` }
  }
  return {
    short: `ab ${item.age_rating}*`,
    long: `ab ${item.age_rating} (geschätzt aus US-Freigabe ${item.age_rating_raw})`,
  }
}

/** Short description of a dynamic list's filter, e.g. "Serien · Action · bis 12 Jahre". */
export function describeFilters(filters, serviceNames = {}) {
  if (!filters) return ''
  const parts = []
  if (filters.media_type) parts.push(filters.media_type === 'tv' ? 'Serien' : 'Filme')
  if (filters.genre?.length) parts.push(filters.genre.join(' / '))
  if (filters.services?.includes('all')) parts.push('alle Dienste')
  else if (filters.services?.includes('mine')) parts.push('meine Dienste')
  else if (filters.services?.length) parts.push(filters.services.map((k) => serviceNames[k] ?? k).join(', '))
  if (filters.include_free) parts.push('inkl. kostenlos')
  if (filters.max_age != null) {
    parts.push(`bis ${filters.max_age} Jahre${filters.include_unrated ? ' (auch ohne Angabe)' : ''}`)
  }
  if (filters.year_from || filters.year_to) parts.push(`${filters.year_from || '…'}–${filters.year_to || '…'}`)
  if (filters.min_rating) parts.push(`ab ★ ${filters.min_rating}`)
  if (filters.new_days) parts.push(`neu: ${filters.new_days} Tage`)
  if (filters.q) parts.push(`„${filters.q}“`)
  return parts.join(' · ')
}
