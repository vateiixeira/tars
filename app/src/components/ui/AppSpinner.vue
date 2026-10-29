<script setup lang="ts">
// Indicador de carregamento circular. Herda a cor do texto (`currentColor`); sem `label` é só decorativo.
withDefaults(defineProps<{ size?: number; label?: string }>(), { size: 16 });
</script>

<template>
  <span
    class="spinner"
    :role="label ? 'status' : undefined"
    :aria-label="label || undefined"
    :aria-hidden="label ? undefined : 'true'"
    :style="{
      width: `${size}px`,
      height: `${size}px`,
      borderWidth: size <= 12 ? '1.5px' : '2px',
    }"
  ></span>
</template>

<style scoped>
.spinner {
  display: inline-block;
  flex: none;
  box-sizing: border-box;
  border-style: solid;
  border-color: currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spinner-turn 0.8s linear infinite;
}
@keyframes spinner-turn {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation: none;
  }
}
</style>
