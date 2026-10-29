<script setup lang="ts">
// Reunião em gravação: orbe pulsando, cronômetro, os medidores de microfone e áudio do PC e os
// alertas ao vivo. Níveis com mais de 3 s (ou sem conexão) contam como desatualizados: os
// medidores zeram e o texto diz que está aguardando, em vez de congelar o último valor.
import { computed, onBeforeUnmount, ref } from "vue";
import { useStatus } from "../composables/useStatus";
import { mmss } from "../lib/format";
import { LEVELS_STALE_MS } from "../lib/levels";
import AppBanner from "./ui/AppBanner.vue";
import AppIcon from "./ui/AppIcon.vue";
import GlowOrb from "./ui/GlowOrb.vue";
import LevelMeter from "./settings/LevelMeter.vue";

const props = defineProps<{ startedAt: string }>();

const { connected, alerts, levels } = useStatus();

const now = ref(Date.now());
const timer = setInterval(() => (now.value = Date.now()), 500);
onBeforeUnmount(() => clearInterval(timer));

const elapsed = computed(() => {
  const start = Date.parse(props.startedAt);
  return Number.isNaN(start) ? 0 : Math.max(0, (now.value - start) / 1000);
});
const fresh = computed(() =>
  connected.value && levels.value && now.value - levels.value.at < LEVELS_STALE_MS ? levels.value : null,
);
const SILENT_DB = -90; // piso do backend: o medidor mostra "sem sinal"
</script>

<template>
  <div class="recording">
    <GlowOrb :size="96"><AppIcon name="mic" /></GlowOrb>
    <div>
      <h2><span class="sr-only">Gravando há</span><span aria-hidden="true">Gravando · </span>{{ mmss(elapsed) }}</h2>
      <p class="hint">Microfone e áudio do computador. A transcrição completa aparece após o processamento.</p>
    </div>
    <div class="meters">
      <LevelMeter icon="mic" label="Microfone" :db="fresh?.mic_db ?? SILENT_DB" />
      <LevelMeter icon="volume" label="Áudio do PC" :db="fresh?.pc_db ?? SILENT_DB" />
      <p v-if="!fresh" class="stale">Aguardando níveis de áudio recentes.</p>
    </div>
    <AppBanner v-for="alert in alerts" :key="alert.kind" tone="warn" class="alert" role="status">
      {{ alert.message }}
    </AppBanner>
    <p class="hint">Use Parar, na barra superior, para encerrar a reunião.</p>
  </div>
</template>

<style scoped>
.recording {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-5);
  padding: var(--space-8) var(--page-gutter);
  text-align: center;
}
h2 {
  margin: 0;
  font-size: 26px;
  line-height: 32px;
  font-weight: 700;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
}
.hint {
  max-width: 55ch;
  margin: var(--space-2) auto 0;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 600;
}
.meters {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  width: min(420px, 100%);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  text-align: left;
}
.stale {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
}
/* Alerta ao vivo (AppBanner, tom de aviso). */
.alert {
  width: min(560px, 100%);
  text-align: left;
}
</style>
