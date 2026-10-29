<script setup lang="ts">
// Cartão de reunião da biblioteca (am-card): título e metadado; gravando com ponto pulsante e
// cronômetro, processando com loader pequeno, falhou em danger.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { MeetingListItem } from "../api/types";
import { mmss, timeOfDay } from "../lib/format";
import { formatDuration, peopleLabel } from "../lib/meeting";
import AppLoader from "./ui/AppLoader.vue";

const props = defineProps<{ meeting: MeetingListItem; selected: boolean }>();

const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;
const live = computed(() => props.meeting.status === "recording");
watch(
  live,
  (on) => {
    clearInterval(timer);
    timer = undefined;
    if (on) {
      now.value = Date.now();
      timer = setInterval(() => (now.value = Date.now()), 1000);
    }
  },
  { immediate: true },
);
onBeforeUnmount(() => clearInterval(timer));

const meta = computed(() => {
  const m = props.meeting;
  const time = timeOfDay(m.started_at);
  if (m.status === "recording") {
    const start = Date.parse(m.started_at);
    const elapsed = Number.isNaN(start) ? 0 : Math.max(0, (now.value - start) / 1000);
    return `gravando · ${mmss(elapsed)}`;
  }
  if (m.status === "processing") return `${time} · processando…`;
  if (m.status === "failed") return `${time} · falhou`;
  return `${time} · ${formatDuration(m.duration_s)} · ${peopleLabel(m.speakers_count)}`;
});
</script>

<template>
  <button
    type="button"
    class="item card"
    :class="{ on: selected, live, failed: meeting.status === 'failed' }"
    :aria-current="selected ? 'page' : undefined"
  >
    <b class="title">{{ meeting.title || "Reunião sem título" }}</b>
    <span class="meta">
      <span v-if="live" class="rec-dot" aria-hidden="true" />
      <AppLoader v-else-if="meeting.status === 'processing'" size="sm" />
      <!-- A mini forma de onda da prévia entra aqui, antes do texto, quando a listagem do
           backend passar a expor picos de áudio. Sem esses dados ela seria inventada. -->
      {{ meta }}
    </span>
  </button>
</template>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
  padding: 14px;
  border-radius: var(--radius-lg);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background var(--duration-fast);
}
.card:hover {
  background: var(--selected);
}
.card.on {
  background: linear-gradient(160deg, var(--selected), transparent 140%);
  border-color: var(--selected-border);
}
.title {
  font-size: 14px;
  line-height: 19px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 12px;
  line-height: 16px;
  font-weight: 600;
  color: var(--text-muted);
}
.card.live .meta,
.card.failed .meta {
  color: var(--danger);
}
.rec-dot {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--danger);
  animation: pulse var(--duration-glow) ease-in-out infinite;
}
@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--danger-surface);
  }
  50% {
    box-shadow: 0 0 0 6px transparent;
  }
}
</style>
