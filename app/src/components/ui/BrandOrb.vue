<script setup lang="ts">
// Orbe da marca (mesmo gradiente de logos/tars-orb.svg). Sempre decorativo.
withDefaults(defineProps<{ size?: number }>(), { size: 36 });
</script>

<template>
  <span class="orb" :style="{ width: `${size}px`, height: `${size}px` }" aria-hidden="true" />
</template>

<style scoped>
.orb {
  display: inline-block;
  flex-shrink: 0;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, var(--text-accent), var(--accent-glow) 45%, var(--accent-grad-to));
  box-shadow: var(--shadow-orb);
}
</style>
