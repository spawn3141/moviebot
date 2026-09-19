<script setup>
import { computed } from 'vue'
import { currentState, ui, updateUserState } from '../store'
import StarRating from './StarRating.vue'

const props = defineProps({
  item: { type: Object, required: true },
  badge: { type: String, default: '' },
})

const state = computed(() => currentState(props.item))
const services = computed(() => props.item.available_on.map((a) => a.name).join(' · '))

function toggle(status) {
  updateUserState(props.item, { status: state.value.status === status ? 'unseen' : status })
}
</script>

<template>
  <article class="card" :class="{ dimmed: state.status !== 'unseen' }" tabindex="0"
           @click="ui.detailId = item.id" @keydown.enter="ui.detailId = item.id">
    <div class="poster">
      <img v-if="item.poster_url" :src="item.poster_url" :alt="item.title" loading="lazy" />
      <div v-else class="poster-fallback">{{ item.title }}</div>
      <span class="kind">{{ item.media_type === 'tv' ? 'Serie' : 'Film' }}</span>
      <span v-if="item.vote_average" class="score">★ {{ item.vote_average.toFixed(1) }}</span>
      <span v-if="badge" class="badge">{{ badge }}</span>
    </div>
    <div class="card-body">
      <h3 :title="item.title">{{ item.title }}</h3>
      <p class="meta">{{ item.year || '–' }}<template v-if="item.genres.length"> · {{ item.genres.slice(0, 2).join(', ') }}</template></p>
      <p class="services" :title="services">{{ services }}</p>
      <div class="actions" @click.stop>
        <button type="button" class="icon" :class="{ on: state.status === 'seen' }"
                :title="state.status === 'seen' ? 'Doch nicht gesehen' : 'Gesehen'"
                @click="toggle('seen')">✓</button>
        <StarRating :model-value="state.rating" @update:model-value="(r) => updateUserState(item, { rating: r })" />
        <button type="button" class="icon" :class="{ on: state.status === 'not_interested' }"
                :title="state.status === 'not_interested' ? 'Wieder anzeigen' : 'Nicht interessiert'"
                @click="toggle('not_interested')">✕</button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
  overflow: hidden; cursor: pointer; display: flex; flex-direction: column;
  transition: transform .12s ease, border-color .12s ease;
}
.card:hover, .card:focus-visible { transform: translateY(-2px); border-color: var(--border-strong); outline: none; }
.card.dimmed { opacity: .6; }
.poster { position: relative; aspect-ratio: 2 / 3; background: var(--panel-2); }
.poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
.poster-fallback {
  position: absolute; inset: 0; display: grid; place-items: center; padding: 12px;
  text-align: center; color: var(--muted); font-weight: 600;
}
.kind, .score, .badge {
  position: absolute; font-size: .72rem; font-weight: 600; padding: 2px 7px; border-radius: 999px;
  background: rgba(10, 12, 16, .82); color: var(--text); backdrop-filter: blur(4px);
}
.kind { top: 8px; left: 8px; }
.score { top: 8px; right: 8px; color: var(--accent); }
.badge { bottom: 8px; left: 8px; background: var(--accent-2); color: #fff; }
.card-body { padding: 10px 10px 8px; display: flex; flex-direction: column; gap: 3px; flex: 1; }
h3 { margin: 0; font-size: .95rem; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta, .services { margin: 0; font-size: .78rem; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.services { color: var(--text-soft); }
.actions { display: flex; align-items: center; justify-content: space-between; margin-top: auto; padding-top: 6px; }
.icon {
  background: var(--panel-2); border: 1px solid var(--border); color: var(--muted);
  width: 30px; height: 30px; border-radius: 8px; cursor: pointer; font-size: .9rem;
}
.icon:hover { color: var(--text); border-color: var(--border-strong); }
.icon.on { background: var(--accent-2); border-color: var(--accent-2); color: #fff; }
</style>
