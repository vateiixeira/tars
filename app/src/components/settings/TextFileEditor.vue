<script setup lang="ts">
// Editor de um arquivo de texto do ditado (vocabulário ou substituições): carrega, edita e salva.
import { computed, inject, onBeforeUnmount, onMounted, ref } from "vue";
import { textDraftsKey } from "../../composables/textDrafts";
import type { ApiClient } from "../../api/client";
import type { TextKind } from "../../api/types";
import { messageOf } from "./useSettingsForm";

const props = defineProps<{
  client: ApiClient;
  kind: TextKind;
  title: string;
  description: string;
}>();

const drafts = inject(textDraftsKey, undefined);
const text = ref("");
const saved = ref("");
const loaded = ref(false);
const saving = ref(false);
const error = ref<string | null>(null);
const justSaved = ref(false);

const dirty = computed(() => text.value !== saved.value);

async function load(): Promise<void> {
  error.value = null;
  try {
    saved.value = await props.client.getText(props.kind);
    const cached = drafts?.get(props.kind);
    text.value = cached && cached.text !== cached.saved ? cached.text : saved.value;
    loaded.value = true;
  } catch (e) {
    error.value = messageOf(e);
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  justSaved.value = false;
  try {
    saved.value = await props.client.putText(props.kind, text.value);
    drafts?.set(props.kind, { text: text.value, saved: saved.value });
    justSaved.value = true;
  } catch (e) {
    error.value = messageOf(e);
  } finally {
    saving.value = false;
  }
}

function onInput(): void {
  justSaved.value = false;
  drafts?.set(props.kind, { text: text.value, saved: saved.value });
}

onBeforeUnmount(() => { if (loaded.value) drafts?.set(props.kind, { text: text.value, saved: saved.value }); });
onMounted(load);
</script>

<template>
  <section class="s-section">
    <p class="s-note">{{ description }}</p>
    <textarea
      v-model="text"
      class="s-textarea mono"
      :aria-label="title"
      :disabled="!loaded"
      spellcheck="false"
      @input="onInput"
    ></textarea>
    <div class="s-row">
      <button class="s-btn primary" :disabled="!loaded || !dirty || saving" @click="save">Salvar</button>
      <button v-if="!loaded && error" class="s-btn" @click="load">Tentar de novo</button>
      <span v-if="justSaved" class="s-ok" role="status">Salvo. Vale a partir do próximo ditado.</span>
      <span v-else class="s-note">{{ dirty ? "Alterações não salvas. O rascunho permanece ao trocar de seção." : "Vale a partir do próximo ditado." }}</span>
    </div>
    <p v-if="error" class="s-error" role="alert" data-test="editor-error">{{ error }}</p>
  </section>
</template>
