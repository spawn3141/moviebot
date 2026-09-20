<script setup>
import { ref } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: null },
  size: { type: String, default: 'small' }, // small | large
})
const emit = defineEmits(['update:modelValue'])
const hover = ref(0)

function pick(n) {
  // clicking the current rating again removes it
  emit('update:modelValue', props.modelValue === n ? null : n)
}
</script>

<template>
  <div class="stars" :class="size" role="radiogroup" aria-label="Bewertung" @mouseleave="hover = 0">
    <button
      v-for="n in 5"
      :key="n"
      type="button"
      role="radio"
      :aria-checked="modelValue === n"
      :aria-label="`${n} von 5 Sternen`"
      :title="modelValue === n ? 'Bewertung entfernen' : `${n} von 5`"
      :class="{ filled: n <= (hover || modelValue || 0), preview: hover }"
      @mouseenter="hover = n"
      @click.stop="pick(n)"
    >★</button>
  </div>
</template>

<style scoped>
.stars { display: inline-flex; }
.stars button {
  background: none; border: 0; padding: 0; cursor: pointer;
  color: var(--star-off); font-size: .95rem; line-height: 1;
}
.stars.large button { font-size: 1.6rem; padding: 0 2px; }
.stars button.filled { color: var(--accent); }
.stars button.filled.preview { color: var(--accent-soft); }
.stars button:focus-visible { outline: 2px solid var(--accent-2); border-radius: 4px; }
</style>
