<script setup lang="ts">
// Loader de órbita (am-orbit): significa "processando". O texto visível fica ao lado,
// quem usa passa `label` só quando não há texto visível equivalente.
withDefaults(
  defineProps<{
    size?: "sm" | "lg";
    /** Dentro de botão primário os pontos usam a cor do texto do botão. */
    tone?: "default" | "on-accent";
    /** Texto para leitor de tela. Sem ele o loader é decorativo (aria-hidden). */
    label?: string;
  }>(),
  { size: "lg", tone: "default", label: undefined },
);
</script>

<template>
  <span
    class="loader"
    :class="[`size-${size}`, { 'tone-on-accent': tone === 'on-accent' }]"
    :role="label ? 'status' : undefined"
    :aria-hidden="label ? undefined : 'true'"
  >
    <span class="orbit"><i /><i /><i /></span>
    <span v-if="label" class="sr-only">{{ label }}</span>
  </span>
</template>

<style scoped>
.loader {
  position: relative;
  display: inline-flex;
  flex-shrink: 0;
}
.orbit {
  position: relative;
  display: block;
  width: 56px;
  height: 56px;
  animation: spin var(--duration-orbit) linear infinite;
}
.orbit i {
  position: absolute;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--text-accent);
  box-shadow: 0 0 12px var(--accent-glow);
}
.orbit i:nth-child(1) {
  top: 0;
  left: 23px;
}
.orbit i:nth-child(2) {
  bottom: 6px;
  left: 4px;
  opacity: 0.7;
  transform: scale(0.8);
}
.orbit i:nth-child(3) {
  bottom: 6px;
  right: 4px;
  opacity: 0.45;
  transform: scale(0.6);
}
.size-sm .orbit {
  width: 18px;
  height: 18px;
}
.size-sm .orbit i {
  width: 5px;
  height: 5px;
  box-shadow: none;
}
.size-sm .orbit i:nth-child(1) {
  left: 6.5px;
}
.size-sm .orbit i:nth-child(2) {
  bottom: 1px;
  left: 0;
}
.size-sm .orbit i:nth-child(3) {
  bottom: 1px;
  right: 0;
}
.tone-on-accent .orbit i {
  background: var(--on-accent);
}
@keyframes spin {
  to {
    transform: rotate(1turn);
  }
}
@media (prefers-reduced-motion: reduce) {
  .orbit {
    animation: none;
  }
}
</style>
