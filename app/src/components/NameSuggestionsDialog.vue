<script setup lang="ts">
// "Sugerir nomes": ao abrir (clique em "Sugerir nomes") pede ao Claude que leia a transcrição e
// proponha "Pessoa 2 → João" com o trecho que justifica. Nada é trocado sozinho: "Aplicar" só
// renomeia nesta reunião; cadastrar a voz é opt-in por sugestão ("também salvar a voz"), para um
// palpite do Claude não contaminar o reconhecimento das próximas reuniões.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useFocusScope } from "../composables/useFocusScope";
import type { NameSuggestion } from "../api/types";
import { useStatus } from "../composables/useStatus";

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
      <h4>Sugestão de nomes</h4>
      <p class="hint">Nada é trocado sozinho: “Aplicar” renomeia só nesta reunião.</p>

      <p v-if="loading" class="muted">O Claude está lendo a transcrição…</p>

      <template v-else-if="loadError">
        <p class="error" role="alert">{{ loadError }}</p>
        <button type="button" class="btn" @click="fetchSuggestions">Tentar de novo</button>
      </template>

      <p v-else-if="!rows.length" class="muted">O Claude não encontrou nomes no contexto.</p>

      <ul v-else class="list">
        <li v-for="row in rows" :key="row.suggestion.speaker_id" class="item">
          <div class="line">
            <strong>{{ row.suggestion.label }} → {{ row.suggestion.name }}</strong>
            <span v-if="row.applied" class="done">Aplicado</span>
            <button v-else type="button" class="btn pri" :disabled="row.busy" @click="apply(row)">Aplicar</button>
          </div>
          <blockquote v-if="row.suggestion.evidence" class="evidence">“{{ row.suggestion.evidence }}”</blockquote>
          <label v-if="!row.applied" class="check">
            <input v-model="row.saveVoice" type="checkbox" :disabled="row.busy" /> também salvar a voz
          </label>
          <p v-if="row.error" class="error" role="alert">{{ row.error }}</p>
        </li>
      </ul>

      <div class="foot">
        <button type="button" class="btn" @click="emit('close')">Fechar</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.name-dialog {
  position:fixed; inset:0; z-index:50;
  outline: none;
}
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: var(--backdrop);
}
.card {
  position: fixed;
  z-index: 51;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(560px, calc(100vw - 32px));
  max-height: 80vh;
  overflow-y: auto;
  padding: 16px 18px;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-dialog);
}
h4 {
  margin: 0 0 4px;
  font-size: 1.05rem;
}
.hint,
.muted {
  color: var(--muted);
  margin: 0 0 10px;
  font-size: 0.85rem;
}
.error {
  color: var(--danger);
  margin: 6px 0;
  font-size: 0.85rem;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.item {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.done {
  color: var(--text-accent);
  font-size: 0.85rem;
}
.evidence {
  margin: 6px 0;
  padding:12px 0;
  border-top:1px solid var(--border);
  color: var(--muted);
  font-size: 0.85rem;
}
.check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  cursor: pointer;
}
.foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
