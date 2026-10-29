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
.ctx {
  flex-shrink: 0;
  margin-bottom: 14px;
  padding: 16px;
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: var(--radius-lg);
  background: var(--surface-raised);
}
.ctx-head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.ctx-icon {
  flex: none;
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-md);
  background: var(--selected);
  color: var(--selected-text);
}
h3 {
  margin: 0;
  font-size: 1rem;
}
.ctx-head p {
  max-width: 75ch;
  margin: 2px 0 0;
  color: var(--muted);
  font-size: var(--font-control);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 12px 0 8px;
}
.chip {
  padding: 4px 10px;
  border: 1px dashed var(--border-control);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: var(--font-meta);
  cursor: pointer;
}
.chip:hover:not(:disabled) {
  border-style: solid;
  color: var(--text);
}
.chip:disabled {
  opacity: 0.55;
  cursor: default;
}
textarea {
  display: block;
  width: 100%;
  min-height: 80px;
  max-height: 160px;
  resize: vertical;
  padding: 8px 12px;
  border: 1px solid var(--border-control);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: var(--font-control);
}
textarea:disabled {
  opacity: 0.55;
}
.ctx-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.note {
  margin-right: auto;
  color: var(--muted);
  font-size: var(--font-meta);
}
</style>
