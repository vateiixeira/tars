<script setup lang="ts">
// Orbe pulsando: significa "ouvindo". Decorativo por padrão; o texto do estado
// ("Gravando · 00:12") fica ao lado.
withDefaults(defineProps<{ size?: number }>(), { size: 56 });
</script>

<template>
  <span class="glow" :style="{ '--orb-size': `${size}px`, '--orb-icon': `${Math.round(size * 0.4)}px` }" aria-hidden="true">
    <slot />
  </span>
</template>

<style scoped>
.glow {
  width: var(--orb-size);
  height: var(--orb-size);
  border-radius: 50%;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  color: var(--on-accent);
  background: radial-gradient(circle at 30% 30%, var(--text-accent), var(--accent-glow) 50%, var(--accent-grad-to));
  animation: glow var(--duration-glow) ease-in-out infinite;
}
.glow :deep(svg) {
  width: var(--orb-icon);
  height: var(--orb-icon);
}
@keyframes glow {
  0%,
  100% {
    box-shadow:
      0 0 0 0 var(--selected-border),
      var(--shadow-orb);
  }
  50% {
    box-shadow:
      0 0 0 14px transparent,
      var(--shadow-orb);
  }
}
@media (prefers-reduced-motion: reduce) {
  .glow {
    animation: none;
    box-shadow: var(--shadow-orb);
  }
}
</style>
