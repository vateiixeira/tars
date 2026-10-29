<script setup lang="ts">
import { ref } from "vue";
import { useFocusScope } from "../../composables/useFocusScope";
import AppIcon from "./AppIcon.vue";
defineProps<{ title: string }>();
const emit = defineEmits<{ close: [] }>();
const root = ref<HTMLElement | null>(null);
useFocusScope(root, true, () =>
  root.value?.querySelector<HTMLElement>("[data-dialog-cancel]"),
);
</script>
<template>
  <div
    ref="root"
    class="app-dialog-layer"
    role="dialog"
    aria-modal="true"
    :aria-label="title"
    tabindex="-1"
    @keydown.esc.stop.prevent="emit('close')"
  >
    <div class="app-dialog-backdrop" @click="emit('close')" />
    <section class="app-dialog">
      <header>
        <h2>{{ title }}</h2>
        <button
          class="icon-button"
          type="button"
          aria-label="Fechar diálogo"
          @click="emit('close')"
        >
          <AppIcon name="close" :size="18" />
        </button>
      </header>
      <slot />
    </section>
  </div>
</template>
<style scoped>
.app-dialog-layer {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 16px;
}
.app-dialog-backdrop {
  position: absolute;
  inset: 0;
  background: var(--backdrop);
}
.app-dialog {
  z-index: 1;
  width: min(520px, 100%);
  max-height: 85dvh;
  overflow: auto;
  padding: var(--space-6);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  background: var(--surface-raised);
  box-shadow: var(--shadow-dialog);
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
h2 {
  margin: 0;
  font-size: 20px;
  line-height: 26px;
  font-weight: 700;
}
</style>
