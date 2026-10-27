<script setup lang="ts">
// Seção Reuniões: título e resumo automáticos, prompt do resumo, motor de separação de falantes, limiar de voz por motor e token do Hugging Face.
import { computed, ref } from "vue";
import { openUrl } from "@tauri-apps/plugin-opener";
import { DEFAULT_SUMMARY_PROMPT, HF_MODEL_URL, MEETING_MODEL_IDLE_OPTIONS } from "./constants";
import type { Diarizer } from "../../api/types";
import SettingField from "./SettingField.vue";
import ToggleField from "./ToggleField.vue";
import type { SettingsForm } from "./useSettingsForm";

const props = defineProps<{ form: SettingsForm }>();

const idleSeconds = computed(() => props.form.settings.value?.meeting_model_idle_sec ?? 120);
const idleOptions = computed(() => MEETING_MODEL_IDLE_OPTIONS.some((o) => o.value === idleSeconds.value)
  ? MEETING_MODEL_IDLE_OPTIONS
  : [...MEETING_MODEL_IDLE_OPTIONS, { value: idleSeconds.value, label: `Após ${idleSeconds.value} segundos` }]);

async function saveIdleTime(event: Event): Promise<void> {
  const select = event.target as HTMLSelectElement;
  if (!(await props.form.save("meeting_model_idle_sec", Number(select.value)))) select.value = String(idleSeconds.value);
}

const summary = ref<string | null>(null); // rascunho; null = igual ao salvo
const summaryShown = computed(() => summary.value ?? props.form.settings.value?.summary_prompt ?? "");
const isDefault = computed(() => summaryShown.value === DEFAULT_SUMMARY_PROMPT);

async function saveSummary(): Promise<void> {
  if (summary.value === null) return;
  if (await props.form.save("summary_prompt", summary.value)) summary.value = null;
}

async function restoreSummary(): Promise<void> {
  if (await props.form.save("summary_prompt", DEFAULT_SUMMARY_PROMPT)) summary.value = null;
}

const DIARIZER_OPTIONS: { value: Diarizer; label: string }[] = [
  { value: "local", label: "Local (grátis)" },
  { value: "pyannote", label: "pyannote (token do Hugging Face)" },
];
const diarizer = computed<Diarizer>(() => props.form.settings.value?.diarizer ?? "local");

async function saveDiarizer(e: Event): Promise<void> {
  const select = e.target as HTMLSelectElement;
  if (!(await props.form.save("diarizer", select.value as Diarizer))) select.value = diarizer.value;
}

// Um limiar por motor: o campo lê e salva o do motor escolhido.
const thresholdKey = computed(() => `voice_match_threshold_${diarizer.value}` as const);
const threshold = computed(() => props.form.settings.value?.[thresholdKey.value] ?? 0.6);
const thresholdText = computed(() => threshold.value.toFixed(2).replace(".", ","));

async function saveThreshold(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  if (!(await props.form.save(thresholdKey.value, Number(input.value)))) {
    input.value = String(threshold.value);
  }
}

// O webview não abre abas: o link vai para o navegador do sistema.
function openModelPage(): void {
  openUrl(HF_MODEL_URL).catch((e) => console.error("Falha ao abrir o navegador", e));
}

// O token nunca volta do backend: o campo começa e termina vazio.
const token = ref("");

async function saveToken(): Promise<void> {
  const value = token.value.trim();
  if (!value) return; // salvar vazio não apaga o token; remover é uma ação explícita
  if (await props.form.save("hf_token", value)) token.value = "";
}

async function removeToken(): Promise<void> {
  if (await props.form.save("hf_token", null)) token.value = "";
}
</script>

<template>
  <section v-if="form.settings.value" class="s-section">
    <h2>Reuniões</h2>

    <SettingField
      label="Liberar memória dos modelos de reunião"
      for="meet-model-idle"
      hint="Libera o Whisper das reuniões e o separador de vozes após o prazo sem gravação ou processamento. O ditado continua pronto. Na próxima reunião, a gravação começa imediatamente e os modelos são recarregados quando necessários."
      :error="form.errors.meeting_model_idle_sec"
    >
      <select id="meet-model-idle" class="s-select" :value="idleSeconds" :disabled="form.saving.meeting_model_idle_sec" @change="saveIdleTime">
        <option v-for="o in idleOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </SettingField>

    <ToggleField
      :form="form"
      field="auto_title"
      label="Título automático"
      hint="Ao terminar o processamento, o Claude sugere um título para a reunião."
    />

    <ToggleField
      :form="form"
      field="auto_summary"
      label="Resumir automaticamente"
      hint="Ao terminar o processamento, o Claude gera o resumo sem precisar clicar."
    />

    <SettingField
      label="Prompt do resumo"
      for="meet-summary"
      hint="Instrução enviada ao Claude junto com a transcrição quando você pede o resumo."
      :error="form.errors.summary_prompt"
    >
      <textarea
        id="meet-summary"
        class="s-textarea"
        :value="summaryShown"
        @input="summary = ($event.target as HTMLTextAreaElement).value; form.clearError('summary_prompt')"
        @change="saveSummary"
      ></textarea>
      <div class="s-row">
        <button class="s-btn" :disabled="isDefault" data-test="restore-summary" @click="restoreSummary">
          Restaurar padrão
        </button>
      </div>
    </SettingField>

    <SettingField
      label="Motor de separação de falantes"
      for="meet-diarizer"
      hint="O motor local funciona sem cadastro; os modelos são baixados na primeira reunião. A escolha vale a partir da próxima reunião ou reprocessamento."
      :error="form.errors.diarizer"
    >
      <select
        id="meet-diarizer"
        class="s-select"
        data-test="diarizer-select"
        :value="diarizer"
        :disabled="form.saving.diarizer"
        @change="saveDiarizer"
      >
        <option v-for="o in DIARIZER_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </SettingField>

    <SettingField
      label="Limiar de reconhecimento de voz"
      for="meet-threshold"
      hint="Quanto maior, mais parecida a voz precisa ser para ser reconhecida como uma voz cadastrada. Vale para o motor escolhido."
      :error="form.errors[thresholdKey]"
    >
      <div class="s-row">
        <input
          id="meet-threshold"
          type="range"
          min="0.1"
          max="0.95"
          step="0.05"
          :value="threshold"
          @input="form.clearError(thresholdKey)"
          @change="saveThreshold"
        />
        <output for="meet-threshold" data-test="threshold-value">{{ thresholdText }}</output>
      </div>
    </SettingField>

    <SettingField
      v-if="diarizer === 'pyannote'"
      label="Token do Hugging Face"
      for="meet-token"
      :error="form.errors.hf_token"
    >
      <div class="s-row">
        <input
          id="meet-token"
          v-model="token"
          class="s-input token"
          type="password"
          autocomplete="off"
          :placeholder="form.settings.value.hf_token_set ? 'Digite para trocar o token' : 'Cole o token aqui'"
          @input="form.clearError('hf_token')"
          @keydown.enter.prevent="saveToken"
        />
        <button class="s-btn primary" :disabled="!token.trim() || form.saving.hf_token" data-test="token-save" @click="saveToken">
          Salvar token
        </button>
        <button
          v-if="form.settings.value.hf_token_set"
          class="s-btn danger"
          :disabled="form.saving.hf_token"
          data-test="token-remove"
          @click="removeToken"
        >
          Remover token
        </button>
      </div>
      <p v-if="form.settings.value.hf_token_set" class="s-ok" data-test="token-status">Token configurado</p>
      <p v-else class="s-note" data-test="token-status">Nenhum token configurado</p>
      <p class="s-note">
        Necessário para separar as vozes com o pyannote. Exige o extra pyannote instalado (uv sync --extra pyannote). Crie um token de leitura no Hugging Face e aceite as condições do modelo em
        <a :href="HF_MODEL_URL" data-test="hf-model-link" @click.prevent="openModelPage">pyannote/speaker-diarization-community-1</a>.
      </p>
    </SettingField>
  </section>
</template>

<style scoped>
input[type="range"] {
  flex: 1;
  max-width: 320px;
  accent-color: var(--accent);
}
output {
  font-variant-numeric: tabular-nums;
  min-width: 3ch;
}
.token {
  flex: 1;
  width: auto;
  min-width: 200px;
}
a {
  color: var(--accent);
}
</style>
