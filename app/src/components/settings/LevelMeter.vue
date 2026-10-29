<script setup lang="ts">
// Medidor horizontal de nível em dBFS (am-meter): ícone num círculo, trilha em pílula e o valor
// em números tabulares. A barra cobre de -60 a 0 dB (abaixo disso já é silêncio; o piso do
// backend é -90). Usado na conferência de áudio das configurações e na reunião gravando.
import AppIcon from "../ui/AppIcon.vue";
import { computed } from "vue";

const FLOOR_DB = -60;

const props = defineProps<{
  /** Nome do ícone de áudio. */
  icon: string;
  label: string;
  /** Nível em dBFS (≤ -90 = sem sinal). */
  db: number;
}>();

const percent = computed(() => {
  const clamped = Math.min(0, Math.max(FLOOR_DB, props.db));
  return Math.round(((clamped - FLOOR_DB) / -FLOOR_DB) * 100);
});
const level = computed(() => (props.db > -3 ? "hot" : props.db > -12 ? "loud" : "ok"));
const text = computed(() => (props.db <= -90 ? "sem sinal" : `${Math.round(props.db)} dB`));
</script>

<template>
  <div class="meter">
    <span class="icon" aria-hidden="true"><AppIcon :name="icon" :size="16" /></span>
    <div
      class="track"
      role="meter"
      :aria-label="label"
      aria-valuemin="-90"
      aria-valuemax="0"
      :aria-valuenow="Math.round(db)"
    >
      <div class="bar" :class="level" :style="{ width: percent + '%' }" data-test="meter-bar"></div>
    </div>
    <span class="value">{{ text }}</span>
  </div>
</template>

<style scoped>
.meter {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.icon {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--glass-strong);
  color: var(--text-muted);
}
.track {
  flex: 1;
  min-width: 0;
  height: 10px;
  border-radius: var(--radius-pill);
  background: var(--glass-strong);
  overflow: hidden;
}
.bar {
  height: 100%;
  border-radius: var(--radius-pill);
  transition: width 90ms linear;
}
.bar.ok {
  background: var(--success);
}
.bar.loud {
  background: var(--warning);
}
.bar.hot {
  background: var(--danger);
}
.value {
  width: 72px;
  flex-shrink: 0;
  text-align: right;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
@media (prefers-reduced-motion: reduce) {
  .bar {
    transition: none;
  }
}
</style>
