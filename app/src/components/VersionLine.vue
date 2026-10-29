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
// Divergência: refaz o build e o reinício para o código que já está no clone.
const FORCE_COMMAND = "python3 scripts/update.py --force";

function parts(version: string): number[] | null {
  const m = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** "older": app mais antigo que o serviço; "newer": mais novo; "unknown": não dá para comparar. */
function direction(app: string, service: string): "older" | "newer" | "unknown" {
  const a = parts(app);
  const b = parts(service);
  if (!a || !b) return "unknown";
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] < b[i] ? "older" : "newer";
  return "unknown";
}

const { status, connected } = useStatus();
// Sem conexão o último status é velho: a linha mostra só a versão do app.
const info = computed(() => (connected.value ? (status.value?.version ?? null) : null));
const mismatch = computed(() => info.value !== null && info.value.current !== appVersion);
const side = computed(() => (info.value ? direction(appVersion, info.value.current) : "unknown"));
const command = computed(() => (mismatch.value ? FORCE_COMMAND : UPDATE_COMMAND));
const showCommand = computed(() => info.value?.install === "git");
const updatable = computed(() => !mismatch.value && info.value?.update_available === true);

const open = ref(false);
const copied = ref(false);
let copiedTimer: ReturnType<typeof setTimeout> | undefined;

function openLink(url: string): void {
  openUrl(url).catch((e) => console.error("Falha ao abrir o navegador", e));
}

async function copy(): Promise<void> {
  try {
    await writeText(command.value);
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
          <p v-if="info.install === 'package'">
            O app está na versão {{ appVersion }} e o serviço na {{ info.current }}. Baixe o pacote da versão mais
            recente e reinstale o TARS para igualar as duas.
          </p>
          <p v-else-if="side === 'older'">
            O app está na versão {{ appVersion }} e o serviço na {{ info.current }}: o app não foi recompilado depois
            da atualização. O comando abaixo recompila o app e reinicia o serviço; depois, feche e reabra o app.
          </p>
          <p v-else-if="side === 'newer'">
            O app está na versão {{ appVersion }}, mas o serviço está na {{ info.current }}: o serviço ficou para
            trás. O comando abaixo refaz a instalação e reinicia o serviço.
          </p>
          <p v-else>
            O app está na versão {{ appVersion }} e o serviço na {{ info.current }}. O comando abaixo recompila o app
            e reinicia o serviço; depois, feche e reabra o app.
          </p>
        </template>
        <p v-else-if="info.install === 'git'">Para atualizar, rode este comando na pasta do projeto:</p>
        <p v-else>
          Baixe o pacote da versão {{ info.latest }} e reinstale o TARS no lugar da versão {{ appVersion }}.
        </p>

        <div v-if="showCommand" class="command">
          <code>{{ command }}</code>
          <AppButton size="sm" @click="copy">{{ copied ? "Copiado" : "Copiar" }}</AppButton>
        </div>

        <div v-if="info.url && (!mismatch || info.install === 'package')" class="actions">
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
  color: var(--text-muted);
}
.link {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-accent);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: var(--text);
  font-size: 14px;
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
  border: 1px solid var(--glass-border);
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
