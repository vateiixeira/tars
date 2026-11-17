<script setup lang="ts">
// Botão "Importar" da barra superior: escolhe um arquivo pelo diálogo nativo (ou recebe o que for
// arrastado para a janela) e abre a tela de confirmação (`ImportDialog`). O backend lê o arquivo
// direto do disco pelo caminho. Emite `imported` com a reunião criada, para a janela abri-la.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { useStatus } from "../composables/useStatus";
import AppButton from "./ui/AppButton.vue";
import AppIcon from "./ui/AppIcon.vue";
import ImportDialog from "./ImportDialog.vue";

const emit = defineEmits<{ imported: [meetingId: string] }>();
const { connected } = useStatus();

// Espelho de `MEDIA_EXTENSIONS` (src/whisper_ditado/meeting/importer.py), sem o ponto.
const MEDIA_EXTENSIONS = ["mp4", "mkv", "mov", "webm", "mp3", "m4a", "wav", "ogg", "opus", "flac", "aac"];
// O primeiro filtro (o que o GTK abre marcado) aceita todos os tipos, sem precisar trocar de filtro.
const FILTERS = [
  { name: "Transcrição, vídeo ou áudio", extensions: ["txt", ...MEDIA_EXTENSIONS] },
  { name: "Transcrição do Read AI", extensions: ["txt"] },
  { name: "Vídeo ou áudio", extensions: MEDIA_EXTENSIONS },
];

// Arquivo em confirmação; `null` = tela fechada.
const path = ref<string | null>(null);
const choosing = ref(false);

async function choose(): Promise<void> {
  if (choosing.value || path.value) return;
  choosing.value = true;
  try {
    const picked = await open({ multiple: false, directory: false, filters: FILTERS });
    if (typeof picked === "string") path.value = picked;
  } catch (e) {
    console.error("Não foi possível abrir a escolha de arquivo", e);
  } finally {
    choosing.value = false;
  }
}

function onImported(meetingId: string): void {
  path.value = null;
  emit("imported", meetingId);
}

// Arrastar e soltar na janela: só o primeiro arquivo, e só com a tela de confirmação fechada.
let stopped = false;
let unlisten: (() => void) | null = null;
onMounted(() => {
  try {
    getCurrentWebview()
      .onDragDropEvent(({ payload }) => {
        if (payload.type !== "drop" || path.value || choosing.value) return;
        const first = payload.paths[0];
        if (first) path.value = first;
      })
      .then((off) => {
        if (stopped) off();
        else unlisten = off;
      })
      .catch(() => {});
  } catch {
    // fora do Tauri: fica só o botão
  }
});
onBeforeUnmount(() => {
  stopped = true;
  unlisten?.();
});
</script>

<template>
  <AppButton class="import-button" :disabled="!connected || choosing" title="Importar gravação ou transcrição" @click="choose">
    <AppIcon name="upload" :size="18" /><span class="label">Importar</span>
  </AppButton>
  <ImportDialog v-if="path" :key="path" :path="path" @close="path = null" @imported="onImported" />
</template>

<style scoped>
/* Secundário com contorno: não compete com "Iniciar reunião" */
.import-button {
  flex-shrink: 0;
  margin-left: var(--space-3);
  border-color: var(--selected-border);
  color: var(--text-accent);
}
@media (max-width: 1023px) {
  .import-button {
    margin-left: var(--space-2);
    padding-inline: var(--space-3);
  }
  .label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }
}
</style>
