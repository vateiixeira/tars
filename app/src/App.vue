<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, provide, ref } from "vue";
import MeetingList from "./components/MeetingList.vue";
import RecordButton from "./components/RecordButton.vue";
import VersionLine from "./components/VersionLine.vue";
import AppearanceControl from "./components/ui/AppearanceControl.vue";
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
const viewport = window.matchMedia?.("(max-width: 1023px)");
const compact = ref(viewport?.matches ?? false);
const onResize = (event: MediaQueryListEvent) => {
  compact.value = event.matches;
  libraryOpen.value = false;
};
viewport?.addEventListener("change", onResize);
const { connected, connection, connectionError, status } = useStatus();
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
  if (selectedId.value === id) selectedId.value = null;
  libraryOpen.value = compact.value;
}
function openSettings(target = "dictation") {
  section.value = target;
  route.value = "settings";
  libraryOpen.value = false;
}
const connectionLabel = computed(() =>
  connected.value
    ? "Serviço conectado"
    : connection.value === "open"
      ? "Conectando…"
      : "Serviço desconectado",
);
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
      'library-open': libraryOpen,
      'tasks-route': route === 'meetings' && tasksFocused,
    }"
  >
    <a class="skip-link" href="#main-content">Ir para o conteúdo</a>
    <header v-show="route === 'settings' || (compact && !tasksFocused)" class="appbar">
      <button
        type="button"
        class="btn ghost"
        @click="
          route === 'settings'
            ? (route = 'meetings')
            : (libraryOpen = !libraryOpen)
        "
      >
        <AppIcon :name="libraryOpen ? 'close' : 'arrow'" :size="17" />{{
          route === "settings"
            ? "Voltar à reunião"
            : libraryOpen
              ? "Fechar biblioteca"
              : "Reuniões"
        }}
      </button>
      <div id="capture-global" />
      <AppearanceControl />
    </header>
    <div class="workspace">
      <aside
        v-show="route === 'meetings' && !tasksFocused"
        class="sidebar"
        aria-label="Biblioteca de reuniões"
      >
        <div class="brand">
          <span class="brand-mark"><AppIcon name="mic" :size="22" /></span
          ><span>TARS<small>Reuniões de equipe</small></span>
        </div>
        <div id="capture-sidebar" />
        <button
          type="button"
          class="audio-check"
          @click="openSettings('audio')"
        >
          <AppIcon name="headphones" :size="16" />Conferir áudio
        </button>
        <MeetingList :selected-id="selectedId" @select="openMeeting" />
        <footer class="sidebar-footer">
          <div class="dictation">
            <AppIcon name="mic" :size="16" />{{ dictationLabel }}
          </div>
          <button
            type="button"
            class="settings-link"
            aria-label="Configurações"
            @click="openSettings()"
          >
            <AppIcon name="settings" :size="18" />Configurações
          </button>
          <AppearanceControl v-if="!compact" />
          <div
            class="conn"
            :class="{ ok: connected }"
            :title="connectionError ?? undefined"
          >
            <span class="status-dot" aria-hidden="true" />{{ connectionLabel }}
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
            @tasks-view="tasksFocused = $event; libraryOpen = false"
          />
          <SettingsView
            v-else
            v-model:section="section"
            @back="route = 'meetings'"
          />
        </template>
      </main>
    </div>
    <Teleport
      defer
      :to="
        route === 'settings' || compact ? '#capture-global' : '#capture-sidebar'
      "
      ><RecordButton @started="openMeeting" @stopped="openMeeting"
    /></Teleport>
  </div>
</template>
<style scoped>
.shell {
  height: 100dvh;
  display: flex;
  flex-direction: column;
}
.workspace {
  flex: 1;
  min-height: 0;
  display: flex;
}
.sidebar {
  width: var(--nav-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 28px 16px 16px;
  background: var(--panel);
  border-right: 1px solid var(--border);
  min-height: 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 8px 28px;
  font-weight: 650;
  font-size: 1.125rem;
  letter-spacing: -0.025em;
  line-height: 1.1;
}
.brand small {
  display: block;
  font-size: 0.75rem;
  font-weight: 400;
  margin-top: 5px;
  letter-spacing: 0;
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 44px;
  border-radius: 12px;
  color: var(--on-accent);
  background: var(--accent);
}
.audio-check {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  min-height: 40px;
  padding: 8px;
  margin: 4px 0 16px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: var(--font-meta);
}
.audio-check:hover {
  color: var(--text-accent);
}
.sidebar-footer {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 20px;
}
.dictation {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}
.settings-link {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 40px;
  padding: 8px;
  background: transparent;
  color: var(--text);
  border: 0;
  border-radius: var(--radius-md);
  text-align: left;
  font-size: var(--font-control);
}
.settings-link:hover {
  background: var(--hover);
}
.conn {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 0.6875rem;
  color: var(--muted);
  padding: 14px 0 0;
  border-top: 1px solid var(--border);
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warn);
}
.conn.ok .status-dot {
  background: var(--success);
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
  gap: 16px;
  padding: 32px;
  color: var(--muted);
  text-align: center;
}
.connecting h1 {
  font-size: var(--font-h2);
  color: var(--text);
}
.connecting p {
  margin: 0;
  max-width: 65ch;
}
.connection-banner {
  padding: 12px 24px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: var(--warning-surface);
  color: var(--warn);
  font-size: var(--font-meta);
}
.connection-banner p {
  margin: 4px 0 0;
}
.appbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 56px;
  padding: 8px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}
#capture-global {
  max-width: 300px;
}
.skip-link {
  position: fixed;
  z-index: 100;
  top: -80px;
  left: 16px;
  padding: 12px;
  background: var(--surface);
  color: var(--text-accent);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
}
.skip-link:focus {
  top: 12px;
}
@media (max-width: 1023px) {
  .sidebar {
    display: none;
  }
  .library-open .sidebar {
    display: flex;
    width: 100%;
  }
  .library-open:not(.settings-route) .main {
    display: none;
  }
  .appbar :deep(.rec small) {
    display: none;
  }
  .appbar :deep(.rec) {
    padding: 8px 12px;
    min-height: 40px;
  }
  .appbar .btn {
    font-size: 0.75rem;
    padding: 8px;
  }
  .appbar {
    padding: 8px 12px;
    gap: 8px;
    flex-wrap: wrap;
  }
  .appbar :deep(.appearance-control select) {
    max-width: 150px;
  }
}
@media (max-width: 540px) {
  .appbar {
    align-items: center;
  }
  .appbar :deep(.appearance-control) {
    margin-left: auto;
  }
  #capture-global {
    order: 3;
    width: 100%;
    max-width: none;
  }
}
</style>
