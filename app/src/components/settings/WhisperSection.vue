<script setup lang="ts">
import { computed } from "vue";
import { WHISPER_MODELS, WHISPER_PRECISIONS } from "./constants";
import SettingField from "./SettingField.vue";
import type { SettingsForm } from "./useSettingsForm";

const props = defineProps<{ form: SettingsForm }>();
const model = computed(() => props.form.settings.value?.model ?? "large-v3-turbo");
const precision = computed(() => props.form.settings.value?.compute_type ?? "float16");
const knownModel = computed(() => WHISPER_MODELS.some((o) => o.value === model.value));
const knownPrecision = computed(() => WHISPER_PRECISIONS.some((o) => o.value === precision.value));
const modelHint = computed(() => WHISPER_MODELS.find((o) => o.value === model.value)?.hint ?? "Modelo personalizado já configurado no serviço.");
const precisionHint = computed(() => WHISPER_PRECISIONS.find((o) => o.value === precision.value)?.hint ?? "Precisão personalizada já configurada no serviço.");

async function save(key: "model" | "compute_type", event: Event): Promise<void> {
  const select = event.target as HTMLSelectElement;
  if (!(await props.form.save(key, select.value))) select.value = props.form.settings.value?.[key] ?? "";
}
</script>

<template>
  <section v-if="form.settings.value" class="s-section">
    <p class="s-note">Modelo local de transcrição usado pelo ditado e pelas reuniões. As opções abaixo reconhecem português.</p>

    <div class="s-grid">
      <SettingField label="Modelo de transcrição" for="whisper-model" :hint="modelHint" :error="form.errors.model">
        <select id="whisper-model" class="s-select" :value="model" :disabled="form.saving.model" @change="save('model', $event)">
          <option v-for="o in WHISPER_MODELS" :key="o.value" :value="o.value">{{ o.label }}</option>
          <option v-if="!knownModel" :value="model">{{ model }} (personalizado)</option>
        </select>
      </SettingField>

      <SettingField label="Uso de memória do modelo" for="whisper-precision" :hint="precisionHint" :error="form.errors.compute_type">
        <select id="whisper-precision" class="s-select" :value="precision" :disabled="form.saving.compute_type" @change="save('compute_type', $event)">
          <option v-for="o in WHISPER_PRECISIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
          <option v-if="!knownPrecision" :value="precision">{{ precision }} (personalizada)</option>
        </select>
      </SettingField>
    </div>
    <p class="s-note">O ditado troca de modelo em segundo plano. As reuniões em andamento continuam com o modelo atual; a troca acontece quando ficam ociosas.</p>
    <p class="s-note">Na primeira escolha de um modelo, o serviço pode precisar baixá-lo. O modelo anterior continua atendendo o ditado até o novo estar pronto.</p>
    <p class="s-note">Para liberar a memória das reuniões quando não estiverem em uso, ajuste o prazo na seção Reuniões.</p>
  </section>
</template>
