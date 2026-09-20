<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { MONETIZATION, TV_STATUS, ageLabel, durationLabel, formatDate } from '../format'
import { currentLists, currentState, lists, loadLists, setTitleLists, ui, updateUserState } from '../store'
import StarRating from './StarRating.vue'

const props = defineProps({ id: { type: Number, required: true } })
const emit = defineEmits(['close'])

const title = ref(null)
const error = ref('')
const dialog = ref(null)

async function load() {
  title.value = null
  error.value = ''
  try {
    title.value = await api.title(props.id)
  } catch (e) {
    error.value = e.message
  }
}
watch(() => props.id, load, { immediate: true })

const state = computed(() => (title.value ? currentState(title.value) : null))

// newest season that has already started
const latestSeason = computed(() => {
  const today = new Date().toISOString().slice(0, 10)
  const released = (title.value?.seasons ?? []).filter((s) => s.air_date && s.air_date <= today)
  return released.length ? released[released.length - 1] : null
})

// Offers grouped by kind, e.g. { "Leihen": ["Apple TV", "Amazon Video"] }
const offerGroups = computed(() => {
  if (!title.value) return []
  const groups = new Map()
  for (const kind of Object.keys(MONETIZATION)) groups.set(kind, new Set())
  for (const o of title.value.offers) {
    if (!groups.has(o.monetization)) groups.set(o.monetization, new Set())
    groups.get(o.monetization).add(o.provider.trim())
  }
  return [...groups.entries()]
    .filter(([, names]) => names.size)
    .map(([kind, names]) => ({ label: MONETIZATION[kind] ?? kind, names: [...names] }))
})

function toggle(status) {
  updateUserState(title.value, { status: state.value.status === status ? 'unseen' : status })
}

function onKey(e) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  loadLists()
  document.addEventListener('keydown', onKey)
  document.body.style.overflow = 'hidden'
  dialog.value?.focus()
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.body.style.overflow = ''
})
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div ref="dialog" class="dialog" role="dialog" aria-modal="true" tabindex="-1"
         :aria-label="title?.title ?? 'Details'">
      <button type="button" class="close" aria-label="Schließen" @click="emit('close')">✕</button>

      <p v-if="error" class="error">Konnte nicht geladen werden: {{ error }}</p>
      <p v-else-if="!title" class="loading">Lädt …</p>

      <template v-else>
        <div class="head">
          <img v-if="title.poster_url" :src="title.poster_url" :alt="title.title" class="poster" />
          <div class="head-text">
            <h2>{{ title.title }}</h2>
            <p v-if="title.original_title && title.original_title !== title.title" class="original">
              {{ title.original_title }}
            </p>
            <p class="meta">
              <span>{{ title.media_type === 'tv' ? 'Serie' : 'Film' }}</span>
              <span v-if="title.year">{{ title.year }}</span>
              <span v-if="durationLabel(title)">{{ durationLabel(title) }}</span>
              <span v-if="title.tv_status">{{ TV_STATUS[title.tv_status] ?? title.tv_status }}</span>
              <span v-if="ageLabel(title)" class="age">{{ ageLabel(title).long }}</span>
            </p>
            <p class="dates">
              <template v-if="title.media_type === 'movie'">
                <span v-if="title.release_date">Erschienen: {{ formatDate(title.release_date) }}</span>
              </template>
              <template v-else>
                <span v-if="title.release_date">Erste Folge: {{ formatDate(title.release_date) }}</span>
                <span v-if="latestSeason">
                  Neueste Staffel: {{ latestSeason.name || `Staffel ${latestSeason.season_number}` }}
                  seit {{ formatDate(latestSeason.air_date) }}
                </span>
                <span v-if="title.last_air_date">Letzte Folge: {{ formatDate(title.last_air_date) }}</span>
              </template>
            </p>
            <p class="genres">{{ title.genres.join(' · ') }}</p>
            <p v-if="title.vote_average" class="tmdb-score">
              ★ {{ title.vote_average.toFixed(1) }} <small>TMDB, {{ title.vote_count }} Stimmen</small>
            </p>

            <div class="my">
              <span class="label">Meine Bewertung</span>
              <StarRating size="large" :model-value="state.rating"
                          @update:model-value="(r) => updateUserState(title, { rating: r })" />
              <div class="buttons lists">
                <button v-for="l in lists.items" :key="l.id"
                        type="button" :class="{ on: currentLists(title).includes(l.id) }"
                        @click="setTitleLists(title, currentLists(title).includes(l.id)
                          ? currentLists(title).filter((id) => id !== l.id)
                          : [...currentLists(title), l.id])">
                  {{ currentLists(title).includes(l.id) ? '★' : '☆' }} {{ l.name }}
                </button>
                <button type="button" @click="ui.pickerTitle = title">+ Neue Liste</button>
              </div>
              <div class="buttons">
                <button type="button" :class="{ on: state.status === 'seen' }" @click="toggle('seen')">
                  ✓ Gesehen
                </button>
                <button type="button" :class="{ on: state.status === 'not_interested' }"
                        @click="toggle('not_interested')">✕ Nicht interessiert</button>
              </div>
            </div>
          </div>
        </div>

        <section v-if="title.available_on.length">
          <h3>In deinen Diensten</h3>
          <ul class="chips">
            <li v-for="a in title.available_on" :key="a.service"
                :title="a.baseline ? 'Beim ersten Abgleich gefunden – war vermutlich schon länger da' : 'Von uns an diesem Tag entdeckt'">
              {{ a.name }}
              <small>
                · seit {{ formatDate(a.since) }}<template v-if="a.baseline"> (erster Abgleich)</template>
              </small>
            </li>
          </ul>
        </section>

        <section v-if="title.overview">
          <h3>Handlung</h3>
          <p class="overview">{{ title.overview }}</p>
        </section>

        <section v-if="title.directors.length || title.cast.length" class="people">
          <div v-if="title.directors.length">
            <h3>{{ title.media_type === 'tv' ? 'Idee' : 'Regie' }}</h3>
            <p>{{ title.directors.join(', ') }}</p>
          </div>
          <div v-if="title.cast.length">
            <h3>Besetzung</h3>
            <p>{{ title.cast.slice(0, 8).join(', ') }}</p>
          </div>
        </section>

        <section v-if="title.media_type === 'tv' && title.seasons.length">
          <h3>Staffeln</h3>
          <ul class="seasons">
            <li v-for="s in title.seasons" :key="s.season_number">
              <strong>{{ s.name || `Staffel ${s.season_number}` }}</strong>
              <span>{{ s.episode_count ? `${s.episode_count} Folgen` : '' }}</span>
              <span>{{ s.air_date ? formatDate(s.air_date) : 'Termin offen' }}</span>
            </li>
          </ul>
          <p v-if="title.next_episode_date" class="hint">
            Nächste Folge: {{ formatDate(title.next_episode_date) }}
          </p>
        </section>

        <section>
          <h3>Wo es das gibt</h3>
          <template v-if="title.offers_known">
            <dl v-if="offerGroups.length" class="offers">
              <template v-for="g in offerGroups" :key="g.label">
                <dt>{{ g.label }}</dt>
                <dd>{{ g.names.join(', ') }}</dd>
              </template>
            </dl>
            <p v-else class="hint">Keine Angebote in Deutschland bekannt.</p>
          </template>
          <p v-else class="hint">Die vollständige Angebotsliste für diesen Titel wurde noch nicht geladen.</p>
          <p class="links">
            <a v-if="title.watch_link" :href="title.watch_link" target="_blank" rel="noopener">Alle Angebote (JustWatch) ↗</a>
            <a v-if="title.imdb_url" :href="title.imdb_url" target="_blank" rel="noopener">Auf IMDb ansehen ↗</a>
            <a :href="title.tmdb_url" target="_blank" rel="noopener">Auf TMDB ansehen ↗</a>
          </p>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed; inset: 0; z-index: 50; background: rgba(5, 6, 9, .72);
  display: flex; justify-content: center; align-items: flex-start;
  overflow-y: auto; padding: 40px 16px;
}
.dialog {
  position: relative; width: min(820px, 100%); background: var(--panel);
  border: 1px solid var(--border); border-radius: 14px; padding: 24px; outline: none;
}
.close {
  position: absolute; top: 12px; right: 12px; width: 34px; height: 34px; border-radius: 50%;
  background: var(--panel-2); border: 1px solid var(--border); color: var(--text); cursor: pointer;
}
.head { display: flex; gap: 20px; }
.poster { width: 180px; border-radius: 10px; align-self: flex-start; flex-shrink: 0; }
.head-text { min-width: 0; }
h2 { margin: 0 40px 2px 0; font-size: 1.5rem; line-height: 1.2; }
.original { margin: 0 0 6px; color: var(--muted); font-style: italic; }
.meta { display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 6px 0; color: var(--text-soft); font-size: .9rem; }
.meta .age { border: 1px solid var(--border-strong); border-radius: 6px; padding: 0 6px; }
.dates { display: flex; flex-direction: column; gap: 2px; margin: 0 0 8px; color: var(--text-soft); font-size: .85rem; }
.genres { margin: 0 0 6px; color: var(--muted); font-size: .9rem; }
.tmdb-score { margin: 0 0 14px; color: var(--accent); font-weight: 600; }
.tmdb-score small { color: var(--muted); font-weight: 400; }
.my { background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
.my .label { display: block; font-size: .8rem; color: var(--muted); margin-bottom: 4px; }
.buttons { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.buttons button {
  background: var(--panel); border: 1px solid var(--border); color: var(--text);
  padding: 7px 12px; border-radius: 8px; cursor: pointer;
}
.buttons button.on { background: var(--accent-2); border-color: var(--accent-2); color: #fff; }
section { margin-top: 20px; }
h3 { font-size: .8rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 0 0 6px; }
.overview { line-height: 1.55; margin: 0; }
.people { display: grid; grid-template-columns: 1fr 2fr; gap: 16px; }
.people p { margin: 0; line-height: 1.5; }
.chips { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.chips li { background: var(--panel-2); border: 1px solid var(--border); padding: 4px 10px; border-radius: 999px; font-size: .88rem; }
.chips small { color: var(--muted); }
.seasons { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }
.seasons li { display: grid; grid-template-columns: 1fr auto auto; gap: 12px; font-size: .9rem; padding: 6px 0; border-bottom: 1px solid var(--border); }
.seasons span { color: var(--muted); }
.offers { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0; font-size: .9rem; }
.offers dt { color: var(--muted); }
.offers dd { margin: 0; }
.hint { color: var(--muted); font-size: .9rem; }
.links { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 12px; }
.loading, .error { padding: 40px 0; text-align: center; color: var(--muted); }
.error { color: var(--danger); }
@media (max-width: 600px) {
  .backdrop { padding: 0; }
  .dialog { border-radius: 0; min-height: 100%; padding: 18px; }
  .head { flex-direction: column; }
  .poster { width: 130px; }
  .people { grid-template-columns: 1fr; }
}
</style>
