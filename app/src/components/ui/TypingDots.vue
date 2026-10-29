<script setup lang="ts">
// Três pontos: resposta curta da IA ou fala ao vivo. `label` só quando não há texto visível.
withDefaults(defineProps<{ label?: string }>(), { label: undefined });
</script>

<template>
  <span class="dots" :role="label ? 'status' : undefined" :aria-hidden="label ? undefined : 'true'">
    <i /><i /><i />
    <span v-if="label" class="sr-only">{{ label }}</span>
  </span>
</template>

<style scoped>
.dots {
  position: relative;
  display: inline-flex;
  gap: 4px;
  align-items: center;
}
.dots i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-accent);
  animation: bounce 1.1s ease-in-out infinite;
}
.dots i:nth-child(2) {
  animation-delay: 0.15s;
}
.dots i:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes bounce {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.5;
  }
  40% {
    transform: translateY(-5px);
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .dots i {
    animation: none;
    opacity: 0.8;
  }
}
</style>
