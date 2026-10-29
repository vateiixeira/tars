<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import type { Segment, Speaker } from "../api/types";
import { useStatus } from "../composables/useStatus";
import { mmss } from "../lib/format";
import { speakerName, speakerSamples } from "../lib/meeting";
import AppIcon from "./ui/AppIcon.vue";

const props = defineProps<{ meetingId: string; speaker: Speaker; segments: Segment[] }>();
const emit = defineEmits<{ play: [] }>();
const { client } = useStatus();
const samples = computed(() => speakerSamples(props.segments, props.speaker.id));
const audio = ref<HTMLAudioElement | null>(null);
const url = ref<string | null>(null);
const loading = ref<number | null>(null);
const selected = ref<number | null>(null);
const error = ref<string | null>(null);
let requestSeq = 0;

function stop(): void {
  ++requestSeq;
  audio.value?.pause();
  if (url.value) URL.revokeObjectURL(url.value);
  url.value = null;
  loading.value = null;
  selected.value = null;
  error.value = null;
}

async function play(sample: Segment): Promise<void> {
  stop();
  const seq = requestSeq;
  loading.value = sample.id;
  selected.value = sample.id;
  emit("play");
  try {
    const blob = await client.audioBlob(props.meetingId, sample.start_s, sample.end_s, sample.source);
    if (seq !== requestSeq) return;
    url.value = URL.createObjectURL(blob);
    await nextTick();
    if (seq !== requestSeq) return;
    // Se o navegador bloquear autoplay, os controles nativos continuam disponíveis.
    await audio.value?.play()?.catch(() => {});
  } catch (e) {
    if (seq === requestSeq) error.value = e instanceof Error ? e.message : "Não foi possível carregar o trecho.";
  } finally {
    if (seq === requestSeq) loading.value = null;
  }
}

watch(() => [props.meetingId, props.speaker.id], stop);
onBeforeUnmount(stop);
</script>

<template>
  <section class="speaker-samples" :aria-label="`Trechos de ${speakerName(speaker)}`">
    <p class="hint">Ouvir esta pessoa</p>
    <div v-if="samples.length" class="samples">
      <button
        v-for="(sample, index) in samples"
        :key="sample.id"
        type="button"
        class="sample"
        :class="{ selected: selected === sample.id }"
        :disabled="loading === sample.id"
        :aria-label="`Ouvir trecho ${index + 1} de ${speakerName(speaker)} em ${mmss(sample.start_s)}`"
        :title="sample.text"
        @click="play(sample)"
      >
        <AppIcon name="play" :size="12" />{{ mmss(sample.start_s) }}
      </button>
    </div>
    <p v-else class="hint">Nenhum trecho disponível para ouvir.</p>
    <p v-if="loading !== null" class="hint" role="status">Carregando trecho…</p>
    <audio
      v-if="url"
      ref="audio"
      class="sample-player"
      :src="url"
      :aria-label="`Áudio de ${speakerName(speaker)}`"
      controls
      @error="error = 'Não foi possível reproduzir o trecho. Tente novamente.'"
    ></audio>
    <p v-if="error" class="sample-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.speaker-samples {
  padding: var(--space-1) 10px var(--space-3);
  margin-bottom: var(--space-1);
  border-bottom: 1px solid var(--glass-border);
}
.hint {
  margin: 0 0 6px;
  padding-top: var(--space-1);
  font-size: 11px;
  line-height: 14px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.hint[role="status"],
.hint:not(:first-child) {
  letter-spacing: normal;
  text-transform: none;
  font-size: 12px;
}
.samples {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
/* Trecho: pílula de vidro com o instante; o selecionado leva o realce. */
.sample {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: var(--control-sm);
  padding: 0 var(--space-3);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-pill);
  background: var(--glass);
  color: var(--text);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition: background var(--duration-fast);
}
.sample svg {
  color: var(--text-accent);
}
.sample:hover:not(:disabled) {
  background: var(--selected);
}
.sample.selected {
  background: var(--selected);
  border-color: var(--selected-border);
  color: var(--text-accent);
}
.sample:disabled {
  opacity: 0.6;
  cursor: wait;
}
.sample-player {
  display: block;
  width: 100%;
  height: 36px;
  margin-top: var(--space-2);
}
.sample-error {
  margin: 6px 0 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--danger);
}
@media (prefers-reduced-motion: reduce) {
  .sample {
    transition: none;
  }
}
</style>
