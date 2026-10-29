<script setup lang="ts">
// Menu de uma etiqueta de falante (não aparece para "Você"): renomear só nesta reunião,
// ouvir trechos, identificar como uma voz cadastrada/nova ou juntar com outro falante.
// Chama a API sozinho; ao dar certo emite `changed` (o pai recarrega o detalhe) e `close`.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useFocusScope } from "../composables/useFocusScope";
import type { Segment, Speaker, Voice } from "../api/types";
import { useStatus } from "../composables/useStatus";
import { speakerName } from "../lib/meeting";
import AppIcon from "./ui/AppIcon.vue";
import SpeakerSamples from "./SpeakerSamples.vue";

const props = defineProps<{
  meetingId: string;
  segments: Segment[];
  speaker: Speaker;
  /** Todos os falantes da reunião (os "outros" são os candidatos a juntar). */
  speakers: Speaker[];
  /** Posição (px da janela) do canto superior esquerdo do menu. */
  x: number;
  y: number;
}>();
const emit = defineEmits<{ changed: []; close: []; preview: [] }>();

const { client } = useStatus();

type Mode = "main" | "rename" | "identify" | "new-voice" | "merge";
const mode = ref<Mode>("main");
const text = ref("");
const input = ref<HTMLInputElement | null>(null);
const voices = ref<Voice[] | null>(null);
const busy = ref(false);
const error = ref<string | null>(null);

const others = computed(() => props.speakers.filter((s) => s.id !== props.speaker.id));
const panel = ref<HTMLElement | null>(null);
const panelHeight = ref(220);
const viewport = ref({width:window.innerWidth, height:window.innerHeight});
function updateViewport() { viewport.value = {width:window.innerWidth,height:window.innerHeight}; measurePanel(); }
let resizeObserver: ResizeObserver | undefined;
function measurePanel() { panelHeight.value = panel.value?.offsetHeight || 220; }
watch(mode, () => void nextTick(measurePanel));
const style = computed(() => ({
  // não deixa o menu estourar a janela à direita
  left: `${Math.max(8, Math.min(props.x, viewport.value.width - 308))}px`,
  top: `${Math.max(8, Math.min(props.y, viewport.value.height - panelHeight.value - 8))}px`,
}));

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function go(next: Mode): Promise<void> {
  error.value = null;
  mode.value = next;
  if (next === "rename") text.value = props.speaker.label ?? "";
  if (next === "new-voice") text.value = "";
  if (next === "identify" && !voices.value) {
    try {
      voices.value = await client.listVoices();
    } catch (e) {
      error.value = errorText(e);
    }
  }
  await nextTick();
  input.value?.focus();
  input.value?.select();
}

/** Executa a chamada; sucesso fecha o menu, erro fica visível e o menu continua usável. */
async function act(fn: () => Promise<unknown>): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  error.value = null;
  try {
    await fn();
  } catch (e) {
    error.value = errorText(e);
    return;
  } finally {
    busy.value = false;
  }
  emit("changed");
  emit("close");
}

function submitText(): Promise<void> | void {
  const value = text.value.trim();
  if (!value) return;
  const id = props.speaker.id;
  if (mode.value === "rename") return act(() => client.renameSpeaker(id, value));
  if (mode.value === "new-voice") return act(() => client.enrollSpeaker(id, { name: value }));
}

const identifyAs = (v: Voice) => act(() => client.enrollSpeaker(props.speaker.id, { voice_id: v.id }));
const mergeInto = (s: Speaker) => act(() => client.mergeSpeaker(props.speaker.id, s.id));

const root = ref<HTMLElement | null>(null);
useFocusScope(root);
onMounted(() => { window.addEventListener("resize", updateViewport); if (typeof ResizeObserver !== "undefined") { resizeObserver = new ResizeObserver(measurePanel); if (panel.value) resizeObserver.observe(panel.value); } measurePanel(); });
onBeforeUnmount(() => { window.removeEventListener("resize", updateViewport); resizeObserver?.disconnect(); });
</script>

<template>
  <div ref="root" class="speaker-menu" role="dialog" aria-modal="true" :aria-label="`Opções de ${speakerName(speaker)}`" tabindex="-1" @keydown.esc.stop.prevent="emit('close')">
    <div class="backdrop" @click="emit('close')"></div>
    <div ref="panel" class="panel" :style="style">
      <SpeakerSamples
        :meeting-id="meetingId"
        :speaker="speaker"
        :segments="segments"
        @play="emit('preview')"
      />
      <template v-if="mode === 'main'">
        <button type="button" class="option" @click="go('rename')"><AppIcon name="edit" :size="18" />Renomear nesta reunião</button>
        <button type="button" class="option" @click="go('identify')"><AppIcon name="user-plus" :size="18" />Identificar como…</button>
        <button type="button" class="option" :disabled="!others.length" @click="go('merge')"><AppIcon name="link" :size="18" />Juntar com…</button>
      </template>

      <form v-else-if="mode === 'rename' || mode === 'new-voice'" class="form" @submit.prevent="submitText">
        <label class="hint" :for="`sm-${speaker.id}`">
          {{ mode === "rename" ? "Novo nome (só nesta reunião)" : "Nome da nova voz (vale para as próximas reuniões)" }}
        </label>
        <input :id="`sm-${speaker.id}`" ref="input" v-model="text" maxlength="100" placeholder="Nome da pessoa" />
        <div class="row">
          <button type="button" class="option back" @click="go(mode === 'rename' ? 'main' : 'identify')"><AppIcon name="arrow" :size="18" />Voltar</button>
          <button type="submit" class="option pri" :disabled="busy || !text.trim()">Salvar</button>
        </div>
      </form>

      <template v-else-if="mode === 'identify'">
        <p class="hint">Identificar como…</p>
        <p v-if="voices === null && !error" class="hint">Carregando vozes…</p>
        <button v-for="v in voices ?? []" :key="v.id" type="button" class="option" :disabled="busy" @click="identifyAs(v)">
          <AppIcon name="mic" :size="18" />{{ v.name }}
        </button>
        <button type="button" class="option" @click="go('new-voice')"><AppIcon name="plus" :size="18" />Nova voz…</button>
        <button type="button" class="option back" @click="go('main')"><AppIcon name="arrow" :size="18" />Voltar</button>
      </template>

      <template v-else>
        <p class="hint">Juntar “{{ speakerName(speaker) }}” com…</p>
        <button v-for="s in others" :key="s.id" type="button" class="option" :disabled="busy" @click="mergeInto(s)">
          <AppIcon name="people" :size="18" />{{ speakerName(s) }}
        </button>
        <button type="button" class="option back" @click="go('main')"><AppIcon name="arrow" :size="18" />Voltar</button>
      </template>

      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </div>
  </div>
</template>

<style scoped>
.speaker-menu {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
/* Visual do AppMenu (superfície sólida, raio, sombra, itens em pílula) com a semântica de diálogo. */
.panel {
  position: fixed;
  z-index: 41;
  width: min(300px, calc(100vw - 16px));
  max-height: 70vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px;
  background: var(--surface-solid);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-popover);
}
.option {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 38px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  transition: background var(--duration-fast);
}
.option svg {
  color: var(--text-muted);
}
/* --selected, não --glass-strong: no tema Dia o segundo é quase branco sobre branco. */
.option:hover:not(:disabled),
.option:focus-visible {
  background: var(--selected);
  outline: none;
}
.option:focus-visible {
  box-shadow: inset 0 0 0 2px var(--focus-ring);
}
.option:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.option.back {
  color: var(--text-muted);
}
.option.pri {
  justify-content: center;
  border-radius: var(--radius-pill);
  color: var(--on-accent);
  background: linear-gradient(135deg, var(--accent-grad-from), var(--accent-grad-to));
}
.option.pri:hover:not(:disabled) {
  background: linear-gradient(135deg, var(--accent-grad-from), var(--accent-grad-to));
  filter: brightness(1.08);
}
.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-1);
}
.form input {
  min-height: var(--control-md);
  padding: 0 var(--space-4);
  border: 1px solid var(--border-control);
  border-radius: var(--radius-pill);
  background-color: var(--glass-strong);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
}
.form input::placeholder {
  color: var(--text-muted);
}
.form input:focus {
  outline: none;
  border-color: var(--focus-ring);
  box-shadow:
    0 0 0 1px var(--focus-ring),
    0 0 0 4px var(--selected);
}
.row {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
}
.hint {
  margin: 0;
  padding: var(--space-2) 10px 6px;
  font-size: 11px;
  line-height: 14px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
}
label.hint {
  letter-spacing: normal;
  text-transform: none;
  font-size: 13px;
  line-height: 18px;
}
.error {
  margin: 0;
  padding: var(--space-1) 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--danger);
}
@media (prefers-reduced-motion: reduce) {
  .option {
    transition: none;
  }
}
</style>
