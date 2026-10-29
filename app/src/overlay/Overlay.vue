<script setup lang="ts">
// Pílula do overlay: indica que a gravação (ou o ditado) está rolando e se o áudio está chegando.
// A janela só fica visível gravando, ditando ou avisando que a gravação foi interrompida (o
// backend reiniciou no meio dela); parada, ela é escondida.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import AppIcon from "../components/ui/AppIcon.vue";
import BrandOrb from "../components/ui/BrandOrb.vue";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { AlertKind, TrackSource } from "../api/types";
import { INTERRUPTED_MESSAGE, useStatus } from "../composables/useStatus";
import { mmss } from "../lib/format";
import { BAR_SHAPE, levelBars } from "../lib/levels";

const { status, alerts, levels, meetingState, dictationActive, connected, interrupted } = useStatus();

const BAR_PX = 10; // altura máxima das barras (a escala dB e a expiração ficam em lib/levels)
const CONNECTION_LOST = "Sem conexão com o serviço do ditado";
const SHOW_RETRY_MS = 1000; // `show_overlay` falhou: tenta de novo, dobrando a espera até 10 s
const SHOW_RETRY_MAX_MS = 10000;

const now = ref(Date.now()); // relógio da pílula (atualiza a cada segundo enquanto visível)

const recording = computed(() => meetingState.value === "recording");
// gravando, conexão caída vira alerta (a gravação pode continuar no backend); ditando, a pílula
// some: sem eventos não há como saber quando o ditado acabou, e ela ficaria presa em "Ditando"
const dictating = computed(() => dictationActive.value && connected.value);
// a pílula não pode simplesmente sumir quando a gravação acabou sem o usuário pedir (spec §5)
const wasInterrupted = computed(() => !recording.value && interrupted.value !== null);
const visible = computed(() => recording.value || wasInterrupted.value || dictating.value);

interface PillAlert {
  message: string;
  /** Medidor a mostrar junto (null = nenhum). */
  source: TrackSource | null;
}

function sourceOf(kind: AlertKind): TrackSource {
  return kind.startsWith("mic") ? "mic" : "pc";
}

// só faz sentido gravando. Conexão caída vence: o último status pode estar velho e a pílula não
// pode continuar com cara de "tudo certo". Senão, o alerta mais recente ainda ativo.
const alert = computed<PillAlert | null>(() => {
  if (wasInterrupted.value) return { message: INTERRUPTED_MESSAGE, source: null };
  if (!recording.value) return null;
  if (!connected.value) return { message: CONNECTION_LOST, source: null };
  const last = alerts.value[alerts.value.length - 1];
  return last ? { message: last.message, source: sourceOf(last.kind) } : null;
});

// em alerta mostra só o medidor da fonte com problema; normal, os dois
const meters = computed<TrackSource[]>(() => {
  if (!alert.value) return ["mic", "pc"];
  return alert.value.source ? [alert.value.source] : [];
});

function bars(source: TrackSource): number[] | null {
  return levelBars(levels.value, source, now.value, BAR_PX);
}

// o backend manda ISO local com microssegundos; corta em milissegundos (o JS não garante mais)
function parseStart(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso.replace(/(\.\d{3})\d+/, "$1"));
  return Number.isNaN(ms) ? null : ms;
}

let timer: ReturnType<typeof setInterval> | null = null;
let retry: ReturnType<typeof setTimeout> | null = null;

function cancelRetry(): void {
  if (retry) clearTimeout(retry);
  retry = null;
}

// o Rust mostra e reaplica posição, "sempre por cima" e clique atravessando; se falhar (a janela
// pode ter ficado escondida), tenta de novo enquanto a pílula deve aparecer
function showWindow(delay = SHOW_RETRY_MS): void {
  invoke("show_overlay").catch((e) => {
    console.error("Falha ao mostrar o overlay; tentando de novo", e);
    if (!visible.value) return;
    cancelRetry();
    retry = setTimeout(() => {
      retry = null;
      if (visible.value) showWindow(Math.min(delay * 2, SHOW_RETRY_MAX_MS));
    }, delay);
  });
}

const elapsed = computed(() => {
  const start = parseStart(status.value?.meeting.started_at);
  return start === null ? "00:00" : mmss((now.value - start) / 1000);
});

watch(
  visible,
  (on) => {
    if (on) {
      now.value = Date.now();
      timer ??= setInterval(() => (now.value = Date.now()), 1000);
      cancelRetry();
      showWindow();
    } else {
      if (timer) clearInterval(timer);
      timer = null;
      cancelRetry();
      getCurrentWindow()
        .hide()
        .catch((e) => console.error("Falha ao esconder o overlay", e));
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  cancelRetry();
});
</script>

<template>
  <div
    v-if="visible"
    data-test="pill"
    class="pill"
    :class="{ alert: alert, dictating: !recording && !alert }"
    :style="{ opacity: 1 }"
  >
    <!-- o ponto diz "gravando": não aparece no ditado puro nem no aviso de gravação interrompida -->
    <span v-if="recording" class="dot" :class="{ blink: dictating }"></span>
    <template v-if="recording">
      <span data-test="timer" class="time">{{ elapsed }}</span>
      <span v-for="source in meters" :key="source" class="m" :data-test="`meter-${source}`">
        <AppIcon :name="source === 'mic' ? 'mic' : 'volume'" :size="13" />
        <span class="bars" :class="{ off: !bars(source) }">
          <i v-for="(h, n) in bars(source) ?? BAR_SHAPE" :key="n" :style="bars(source) ? { height: `${h}px` } : {}"></i>
        </span>
      </span>
      <span v-if="alert" data-test="alert" class="alert-text">{{ alert.message }}</span>
    </template>
    <span v-else-if="alert" data-test="alert" class="alert-text">{{ alert.message }}</span>
    <template v-else>
      <BrandOrb :size="22" class="orb" />
      <span>{{ status?.dictation.source === "pc" ? "Transcrevendo áudio do PC" : "Ditando" }}</span>
    </template>
  </div>
</template>

<style>
html,
body {
  margin: 0;
  background: transparent;
  overflow: hidden;
  user-select: none;
}

/* pílula no canto superior direito da janela (a janela já fica a 16 px das bordas da tela;
   o GTK pode deixá-la mais alta que o pedido, o resto é transparente) */
#overlay {
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
}

.pill {
  max-width: calc(100vw - 4px);
  height: 34px;
  box-sizing: border-box;
  padding: 0 var(--space-4) 0 10px;
  border-radius: var(--radius-pill);
  /* opaca: a janela é transparente e qualquer translucidez vazaria; sem sombra, que a janela cortaria */
  background: var(--surface-raised);
  border: 1px solid var(--glass-border);
  color: var(--text);
  font: 700 13px var(--font-round);
  display: flex;
  gap: 10px;
  align-items: center;
  white-space: nowrap;
}

.pill.dictating {
  padding-left: 6px;
}

.pill.alert {
  /* contorno de aviso sobre a superfície opaca (o tom translúcido nunca fica sozinho na janela) */
  box-shadow: inset 0 0 0 1px var(--warning);
}

.time {
  font-variant-numeric: tabular-nums;
}

.dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--danger);
  animation: pulse var(--duration-glow) ease-in-out infinite;
}

/* gravando e ditando ao mesmo tempo: anel de destaque ao redor do ponto */
.dot.blink {
  outline: 2px solid var(--accent-glow);
  outline-offset: 2px;
}

/* o brilho do orbe (--shadow-orb) vazaria para a janela transparente e seria cortado: só o anel pulsa */
.pill.dictating .orb {
  box-shadow: 0 0 0 0 var(--selected-border);
  animation: glow var(--duration-glow) ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--danger-surface);
  }
  50% {
    box-shadow: 0 0 0 5px transparent;
  }
}

@keyframes glow {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--selected-border);
  }
  50% {
    box-shadow: 0 0 0 5px transparent;
  }
}

.m {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-muted);
}

.bars {
  display: flex;
  gap: 2px;
  align-items: flex-end;
  height: 12px;
}

.bars i {
  width: 3px;
  background: var(--success);
  border-radius: 2px;
  transition: height var(--duration-fast) var(--ease-out);
}

.bars.off i {
  background: var(--text-muted);
  height: 3px;
}

.alert-text {
  /* sem isto o item flex não encolhe abaixo do texto e as reticências nunca aparecem */
  min-width: 0;
  color: var(--warning);
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>

<style>
@media (prefers-reduced-motion: reduce) {
  .pill *,
  .pill {
    animation: none !important;
    transition: none !important;
  }
}
</style>
