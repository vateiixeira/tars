<script setup lang="ts">
// Botão Aparência da barra superior: abre o AppMenu com Tema do sistema, Noite e Dia.
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useAppearance, type Appearance } from "../../composables/useAppearance";
import AppIcon from "../ui/AppIcon.vue";
import AppMenu from "../ui/AppMenu.vue";

const { preference, setAppearance } = useAppearance();
const open = ref(false);
const root = ref<HTMLElement | null>(null);
const button = ref<HTMLButtonElement | null>(null);

const OPTIONS: { key: Appearance; label: string; icon: string }[] = [
  { key: "system", label: "Tema do sistema", icon: "compare" },
  { key: "dark", label: "Noite", icon: "moon" },
  { key: "light", label: "Dia", icon: "sun" },
];
const items = computed(() => OPTIONS.map((o) => ({ ...o, checked: o.key === preference.value })));

function close(refocus = true): void {
  open.value = false;
  if (refocus) button.value?.focus();
}
function onSelect(key: string): void {
  setAppearance(key as Appearance);
  close();
}
// Clique fora fecha sem roubar o foco do que foi clicado.
function onPointerDown(event: PointerEvent): void {
  if (!root.value?.contains(event.target as Node)) close(false);
}
watch(open, (on) => {
  if (on) void nextTick(() => document.addEventListener("pointerdown", onPointerDown));
  else document.removeEventListener("pointerdown", onPointerDown);
});
onBeforeUnmount(() => document.removeEventListener("pointerdown", onPointerDown));
</script>

<template>
  <div ref="root" class="appearance-menu">
    <button
      ref="button"
      type="button"
      class="icon-button"
      aria-label="Aparência"
      title="Aparência"
      aria-haspopup="menu"
      :aria-expanded="open"
      @click="open ? close() : (open = true)"
    >
      <AppIcon name="sun" :size="18" />
    </button>
    <AppMenu v-if="open" class="popover" :items="items" label="Aparência" @select="onSelect" @close="close()" />
  </div>
</template>

<style scoped>
.appearance-menu {
  position: relative;
}
.popover {
  position: absolute;
  top: calc(100% + var(--space-2));
  right: 0;
  z-index: 30;
  width: 220px;
}
</style>
