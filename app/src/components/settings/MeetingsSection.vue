<script setup lang="ts">
// Seção Reuniões: título e resumo automáticos, prompt do resumo, motor de separação de falantes, limiar de voz por motor, prints de tela e token do Hugging Face.
import { computed, reactive, ref, watch } from "vue";
import { openUrl } from "@tauri-apps/plugin-opener";
import { DEFAULT_SUMMARY_PROMPT, HF_MODEL_URL, MEETING_MODEL_IDLE_OPTIONS } from "./constants";
import type { Diarizer, PrintMode, PrintMonitor } from "../../api/types";
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

const printMode = computed<PrintMode>(() => props.form.settings.value?.print_mode ?? "off");

async function savePrintMode(event: Event): Promise<void> {
  const select = event.target as HTMLSelectElement;
  if (!(await props.form.save("print_mode", select.value as PrintMode))) select.value = printMode.value;
}

/** Monitores ligados, carregados ao entrar no modo Nativo (fora do GNOME a lista vem vazia). */
const monitors = ref<PrintMonitor[]>([]);
const monitorsState = ref<"loading" | "loaded" | "error">("loading");
const monitorsError = ref<string | undefined>();
const printMonitor = computed(() => props.form.settings.value?.print_monitor ?? "");

/** Fabricante|modelo|série, sem a porta: o mesmo monitor em outra porta continua sendo ele. */
const modelOf = (id: string): string => id.slice(0, id.lastIndexOf("|"));

/** Item da lista que corresponde ao monitor salvo, com a mesma regra do backend (`find_monitor`). */
const matchedMonitor = computed(() => {
  const id = printMonitor.value;
  if (!id) return null;
  const exact = monitors.value.find((m) => m.id === id);
  if (exact) return exact;
  const same = monitors.value.filter((m) => modelOf(m.id) === modelOf(id));
  return same.length === 1 ? same[0] : null;
});

const monitorSelected = computed(() => matchedMonitor.value?.id ?? printMonitor.value);

/** Monitor salvo fora da lista: continua selecionado. Só é "desconectado" depois que a lista carregou. */
const savedMonitorOption = computed(() => {
  const id = printMonitor.value;
  if (!id || matchedMonitor.value) return null;
  const [vendor = "", product = ""] = id.split("|");
  const name = `${vendor} ${product}`.trim() || "Monitor";
  return { id, label: `${name} (${monitorsState.value === "loaded" ? "desconectado" : "salvo"})` };
});

async function loadMonitors(): Promise<void> {
  monitorsState.value = "loading";
  monitorsError.value = undefined;
  try {
    monitors.value = (await props.form.client.monitors()).monitors;
    monitorsState.value = "loaded";
  } catch {
    monitors.value = [];
    monitorsState.value = "error";
    monitorsError.value = "Não foi possível listar os monitores. Para trocar, escolha Todos os monitores ou reabra as configurações.";
  }
}

watch(printMode, (mode) => { if (mode === "native") void loadMonitors(); }, { immediate: true });

async function savePrintMonitor(event: Event): Promise<void> {
  const select = event.target as HTMLSelectElement;
  if (!(await props.form.save("print_monitor", select.value === "" ? null : select.value))) select.value = monitorSelected.value;
}

/** Rascunho dos campos de texto dos prints: o valor recusado pelo backend continua visível, com o erro embaixo. */
const printDrafts = reactive<{ print_folder?: string; print_key?: string }>({});
const printFolderShown = computed(() => printDrafts.print_folder ?? props.form.settings.value?.print_folder ?? "");
const printKeyShown = computed(() => printDrafts.print_key ?? props.form.settings.value?.print_key ?? "");

function onPrintInput(key: "print_folder" | "print_key", e: Event): void {
  printDrafts[key] = (e.target as HTMLInputElement).value;
  props.form.clearError(key);
}

async function savePrintFolder(e: Event): Promise<void> {
  // Vazio vai como `null` (o backend usa a pasta de Imagens).
  const value = (e.target as HTMLInputElement).value.trim();
  if (await props.form.save("print_folder", value === "" ? null : value)) delete printDrafts.print_folder;
}

async function savePrintKey(e: Event): Promise<void> {
  if (await props.form.save("print_key", (e.target as HTMLInputElement).value.trim())) delete printDrafts.print_key;
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
    <div class="s-card">
      <h3>Memória dos modelos</h3>
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
    </div>

    <div class="s-card">
      <h3>Título e resumo</h3>
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
    </div>

    <div class="s-card">
      <h3>Separação de falantes</h3>
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
            step="0.01"
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
    </div>

    <div class="s-card">
      <h3>Capturas durante a reunião</h3>
      <SettingField
        label="Prints de tela"
        for="meet-print-mode"
        hint="Guarda imagens da tela durante a reunião para anexar às tarefas e ao resumo."
        :error="form.errors.print_mode"
      >
        <select id="meet-print-mode" class="s-select" :value="printMode" :disabled="form.saving.print_mode" @change="savePrintMode">
          <option value="off">Desligado</option>
          <option value="flameshot">Flameshot</option>
          <option value="native">Nativo</option>
        </select>
      </SettingField>

      <SettingField
        v-if="printMode === 'flameshot'"
        label="Pasta dos prints"
        for="meet-print-folder"
        hint="Vazio usa a pasta de Imagens. Os arquivos criados durante a reunião são copiados ao finalizar; os originais não são alterados."
        :error="form.errors.print_folder"
      >
        <input
          id="meet-print-folder"
          class="s-input"
          type="text"
          :value="printFolderShown"
          spellcheck="false"
          placeholder="Pasta de Imagens"
          @input="onPrintInput('print_folder', $event)"
          @change="savePrintFolder"
        />
      </SettingField>

      <SettingField
        v-if="printMode === 'native'"
        label="Atalho do print"
        for="meet-print-key"
        hint="Funciona só durante uma reunião. Captura o monitor escolhido abaixo e toca um som curto."
        :error="form.errors.print_key"
      >
        <input
          id="meet-print-key"
          class="s-input mono"
          type="text"
          :value="printKeyShown"
          spellcheck="false"
          @input="onPrintInput('print_key', $event)"
          @change="savePrintKey"
        />
      </SettingField>

      <SettingField
        v-if="printMode === 'native'"
        label="Monitor do print"
        for="meet-print-monitor"
        hint="Todos os monitores gera uma imagem só com todas as telas. Se o monitor escolhido estiver desconectado na hora, o print sai com a tela inteira."
        :error="form.errors.print_monitor ?? monitorsError"
      >
        <select
          id="meet-print-monitor"
          class="s-select"
          :value="monitorSelected"
          :disabled="form.saving.print_monitor"
          @change="savePrintMonitor"
        >
          <option value="">Todos os monitores</option>
          <option v-for="m in monitors" :key="m.id" :value="m.id">{{ m.label }}</option>
          <option v-if="savedMonitorOption" :value="savedMonitorOption.id">{{ savedMonitorOption.label }}</option>
        </select>
      </SettingField>
    </div>
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
  color: var(--text-accent);
}
</style>
