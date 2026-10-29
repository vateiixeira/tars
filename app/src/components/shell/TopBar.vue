<script setup lang="ts">
// Barra superior (84 px, sem fundo): marca ou "Voltar à reunião" à esquerda, a cápsula de
// gravação no centro (slot) e as ferramentas à direita, com o ponto de status da conexão.
import { computed } from "vue";
import { useStatus } from "../../composables/useStatus";
import AppIcon from "../ui/AppIcon.vue";
import BrandOrb from "../ui/BrandOrb.vue";
import AppearanceMenu from "./AppearanceMenu.vue";

const props = defineProps<{
  route: "meetings" | "settings";
  /** Biblioteca recolhida (janela estreita ou tarefas abertas): mostra o botão "Reuniões". */
  libraryToggle: boolean;
  libraryOpen: boolean;
  /** Janela estreita: o botão "Reuniões" ocupa o lugar da marca. */
  compact: boolean;
}>();
const emit = defineEmits<{
  back: [];
  toggleLibrary: [];
  search: [];
  audioCheck: [];
  settings: [];
}>();

const { connected, connection, connectionError } = useStatus();
const connState = computed(() =>
  connected.value ? "ok" : connection.value === "open" ? "connecting" : "offline",
);
const connLabel = computed(
  () =>
    ({ ok: "Serviço conectado", connecting: "Conectando…", offline: "Serviço desconectado" })[
      connState.value
    ],
);
const connTitle = computed(() =>
  connectionError.value && !connected.value ? `${connLabel.value}: ${connectionError.value}` : connLabel.value,
);
const showToggle = computed(() => props.route === "meetings" && props.libraryToggle);
</script>

<template>
  <header class="appbar topbar">
    <div class="start">
      <button
        v-if="route === 'settings'"
        type="button"
        class="btn ghost back"
        @click="emit('back')"
      >
        <AppIcon name="arrow" :size="18" />Voltar à reunião
      </button>
      <template v-else>
        <button
          v-if="showToggle"
          type="button"
          class="btn ghost library-toggle"
          aria-controls="library"
          :aria-expanded="libraryOpen"
          @click="emit('toggleLibrary')"
        >
          <AppIcon :name="libraryOpen ? 'close' : 'list'" :size="18" />{{
            libraryOpen ? "Fechar biblioteca" : "Reuniões"
          }}
        </button>
        <span v-if="!(showToggle && compact)" class="brand"><BrandOrb :size="36" />TARS</span>
      </template>
    </div>
    <div class="center"><slot /></div>
    <div class="tools">
      <button type="button" class="icon-button" aria-label="Buscar" title="Buscar" @click="emit('search')">
        <AppIcon name="search" :size="18" />
      </button>
      <button
        type="button"
        class="icon-button"
        aria-label="Conferir áudio"
        title="Conferir áudio"
        @click="emit('audioCheck')"
      >
        <AppIcon name="headphones" :size="18" />
      </button>
      <AppearanceMenu />
      <span class="conn-dot" :class="connState" role="img" :aria-label="connLabel" :title="connTitle" />
      <button
        type="button"
        class="icon-button settings-link"
        aria-label="Configurações"
        title="Configurações"
        :aria-current="route === 'settings' ? 'page' : undefined"
        @click="emit('settings')"
      >
        <AppIcon name="settings" :size="18" />
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: grid;
  /* as laterais nunca encolhem abaixo do conteúdo: quem cede espaço é a cápsula (o rótulo
     "Seguindo: …" corta com reticências) */
  grid-template-columns: minmax(max-content, 1fr) minmax(0, auto) minmax(max-content, 1fr);
  align-items: center;
  gap: var(--space-4);
  height: var(--topbar-height);
  flex-shrink: 0;
  padding: 0 var(--space-7);
}
.start {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 700;
  font-size: 20px;
  letter-spacing: -0.01em;
  white-space: nowrap;
}
.back,
.library-toggle {
  flex-shrink: 0;
  padding-left: var(--space-3);
  white-space: nowrap;
}
.center {
  display: flex;
  justify-content: center;
  min-width: 0;
}
.tools {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
}
.settings-link[aria-current="page"] {
  background: var(--selected);
  border-color: var(--selected-border);
  color: var(--text-accent);
}
.conn-dot {
  width: 8px;
  height: 8px;
  margin-left: var(--space-1);
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--danger);
}
.conn-dot.connecting {
  background: var(--warning);
}
.conn-dot.ok {
  background: var(--success);
}
@media (max-width: 1023px) {
  .topbar {
    gap: var(--space-2);
    padding: 0 var(--space-4);
  }
  .tools {
    gap: var(--space-1);
  }
}
</style>
