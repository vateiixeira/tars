<script setup lang="ts">
import { computed } from "vue";

// Skeleton só no primeiro carregamento. `label` só quando não há texto visível.
const props = withDefaults(defineProps<{ lines?: number; label?: string }>(), { lines: 3, label: undefined });

const BODY_WIDTHS = [100, 85, 92, 70];

const widths = computed(() => {
  const n = Math.max(0, Math.floor(props.lines));
  return Array.from({ length: n }, (_, i) => (n > 1 && i === n - 1 ? 60 : BODY_WIDTHS[i % BODY_WIDTHS.length]));
});
</script>

<template>
  <div class="skeleton" :role="label ? 'status' : undefined" :aria-hidden="label ? undefined : 'true'">
    <span v-for="(w, i) in widths" :key="i" class="skel" :style="{ width: `${w}%` }" aria-hidden="true" />
    <span v-if="label" class="sr-only">{{ label }}</span>
  </div>
</template>

<style scoped>
.skeleton {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}
.skel {
  display: block;
  height: 12px;
  border-radius: var(--radius-pill);
  background: linear-gradient(90deg, var(--glass) 0%, var(--selected) 50%, var(--glass) 100%);
  background-size: 200% 100%;
  animation: shim 1.4s linear infinite;
}
@keyframes shim {
  to {
    background-position: -200% 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .skel {
    animation: none;
  }
}
</style>
