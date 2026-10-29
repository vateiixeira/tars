<script setup lang="ts">
// "Sugerir nomes": ao abrir (clique em "Sugerir nomes") pede ao Claude que leia a transcrição e
// proponha "Pessoa 2 → João" com o trecho que justifica. Nada é trocado sozinho: "Aplicar" só
// renomeia nesta reunião; cadastrar a voz é opt-in por sugestão ("também salvar a voz"), para um
// palpite do Claude não contaminar o reconhecimento das próximas reuniões.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useFocusScope } from "../composables/useFocusScope";
import type { NameSuggestion } from "../api/types";
import { useStatus } from "../composables/useStatus";
import AppBanner from "./ui/AppBanner.vue";
import AppIcon from "./ui/AppIcon.vue";
import TypingDots from "./ui/TypingDots.vue";

const props = defineProps<{ meetingId: string }>();
const emit = defineEmits<{
  /** Algum falante foi renomeado/cadastrado: o pai recarrega o detalhe. */
  changed: [];
  close: [];
}>();

const { client } = useStatus();

interface Row {
  suggestion: NameSuggestion;
  saveVoice: boolean;
  busy: boolean;
  applied: boolean;
  error: string | null;
}

const rows = ref<Row[]>([]);
const loading = ref(false);
const loadError = ref<string | null>(null);
let alive = true;
let seq = 0;

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function fetchSuggestions(): Promise<void> {
  const mine = ++seq;
  loading.value = true;
  loadError.value = null;
  try {
    const list = await client.nameSuggestions(props.meetingId);
    if (!alive || mine !== seq) return;
    rows.value = list.map((suggestion) => ({ suggestion, saveVoice: false, busy: false, applied: false, error: null }));
  } catch (e) {
    if (!alive || mine !== seq) return;
    loadError.value = errorText(e);
  } finally {
    if (alive && mine === seq) loading.value = false;
  }
}

async function apply(row: Row): Promise<void> {
  if (row.busy || row.applied) return;
  const { speaker_id, name } = row.suggestion;
  row.busy = true;
  row.error = null;
  try {
    await client.renameSpeaker(speaker_id, name);
  } catch (e) {
    row.error = errorText(e);
    row.busy = false;
    return;
  }
  row.applied = true; // o rename já valeu, mesmo que salvar a voz falhe
  if (row.saveVoice) {
    try {
      await client.enrollSpeaker(speaker_id, { name });
    } catch (e) {
      row.error = `Renomeado, mas não foi possível salvar a voz: ${errorText(e)}`;
    }
  }
  row.busy = false;
  if (alive) emit("changed");
}

const root = ref<HTMLElement | null>(null);
useFocusScope(root);
onMounted(() => {
  root.value?.focus();
  void fetchSuggestions();
});
onBeforeUnmount(() => (alive = false));
</script>

<template>
  <div ref="root" class="name-dialog" role="dialog" aria-modal="true" aria-label="Sugestão de nomes" tabindex="-1" @keydown.esc.stop.prevent="emit('close')">
    <div class="backdrop" @click="emit('close')"></div>
    <div class="card">
      <header>
        <h4>Sugestão de nomes</h4>
        <button type="button" class="icon-button" aria-label="Fechar diálogo" @click="emit('close')">
          <AppIcon name="close" :size="18" />
        </button>
      </header>
      <div class="body">
        <p class="hint">Nada é trocado sozinho: “Aplicar” renomeia só nesta reunião.</p>

        <p v-if="loading" class="loading" role="status"><TypingDots /><span>O Claude está lendo a transcrição…</span></p>

        <template v-else-if="loadError">
          <AppBanner class="error" role="alert">{{ loadError }}</AppBanner>
          <button type="button" class="btn" @click="fetchSuggestions">Tentar de novo</button>
        </template>

        <p v-else-if="!rows.length" class="muted">O Claude não encontrou nomes no contexto.</p>

        <ul v-else class="list">
          <li v-for="row in rows" :key="row.suggestion.speaker_id" class="item">
            <div class="line">
              <strong>{{ row.suggestion.label }} → {{ row.suggestion.name }}</strong>
              <span v-if="row.applied" class="done"><AppIcon name="check" :size="15" />Aplicado</span>
              <button v-else type="button" class="btn pri btn-sm" :disabled="row.busy" @click="apply(row)">Aplicar</button>
            </div>
            <blockquote v-if="row.suggestion.evidence" class="evidence">“{{ row.suggestion.evidence }}”</blockquote>
            <label v-if="!row.applied" class="check">
              <input v-model="row.saveVoice" type="checkbox" :disabled="row.busy" /> também salvar a voz
            </label>
            <p v-if="row.error" class="item-error" role="alert">{{ row.error }}</p>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.name-dialog {
  position: fixed;
  inset: 0;
  z-index: 50;
  outline: none;
}
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: var(--backdrop);
}
/* Mesmo visual do AppDialog (surface-raised, radius-xl, shadow-dialog). */
.card {
  position: fixed;
  z-index: 51;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(560px, calc(100vw - 32px));
  max-height: 80vh;
  /* o cabeçalho (com o X de fechar) fica fixo; só o corpo rola */
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface-raised);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-dialog);
}
header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-6) var(--space-6) var(--space-3);
}
.body {
  min-height: 0;
  overflow-y: auto;
  padding: 0 var(--space-6) var(--space-6);
}
h4 {
  margin: 0;
  font-size: 20px;
  line-height: 26px;
  font-weight: 700;
}
.hint,
.muted {
  margin: 0 0 var(--space-4);
  color: var(--text-muted);
  font-size: 14px;
}
.loading {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  color: var(--text-accent);
  font-size: 14px;
  font-weight: 700;
}
.error {
  margin: 0 0 var(--space-3);
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.item {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  background: var(--glass);
}
.line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.done {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--success);
  font-size: 13px;
  font-weight: 700;
}
.evidence {
  margin: var(--space-2) 0;
  padding: var(--space-2) 0 0;
  border-top: 1px solid var(--glass-border);
  color: var(--text-muted);
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
}
.check {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.item-error {
  margin: 6px 0 0;
  color: var(--danger);
  font-size: 13px;
  font-weight: 600;
}
</style>
