<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import AppLoader from "./AppLoader.vue";

// Acessibilidade: a lista (`ol`) é navegável e cada item diz o próprio estado em texto.
// O contêiner NÃO é uma região viva: reanunciar a lista inteira a cada mudança seria
// ruidoso. Só um `aria-live="polite"` pequeno e oculto anuncia a etapa que acabou de começar.
// Mantenha o componente montado durante todo o processamento: a região viva só anuncia
// mudanças depois de existir, então montar já com a etapa ou desmontar ao fim perde o aviso.
// Sem porcentagem: o backend só informa a etapa, nunca o progresso dentro dela.
type State = "done" | "run" | "todo";

const props = withDefaults(
  defineProps<{
    steps: { key: string; label: string }[];
    current: string | null;
    /** Todas as etapas concluídas (processamento terminou). */
    done?: boolean;
  }>(),
  { done: false },
);

const STATE_TEXT: Record<State, string> = { done: "concluída", run: "em andamento", todo: "pendente" };

const items = computed(() => {
  const at = props.current === null ? -1 : props.steps.findIndex((s) => s.key === props.current);
  return props.steps.map((s, i) => {
    const state: State = props.done || (at >= 0 && i < at) ? "done" : at === i ? "run" : "todo";
    return { ...s, state, text: STATE_TEXT[state] };
  });
});

const announcement = computed(() => {
  if (props.done) return "Todas as etapas concluídas";
  const run = items.value.find((i) => i.state === "run");
  return run ? `Em andamento: ${run.label}` : "";
});
</script>

<template>
  <div class="steps-wrap">
    <ol class="steps">
      <li v-for="s in items" :key="s.key" class="step" :class="`is-${s.state}`" :data-state="s.state">
        <span class="mark" aria-hidden="true">
          <AppIcon v-if="s.state === 'done'" name="check" :size="13" />
          <AppLoader v-else-if="s.state === 'run'" size="sm" />
        </span>
        <span class="label">
          {{ s.label }}<span class="sr-only">: {{ s.text }}</span>
        </span>
        <span class="bar" aria-hidden="true"><i /></span>
      </li>
    </ol>
    <span class="sr-only" aria-live="polite">{{ announcement }}</span>
  </div>
</template>

<style scoped>
.steps-wrap {
  position: relative;
  width: 100%;
}
.steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  margin: 0;
  padding: 0;
  list-style: none;
}
.step {
  display: grid;
  grid-template-columns: 22px 132px 1fr;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
  font-weight: 700;
}
.mark {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--glass-strong);
  color: var(--text-muted);
}
.label {
  overflow-wrap: break-word;
}
.bar {
  height: 6px;
  border-radius: var(--radius-pill);
  background: var(--glass-strong);
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  border-radius: var(--radius-pill);
  background: linear-gradient(90deg, var(--accent), var(--text-accent));
}
.is-done .mark {
  background: var(--success-surface);
  color: var(--success);
}
.is-done .bar i {
  width: 100%;
}
.is-run .bar i {
  width: 45%;
  animation: fill 2.4s ease-in-out infinite;
}
.is-todo {
  color: var(--text-muted);
}
.is-todo .bar i {
  width: 0;
}
@keyframes fill {
  0% {
    width: 10%;
  }
  100% {
    width: 92%;
  }
}
@media (prefers-reduced-motion: reduce) {
  .is-run .bar i {
    animation: none;
  }
}
</style>
