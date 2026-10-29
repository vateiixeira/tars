<script setup lang="ts">
// Player da reunião (am-player): pílula fixa no rodapé do painel com o botão redondo, a trilha de
// progresso e o tempo atual / duração. O `<audio>` fica escondido; este componente só controla.
// Quem baixa o áudio é o pai: sem nada carregado, o botão pede `load` (reunião inteira). Um trecho
// (`end` definido) começa em `start` da reunião, e o tempo mostrado é sempre o da reunião.
// `progress` informa o tempo atual (em segundos da reunião) e se está tocando, a cada
// `timeupdate`: é o dado para marcar a fala que está tocando na transcrição.
import { computed, ref, watch } from "vue";
import { mmss } from "../lib/format";
import AppIcon from "./ui/AppIcon.vue";
import AppLoader from "./ui/AppLoader.vue";

export interface Playback {
  /** Segundos desde o início da reunião. */
  at: number;
  playing: boolean;
}

const props = defineProps<{
  src: string | null;
  /** Início do áudio carregado, em segundos da reunião (0 para a reunião inteira). */
  start: number;
  /** Fim do trecho carregado; `null` quando é a reunião inteira. */
  end: number | null;
  /** Duração da reunião, até o `<audio>` informar a dele. */
  total: number;
  loading: boolean;
  disabled: boolean;
}>();
const emit = defineEmits<{ load: []; progress: [state: Playback | null] }>();

const audioEl = ref<HTMLAudioElement | null>(null);
const current = ref(0);
const mediaDuration = ref<number | null>(null);
const playing = ref(false);

// Outro áudio (ou outro trecho): volta ao começo.
watch(
  [() => props.src, () => props.start, () => props.end],
  () => {
    current.value = 0;
    mediaDuration.value = null;
    playing.value = false;
  },
);

const length = computed(() => {
  if (mediaDuration.value !== null) return mediaDuration.value;
  return Math.max(0, (props.end ?? props.total) - props.start);
});
const at = computed(() => props.start + current.value);
const shownTotal = computed(() => props.start + length.value);
const percent = computed(() => (length.value > 0 ? Math.min(100, (current.value / length.value) * 100) : 0));
const isClip = computed(() => props.src !== null && props.end !== null);

watch([() => props.src, at, playing], () => emit("progress", props.src ? { at: at.value, playing: playing.value } : null));

function onDuration(): void {
  const d = audioEl.value?.duration;
  // alguns formatos chegam sem duração (Infinity) até o fim: fica a estimativa
  mediaDuration.value = d !== undefined && Number.isFinite(d) && d > 0 ? d : null;
}
function onTime(): void {
  current.value = audioEl.value?.currentTime ?? 0;
}

/** Toca o que estiver carregado (um autoplay barrado não vira erro na tela). */
async function play(): Promise<void> {
  // jsdom não implementa `play`
  await audioEl.value?.play?.()?.catch(() => {});
}
function pause(): void {
  audioEl.value?.pause?.();
}
function toggle(): void {
  if (!props.src) emit("load");
  else if (playing.value) pause();
  else void play();
}
function seek(event: Event): void {
  const value = Number((event.target as HTMLInputElement).value);
  if (audioEl.value) audioEl.value.currentTime = value;
  current.value = value;
}

const buttonLabel = computed(() =>
  props.loading ? "Carregando áudio…" : !props.src ? "Ouvir áudio da reunião" : playing.value ? "Pausar" : "Tocar",
);

defineExpose({ play, pause });
</script>

<template>
  <div class="meeting-player" role="group" aria-label="Áudio da reunião">
    <button
      type="button"
      class="play-btn"
      :aria-label="buttonLabel"
      :title="buttonLabel"
      :aria-busy="loading || undefined"
      :disabled="loading || (disabled && !src)"
      @click="toggle"
    >
      <AppLoader v-if="loading" size="sm" tone="on-accent" />
      <AppIcon v-else :name="playing ? 'pause' : 'play'" :size="15" />
    </button>
    <input
      class="track"
      type="range"
      min="0"
      :max="length"
      step="0.1"
      :value="current"
      :style="{ '--pct': `${percent}%` }"
      aria-label="Posição no áudio"
      :aria-valuetext="`${mmss(at)} de ${mmss(shownTotal)}`"
      :disabled="!src"
      @input="seek"
    />
    <span class="time">{{ mmss(at) }} / {{ mmss(shownTotal) }}</span>
    <button
      v-if="isClip"
      type="button"
      class="btn ghost btn-sm whole"
      :disabled="loading || disabled"
      @click="emit('load')"
    >
      Reunião inteira
    </button>
    <audio
      v-if="src"
      ref="audioEl"
      class="player"
      :src="src"
      aria-label="Áudio da reunião"
      hidden
      @loadedmetadata="onDuration"
      @durationchange="onDuration"
      @timeupdate="onTime"
      @seeked="onTime"
      @play="playing = true"
      @pause="playing = false"
      @ended="playing = false"
    />
  </div>
</template>

<style scoped>
.meeting-player {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-shrink: 0;
  margin: var(--space-2) var(--page-gutter) var(--space-4);
  padding: var(--space-1) var(--space-4) var(--space-1) var(--space-1);
  border-radius: var(--radius-pill);
  background: var(--glass);
  border: 1px solid var(--glass-border);
}
.play-btn {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 50%;
  color: var(--on-accent);
  background: linear-gradient(135deg, var(--accent-grad-from), var(--accent-grad-to));
  box-shadow: var(--shadow-highlight);
  cursor: pointer;
}
.play-btn:hover:not(:disabled) {
  filter: brightness(1.08);
}
.play-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.play-btn[aria-busy="true"] {
  opacity: 1;
  cursor: progress;
}
/* Trilha simples: o trecho já tocado em accent-glow sobre a cor de onda, sem forma inventada. */
.track {
  flex: 1;
  min-width: 0;
  height: 24px;
  margin: 0;
  background: transparent;
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
}
.track:disabled {
  cursor: default;
}
.track::-webkit-slider-runnable-track {
  height: 6px;
  border-radius: var(--radius-pill);
  background:
    linear-gradient(var(--accent-glow), var(--accent-glow)) 0 / var(--pct, 0%) 100% no-repeat,
    var(--wave);
}
.track::-moz-range-track {
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--wave);
}
.track::-moz-range-progress {
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--accent-glow);
}
.track::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  margin-top: -4px;
  border: 0;
  border-radius: 50%;
  background: var(--text-accent);
  box-shadow: var(--shadow-highlight);
}
.track::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border: 0;
  border-radius: 50%;
  background: var(--text-accent);
}
.track:disabled::-webkit-slider-thumb {
  visibility: hidden;
}
.track:disabled::-moz-range-thumb {
  visibility: hidden;
}
.track:focus-visible {
  outline: none;
}
.track:focus-visible::-webkit-slider-thumb {
  box-shadow: 0 0 0 3px var(--focus-ring);
}
.track:focus-visible::-moz-range-thumb {
  box-shadow: 0 0 0 3px var(--focus-ring);
}
.time {
  flex-shrink: 0;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.whole {
  flex-shrink: 0;
  margin-right: calc(-1 * var(--space-2));
}
@container detail (max-width: 900px) {
  .meeting-player {
    margin: var(--space-2) var(--space-6) var(--space-4);
  }
}
@container detail (max-width: 560px) {
  .meeting-player {
    margin: var(--space-2) var(--space-5) var(--space-4);
  }
}
</style>
