<script setup lang="ts">
import AppIcon from "./AppIcon.vue";

// Aviso em faixa (am-banner): ícone, mensagem e, quando houver, uma ação à direita.
// O papel (`role="alert"` ou `"status"`) vem de quem usa, pois depende da urgência.
withDefaults(defineProps<{ tone?: "warn" | "danger" }>(), { tone: "danger" });
</script>

<template>
  <div class="app-banner" :class="`tone-${tone}`">
    <AppIcon name="warning" :size="18" />
    <div class="body"><slot /></div>
    <slot name="action" />
  </div>
</template>

<style scoped>
.app-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  border-radius: var(--radius-lg);
  background: var(--warning-surface);
  color: var(--warning);
  font-size: 13px;
  line-height: 19px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.tone-danger {
  background: var(--danger-surface);
  color: var(--danger);
}
.app-banner svg {
  flex-shrink: 0;
}
.body {
  min-width: 0;
}
.body :deep(strong) {
  display: block;
  font-size: 14px;
  font-weight: 700;
}
.app-banner :deep(.btn) {
  margin-left: auto;
  flex-shrink: 0;
}
</style>
