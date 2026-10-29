<script setup lang="ts">
// Botão grande de gravar reunião: inicia e para (com cronômetro e app seguido). Enquanto o backend
// processa a reunião anterior (ou um "Reprocessar") o botão continua iniciando uma nova: reuniões
// em sequência são comuns e o trabalho da GPU vai para a fila.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { INTERRUPTED_MESSAGE, useStatus } from "../composables/useStatus";
import AppButton from "./ui/AppButton.vue";
import AppIcon from "./ui/AppIcon.vue";
import { mmss } from "../lib/format";

const emit = defineEmits<{ started: [meetingId: string]; stopped: [meetingId: string] }>();
const { client, connected, status, meetingState, interrupted } = useStatus();

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
    <AppButton
      variant="primary"
      class="rec"
      :class="{ recording }"
      :disabled="!connected || busy"
      @click="toggle"
    >
      <template v-if="recording">
        <span class="capture-label"><span class="stop-dot" /> Parar · {{ mmss(elapsed) }}</span>
        <small>{{ followedApp ? `Seguindo: ${followedApp}` : "Saída padrão do sistema" }}</small>
      </template>
      <template v-else>
        <span class="capture-label"><AppIcon name="plus" :size="18" /> Iniciar reunião</span>
        <small>{{ processing ? "Processando a anterior…" : "microfone + áudio do PC" }}</small>
      </template>
    </AppButton>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.rec { display:block; width:100%; padding:10px 12px; border:1px solid var(--accent); border-radius:var(--radius-md); background:var(--accent); color:var(--on-accent); font-weight:600; font-size:var(--font-control); text-align:center; cursor:pointer; min-height:var(--control-lg); }
.rec:hover:not(:disabled) { background:var(--accent-hover); }
.capture-label { display:flex; align-items:center; justify-content:center; gap:8px; }
.rec small { display:block; font-weight:400; font-size:.6875rem; margin-top:5px; }
.rec.recording { background:var(--danger-surface); color:var(--danger); border-color:var(--danger); box-shadow:none; }
.rec.recording:hover:not(:disabled) { background:var(--danger-surface); }
.rec:disabled { opacity:.55; cursor:not-allowed; }
.stop-dot { width:9px; height:9px; border-radius:2px; background:currentColor; }
.error { margin:8px 0 0; color:var(--danger); font-size:var(--font-meta); }
</style>
