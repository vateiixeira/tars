<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import AppIcon from "./AppIcon.vue";

// Só o painel: quem usa posiciona, abre e devolve o foco ao fechar.
// Itens fora da ordem de Tab (tabindex -1): o foco entra no primeiro e anda pelas setas.
// Com `checked` definido o item vira opção de escolha única (menuitemradio) e mostra o visto.
type MenuItem = { key: string; label: string; icon?: string; danger?: boolean; disabled?: boolean; checked?: boolean };

const props = defineProps<{ items: MenuItem[]; label: string }>();
const emit = defineEmits<{ select: [key: string]; close: [] }>();

const root = ref<HTMLElement | null>(null);

// Itens destrutivos sempre por último, depois do separador (ordem relativa preservada).
const regular = computed(() => props.items.filter((i) => !i.danger));
const destructive = computed(() => props.items.filter((i) => i.danger));

function enabledItems(): HTMLButtonElement[] {
  return Array.from(root.value?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]:not(:disabled)') ?? []);
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
    return;
  }
  if (e.key === "Tab") {
    emit("close");
    return;
  }
  const list = enabledItems();
  if (!list.length) return;
  const at = list.indexOf(document.activeElement as HTMLButtonElement);
  let next: number;
  if (e.key === "ArrowDown") next = at < 0 ? 0 : (at + 1) % list.length;
  else if (e.key === "ArrowUp") next = at < 0 ? list.length - 1 : (at - 1 + list.length) % list.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = list.length - 1;
  else return;
  e.preventDefault();
  list[next].focus();
}

// Em menu de escolha, o foco entra na opção marcada.
onMounted(() => {
  const list = enabledItems();
  (list.find((b) => b.getAttribute("aria-checked") === "true") ?? list[0])?.focus();
});
</script>

<template>
  <div ref="root" class="app-menu" role="menu" :aria-label="label" @keydown="onKeydown">
    <button
      v-for="item in regular"
      :key="item.key"
      type="button"
      :role="item.checked === undefined ? 'menuitem' : 'menuitemradio'"
      :aria-checked="item.checked"
      tabindex="-1"
      class="menu-item"
      :disabled="item.disabled"
      @click="emit('select', item.key)"
    >
      <AppIcon v-if="item.icon" :name="item.icon" :size="18" />
      {{ item.label }}
      <AppIcon v-if="item.checked" class="checked" name="check" :size="16" />
    </button>
    <div v-if="regular.length && destructive.length" class="sep" role="separator" />
    <button
      v-for="item in destructive"
      :key="item.key"
      type="button"
      role="menuitem"
      tabindex="-1"
      class="menu-item danger"
      :disabled="item.disabled"
      @click="emit('select', item.key)"
    >
      <AppIcon v-if="item.icon" :name="item.icon" :size="18" />
      {{ item.label }}
    </button>
  </div>
</template>

<style scoped>
.app-menu {
  width: 260px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-radius: var(--radius-md);
  background: var(--surface-solid);
  border: 1px solid var(--glass-border);
  box-shadow: var(--shadow-popover);
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 38px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  transition: background var(--duration-fast);
}
/* --selected, não --glass-strong: no tema Dia o segundo é quase branco sobre branco. */
.menu-item:hover:not(:disabled),
.menu-item:focus-visible {
  background: var(--selected);
  outline: none;
}
.menu-item:focus-visible {
  box-shadow: inset 0 0 0 2px var(--focus-ring);
}
.menu-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.menu-item svg {
  color: var(--text-muted);
}
.menu-item svg.checked {
  margin-left: auto;
  color: var(--text-accent);
}
.menu-item.danger,
.menu-item.danger svg {
  color: var(--danger);
}
.sep {
  height: 1px;
  margin: 4px 6px;
  background: var(--glass-border);
}
@media (prefers-reduced-motion: reduce) {
  .menu-item {
    transition: none;
  }
}
</style>
