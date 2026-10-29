<script setup lang="ts">
// Seção Ditado: cada campo salva ao sair dele (blur/Enter/mudança) e mostra o erro embaixo.
import { reactive } from "vue";
import { PASTE_OPTIONS } from "./constants";
import SettingField from "./SettingField.vue";
import ToggleField from "./ToggleField.vue";
import type { SettingKey, SettingsForm } from "./useSettingsForm";

const props = defineProps<{ form: SettingsForm }>();

/** Rascunho por campo: o texto recusado pelo backend continua visível, com o erro embaixo. */
const drafts = reactive<Record<string, string>>({});

function shown(key: SettingKey, saved: unknown): string {
  return drafts[key] ?? String(saved ?? "");
}

function onInput(key: SettingKey, e: Event): void {
  drafts[key] = (e.target as HTMLInputElement).value;
  props.form.clearError(key);
}

async function commit(key: SettingKey, value: unknown): Promise<void> {
  if (await props.form.save(key, value as never)) delete drafts[key];
}

function saveText(key: SettingKey, e: Event): Promise<void> {
  return commit(key, (e.target as HTMLInputElement).value);
}

/** Número vazio vai como `null` para o backend recusar com a mensagem dele. */
function saveNumber(key: SettingKey, e: Event): Promise<void> {
  const raw = (e.target as HTMLInputElement).value.trim();
  return commit(key, raw === "" ? null : Number(raw.replace(",", ".")));
}

async function savePaste(e: Event): Promise<void> {
  const select = e.target as HTMLSelectElement;
  if (!(await props.form.save("paste", select.value))) select.value = props.form.settings.value?.paste ?? "";
}
</script>

<template>
  <section v-if="form.settings.value" class="s-section">
    <ToggleField
      :form="form"
      field="dictation_enabled"
      label="Ditado ligado"
      hint="Desligado, os atalhos do microfone e do áudio do computador ficam pausados."
    />

    <ToggleField
      :form="form"
      field="dictation_evaluation_enabled"
      label="Avaliador de ditado"
      hint="Guarda o áudio e a transcrição de cada ditado neste computador para avaliação posterior. A colagem continua normalmente. Desligar interrompe a coleta e mantém os exemplos já salvos."
    />

    <div class="s-grid">
      <SettingField label="Atalho do microfone" for="dict-key" hint="Ex.: ctrl+super, KEY_RIGHTCTRL, alt+KEY_SPACE." :error="form.errors.key">
        <input
          id="dict-key"
          class="s-input mono"
          type="text"
          :value="shown('key', form.settings.value.key)"
          spellcheck="false"
          @input="onInput('key', $event)"
          @change="saveText('key', $event)"
        />
      </SettingField>

      <SettingField
        label="Atalho do áudio do computador"
        for="dict-output-key"
        hint="Padrão: Ctrl esquerdo + Shift esquerdo. Segure para capturar a saída de áudio padrão do PC; solte para transcrever e colar. Outra tecla cancela a captura."
        :error="form.errors.output_key"
      >
        <input
          id="dict-output-key"
          class="s-input mono"
          type="text"
          :value="shown('output_key', form.settings.value.output_key)"
          spellcheck="false"
          @input="onInput('output_key', $event)"
          @change="saveText('output_key', $event)"
        />
      </SettingField>
    </div>

    <SettingField label="Colar com" for="dict-paste" class="s-narrow" :error="form.errors.paste">
      <select id="dict-paste" class="s-select" :value="form.settings.value.paste" @change="savePaste">
        <option v-for="opt in PASTE_OPTIONS" :key="opt" :value="opt">{{ opt }}</option>
      </select>
    </SettingField>

    <SettingField
      label="Prompt de estilo"
      for="dict-prompt"
      hint="Texto de exemplo que mostra ao Whisper o estilo esperado. Vazio desativa."
      :error="form.errors.prompt"
    >
      <textarea
        id="dict-prompt"
        class="s-textarea"
        :value="shown('prompt', form.settings.value.prompt)"
        @input="onInput('prompt', $event)"
        @change="saveText('prompt', $event)"
      ></textarea>
    </SettingField>

    <ToggleField
      :form="form"
      field="trailing_space"
      label="Espaço no fim"
      hint="Acrescenta um espaço depois do texto colado."
    />

    <div class="s-grid">
      <SettingField label="Duração mínima (s)" for="dict-min" :error="form.errors.min_sec">
        <input
          id="dict-min"
          class="s-input"
          type="number"
          min="0"
          step="0.1"
          :value="shown('min_sec', form.settings.value.min_sec)"
          @input="onInput('min_sec', $event)"
          @change="saveNumber('min_sec', $event)"
        />
      </SettingField>
      <SettingField label="Duração máxima (s)" for="dict-max" :error="form.errors.max_sec">
        <input
          id="dict-max"
          class="s-input"
          type="number"
          min="1"
          step="10"
          :value="shown('max_sec', form.settings.value.max_sec)"
          @input="onInput('max_sec', $event)"
          @change="saveNumber('max_sec', $event)"
        />
      </SettingField>
    </div>
    <p class="s-note">Gravações mais curtas que o mínimo são descartadas; a gravação para sozinha no máximo.</p>
  </section>
</template>
