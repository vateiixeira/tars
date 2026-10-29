<script setup lang="ts">
import { nextTick, onMounted, onUpdated, ref, useId } from "vue";
const props = defineProps<{
  label: string;
  for?: string;
  hint?: string;
  error?: string;
}>();
const root = ref<HTMLElement | null>(null);
const uid = `field-${useId()}`;
// Adapter para controles existentes, inclusive grupos com controles dentro do slot.
function associate() {
  const controls = root.value?.querySelectorAll<
    HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
  >("input,select,textarea");
  controls?.forEach((control, index) => {
    if (!control.id)
      control.id = index === 0 ? props.for || uid : `${uid}-${index}`;
    const previous = (control.getAttribute("aria-describedby") || "")
      .split(" ")
      .filter((id) => id && id !== `${uid}-hint` && id !== `${uid}-error`);
    if (props.hint) previous.push(`${uid}-hint`);
    if (props.error) previous.push(`${uid}-error`);
    if (previous.length)
      control.setAttribute("aria-describedby", previous.join(" "));
    else control.removeAttribute("aria-describedby");
    if (props.error) control.setAttribute("aria-invalid", "true");
    else control.removeAttribute("aria-invalid");
  });
  const first = controls?.[0];
  if (first) root.value?.querySelector("label")?.setAttribute("for", first.id);
}
onMounted(() => void nextTick(associate));
onUpdated(associate);
</script>
<template>
  <div ref="root" class="field">
    <label :for="$props.for || uid" class="label">{{ label }}</label
    ><slot />
    <p v-if="hint" :id="`${uid}-hint`" class="s-note">{{ hint }}</p>
    <p
      v-if="error"
      :id="`${uid}-error`"
      class="s-error"
      role="alert"
      data-test="field-error"
    >
      {{ error }}
    </p>
  </div>
</template>
<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.label {
  font-size: 13px;
  line-height: 18px;
  font-weight: 700;
}
</style>
