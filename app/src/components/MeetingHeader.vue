<script setup lang="ts">
// Cabeçalho da reunião: título editável (h1), data, pílulas dos participantes, status e o menu
// "Mais opções". O título é salvo por `rename` (do pai, que chama a API e mostra o erro); as
// ações do menu sobem por `menu`, exceto "Participantes", que só mostra/oculta as pílulas aqui.
// O pai desmonta o cabeçalho ao trocar de reunião, então edição e menu não passam para a outra.
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import type { MeetingDetail, Speaker } from "../api/types";
import { groupByDay, timeOfDay } from "../lib/format";
import AppIcon from "./ui/AppIcon.vue";
import AppMenu from "./ui/AppMenu.vue";
import StatusPill from "./ui/StatusPill.vue";
import SpeakerChips from "./SpeakerChips.vue";

export type MeetingMenuKey = "export" | "suggest" | "archive" | "delete";

const props = defineProps<{
  meeting: MeetingDetail;
  connected: boolean;
  /** Salva o título; `true` quando deu certo (o erro fica com o pai). */
  rename: (title: string) => Promise<boolean>;
  titleInvalid: boolean;
  exportBusy: boolean;
  archiveBusy: boolean;
}>();
const emit = defineEmits<{
  menu: [key: MeetingMenuKey];
  selectSpeaker: [speaker: Speaker, event: MouseEvent];
}>();

const title = computed(() => props.meeting.title || "Reunião sem título");
const when = computed(() => {
  const m = props.meeting;
  const day = groupByDay([m])[0].label;
  const end = m.ended_at ? `–${timeOfDay(m.ended_at)}` : "";
  return `${day}, ${timeOfDay(m.started_at)}${end}`;
});
const archived = computed(() => !!props.meeting.archived_at);
// gravando/processando: o backend recusa (409), igual à exclusão
const canArchive = computed(
  () => props.meeting.status !== "recording" && props.meeting.status !== "processing",
);

// -- título ---------------------------------------------------------------------------------

const editing = ref(false);
const draft = ref("");
const saving = ref(false);
const titleInput = ref<HTMLInputElement | null>(null);
const renameButton = ref<HTMLButtonElement | null>(null);

function startEdit(): void {
  draft.value = props.meeting.title ?? "";
  editing.value = true;
  void nextTick(() => titleInput.value?.select());
}
function cancelTitle(): void {
  editing.value = false;
  void nextTick(() => renameButton.value?.focus());
}
async function commitTitle(): Promise<void> {
  if (!editing.value || saving.value) return;
  const next = draft.value.trim();
  if (!next || next === (props.meeting.title ?? "")) {
    cancelTitle();
    return;
  }
  saving.value = true;
  try {
    const ok = await props.rename(next);
    if (ok) cancelTitle();
    else void nextTick(() => titleInput.value?.focus());
  } finally {
    saving.value = false;
  }
}

// -- menu -----------------------------------------------------------------------------------

const menuOpen = ref(false);
const peopleExpanded = ref(false);
const moreButton = ref<HTMLButtonElement | null>(null);
const moreWrap = ref<HTMLElement | null>(null);

const menuItems = computed(() => [
  {
    key: "export",
    label: props.meeting.prints.length ? "Exportar reunião com prints (.zip)" : "Exportar reunião em Markdown",
    icon: "export",
    disabled: props.exportBusy || !props.connected,
  },
  { key: "suggest", label: "Sugerir nomes", icon: "user-plus", disabled: !props.connected },
  { key: "people", label: peopleExpanded.value ? "Ocultar participantes" : "Participantes", icon: "people" },
  {
    key: "archive",
    label: archived.value ? "Desarquivar" : "Arquivar reunião",
    icon: "archive",
    disabled: !props.connected || props.archiveBusy || (!archived.value && !canArchive.value),
  },
  { key: "delete", label: "Excluir reunião", icon: "trash", danger: true, disabled: !props.connected },
]);

function closeMenu(): void {
  menuOpen.value = false;
  void nextTick(() => moreButton.value?.focus());
}
function onMenuSelect(key: string): void {
  if (key === "people") {
    peopleExpanded.value = !peopleExpanded.value;
    closeMenu();
    return;
  }
  // Excluir e Sugerir nomes abrem diálogos, que cuidam do foco; as outras ações devolvem ao botão.
  if (key === "delete" || key === "suggest") menuOpen.value = false;
  else closeMenu();
  emit("menu", key as MeetingMenuKey);
}
function closeOutside(event: PointerEvent): void {
  if (menuOpen.value && !moreWrap.value?.contains(event.target as Node)) menuOpen.value = false;
}
onMounted(() => document.addEventListener("pointerdown", closeOutside));
onBeforeUnmount(() => document.removeEventListener("pointerdown", closeOutside));

function onEscape(event: KeyboardEvent): void {
  if (!menuOpen.value) return;
  event.preventDefault(); // o painel sobreposto da biblioteca não fecha junto
  closeMenu();
}

defineExpose({ focusMenuButton: () => moreButton.value?.focus() });
</script>

<template>
  <header class="head">
    <div class="head-main">
      <input
        v-if="editing"
        ref="titleInput"
        v-model="draft"
        class="title-input"
        maxlength="200"
        aria-label="Título da reunião"
        :aria-invalid="titleInvalid ? true : undefined"
        :disabled="saving"
        @keydown.enter.prevent="commitTitle"
        @keydown.esc.prevent="cancelTitle"
        @blur="commitTitle"
      />
      <h1 v-else>
        <button
          ref="renameButton"
          type="button"
          class="title"
          title="Editar título da reunião"
          :aria-label="`Renomear reunião: ${title}`"
          :disabled="!connected"
          @click="startEdit"
        >
          {{ title }}<AppIcon name="edit" :size="17" />
        </button>
      </h1>
      <div class="meta-row">
        <span class="meta">
          <AppIcon name="clock" :size="15" />{{ when }}
          <StatusPill v-if="archived" class="archived-tag" icon="archive">Arquivada</StatusPill>
          <span v-if="saving" role="status">Salvando título…</span>
        </span>
        <SpeakerChips
          class="people"
          :class="{ expanded: peopleExpanded }"
          :speakers="meeting.speakers"
          @select="(s, e) => emit('selectSpeaker', s, e)"
        />
      </div>
    </div>
    <div class="head-side">
      <StatusPill v-if="meeting.status === 'ready'" class="ready" tone="ok" icon="check">Pronta para revisar</StatusPill>
      <div ref="moreWrap" class="more-wrap" @keydown.esc="onEscape">
        <button
          ref="moreButton"
          type="button"
          class="more icon-button"
          aria-label="Mais opções"
          aria-haspopup="menu"
          :aria-expanded="menuOpen"
          @click="menuOpen = !menuOpen"
        >
          <AppIcon name="more" />
        </button>
        <AppMenu
          v-if="menuOpen"
          class="menu"
          label="Mais opções"
          :items="menuItems"
          @select="onMenuSelect"
          @close="closeMenu"
        />
      </div>
    </div>
  </header>
</template>

<style scoped>
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-6) var(--page-gutter) var(--space-3);
}
.head-main {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
}
h1 {
  margin: 0;
  font-size: 26px;
  line-height: 32px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  max-width: 100%;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text);
  font: inherit;
  letter-spacing: inherit;
  text-align: left;
  overflow-wrap: anywhere;
}
.title svg {
  flex-shrink: 0;
  color: var(--text-muted);
}
.title:hover:not(:disabled) svg {
  color: var(--text-accent);
}
.title-input {
  min-width: 0;
  font: inherit;
  font-size: 26px;
  line-height: 32px;
  font-weight: 700;
  padding: 0 var(--space-2);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: var(--text);
  background: var(--surface-solid);
}
.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
}
.meta {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-muted);
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
}
.head-side {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
.more-wrap {
  position: relative;
}
.menu {
  position: absolute;
  right: 0;
  top: calc(100% + var(--space-1));
  z-index: 20;
  width: 300px; /* "Exportar reunião com prints (.zip)" numa linha */
}
@container detail (max-width: 900px) {
  .head {
    padding: var(--space-4) var(--space-6) var(--space-2);
  }
  h1,
  .title-input {
    font-size: 22px;
    line-height: 28px;
  }
}
@container detail (max-width: 560px) {
  .head {
    padding: var(--space-4) var(--space-5) var(--space-2);
  }
  .ready {
    display: none;
  }
  h1,
  .title-input {
    font-size: 20px;
    line-height: 26px;
  }
}
@media (max-height: 600px) {
  .head {
    padding-top: var(--space-3);
  }
  .meta,
  .people {
    display: none;
  }
  .people.expanded {
    display: flex;
  }
  h1,
  .title-input {
    font-size: 20px;
    line-height: 26px;
  }
}
</style>
