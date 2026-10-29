<script setup lang="ts">
// Seção Criador de issues: liga o levantamento de tarefas ao fim de cada reunião, define a pasta raiz dos
// projetos, o endereço do MCP de issues (com teste de conexão) e o prompt do levantamento.
import { computed, ref } from "vue";
import { messageOf, type SettingsForm } from "./useSettingsForm";
import SettingField from "./SettingField.vue";
import ToggleField from "./ToggleField.vue";

const props = defineProps<{ form: SettingsForm }>();

// Rascunhos; null = igual ao salvo.
const root = ref<string | null>(null);
const url = ref<string | null>(null);
const server = ref<string | null>(null);
const prompt = ref<string | null>(null);

const rootShown = computed(() => root.value ?? props.form.settings.value?.issues_root ?? "");
const serverShown = computed(() => server.value ?? props.form.settings.value?.issues_mcp_name ?? "issues");
const urlShown = computed(() => url.value ?? props.form.settings.value?.issues_mcp_url ?? "");
const promptShown = computed(() => prompt.value ?? props.form.settings.value?.tasks_prompt ?? "");

async function saveRoot(): Promise<void> {
  if (root.value === null) return;
  if (await props.form.save("issues_root", root.value)) root.value = null;
}

async function saveUrl(): Promise<void> {
  if (url.value === null) return;
  if (await props.form.save("issues_mcp_url", url.value)) {
    url.value = null;
    status.value = null; // o "Conectado como" era do endereço antigo
  }
}

async function saveServer(): Promise<void> {
  if (server.value === null) return;
  if (await props.form.save("issues_mcp_name", server.value)) {
    server.value = null;
    status.value = null;
  }
}

async function savePrompt(): Promise<void> {
  if (prompt.value === null) return;
  if (await props.form.save("tasks_prompt", prompt.value)) prompt.value = null;
}

// O front não conhece o texto padrão: null pede ao backend que volte a usá-lo.
async function restorePrompt(): Promise<void> {
  if (await props.form.save("tasks_prompt", null)) prompt.value = null;
}

const testing = ref(false);
const status = ref<{ ok: boolean; text: string } | null>(null);

async function testConnection(): Promise<void> {
  testing.value = true;
  status.value = null;
  try {
    const r = await props.form.client.issueTest();
    status.value = { ok: true, text: r.username ? `Conectado como ${r.username}` : "Conexão estabelecida" };
  } catch (e) {
    status.value = { ok: false, text: messageOf(e) };
  } finally {
    testing.value = false;
  }
}
</script>

<template>
  <section v-if="form.settings.value" class="s-section">
    <ToggleField
      :form="form"
      field="issues_enabled"
      label="Criador de issues"
      hint="Ao terminar cada reunião, o Claude levanta as tarefas a partir da pasta raiz dos projetos."
    />

    <SettingField
      label="Pasta raiz dos projetos"
      for="issues-root"
      hint="O Claude lê os CLAUDE.md e o código daqui (só leitura) para escrever as issues."
      :error="form.errors.issues_root"
    >
      <input
        id="issues-root"
        class="s-input"
        type="text"
        autocomplete="off"
        :value="rootShown"
        @input="root = ($event.target as HTMLInputElement).value; form.clearError('issues_root')"
        @change="saveRoot"
      />
    </SettingField>

    <div class="s-grid">
      <SettingField
        label="Endereço do MCP de issues"
        hint="Informe a URL HTTPS do seu MCP, com as ferramentas listar_opcoes e criar_issue."
        for="issues-url"
        :error="form.errors.issues_mcp_url"
      >
        <input
          id="issues-url"
          class="s-input"
          type="text"
          autocomplete="off"
          :value="urlShown"
          @input="url = ($event.target as HTMLInputElement).value; form.clearError('issues_mcp_url')"
          @change="saveUrl"
        />
      </SettingField>

      <SettingField
        label="Nome do servidor MCP"
        for="issues-server"
        hint="Use o mesmo nome configurado no Claude Code para aproveitar o login. Padrão: issues."
        :error="form.errors.issues_mcp_name"
      >
        <input
          id="issues-server"
          class="s-input"
          type="text"
          autocomplete="off"
          spellcheck="false"
          :value="serverShown"
          @input="server = ($event.target as HTMLInputElement).value; form.clearError('issues_mcp_name')"
          @change="saveServer"
        />
      </SettingField>
    </div>

    <SettingField label="Conexão com o MCP de issues">
      <div class="s-row">
        <button class="s-btn" :disabled="testing" data-test="issues-test" @click="testConnection">
          {{ testing ? "Testando…" : "Testar conexão" }}
        </button>
      </div>
      <p
        v-if="status"
        :class="status.ok ? 's-ok' : 's-error'"
        :role="status.ok ? undefined : 'alert'"
        data-test="issues-status"
      >
        {{ status.text }}
      </p>
    </SettingField>

    <SettingField
      label="Prompt do levantamento"
      hint="O padrão usa Spec Driven Development (SDD): especificação, critérios de aceite, plano e validação. Adapte as regras aos seus projetos; as sugestões serão revisadas antes da criação."
      for="issues-prompt"
      :error="form.errors.tasks_prompt"
    >
      <textarea
        id="issues-prompt"
        class="s-textarea"
        :value="promptShown"
        @input="prompt = ($event.target as HTMLTextAreaElement).value; form.clearError('tasks_prompt')"
        @change="savePrompt"
      ></textarea>
      <div class="s-row">
        <button class="s-btn" :disabled="form.saving.tasks_prompt" data-test="restore-tasks-prompt" @click="restorePrompt">
          Restaurar padrão
        </button>
      </div>
    </SettingField>
  </section>
</template>
