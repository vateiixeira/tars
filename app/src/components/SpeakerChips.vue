<script setup lang="ts">
// Etiquetas dos participantes (cor por `color_index`). Falante sem voz cadastrada que não é "Você"
// fica com borda tracejada e "identificar"; o clique é re-emitido para abrir o menu do falante
// ("Você" não tem menu: a etiqueta não é clicável).
import type { Speaker } from "../api/types";
import { speakerColor } from "../lib/format";
import { speakerName } from "../lib/meeting";

defineProps<{ speakers: Speaker[] }>();
const emit = defineEmits<{ select: [speaker: Speaker, event: MouseEvent] }>();

const unidentified = (s: Speaker) => !s.voice_id && !s.is_me;
</script>

<template>
  <div class="chips">
    <template v-for="s in speakers" :key="s.id">
    <span v-if="s.is_me" class="chip me" :data-speaker="s.id">
      <i :style="{ background: speakerColor(s.color_index) }"></i>{{ speakerName(s) }}
    </span>
    <button
      v-else
      type="button"
      class="chip"
      :class="{ unknown: unidentified(s) }"
      :data-speaker="s.id"
      @click="emit('select', s, $event)"
    >
      <i :style="{ background: speakerColor(s.color_index) }"></i>{{ speakerName(s) }}<template v-if="unidentified(s)"> · identificar</template>
    </button>
    </template>
  </div>
</template>

<style scoped>
/* Pílulas de falante (am-pill). */
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: var(--control-sm);
  padding: 0 var(--space-3) 0 10px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--glass-border);
  background: var(--glass);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  line-height: 18px;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  transition: background var(--duration-fast);
}
/* --selected, não --glass-strong: no tema Dia o segundo some sobre o branco. */
.chip:hover:not(.me) {
  background: var(--selected);
}
.chip.me {
  cursor: default;
}
.chip i {
  width: 8px;
  height: 8px;
  flex-shrink: 0;
  border-radius: 50%;
}
.chip.unknown {
  border-style: dashed;
  border-color: var(--warning);
  color: var(--warning);
}
@media (prefers-reduced-motion: reduce) {
  .chip {
    transition: none;
  }
}
</style>
