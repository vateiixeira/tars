<script setup lang="ts">
// Cartão de uma tarefa sugerida: tipo, título, descrição (Markdown cru num textarea), projeto,
// seletores do MCP de issues, trecho que justifica, estado de criação. Só apresenta e emite: quem salva,
// cria e descarta é o `TasksTab`. Texto do Claude é sempre interpolado como texto (nada de HTML bruto).
import { computed, reactive, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { openUrl } from "@tauri-apps/plugin-opener";
import { usePrintThumbs } from "../composables/usePrintThumbs";
import type {
  IssueOption,
  IssueOptions,
  MeetingPrint,
  TaskFields,
  TaskKind,
  TaskPriority,
  TaskSuggestion,
} from "../api/types";
import { mmss } from "../lib/format";
import {
  isLocked,
  issueUrl as validateIssueUrl,
  KIND_LABELS,
  PRIORITY_LABELS,
} from "../lib/tasks";

type Changes = Partial<TaskFields> & { discarded?: boolean; print_ids?: string[] };

const props = defineProps<{
  task: TaskSuggestion;
  options: IssueOptions | null;
  /** Campos obrigatórios que faltam (vazio quando o item não pode mais ser criado). */
  missing: string[];
  /** Erro desta tarefa (recusa na criação, falha ao salvar, ao resolver...). */
  error?: string | null;
  /** Todos os prints da reunião (a tarefa guarda só os ids dos que vão junto). */
  prints: MeetingPrint[];
}>();
const emit = defineEmits<{
  // `update`, `draft` e `resolve` levam o id do próprio cartão: quem trata não depende de qual item está aberto.
  update: [id: string, changes: Changes];
  draft: [id: string, title: string];
  play: [start: number, end: number];
  resolve: [
    id: string,
    body: { action: "retry" } | { action: "number"; number: number },
  ];
}>();

const locked = computed(() => isLocked(props.task));
const noOptions = computed(() => props.options === null);

// Campos de texto: rascunho local; só volta ao valor do servidor quando o valor do servidor muda
// (um recarregamento qualquer não apaga o que o operador está digitando).
const draft = reactive({
  title: props.task.title,
  description: props.task.description,
  project: props.task.project ?? "",
});
watch(
  () => props.task.title,
  (v) => (draft.title = v),
);
// O título em edição (ainda sem `change`) alimenta o "Falta: título" e o botão Criar.
watch(
  () => draft.title,
  (v) => emit("draft", props.task.id, v),
);
watch(
  () => props.task.description,
  (v) => (draft.description = v),
);
watch(
  () => props.task.project,
  (v) => (draft.project = v ?? ""),
);

function saveTitle(): void {
  if (draft.title !== props.task.title)
    emit("update", props.task.id, { title: draft.title });
}
function saveDescription(): void {
  if (draft.description !== props.task.description)
    emit("update", props.task.id, { description: draft.description });
}
function saveProject(): void {
  const next = draft.project.trim() === "" ? null : draft.project;
  if (next !== props.task.project)
    emit("update", props.task.id, { project: next });
}

type IdKey = "assignee_id" | "team_id" | "product_id" | "stage_id";
interface Choice {
  id: number;
  label: string;
  selected: boolean;
}

/** Opções da lista + o valor atual (`#id`) quando ele não está na lista; marca a sugestão do Claude. */
function choices(list: IssueOption[] | undefined, key: IdKey): Choice[] {
  const current = props.task[key];
  const suggested =
    props.task.suggested?.[key as "assignee_id" | "team_id" | "product_id"] ??
    null;
  const out: Choice[] = (list ?? []).map((o) => ({
    id: o.id,
    label: o.id === suggested ? `${o.name} (sugestão)` : o.name,
    selected: o.id === current,
  }));
  if (current !== null && !out.some((o) => o.id === current))
    out.push({ id: current, label: `#${current}`, selected: true });
  return out;
}

const people = computed(() => choices(props.options?.people, "assignee_id"));
const teams = computed(() => choices(props.options?.teams, "team_id"));
const products = computed(() => choices(props.options?.products, "product_id"));
const stages = computed(() => choices(props.options?.stages, "stage_id"));

function onId(key: IdKey, ev: Event): void {
  const raw = (ev.target as HTMLSelectElement).value;
  emit("update", props.task.id, { [key]: raw === "" ? null : Number(raw) });
}
function onKind(ev: Event): void {
  emit("update", props.task.id, {
    kind: (ev.target as HTMLSelectElement).value as TaskKind,
  });
}
function onPriority(ev: Event): void {
  emit("update", props.task.id, {
    priority: (ev.target as HTMLSelectElement).value as TaskPriority,
  });
}

// Dica do Claude que não casou com ninguém do MCP de issues (o id veio vazio).
const assigneeHint = computed(() => {
  const s = props.task.suggested;
  return s && s.assignee_id === null && props.task.assignee_id === null
    ? s.assignee_hint
    : null;
});
const teamHint = computed(() => {
  const s = props.task.suggested;
  return s && s.team_id === null && props.task.team_id === null
    ? s.team_hint
    : null;
});

const issueUrl = computed(() => validateIssueUrl(props.task.issue_url));
const openError = ref<string | null>(null);
watch(
  () => [props.task.id, props.task.issue_url],
  () => (openError.value = null),
);
async function openIssue(): Promise<void> {
  if (!issueUrl.value) return;
  openError.value = null;
  try {
    await openUrl(issueUrl.value);
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    openError.value = `Não foi possível abrir o navegador: ${detail}`;
  }
}

// -- prints que vão junto ---------------------------------------------------------------------
// As miniaturas vêm do composable (object URLs com ciclo de vida próprio). Se uma falha, fica só o rótulo.

const attached = computed(() =>
  props.task.print_ids.flatMap((id) => {
    const print = props.prints.find((p) => p.id === id);
    return print ? [print] : [];
  }),
);
const { thumbs } = usePrintThumbs(
  () => props.task.meeting_id,
  () => attached.value.map((p) => p.id),
);

function removePrint(id: string): void {
  if (locked.value) return;
  emit("update", props.task.id, {
    print_ids: props.task.print_ids.filter((p) => p !== id),
  });
}

const ATTACH_NOTES: Partial<Record<TaskSuggestion["attach_state"], string>> = {
  sending: "Enviando os prints ao MCP de issues…",
  sent: "Prints enviados ao MCP de issues",
};
const attachNote = computed(() => {
  const { attach_state: state, attach_error: error } = props.task;
  if (state === "unsupported") return "Prints não enviados: o MCP não aceita imagens";
  if (state === "failed")
    return `Prints não enviados: ${error ?? "erro desconhecido"}`;
  return ATTACH_NOTES[state] ?? null;
});

const numberDraft = ref("");
const numberValid = computed(
  () =>
    /^[0-9]+$/.test(numberDraft.value.trim()) && Number(numberDraft.value) > 0,
);
function informNumber(): void {
  if (numberValid.value)
    emit("resolve", props.task.id, {
      action: "number",
      number: Number(numberDraft.value),
    });
}
</script>

<template>
  <div v-if="task.discarded" class="task discarded" :data-task-id="task.id">
    <span>Tarefa descartada: {{ task.title || "sem título" }}</span>
    <button
      type="button"
      class="btn"
      @click="emit('update', task.id, { discarded: false })"
    >
      Desfazer
    </button>
  </div>

  <article v-else class="task" :class="{ locked }" :data-task-id="task.id">


    <label class="title-field"
      >Título *
      <input
        v-model="draft.title"
        class="title-input"
        aria-label="Título"
        placeholder="Título da tarefa"
        maxlength="300"
        :disabled="locked"
        @change="saveTitle"
      />
    </label>

    <header class="top">
      <select
        aria-label="Tipo"
        class="kind"
        :disabled="locked"
        @change="onKind"
      >
        <option
          v-for="(label, value) in KIND_LABELS"
          :key="value"
          :value="value"
          :selected="value === task.kind"
        >
          {{ label }}
        </option>
      </select>
      <span v-if="task.create_state === 'created'" class="state ok">
        <a
          v-if="issueUrl"
          :href="issueUrl"
          :title="issueUrl"
          @click.prevent="openIssue"
          >#{{ task.issue_number }}</a
        >
        <span v-else title="Link da issue indisponível"
          >#{{ task.issue_number }}</span
        >
        criada no MCP de issues
      </span>
      <span v-else-if="task.create_state === 'creating'" class="state"
        >criando no MCP de issues…</span
      >
      <span v-else-if="task.create_state === 'uncertain'" class="state warn"
        >incerto — confira no MCP de issues</span
      >
      <span v-else-if="task.create_state === 'failed'" class="state bad"
        >falhou ao criar</span
      >
      <span v-if="task.repeats_number !== null" class="state warn"
        >parece repetir #{{ task.repeats_number }}</span
      >
    </header>

    <label class="description-field"
      >Descrição (Markdown)
      <textarea
        v-model="draft.description"
        class="description"
        aria-label="Descrição (Markdown)"
        placeholder="Descrição (Markdown)"
        rows="6"
        :disabled="locked"
        @change="saveDescription"
      ></textarea>
    </label>

    <div class="grid">
      <label
        >Projeto
        <input
          v-model="draft.project"
          aria-label="Projeto"
          :disabled="locked"
          @change="saveProject"
        />
      </label>
      <label
        >Produto *
        <select
          aria-label="Produto"
          :disabled="locked || noOptions"
          @change="onId('product_id', $event)"
        >
          <option value="" :selected="task.product_id === null">—</option>
          <option
            v-for="o in products"
            :key="o.id"
            :value="o.id"
            :selected="o.selected"
          >
            {{ o.label }}
          </option>
        </select>
      </label>
      <label
        >Responsável *
        <select
          aria-label="Responsável"
          :disabled="locked || noOptions"
          @change="onId('assignee_id', $event)"
        >
          <option value="" :selected="task.assignee_id === null">—</option>
          <option
            v-for="o in people"
            :key="o.id"
            :value="o.id"
            :selected="o.selected"
          >
            {{ o.label }}
          </option>
        </select>
        <small v-if="assigneeHint" class="hint"
          >Sugestão do Claude: {{ assigneeHint }} — não encontrado no MCP de
          issues</small
        >
      </label>
      <label
        >Equipe *
        <select
          aria-label="Equipe"
          :disabled="locked || noOptions"
          @change="onId('team_id', $event)"
        >
          <option value="" :selected="task.team_id === null">—</option>
          <option
            v-for="o in teams"
            :key="o.id"
            :value="o.id"
            :selected="o.selected"
          >
            {{ o.label }}
          </option>
        </select>
        <small v-if="teamHint" class="hint"
          >Sugestão do Claude: {{ teamHint }} — não encontrado no MCP de
          issues</small
        >
      </label>
      <label
        >Etapa *
        <select
          aria-label="Etapa"
          :disabled="locked || noOptions"
          @change="onId('stage_id', $event)"
        >
          <option value="" :selected="task.stage_id === null">—</option>
          <option
            v-for="o in stages"
            :key="o.id"
            :value="o.id"
            :selected="o.selected"
          >
            {{ o.label }}
          </option>
        </select>
      </label>
      <label
        >Prioridade
        <select aria-label="Prioridade" :disabled="locked" @change="onPriority">
          <option
            v-for="(label, value) in PRIORITY_LABELS"
            :key="value"
            :value="value"
            :selected="value === task.priority"
          >
            {{ label }}
          </option>
        </select>
      </label>
    </div>

    <ul v-if="task.evidence.length" class="evidence">
      <li v-for="(e, i) in task.evidence" :key="i">
        <button
          type="button"
          class="play"
          title="Ouvir este trecho"
          aria-label="Ouvir este trecho"
          @click="emit('play', e.start_s, e.end_s)"
        >
          <AppIcon name="play" :size="14" />
        </button>
        <span class="ts">{{ mmss(e.start_s) }}</span>
        <span class="who">{{ e.speaker }}</span>
        <span class="text">{{ e.text }}</span>
      </li>
    </ul>

    <section
      v-if="attached.length || attachNote"
      class="task-prints"
      aria-label="Prints que vão junto"
    >
      <h4>Prints que vão junto</h4>
      <ul v-if="attached.length">
        <li v-for="p in attached" :key="p.id" :data-print-id="p.id">
          <img
            v-if="thumbs[p.id]"
            :src="thumbs[p.id]"
            :alt="`Print ${p.label}`"
            width="96"
            height="60"
          />
          <span v-else class="thumb-fallback">{{ p.label }}</span>
          <span class="plabel">{{ p.label }} · {{ mmss(p.at_s) }}</span>
          <button
            type="button"
            class="remove"
            :aria-label="`Tirar ${p.label} desta tarefa`"
            :title="`Tirar ${p.label} desta tarefa`"
            :disabled="locked"
            @click="removePrint(p.id)"
          >
            ×
          </button>
        </li>
      </ul>
      <p
        v-if="attachNote"
        class="msg"
        :class="{
          bad: task.attach_state === 'failed',
          warn: task.attach_state === 'unsupported',
        }"
      >
        {{ attachNote }}
      </p>
    </section>

    <p v-if="missing.length" class="msg bad">Falta: {{ missing.join(", ") }}</p>
    <p
      v-if="task.create_state === 'failed' && task.create_error"
      class="msg bad"
    >
      {{ task.create_error }}
    </p>
    <p
      v-else-if="task.create_state === 'created' && task.create_error"
      class="msg warn"
    >
      {{ task.create_error }}
    </p>
    <p v-if="error" class="msg bad">{{ error }}</p>
    <p v-if="openError" class="msg bad" role="alert">{{ openError }}</p>

    <div v-if="task.create_state === 'uncertain'" class="resolve">
      <p class="msg warn">
        Não deu para saber se a tarefa foi criada. Confira no MCP de issues
        antes de escolher.
      </p>
      <p v-if="task.create_error" class="msg warn">{{ task.create_error }}</p>
      <button
        type="button"
        class="btn"
        @click="emit('resolve', task.id, { action: 'retry' })"
      >
        Não foi criada, tentar de novo
      </button>
      <span class="number">
        <input
          v-model="numberDraft"
          inputmode="numeric"
          aria-label="Número da issue no MCP de issues"
          placeholder="Nº da issue"
          @keydown.enter.prevent="informNumber"
        />
        <button
          type="button"
          class="btn"
          :disabled="!numberValid"
          @click="informNumber"
        >
          Informar número
        </button>
      </span>
    </div>
  </article>
</template>

<style scoped>
.task-prints h4 {
  margin: 0 0 6px;
  font-size: 0.85rem;
  font-weight: 600;
}
.task-prints ul {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 6px;
  padding: 0;
  list-style: none;
}
.task-prints li {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 96px;
  font-size: 0.75rem;
  color: var(--muted);
}
.task-prints img,
.thumb-fallback {
  width: 96px;
  height: 60px;
  object-fit: cover;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--surface);
}
.thumb-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
}
.task-prints .remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 24px;
  height: 24px;
  min-width: 24px;
  min-height: 24px;
  padding: 0;
  line-height: 1;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
}
.task-prints .remove:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.task {
  container: task-editor / inline-size;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 4px 0;
  background: var(--surface);
  border: 0;
  border-radius: 0;
}
.task.locked {
  color: var(--text);
}
.task.discarded {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  color: var(--muted);
  font-size: 0.85rem;
  overflow-wrap: anywhere;
}
.top {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.state {
  font-size: 0.8rem;
  color: var(--muted);
}
.state.ok a {
  color: var(--accent);
  font-weight: 600;
}
.state.warn,
.msg.warn {
  color: var(--warn);
}
.state.bad,
.msg.bad {
  color: var(--danger);
}
.msg {
  margin: 0;
  font-size: 0.85rem;
  overflow-wrap: anywhere;
}
input,
select,
textarea {
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid var(--border-control);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  font-family: inherit;
  font-size: var(--font-control);
}
input:disabled,
select:disabled,
textarea:disabled {
  opacity: 0.6;
}
.title-input {
  min-height: 52px;
  font-size: 1rem;
  font-weight: 600;
}
.grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px 16px;
}
.grid label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 0.75rem;
  color: var(--muted);
  min-width: 0;
}
.hint {
  color: var(--warn);
  font-size: 0.75rem;
}
.description {
  flex: 1 1 auto;
  resize: vertical;
  min-height: clamp(260px, 38vh, 420px);
  line-height: 1.6;
}
.evidence {
  list-style: none;
  margin: 0;
  padding: 16px 0 0;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 0.82rem;
}
.evidence li {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin: 2px 0;
}
.evidence .text {
  color: var(--text);
  overflow-wrap: anywhere;
}
.evidence .who {
  font-weight: 600;
}
.play {
  border: 0;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  padding: 0 2px;
}
.resolve {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.resolve .msg {
  flex-basis: 100%;
}
.number {
  display: inline-flex;
  gap: 6px;
}
.number input {
  width: 110px;
}
.evidence li {
  flex-wrap: wrap;
}
.play {
  min-width: 32px;
  min-height: 32px;
}
.title-field,
.description-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: var(--font-meta);
  font-weight: 500;
}
.description-field {
  flex: 1;
}
.description-field textarea {
  font-size: var(--font-body);
}
@container task-editor (max-width: 600px) {
  .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@container task-editor (max-width: 360px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
