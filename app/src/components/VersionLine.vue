<script setup lang="ts">
// Versão no rodapé da barra lateral, com aviso (e diálogo de como atualizar) quando há release
// nova ou quando o app e o serviço estão em versões diferentes.
import { computed, onBeforeUnmount, ref } from "vue";
import { openUrl } from "@tauri-apps/plugin-opener";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";
import { useStatus } from "../composables/useStatus";
import { appVersion } from "../lib/appVersion";
import AppButton from "./ui/AppButton.vue";
import AppDialog from "./ui/AppDialog.vue";

const UPDATE_COMMAND = "python3 scripts/update.py";

const { status } = useStatus();
const info = computed(() => status.value?.version ?? null);
const mismatch = computed(() => info.value !== null && info.value.current !== appVersion);
const updatable = computed(() => !mismatch.value && info.value?.update_available === true);

const open = ref(false);
const copied = ref(false);
let copiedTimer: ReturnType<typeof setTimeout> | undefined;

function openLink(url: string): void {
  openUrl(url).catch((e) => console.error("Falha ao abrir o navegador", e));
}

async function copy(): Promise<void> {
  try {
    await writeText(UPDATE_COMMAND);
    copied.value = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (copied.value = false), 2000);
  } catch (e) {
    console.error("Falha ao copiar o comando", e);
  }
}

onBeforeUnmount(() => clearTimeout(copiedTimer));
</script>

<template>
  <div class="version-line">
    <button v-if="mismatch" type="button" class="link" @click="open = true">
      app {{ appVersion }} · serviço {{ info!.current }}
    </button>
    <template v-else>
      TARS {{ appVersion }}<template v-if="updatable"> · <button type="button" class="link" @click="open = true">atualizar para {{ info!.latest }}</button></template>
    </template>

    <AppDialog
      v-if="open && info"
      :title="mismatch ? 'Versões diferentes' : `TARS ${info.latest} disponível`"
      @close="open = false"
    >
      <div class="body">
        <template v-if="mismatch">
          <p>
            O app está na versão {{ appVersion }} e o serviço na {{ info.current }}. Para igualar, recompile o app
            com o comando abaixo e reabra o app.
          </p>
        </template>
        <p v-else-if="info.install === 'git'">Para atualizar, rode este comando na pasta do projeto:</p>
        <p v-else>
          Baixe o pacote da versão {{ info.latest }} e reinstale o TARS no lugar da versão {{ appVersion }}.
        </p>

        <div v-if="mismatch || info.install === 'git'" class="command">
          <code>{{ UPDATE_COMMAND }}</code>
          <AppButton size="sm" @click="copy">{{ copied ? "Copiado" : "Copiar" }}</AppButton>
        </div>

        <div v-if="!mismatch && info.url" class="actions">
          <AppButton v-if="info.install === 'package'" variant="primary" @click="openLink(info.url)">
            Baixar a versão {{ info.latest }}
          </AppButton>
          <AppButton v-else variant="ghost" @click="openLink(info.url)">Ver novidades</AppButton>
        </div>
      </div>
    </AppDialog>
  </div>
</template>

<style scoped>
.version-line {
  font-size: 0.6875rem;
  color: var(--muted);
}
.link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--accent);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: var(--text);
  font-size: var(--font-control);
}
.body p {
  margin: 0;
}
.command {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.command code {
  overflow-wrap: anywhere;
}
.actions {
  display: flex;
  justify-content: flex-end;
}
</style>
