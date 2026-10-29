<script setup lang="ts">
// Tela de configurações: navegação por seções à esquerda e a seção ativa à direita. Só a seção
// ativa fica montada (o monitor de níveis da seção Áudio depende disso).
import { computed, inject, onMounted, provide, ref, watch } from "vue";
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

/** Grupo da seção ativa: o rótulo do último item com grupo até ela. */
const activeGroup = computed(() => {
  const index = sections.findIndex((s) => s.id === active.value);
  for (let i = index; i >= 0; i--) {
    const group = sections[i]?.group;
    if (group) return group;
  }
  return "";
});
const activeLabel = computed(() => sections.find((s) => s.id === active.value)?.label ?? "");


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
    <nav class="snav" aria-label="Seções das configurações">
      <template v-for="s in sections" :key="s.id">
        <div v-if="s.group" class="snav-group">{{ s.group }}</div>
        <button
          class="snav-item"
          :class="{ active: active === s.id }"
          :aria-current="active === s.id ? 'page' : undefined"
          :data-section="s.id"
          @click="active = s.id"
        >
          {{ s.label }}
        </button>
      </template>
    </nav>

    <div class="panel">
      <header class="panel-head">
        <span class="eyebrow">{{ activeGroup }}</span>
        <h1>{{ activeLabel }}</h1>
      </header>

      <div class="panel-body">
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
/* Navegação e painel em vidro, sem backdrop-filter (Ruling R2). */
.settings {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: var(--settings-nav-width) minmax(0, 1fr);
  gap: 18px;
}
.snav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 0;
  padding: var(--space-4);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  overflow-y: auto;
}
.snav-group {
  margin: var(--space-4) 12px 6px;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 14px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.snav-group:first-child {
  margin-top: 0;
}
.snav-item {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  height: 40px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 14px;
  font-weight: 700;
  text-align: left;
  cursor: pointer;
  transition:
    background var(--duration-fast),
    color var(--duration-fast);
}
.snav-item:hover {
  background: var(--selected);
  color: var(--text);
}
.snav-item[aria-current="page"] {
  background: var(--selected);
  border-color: var(--selected-border);
  color: var(--text-accent);
}
.panel {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: var(--space-6) var(--space-7);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  overflow-y: auto;
}
.panel-head {
  flex-shrink: 0;
  max-width: var(--form-max);
}
.eyebrow {
  display: block;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 14px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.panel-head h1 {
  margin: var(--space-1) 0 0;
}
.panel-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}
.load-error {
  max-width: var(--form-max);
}
@media (prefers-reduced-motion: reduce) {
  .snav-item {
    transition: none;
  }
}
@media (max-width: 800px) {
  .settings {
    grid-template-columns: 210px minmax(0, 1fr);
    gap: var(--space-3);
  }
  .panel {
    padding: var(--space-5);
  }
}
/* Estreita, a navegação vira uma faixa horizontal de pílulas acima do painel. */
@media (max-width: 620px) {
  .settings {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1fr);
  }
  .snav {
    flex-direction: row;
    gap: var(--space-1);
    padding: var(--space-3);
    overflow-x: auto;
    overflow-y: hidden;
  }
  .snav-group {
    display: none;
  }
  .snav-item {
    white-space: nowrap;
  }
}
</style>
