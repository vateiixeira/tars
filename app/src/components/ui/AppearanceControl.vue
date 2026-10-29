<script setup lang="ts">
import {
  useAppearance,
  type Appearance,
} from "../../composables/useAppearance";
import AppIcon from "./AppIcon.vue";
const { preference, setAppearance } = useAppearance();
</script>
<template>
  <label class="appearance-control"
    ><AppIcon name="sun" :size="16" /><span class="sr-only">Aparência</span
    ><select
      :value="preference"
      @change="
        setAppearance(($event.target as HTMLSelectElement).value as Appearance)
      "
    >
      <option value="system">Tema do sistema</option>
      <option value="dark">Noite</option>
      <option value="light">Dia</option>
    </select></label
  >
</template>
<style scoped>
.appearance-control {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 700;
}
select {
  appearance: none;
  min-width: 0;
  max-width: 100%;
  min-height: var(--control-sm);
  padding: 0 34px 0 14px;
  background-color: var(--glass-strong);
  background-image:
    linear-gradient(45deg, transparent 50%, currentColor 50%),
    linear-gradient(135deg, currentColor 50%, transparent 50%);
  background-position:
    calc(100% - 18px) 50%,
    calc(100% - 13px) 50%;
  background-size: 5px 5px;
  background-repeat: no-repeat;
  color: var(--text);
  border: 1px solid var(--border-control);
  border-radius: var(--radius-pill);
  font: inherit;
  cursor: pointer;
}
select option {
  background: var(--surface-solid);
  color: var(--text);
}
</style>
