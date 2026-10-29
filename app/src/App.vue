<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, provide, ref } from "vue";
import MeetingList from "./components/MeetingList.vue";
import RecordButton from "./components/RecordButton.vue";
import VersionLine from "./components/VersionLine.vue";
import TopBar from "./components/shell/TopBar.vue";
import AppIcon from "./components/ui/AppIcon.vue";
import { useStatus } from "./composables/useStatus";
import { textDraftsKey, type TextDraft } from "./composables/textDrafts";
import MeetingsView from "./views/MeetingsView.vue";
import SettingsView from "./views/SettingsView.vue";

type Route = "meetings" | "settings";
const route = ref<Route>("meetings");
const selectedId = ref<string | null>(null);
const libraryOpen = ref(false);
const tasksFocused = ref(false);
const section = ref("dictation");
const list = ref<InstanceType<typeof MeetingList> | null>(null);
const viewport = window.matchMedia?.("(max-width: 1023px)");
const compact = ref(viewport?.matches ?? false);
const onResize = (event: MediaQueryListEvent) => {
  compact.value = event.matches;
  libraryOpen.value = false;
};
viewport?.addEventListener("change", onResize);
// Janela estreita ou tarefas abertas: a biblioteca recolhe e abre por cima pelo botão "Reuniões".
const collapsed = computed(() => compact.value || tasksFocused.value);
const libraryVisible = computed(
  () => route.value === "meetings" && (!collapsed.value || libraryOpen.value),
);
const { status, connected, connectionError } = useStatus();
const drafts = new Map<string, TextDraft>();
provide(textDraftsKey, drafts);
provide("meeting-tabs", new Map<string, string>());
const hasDrafts = () =>
  [...drafts.values()].some((draft) => draft.text !== draft.saved);
const beforeUnload = (event: BeforeUnloadEvent) => {
  if (hasDrafts()) event.preventDefault();
};
window.addEventListener("beforeunload", beforeUnload);
onBeforeUnmount(() => {
  viewport?.removeEventListener("change", onResize);
  window.removeEventListener("beforeunload", beforeUnload);
});
function openMeeting(id: string): void {
  tasksFocused.value = false;
  selectedId.value = id;
  route.value = "meetings";
  libraryOpen.value = false;
  void nextTick(() => {
    if (compact.value) document.getElementById("main-content")?.focus();
  });
}
function onDeleted(id: string): void {
  if (selectedId.value === id) {
    selectedId.value = null;
    tasksFocused.value = false;
  }
  libraryOpen.value = collapsed.value;
}
function openSettings(target = "dictation") {
  // o detalhe desmonta: ao voltar, ele avisa de novo se reabrir em Tarefas
  tasksFocused.value = false;
  section.value = target;
  route.value = "settings";
  libraryOpen.value = false;
}
function onTasksView(active: boolean): void {
  tasksFocused.value = active;
  libraryOpen.value = false;
}
// Botão Buscar: volta à biblioteca (abrindo o painel se ela estiver recolhida) e foca a busca.
function focusSearch(): void {
  route.value = "meetings";
  if (collapsed.value) libraryOpen.value = true;
  void nextTick(() => list.value?.focusSearch());
}
// Painel sobreposto: Esc fecha e devolve o foco ao botão "Reuniões".
function onLibraryKeydown(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !collapsed.value || !libraryOpen.value) return;
  if (event.defaultPrevented) return; // um diálogo ou menu dentro do painel já tratou
  libraryOpen.value = false;
  void nextTick(() => document.querySelector<HTMLElement>(".library-toggle")?.focus());
}
const KEY_NAMES: Record<string, string> = {
  ctrl: "Ctrl",
  super: "Super",
  alt: "Alt",
  shift: "Shift",
};
const dictationLabel = computed(() => {
  const d = status.value?.dictation;
  if (!d) return "Ditado indisponível";
  const key = d.key
    .split("+")
    .map((k) => KEY_NAMES[k.toLowerCase()] ?? k.toUpperCase())
    .join(" + ");
  return d.enabled ? `Ditado ativo · ${key}` : "Ditado pausado";
});
</script>
<template>
  <div
    class="shell"
    :class="{
      'settings-route': route === 'settings',
      'library-collapsed': collapsed,
      'library-open': libraryOpen,
    }"
  >
    <a class="skip-link" href="#main-content">Ir para o conteúdo</a>
    <TopBar
      :route="route"
      :library-toggle="collapsed"
      :library-open="libraryOpen"
      :compact="compact"
      @back="route = 'meetings'"
      @toggle-library="libraryOpen = !libraryOpen"
      @search="focusSearch"
      @audio-check="openSettings('audio')"
      @settings="openSettings()"
    >
      <RecordButton @started="openMeeting" @stopped="openMeeting" />
    </TopBar>
    <div class="workspace">
      <aside
        v-show="libraryVisible"
        id="library"
        class="library"
        aria-label="Biblioteca de reuniões"
        @keydown="onLibraryKeydown"
      >
        <MeetingList ref="list" :selected-id="selectedId" @select="openMeeting" />
        <footer class="library-footer">
          <div class="dictation">
            <AppIcon name="keyboard" :size="15" />{{ dictationLabel }}
          </div>
          <VersionLine />
        </footer>
      </aside>
      <main id="main-content" class="main" tabindex="-1">
        <div v-if="!status" class="connecting" role="status">
          <AppIcon name="refresh" :size="28" />
          <h1>Conectando ao serviço…</h1>
          <p v-if="connectionError">{{ connectionError }}</p>
          <p>Suas reuniões aparecerão quando a conexão estiver pronta.</p>
        </div>
        <template v-else>
          <div v-if="!connected" class="connection-banner" role="status">
            <AppIcon name="warning" :size="18" />
            <div>
              <strong>Reconectando ao serviço…</strong>
              <p v-if="connectionError">{{ connectionError }}</p>
              <p v-if="status.meeting.state === 'recording'">
                O estado da gravação será confirmado após a reconexão.
              </p>
            </div>
          </div>
          <MeetingsView
            v-if="route === 'meetings'"
            :meeting-id="selectedId"
            @deleted="onDeleted"
            @tasks-view="onTasksView"
          />
          <SettingsView
            v-else
            v-model:section="section"
            @back="route = 'meetings'"
          />
        </template>
      </main>
    </div>
  </div>
</template>
<style scoped>
.shell {
  height: 100dvh;
  display: flex;
  flex-direction: column;
}
.workspace {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 18px;
  padding: 0 var(--space-7) var(--space-6);
}
/* Vidro sem backdrop-filter: o painel tem diálogo (VersionLine) com position fixed (Ruling R2). */
.library {
  width: var(--list-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 0;
  padding: var(--space-4);
  border-radius: var(--radius-xl);
  background: var(--glass);
  border: 1px solid var(--glass-border);
}
.library-footer {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  flex-shrink: 0;
  padding: var(--space-3) var(--space-2) 0;
  border-top: 1px solid var(--glass-border);
}
.dictation {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-muted);
  font-size: 13px;
  line-height: 18px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.main:focus {
  outline: none;
}
.connecting {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  padding: var(--space-8);
  color: var(--text-muted);
  text-align: center;
}
.connecting h1 {
  font-size: 20px;
  line-height: 26px;
  color: var(--text);
}
.connecting p {
  margin: 0;
  max-width: 65ch;
}
.connection-banner {
  padding: var(--space-3) var(--space-6);
  margin-bottom: var(--space-3);
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border-radius: var(--radius-lg);
  background: var(--warning-surface);
  color: var(--warning);
  font-size: 13px;
  font-weight: 600;
}
.connection-banner p {
  margin: 4px 0 0;
}
.skip-link {
  position: fixed;
  z-index: 100;
  top: -80px;
  left: var(--space-4);
  padding: var(--space-3);
  background: var(--surface-solid);
  color: var(--text-accent);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
}
.skip-link:focus {
  top: var(--space-3);
}
/* Recolhida, a biblioteca abre como painel sobreposto ao detalhe: superfície opaca, já que o
   vidro deixaria o conteúdo de trás vazar. */
.library-collapsed .library {
  position: absolute;
  z-index: 10;
  top: 0;
  bottom: var(--space-6);
  left: var(--space-7);
  width: min(var(--list-width), calc(100% - 2 * var(--space-7)));
  background: var(--surface-solid);
  box-shadow: var(--shadow-popover);
}
@media (max-width: 1023px) {
  .workspace {
    padding: 0 var(--space-4) var(--space-4);
  }
  .library-collapsed .library {
    bottom: var(--space-4);
    left: var(--space-4);
  }
}
</style>
