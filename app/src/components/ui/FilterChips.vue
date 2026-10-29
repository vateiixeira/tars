<script setup lang="ts">
// Chips de filtro (am-chips): botões com aria-pressed num grupo rotulado; um ligado por vez.
// `class` por opção deixa o chamador manter ganchos próprios (ex.: `.archive-toggle`).
defineProps<{
  options: { key: string; label: string; class?: string }[];
  label: string;
}>();
const model = defineModel<string>({ required: true });
</script>

<template>
  <div class="filter-chips" role="group" :aria-label="label">
    <button
      v-for="o in options"
      :key="o.key"
      type="button"
      class="filter-chip"
      :class="[o.class, { on: model === o.key }]"
      :aria-pressed="model === o.key"
      @click="model = o.key"
    >
      {{ o.label }}
    </button>
  </div>
</template>

<style scoped>
.filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: var(--control-sm);
  padding: 0 12px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--glass-border);
  background: transparent;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background var(--duration-fast),
    color var(--duration-fast);
}
.filter-chip:hover {
  color: var(--text);
  background: var(--selected);
}
.filter-chip.on {
  background: var(--selected);
  border-color: var(--selected-border);
  color: var(--text-accent);
}
</style>
