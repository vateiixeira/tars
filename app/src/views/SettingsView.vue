<script setup lang="ts">
// Tela de configurações: navegação por seções à esquerda e a seção ativa à direita. Só a seção
// ativa fica montada (o monitor de níveis da seção Áudio depende disso).
import { inject, onMounted, provide, ref, watch } from "vue";
import IssueCreatorSection from "../components/settings/IssueCreatorSection.vue";
import ClaudeSection from "../components/settings/ClaudeSection.vue";
import AudioSection from "../components/settings/AudioSection.vue";
import DictationSection from "../components/settings/DictationSection.vue";
import MeetingsSection from "../components/settings/MeetingsSection.vue";
import TextFileEditor from "../components/settings/TextFileEditor.vue";
import VoicesSection from "../components/settings/VoicesSection.vue";
import WhisperSection from "../components/settings/WhisperSection.vue";
import { createSettingsForm } from "../components/settings/useSettingsForm";
import "../components/settings/settings.css";
import { useStatus } from "../composables/useStatus";

import { textDraftsKey, type TextDraft } from "../composables/textDrafts";
provide(textDraftsKey, inject(textDraftsKey, new Map<string, TextDraft>()));
const props = defineProps<{ section?: string }>();
const emit = defineEmits<{ "update:section": [value: string]; back: [] }>();

type SectionId =
  | "dictation"
  | "whisper"
  | "vocab"
  | "subs"
  | "voices"
  | "meetings"
  | "issues"
  | "claude"
  | "audio";

const sections: { id: SectionId; label: string; group?: string }[] = [
  { id: "dictation", label: "Ditado", group: "Captura" },
  { id: "audio", label: "Áudio" },
  { id: "whisper", label: "Modelo Whisper", group: "Transcrição" },
  { id: "vocab", label: "Vocabulário" },
  { id: "subs", label: "Substituições" },
  { id: "meetings", label: "Reuniões", group: "Organização" },
  { id: "voices", label: "Vozes" },
  { id: "claude", label: "Claude", group: "Integrações" },
  { id: "issues", label: "Criador de issues" },
];

const { client, status } = useStatus();
const form = createSettingsForm(client);
const active = ref<SectionId>((props.section as SectionId) || "dictation");
watch(
  () => props.section,
  (value) => {
    if (value && sections.some((s) => s.id === value))
      active.value = value as SectionId;
  },
);
watch(active, (value) => emit("update:section", value));

onMounted(() => form.load());
// Pausar/retomar o ditado pela bandeja muda a configuração por fora desta tela: recarrega para
// "Ditado ligado" não ficar divergente (só nas mudanças; o primeiro status não conta).
watch(
  () => status.value?.dictation.enabled,
  (now, before) => {
    if (now !== undefined && before !== undefined) void form.load();
  },
);
</script>

<template>
  <section class="settings">
    <header class="settings-title">
      <span>Preferências do aplicativo</span>
      <h1>Configurações</h1>
    </header>

    <div class="layout">
      <nav class="sections" aria-label="Seções das configurações">
        <template v-for="s in sections" :key="s.id">
          <span v-if="s.group" class="section-group">{{ s.group }}</span>
          <button
            :class="{ active: active === s.id }"
            :aria-current="active === s.id ? 'page' : undefined"
            :data-section="s.id"
            @click="active = s.id"
          >
            {{ s.label }}
          </button>
        </template>
      </nav>

      <div class="content">
        <div
          v-if="form.loadError.value"
          class="s-error load-error"
          role="alert"
        >
          {{ form.loadError.value }}
          <button class="s-btn" @click="form.load()">Tentar de novo</button>
        </div>
        <p
          v-else-if="
            !form.settings.value &&
            !['vocab', 'subs', 'voices'].includes(active)
          "
          class="s-note"
        >
          Carregando…
        </p>

        <DictationSection v-if="active === 'dictation'" :form="form" />
        <WhisperSection v-else-if="active === 'whisper'" :form="form" />
        <TextFileEditor
          v-else-if="active === 'vocab'"
          :client="client"
          kind="vocab"
          title="Vocabulário"
          description="Um termo por linha, na ordem de prioridade (o fim da lista pode ser ignorado se passar do limite). Linhas com # são comentários."
        />
        <TextFileEditor
          v-else-if="active === 'subs'"
          :client="client"
          kind="subs"
          title="Substituições"
          description="Uma correção por linha, no formato errado => certo. Troca palavras inteiras, sem diferenciar maiúsculas. Linhas com # são comentários."
        />
        <VoicesSection v-else-if="active === 'voices'" :client="client" />
        <MeetingsSection v-else-if="active === 'meetings'" :form="form" />
        <IssueCreatorSection v-else-if="active === 'issues'" :form="form" />
        <ClaudeSection v-else-if="active === 'claude'" :form="form" />
        <AudioSection v-else :form="form" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.settings {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.settings-title {
  padding: 28px 32px 20px;
  border-bottom: 1px solid var(--border);
}
.settings-title span {
  display: block;
  color: var(--muted);
  font-size: var(--font-meta);
  margin-bottom: 8px;
}
.settings-title h1 {
  margin: 0;
}
.layout {
  display: flex;
  flex: 1;
  min-height: 0;
}
.sections {
  width: var(--nav-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 24px 16px;
  border-right: 1px solid var(--border);
  background: var(--panel);
  overflow-y: auto;
}
.sections button {
  text-align: left;
  padding: 10px 12px;
  min-height: 42px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  color: var(--muted);
  font-size: var(--font-control);
  cursor: pointer;
}
.sections button:hover {
  background: var(--hover);
  color: var(--text);
}
.sections button.active {
  background: var(--selected);
  color: var(--selected-text);
  font-weight: 600;
}
.content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 32px;
}
.load-error {
  margin-bottom: 16px;
}
@media (max-width: 800px) {
  .sections {
    width: 190px;
    padding: 16px 10px;
  }
  .content {
    padding: 24px;
  }
}
@media (max-width: 620px) {
  .settings-title {
    padding: 20px;
  }
  .layout {
    flex-direction: column;
  }
  .sections {
    width: 100%;
    flex-direction: row;
    flex-shrink: 0;
    gap: 4px;
    padding: 12px;
    border-right: 0;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
  }
  .sections button {
    white-space: nowrap;
  }
  .content {
    padding: 20px;
  }
}
.section-group {
  margin: 14px 12px 5px;
  color: var(--muted);
  font-size: 0.6875rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.section-group:first-child {
  margin-top: 0;
}
@media (max-width: 620px) {
  .section-group {
    display: none;
  }
}
</style>
