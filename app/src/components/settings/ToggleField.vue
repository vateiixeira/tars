<script setup lang="ts">
// Liga/desliga que salva na hora; se o backend recusar, volta o controle ao valor confirmado.
import { computed } from "vue";
import type { SettingKey, SettingsForm } from "./useSettingsForm";

const props = defineProps<{
  form: SettingsForm;
  field: SettingKey;
  label: string;
  hint?: string;
}>();

const checked = computed(() => Boolean((props.form.settings.value as Record<string, unknown> | null)?.[props.field]));
const id = `toggle-${props.field}`;

async function onChange(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  const ok = await props.form.save(props.field, input.checked as never);
  if (!ok) input.checked = checked.value;
}
</script>

<template>
  <div class="toggle">
    <!-- a linha inteira é clicável; o nome acessível é só o título e a dica entra como descrição -->
    <label class="row" :for="id">
      <input :id="id" class="switch" type="checkbox" role="switch" :checked="checked" :disabled="form.saving[field]" :aria-labelledby="`${id}-label`" :aria-describedby="hint ? `${id}-hint` : undefined" :aria-invalid="form.errors[field] ? true : undefined" :aria-errormessage="form.errors[field] ? `${id}-error` : undefined" @change="onChange" />
      <span class="text">
        <span :id="`${id}-label`" class="title">{{ label }}</span>
        <span v-if="hint" :id="`${id}-hint`" class="s-note">{{ hint }}</span>
      </span>
    </label>
    <p v-if="form.errors[field]" :id="`${id}-error`" class="s-error" role="alert" data-test="field-error">{{ form.errors[field] }}</p>
  </div>
</template>

<style scoped>
.toggle {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  cursor: pointer;
}
.text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.title {
  font-size: 15px;
  line-height: 22px;
  font-weight: 700;
}
/* am-switch: o checkbox nativo desenhado como interruptor */
.switch {
  appearance: none;
  position: relative;
  flex-shrink: 0;
  width: 46px;
  height: 28px;
  margin: 0;
  border-radius: var(--radius-pill);
  background: var(--glass-strong);
  border: 1px solid var(--border-control);
  cursor: pointer;
  transition: background var(--duration-base);
}
.switch::after {
  content: "";
  position: absolute;
  top: 3px;
  left: 3px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--text-muted);
  transition:
    transform var(--duration-base) var(--ease-out),
    background var(--duration-base);
}
.switch:checked {
  background: linear-gradient(135deg, var(--accent-grad-from), var(--accent-grad-to));
  border-color: transparent;
  box-shadow: var(--shadow-orb);
}
.switch:checked::after {
  transform: translateX(18px);
  background: var(--on-accent);
}
.switch:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
@media (prefers-reduced-motion: reduce) {
  .switch,
  .switch::after {
    transition: none;
  }
}
</style>
