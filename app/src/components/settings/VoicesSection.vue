<script setup lang="ts">
// Seção Vozes: vozes cadastradas, com renomear e apagar (com confirmação).
import { nextTick, onMounted, ref } from "vue";
import type { ApiClient } from "../../api/client";
import type { Voice } from "../../api/types";
import { messageOf } from "./useSettingsForm";

const props = defineProps<{ client: ApiClient }>();

const voices = ref<Voice[]>([]);
const loaded = ref(false);
const loadError = ref<string | null>(null);
/** Erro da última ação, por id de voz. */
const errors = ref<Record<string, string>>({});
const editingId = ref<string | null>(null);
const draft = ref("");
const confirmingId = ref<string | null>(null);

async function load(): Promise<void> {
  loadError.value = null;
  try {
    voices.value = await props.client.listVoices();
    loaded.value = true;
  } catch (e) {
    loadError.value = messageOf(e);
  }
}

function startRename(voice: Voice): void {
  confirmingId.value = null;
  editingId.value = voice.id;
  draft.value = voice.name;
  delete errors.value[voice.id];
  nextTick(() => document.getElementById(`voice-name-${voice.id}`)?.focus());
}

async function commitRename(voice: Voice): Promise<void> {
  const name = draft.value.trim();
  if (name === voice.name) {
    editingId.value = null;
    return;
  }
  try {
    const updated = await props.client.renameVoice(voice.id, name);
    voices.value = voices.value.map((v) => (v.id === voice.id ? updated : v));
    editingId.value = null;
    delete errors.value[voice.id];
  } catch (e) {
    errors.value[voice.id] = messageOf(e);
  }
}

function cancelRename(): void {
  editingId.value = null;
}

async function confirmDelete(voice: Voice): Promise<void> {
  try {
    await props.client.deleteVoice(voice.id);
    voices.value = voices.value.filter((v) => v.id !== voice.id);
    confirmingId.value = null;
    delete errors.value[voice.id];
  } catch (e) {
    errors.value[voice.id] = messageOf(e);
  }
}

onMounted(load);
</script>

<template>
  <section class="s-section">
    <p class="s-note">
      Vozes cadastradas a partir das reuniões. Elas ajudam a identificar quem fala nas próximas gravações.
    </p>

    <p v-if="loadError" class="s-error" role="alert">
      {{ loadError }} <button class="s-btn" @click="load">Tentar de novo</button>
    </p>
    <p v-else-if="loaded && !voices.length" class="s-note" data-test="voices-empty">
      Nenhuma voz cadastrada ainda. Cadastre uma voz a partir de um falante, na tela da reunião.
    </p>

    <ul class="voices">
      <li v-for="voice in voices" :key="voice.id" class="voice" data-test="voice">
        <div class="s-row line">
          <template v-if="editingId === voice.id">
            <input
              :id="`voice-name-${voice.id}`"
              v-model="draft"
              class="s-input name"
              type="text"
              :aria-label="`Novo nome da voz ${voice.name}`"
              @keydown.enter.prevent="commitRename(voice)"
              @keydown.esc="cancelRename"
            />
            <button class="s-btn primary" data-test="voice-save" @click="commitRename(voice)">Salvar</button>
            <button class="s-btn" @click="cancelRename">Cancelar</button>
          </template>
          <template v-else-if="confirmingId === voice.id">
            <span class="name-text">Apagar a voz “{{ voice.name }}”?</span>
            <button class="s-btn danger" data-test="voice-confirm-delete" @click="confirmDelete(voice)">
              Apagar
            </button>
            <button class="s-btn" @click="confirmingId = null">Cancelar</button>
          </template>
          <template v-else>
            <span class="name-text">{{ voice.name }}</span>
            <span class="s-note samples">{{ voice.n_samples }} {{ voice.n_samples === 1 ? "amostra" : "amostras" }}</span>
            <button class="s-btn" data-test="voice-rename" @click="startRename(voice)">Renomear</button>
            <button class="s-btn danger" data-test="voice-delete" @click="confirmingId = voice.id">Apagar</button>
          </template>
        </div>
        <p v-if="errors[voice.id]" class="s-error" role="alert" data-test="voice-error">{{ errors[voice.id] }}</p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.voices {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.voice {
  padding: var(--space-3) var(--space-4);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
}
.line {
  min-height: var(--control-md);
}
.name-text {
  flex: 1;
  min-width: 120px;
}
.name {
  flex: 1;
  min-width: 160px;
  width: auto;
}
.samples {
  white-space: nowrap;
}
</style>
