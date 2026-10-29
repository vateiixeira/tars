<script setup lang="ts">
import AppIcon from "./AppIcon.vue";
import AppLoader from "./AppLoader.vue";

withDefaults(
  defineProps<{
    tone?: "neutral" | "ok" | "accent" | "warn" | "danger";
    /** Nome de um ícone do AppIcon. */
    icon?: string;
    /** Mostra o loader pequeno no lugar do ícone. */
    busy?: boolean;
  }>(),
  { tone: "neutral", icon: undefined, busy: false },
);
</script>

<template>
  <span class="status" :class="`tone-${tone}`">
    <AppLoader v-if="busy" size="sm" />
    <AppIcon v-else-if="icon" :name="icon" :size="14" />
    <slot />
  </span>
</template>

<style scoped>
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--glass-border);
  background: var(--glass);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.tone-ok {
  background: var(--success-surface);
  border-color: transparent;
  color: var(--success);
}
.tone-warn {
  background: var(--warning-surface);
  border-color: transparent;
  color: var(--warning);
}
.tone-danger {
  background: var(--danger-surface);
  border-color: transparent;
  color: var(--danger);
}
.tone-accent {
  background: var(--selected);
  border-color: transparent;
  color: var(--text-accent);
}
</style>
