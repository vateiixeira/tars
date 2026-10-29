<script setup lang="ts">
// Cápsula de gravação no centro da barra superior: inicia e para a reunião (com cronômetro, mini
// medidores e app seguido). Enquanto o backend processa a reunião anterior (ou um "Reprocessar")
// o botão continua iniciando uma nova: reuniões em sequência são comuns e o trabalho da GPU vai
// para a fila.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { INTERRUPTED_MESSAGE, useStatus } from "../composables/useStatus";
import AppButton from "./ui/AppButton.vue";
import AppIcon from "./ui/AppIcon.vue";
import AppLoader from "./ui/AppLoader.vue";
import { mmss } from "../lib/format";
import { BAR_SHAPE, levelBars } from "../lib/levels";

const emit = defineEmits<{ started: [meetingId: string]; stopped: [meetingId: string] }>();
const { client, connected, connection, status, meetingState, interrupted, levels } = useStatus();

const MINI_BAR_PX = 12; // altura máxima das barras dos mini medidores

const busy = ref(false);
const error = ref<string | null>(null);
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;

const recording = computed(() => meetingState.value === "recording");
const processing = computed(() => meetingState.value === "processing");
const followedApp = computed(() => status.value?.meeting.followed_app ?? null);
const elapsed = computed(() => {
  const startedAt = status.value?.meeting.started_at;
  const start = startedAt ? Date.parse(startedAt) : NaN;
  return Number.isNaN(start) ? 0 : Math.max(0, (now.value - start) / 1000);
});
// Sem conexão o último status pode estar velho: o rótulo diz isso em vez de "tudo certo".
const offlineLabel = computed(() =>
  connected.value ? null : connection.value === "open" ? "Conectando…" : "Sem conexão com o serviço",
);

const followLabel = computed(
  () => offlineLabel.value ?? (followedApp.value ? `Seguindo: ${followedApp.value}` : "Saída padrão do sistema"),
);

const meters = [
  { source: "mic", icon: "mic", label: "Microfone" },
  { source: "pc", icon: "volume", label: "Áudio do PC" },
] as const;
// Alturas por fonte, ou null sem sinal recente (o relógio de 1 s também expira níveis velhos).
const bars = computed(() => {
  const at = (source: "mic" | "pc") =>
    connected.value ? levelBars(levels.value, source, now.value, MINI_BAR_PX) : null;
  return { mic: at("mic"), pc: at("pc") };
});

watch(
  recording,
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

// O backend reiniciou no meio da gravação e voltou parado: explica por que o botão voltou a
// "Iniciar" (fica até a próxima ação, como um erro).
watch(interrupted, (info) => {
  if (info) error.value = INTERRUPTED_MESSAGE;
});

async function toggle(): Promise<void> {
  if (busy.value) return; // chamada em voo: clique duplo não manda outro start/stop
  busy.value = true;
  error.value = null;
  try {
    if (recording.value) {
      const { meeting_id } = await client.stopMeeting();
      emit("stopped", meeting_id);
    } else {
      const { meeting_id } = await client.startMeeting();
      emit("started", meeting_id);
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="record-wrap">
    <div
      class="capsule"
      :class="{ 'is-recording': recording, offline: !connected }"
      role="group"
      aria-label="Gravação de reunião"
    >
      <template v-if="recording">
        <span class="rec-dot" aria-hidden="true" />
        <span class="sr-only">Gravando há</span>
        <span class="time">{{ mmss(elapsed) }}</span>
        <span
          v-for="m in meters"
          :key="m.source"
          class="mini-meter"
          :class="m.source"
          role="img"
          :aria-label="`${m.label}: ${bars[m.source] ? 'sinal recebido' : 'sem sinal recente'}`"
        >
          <AppIcon :name="m.icon" :size="13" />
          <span class="mini" :class="{ off: !bars[m.source] }">
            <!-- sem sinal: barras baixas e cinzas (alturas da forma, só para o desenho) -->
            <i
              v-for="(h, n) in bars[m.source] ?? BAR_SHAPE"
              :key="n"
              :style="bars[m.source] ? { height: `${h}px` } : {}"
            />
          </span>
        </span>
        <span class="label follow" :title="followLabel">{{ followLabel }}</span>
      </template>
      <span v-else class="label">
        <AppLoader v-if="processing && connected" size="sm" />
        <AppIcon v-else-if="offlineLabel" name="warning" :size="15" />
        {{ offlineLabel ?? (processing ? "Processando a anterior…" : "microfone + áudio do PC") }}
      </span>
      <AppButton
        :variant="recording ? 'danger' : 'primary'"
        class="rec"
        :class="{ recording }"
        :busy="busy"
        :disabled="!connected"
        @click="toggle"
      >
        <template v-if="recording"><span class="stop" aria-hidden="true" />Parar</template>
        <template v-else><AppIcon name="mic" :size="18" />Iniciar reunião</template>
      </AppButton>
    </div>
    <div v-if="error" class="error-pop">
      <p class="error" role="alert">{{ error }}</p>
      <button type="button" class="dismiss" aria-label="Dispensar aviso" @click="error = null">
        <AppIcon name="close" :size="15" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.record-wrap {
  position: relative;
  display: flex;
  justify-content: center;
  min-width: 0;
}
.capsule {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  max-width: 100%;
  padding: 6px 6px 6px var(--space-4);
  border-radius: var(--radius-pill);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  /* blur só aqui: a cápsula não tem descendentes fixed (Ruling R2) */
  -webkit-backdrop-filter: blur(var(--blur-bar));
  backdrop-filter: blur(var(--blur-bar));
  transition: padding var(--duration-base) var(--ease-out);
}
.capsule.is-recording {
  padding-left: 14px;
  border-color: var(--danger-surface);
}
.label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
  color: var(--text-muted);
}
.label.follow {
  display: block;
}
.capsule.offline .label {
  color: var(--warning);
}
.time {
  flex-shrink: 0;
  font-weight: 700;
  font-size: 16px;
  font-variant-numeric: tabular-nums;
}
.rec-dot {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--danger);
  animation: pulse var(--duration-glow) ease-in-out infinite;
}
.mini-meter {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
  color: var(--text-muted);
}
.mini {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 12px;
}
.mini i {
  width: 3px;
  height: 3px;
  border-radius: 2px;
  background: var(--success);
  transition: height var(--duration-fast) var(--ease-out);
}
.mini.off i {
  height: 3px;
  background: var(--text-muted);
}
.rec {
  flex-shrink: 0;
  height: 44px;
  min-height: 44px;
  padding: 0 22px;
}
.stop {
  width: 10px;
  height: 10px;
  border-radius: 3px;
  background: currentColor;
}
.error-pop {
  position: absolute;
  top: calc(100% + var(--space-2));
  z-index: 20;
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  width: max-content;
  max-width: min(420px, 90vw);
  padding: var(--space-2) var(--space-2) var(--space-2) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--glass-border);
  background: var(--surface-solid);
  box-shadow: var(--shadow-popover);
}
.error {
  margin: 6px 0;
  color: var(--danger);
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
}
.dismiss {
  display: grid;
  place-items: center;
  width: var(--control-sm);
  height: var(--control-sm);
  flex-shrink: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-muted);
}
.dismiss:hover {
  background: var(--selected);
  color: var(--text);
}
@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 var(--danger-surface);
  }
  50% {
    box-shadow: 0 0 0 7px transparent;
  }
}
@media (max-width: 1023px) {
  .capsule {
    gap: var(--space-2);
  }
  .rec {
    padding: 0 var(--space-4);
  }
}
@media (prefers-reduced-motion: reduce) {
  .rec-dot,
  .mini i {
    animation: none;
    transition: none;
  }
}
</style>
