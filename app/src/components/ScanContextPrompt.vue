<script setup lang="ts">
// Caixa de contexto do levantamento, em destaque na aba Tarefas enquanto a reunião ainda não tem
// levantamento. O texto é opcional: os chips só acrescentam frases prontas ao que já foi digitado.
import { useId } from "vue";
import AppButton from "./ui/AppButton.vue";
import AppIcon from "./ui/AppIcon.vue";

// `disabled` trava campo, chips e "Gerar tarefas"; "Agora não" só esconde a caixa e fica sempre disponível.
const props = defineProps<{ modelValue: string; disabled: boolean }>();
const emit = defineEmits<{
  "update:modelValue": [value: string];
  generate: [];
  dismiss: [];
}>();

const id = useId();

// Rótulo do chip e o texto que ele acrescenta ao campo.
const CHIPS: { label: string; text: string }[] = [
  { label: "Foque só nas pendências de um produto", text: "Foque só nas pendências do produto: " },
  { label: "Ignore assuntos de infraestrutura", text: "Ignore assuntos de infraestrutura." },
  { label: "Junte os ajustes relacionados numa tarefa", text: "Junte os ajustes relacionados numa tarefa só." },
];

// Acrescenta sem nunca substituir o que o operador escreveu.
function append(text: string) {
  emit("update:modelValue", props.modelValue ? `${props.modelValue}\n${text}` : text);
}
</script>

<template>
  <section class="ctx" :aria-labelledby="`${id}-title`">
    <div class="ctx-head">
      <span class="ctx-icon"><AppIcon name="sparkle" :size="20" /></span>
      <div>
        <h3 :id="`${id}-title`">Quer orientar as sugestões de tarefas?</h3>
        <p :id="`${id}-hint`">
          Diga ao Claude no que focar, o que agrupar ou ignorar. O texto vai junto com a transcrição e os
          prints. É opcional: dá para gerar sem escrever nada.
        </p>
      </div>
    </div>
    <div class="chips">
      <button
        v-for="chip in CHIPS"
        :key="chip.label"
        type="button"
        class="chip"
        :disabled="disabled"
        @click="append(chip.text)"
      >
        + {{ chip.label }}
      </button>
    </div>
    <textarea
      :id="id"
      :value="modelValue"
      :aria-labelledby="`${id}-title`"
      :aria-describedby="`${id}-hint`"
      :disabled="disabled"
      rows="3"
      maxlength="10000"
      placeholder="Ex.: Foque nas pendências do checkout e agrupe os ajustes de pagamento em uma tarefa."
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    ></textarea>
    <div class="ctx-actions">
      <span class="note">Tarefas editadas, descartadas ou já enviadas são preservadas.</span>
      <AppButton variant="ghost" @click="emit('dismiss')">Agora não</AppButton>
      <AppButton variant="primary" :disabled="disabled" @click="emit('generate')">
        <AppIcon name="sparkle" :size="16" /> Gerar tarefas
      </AppButton>
    </div>
  </section>
</template>

<style scoped>
/* Cartão de vidro com a faixa de destaque (Ametista). */
.ctx {
  padding: var(--space-5);
  border: 1px solid var(--glass-border);
  border-left: 4px solid var(--accent-glow);
  border-radius: var(--radius-xl);
  background: var(--glass);
  box-shadow: var(--shadow-panel);
}
.ctx-head {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
}
.ctx-icon {
  flex: none;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--selected);
  color: var(--text-accent);
}
h3 {
  margin: 0;
  font-size: 16px;
  line-height: 22px;
  font-weight: 700;
}
.ctx-head p {
  max-width: var(--read-max);
  margin: 2px 0 0;
  color: var(--text-muted);
  font-size: 14px;
  line-height: 20px;
  font-weight: 500;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: var(--space-4) 0 var(--space-3);
}
.chip {
  height: var(--control-sm);
  padding: 0 var(--space-3);
  border: 1px dashed var(--border-control);
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background var(--duration-fast), color var(--duration-fast);
}
.chip:hover:not(:disabled) {
  border-style: solid;
  background: var(--selected);
  color: var(--text);
}
.chip:disabled {
  opacity: 0.55;
  cursor: default;
}
textarea {
  display: block;
  width: 100%;
  min-height: 88px;
  max-height: 160px;
  resize: vertical;
  padding: 14px var(--space-5);
  border: 1px solid var(--border-control);
  border-radius: var(--radius-md);
  background-color: var(--glass-strong);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  line-height: 22px;
  font-weight: 500;
}
textarea::placeholder {
  color: var(--text-muted);
}
textarea:focus {
  outline: none;
  border-color: var(--focus-ring);
  box-shadow:
    0 0 0 1px var(--focus-ring),
    0 0 0 5px var(--selected);
}
textarea:disabled {
  opacity: 0.55;
}
.ctx-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
.note {
  margin-right: auto;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
}
</style>
