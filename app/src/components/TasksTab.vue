<script setup lang="ts">
// Aba "Tarefas" da reunião (criador de issues): lista à esquerda (pendentes e concluídas) e uma tarefa
// aberta por vez à direita. O operador revisa as sugestões do Claude, edita, descarta, adiciona,
// copia/exporta e cria no MCP de issues uma de cada vez. Nada é criado sem clique, e o pedido de criação
// leva os campos que estão na tela (não os do último salvamento).
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";
import { save } from "@tauri-apps/plugin-dialog";
import { writeTextFile } from "@tauri-apps/plugin-fs";
import { ApiError } from "../api/client";
import type {
  JobState,
  IssueOptions,
  MeetingPrint,
  TaskFields,
  TaskSuggestion,
} from "../api/types";
import { useStatus } from "../composables/useStatus";
import {
  isLocked,
  KIND_LABELS,
  missingFields,
  taskFields,
  tasksExportFileName,
  tasksMarkdown,
} from "../lib/tasks";
import AppIcon from "./ui/AppIcon.vue";
import AppSpinner from "./ui/AppSpinner.vue";
import ScanContextPrompt from "./ScanContextPrompt.vue";
import TaskCard from "./TaskCard.vue";

const props = defineProps<{ meetingId: string; startedAt: string; prints: MeetingPrint[] }>();
const emit = defineEmits<{ play: [start: number, end: number] }>();

const { client, events, connected } = useStatus();

type Changes = Partial<TaskFields> & { discarded?: boolean; print_ids?: string[] };

const loaded = ref(false);
const loadError = ref<string | null>(null);
const enabled = ref(true);
const scan = ref<JobState | null>(null);
const items = ref<TaskSuggestion[]>([]);
const options = ref<IssueOptions | null>(null);
const optionsError = ref<string | null>(null);
const actionError = ref<string | null>(null);
const scanContext = ref("");
const contextId = useId();
// "Agora não" na caixa de contexto: vale só enquanto a aba está aberta (não é persistido).
const contextDismissed = ref(false);
// Erro por cartão (recusa na criação, falha ao salvar, ao resolver).
const cardErrors = ref<Record<string, string>>({});
// Tarefa aberta à direita (por id: sobrevive às recargas).
const openId = ref<string | null>(null);
const root = ref<HTMLElement | null>(null);
// Ações em andamento (`scan`, `add`, `create:<id>`...): ocupado por nome, não um flag global.
const busy = ref<ReadonlySet<string>>(new Set());
const isBusy = (name: string) => busy.value.has(name);
// Título como está no campo (antes do `change`), por id: só para o "Falta: título" e o botão Criar.
const liveTitles = ref<Record<string, string>>({});

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const withId = (
  set: ReadonlySet<string>,
  id: string,
  on: boolean,
): ReadonlySet<string> => {
  const next = new Set(set);
  if (on) next.add(id);
  else next.delete(id);
  return next;
};

// Grupos de apresentação derivados dos contratos existentes. Resultado incerto requer conferência.
const isPending = (t: TaskSuggestion): boolean =>
  !t.discarded &&
  (t.create_state === "none" || t.create_state === "failed") &&
  !isBusy(`create:${t.id}`);

// -- carga ----------------------------------------------------------------------------------

let loadSeq = 0;
let optionsRequested = false;
async function load(): Promise<void> {
  const seq = ++loadSeq;
  const id = props.meetingId;
  try {
    const result = await client.listTasks(id);
    if (seq !== loadSeq || id !== props.meetingId) return;
    enabled.value = result.enabled;
    scan.value = result.scan;
    items.value = result.items;
    loadError.value = null;
    // As opções (uma chamada ao Claude) só valem a pena com o fluxo ligado ou com itens a mostrar.
    if (!optionsRequested && (result.enabled || result.items.length > 0)) {
      optionsRequested = true;
      void loadOptions();
    }
    // erros de itens que já estão sendo criados ou foram criados (ex.: salvamento que chegou tarde) saem
    const settled = new Set(
      result.items.filter((t) => isLocked(t)).map((t) => t.id),
    );
    if (Object.keys(cardErrors.value).some((tid) => settled.has(tid))) {
      cardErrors.value = Object.fromEntries(
        Object.entries(cardErrors.value).filter(([tid]) => !settled.has(tid)),
      );
    }
    // o aberto some (ex.: novo levantamento) ou ainda não há aberto: primeiro pendente, senão a primeira concluída
    if (
      openId.value === null ||
      !result.items.some((t) => t.id === openId.value)
    ) {
      openId.value =
        (result.items.find(isPending) ?? result.items[0])?.id ?? null;
    }
  } catch (e) {
    if (seq !== loadSeq) return;
    loadError.value = errorText(e);
  } finally {
    if (seq === loadSeq) loaded.value = true;
  }
}

async function loadOptions(refresh = false): Promise<void> {
  try {
    options.value = await client.issueOptions(refresh);
    optionsError.value = null;
  } catch (e) {
    optionsError.value = errorText(e);
  }
}

const offChanged = events.on("tasks_changed", (e) => {
  if (e.meeting_id === props.meetingId) void load();
});
const offJob = events.on("meeting_job", (e) => {
  if (e.meeting_id === props.meetingId && e.kind === "tasks") void load();
});
// (Re)conexão: eventos podem ter se perdido durante a queda do backend.
watch(connected, (on) => {
  if (on) void load();
});
onBeforeUnmount(() => {
  offChanged();
  offJob();
  clearTimeout(copiedTimer);
});

void load();

// -- lista ----------------------------------------------------------------------------------

const running = computed(() => scan.value?.state === "running");
const pending = computed(() => items.value.filter(isPending));
const creating = computed(() =>
  items.value.filter(
    (t) =>
      !t.discarded &&
      (t.create_state === "creating" || isBusy(`create:${t.id}`)),
  ),
);
const uncertain = computed(() =>
  items.value.filter((t) => !t.discarded && t.create_state === "uncertain"),
);
const otherGroups = computed(() => [
  { label: "Em criação", items: creating.value },
  { label: "Verificar", items: uncertain.value },
  {
    label: "Criadas",
    items: items.value.filter(
      (t) => !t.discarded && t.create_state === "created",
    ),
  },
  { label: "Descartadas", items: items.value.filter((t) => t.discarded) },
]);
function presentationLabel(t: TaskSuggestion): string {
  return t.discarded
    ? "Descartada"
    : t.create_state === "uncertain"
      ? "Verificar"
      : t.create_state === "created"
        ? "Criada"
        : t.create_state === "creating" || isBusy(`create:${t.id}`)
          ? "Em criação"
          : "A revisar";
}
const open = computed(
  () => items.value.find((t) => t.id === openId.value) ?? null,
);
const openIndex = computed(() =>
  pending.value.findIndex((t) => t.id === openId.value),
);
const allReviewed = computed(
  () =>
    items.value.length > 0 &&
    pending.value.length === 0 &&
    uncertain.value.length === 0 &&
    creating.value.length === 0,
);
const showOptionsError = computed(
  () =>
    optionsError.value !== null && (enabled.value || items.value.length > 0),
);
// Só o item aberto e pendente mostra (e é barrado por) "Falta: ..."; some ao preencher.
const openMissing = computed(() => {
  const t = open.value;
  if (!t || !isPending(t)) return [];
  return missingFields({ ...t, title: liveTitles.value[t.id] ?? t.title });
});

// Cartão aberto em criação (no servidor ou neste clique): fica esmaecido, com a mensagem por cima.
const openCreating = computed(
  () =>
    open.value !== null &&
    !open.value.discarded &&
    (open.value.create_state === "creating" || isBusy(`create:${open.value.id}`)),
);

const ROW_STATE: Partial<Record<TaskSuggestion["create_state"], string>> = {
  creating: "criando…",
  uncertain: "conferir no MCP de issues",
};
function rowState(t: TaskSuggestion): string | null {
  if (t.discarded) return null;
  if (isBusy(`create:${t.id}`)) return "criando…";
  if (t.create_state === "created") return `#${t.issue_number}`;
  return ROW_STATE[t.create_state] ?? null;
}

/** Depois de tirar `t` dos pendentes: o que ocupa o mesmo lugar na lista nova, ou o anterior se era o último. */
function neighborOf(t: TaskSuggestion): string | null {
  const index = pending.value.findIndex((p) => p.id === t.id);
  if (index < 0) return null;
  const rest = pending.value.filter((p) => p.id !== t.id);
  return (rest[index] ?? rest[index - 1])?.id ?? null;
}

function step(delta: -1 | 1): void {
  const target = pending.value[openIndex.value + delta];
  if (target) openId.value = target.id;
}

/** `suggested` com responsável/equipe enviados marcados como escolha do operador, como o servidor faz. */
function markedManual(t: TaskSuggestion, changes: Changes): TaskSuggestion["suggested"] {
  const chosen = (["assignee_id", "team_id"] as const).filter((k) => k in changes);
  if (!chosen.length) return t.suggested;
  const fill = { ...t.suggested?.fill };
  for (const k of chosen) fill[k] = "manual";
  return { ...t.suggested, fill };
}

/** Aplica na tela na hora (o "Criar" seguinte já leva o valor) e salva no servidor. */
async function onUpdate(id: string, changes: Changes): Promise<void> {
  loadSeq++; // uma carga em voo traria o valor antigo por cima desta edição
  const touchesFields = Object.keys(changes).some((k) => k !== "discarded");
  items.value = items.value.map((i) =>
    i.id === id ? { ...i, ...changes, suggested: markedManual(i, changes), edited: i.edited || touchesFields } : i,
  );
  if ("title" in changes) {
    const { [id]: _saved, ...titles } = liveTitles.value;
    liveTitles.value = titles;
  }
  const { [id]: _cleared, ...rest } = cardErrors.value;
  cardErrors.value = rest;
  try {
    await client.updateTask(id, changes);
  } catch (e) {
    // 409 com a criação em voo: o item já foi reivindicado pelo "Criar", o salvamento tardio não é erro
    if (!(
      e instanceof ApiError &&
      e.status === 409 &&
      isBusy(`create:${id}`)
    )) {
      cardErrors.value = { ...cardErrors.value, [id]: errorText(e) };
    }
    await load(); // volta ao que o servidor tem
  }
}

function onDiscard(t: TaskSuggestion): void {
  openId.value = neighborOf(t) ?? openId.value;
  void onUpdate(t.id, { discarded: true });
}

async function onResolve(
  id: string,
  body: { action: "retry" } | { action: "number"; number: number },
): Promise<void> {
  const { [id]: _cleared, ...rest } = cardErrors.value;
  cardErrors.value = rest;
  let failure: string | null = null;
  try {
    await client.resolveTask(id, body);
  } catch (e) {
    failure = errorText(e);
  }
  await load();
  if (failure !== null)
    cardErrors.value = { ...cardErrors.value, [id]: failure }; // depois da carga, que limpa erros de itens travados
}

// -- ações da barra ---------------------------------------------------------------------------

async function run(name: string, fn: () => Promise<void>): Promise<void> {
  if (busy.value.has(name)) return; // segundo clique não dispara outra chamada
  busy.value = withId(busy.value, name, true);
  actionError.value = null;
  try {
    await fn();
  } catch (e) {
    actionError.value = errorText(e);
  } finally {
    busy.value = withId(busy.value, name, false);
  }
}

const contextDisabled = computed(() => !connected.value || !enabled.value || running.value || isBusy("scan"));
// Antes do primeiro levantamento a caixa de contexto aparece aberta e em destaque.
const showContextPrompt = computed(
  () => enabled.value && loaded.value && scan.value === null && !running.value && !contextDismissed.value,
);

function rescan(): Promise<void> {
  return run("scan", async () => {
    const context = scanContext.value.trim();
    if (context) await client.scanTasks(props.meetingId, context);
    else await client.scanTasks(props.meetingId);
    scan.value = {
      state: "running",
      error: null,
      updated_at: new Date().toISOString(),
    };
    await load();
  });
}

function cancelScan(): Promise<void> {
  return run("cancel-scan", async () => {
    const result = await client.cancelTasksScan(props.meetingId);
    loadSeq++; // descarta cargas anteriores ao cancelamento
    scan.value = result.scan;
    await load();
  });
}

function addManual(): Promise<void> {
  return run("add", async () => {
    const created = await client.addTask(props.meetingId, {});
    await load();
    openId.value = created.id;
    await nextTick();
    root.value
      ?.querySelector<HTMLInputElement>(
        `[data-task-id="${created.id}"] [aria-label="Título"]`,
      )
      ?.focus();
  });
}

const copied = ref(false);
let copiedTimer: ReturnType<typeof setTimeout> | undefined;

function copy(): Promise<void> {
  return run("copy", async () => {
    await writeText(tasksMarkdown(items.value, options.value));
    copied.value = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (copied.value = false), 2000);
  });
}

function exportMarkdown(): Promise<void> {
  return run("export", async () => {
    const path = await save({
      defaultPath: tasksExportFileName(props.startedAt),
      filters: [{ name: "Markdown", extensions: ["md"] }],
    });
    if (!path) return; // diálogo cancelado
    await writeTextFile(path, tasksMarkdown(items.value, options.value));
  });
}

/** Cria só este item e já abre o próximo pendente (sem esperar o MCP de issues); várias criações podem correr juntas. */
async function createInIssue(t: TaskSuggestion): Promise<void> {
  const key = `create:${t.id}`;
  if (busy.value.has(key) || missingFields(t).length > 0) return;
  const next = neighborOf(t); // antes de marcar ocupado: o item ainda está na lista de pendentes
  busy.value = withId(busy.value, key, true);
  if (openId.value === t.id && next !== null) openId.value = next;
  const moved = openId.value;
  const { [t.id]: _cleared, ...rest } = cardErrors.value;
  cardErrors.value = rest;
  try {
    const result = await client.createTasks(props.meetingId, [
      { id: t.id, ...taskFields(t) },
    ]);
    const reasons: Record<string, string> = {};
    for (const r of result.rejected) reasons[r.id] = r.reason;
    // recusas antigas dos aceitos agora saem; as novas entram
    const kept = Object.fromEntries(
      Object.entries(cardErrors.value).filter(
        ([id]) => !result.accepted.includes(id),
      ),
    );
    cardErrors.value = { ...kept, ...reasons };
    // recusado: volta para ele, se o operador ainda não saiu do item para onde avançou
    if (reasons[t.id] !== undefined && openId.value === moved)
      openId.value = t.id;
    await load();
  } catch (e) {
    cardErrors.value = { ...cardErrors.value, [t.id]: errorText(e) };
    if (openId.value === moved) openId.value = t.id;
  } finally {
    busy.value = withId(busy.value, key, false);
  }
}
</script>

<template>
  <div ref="root" class="tasks">
    <div class="toolbar">
      <button
        type="button"
        class="btn"
        :disabled="!connected || !loaded || !enabled || running || isBusy('scan') || isBusy('cancel-scan')"
        :title="enabled ? undefined : 'O criador de issues está desligado'"
        @click="rescan"
      >
        <AppIcon name="refresh" :size="16" />
        {{ scan === null ? "Levantar tarefas" : "Refazer sugestões" }}
      </button>
      <button
        type="button"
        class="btn"
        :disabled="!connected || isBusy('add')"
        @click="addManual"
      >
        <AppIcon name="plus" :size="16" /> Adicionar tarefa
      </button>
      <button type="button" class="btn" @click="copy">
        <AppIcon name="copy" :size="16" /> {{ copied ? "Copiado" : "Copiar" }}
      </button>
      <button type="button" class="btn" @click="exportMarkdown">
        <AppIcon name="export" :size="16" /> Exportar .md
      </button>
    </div>

    <ScanContextPrompt
      v-if="showContextPrompt"
      v-model="scanContext"
      :disabled="contextDisabled"
      @generate="rescan"
      @dismiss="contextDismissed = true"
    />
    <details v-else-if="enabled || items.length" class="scan-context">
      <summary>Orientar as sugestões{{ scanContext.trim() ? " (preenchido)" : " (opcional)" }}</summary>
      <label :for="contextId">Como você quer orientar as sugestões?</label>
      <textarea
        :id="contextId"
        v-model="scanContext"
        :aria-describedby="`${contextId}-hint`"
        :disabled="contextDisabled"
        rows="3"
        maxlength="10000"
        placeholder="Ex.: Foque nas pendências do checkout e agrupe os ajustes de pagamento em uma tarefa."
      ></textarea>
      <p :id="`${contextId}-hint`">
        O texto será usado junto com a transcrição e os prints ao levantar as tarefas.
        Tarefas editadas, descartadas ou já enviadas são preservadas.
      </p>
    </details>

    <p v-if="loadError" class="error" role="alert">{{ loadError }}</p>
    <p v-if="actionError" class="error" role="alert">{{ actionError }}</p>
    <p v-if="showOptionsError" class="warn">
      Não foi possível carregar as opções do MCP de issues: {{ optionsError }}
      <button type="button" class="btn" @click="loadOptions(true)">
        Tentar de novo
      </button>
    </p>

    <div v-if="running" class="scan-box">
      <AppSpinner :size="20" />
      <div class="scan-text" role="status">
        {{ isBusy('cancel-scan') ? "Cancelando levantamento…" : "Levantando tarefas…" }}
        <small>O Claude está lendo a transcrição e os prints. Isso pode levar alguns minutos.</small>
        <div class="scan-bar" aria-hidden="true"><i></i></div>
      </div>
      <span class="spacer"></span>
      <button
        type="button"
        class="btn"
        :disabled="!connected || isBusy('cancel-scan')"
        @click="cancelScan"
      >
        <AppIcon name="close" :size="16" />
        {{ isBusy('cancel-scan') ? "Cancelando…" : "Cancelar levantamento" }}
      </button>
    </div>
    <p v-else-if="scan?.state === 'cancelled'" class="muted" role="status">
      Levantamento cancelado.
    </p>
    <p v-else-if="scan?.state === 'error'" class="error">
      O levantamento de tarefas falhou: {{ scan.error ?? "erro desconhecido" }}
      <button
        type="button"
        class="btn"
        :disabled="!connected || !enabled || running || isBusy('scan')"
        @click="rescan"
      >
        Tentar de novo
      </button>
    </p>

    <div v-if="running && items.length === 0" class="skeleton" aria-hidden="true">
      <div class="sk-list">
        <div class="sk" v-for="n in 3" :key="n"></div>
      </div>
      <div class="sk-card">
        <div class="sk sk-title"></div>
        <div class="sk sk-body"></div>
        <div class="sk-fields">
          <div class="sk" v-for="n in 3" :key="n"></div>
        </div>
      </div>
    </div>

    <template v-if="items.length === 0 && !running">
      <p v-if="!loaded" class="muted">Carregando…</p>
      <p v-else-if="!enabled" class="muted">
        O criador de issues está desligado. Ative em Configurações → Criador de
        issues.
      </p>
      <p v-else-if="scan?.state === 'done'" class="muted">
        Nenhuma tarefa identificada nesta reunião
      </p>
      <p v-else-if="scan === null && !showContextPrompt" class="muted">
        Revise os prints e clique em Levantar tarefas.
      </p>
    </template>

    <p v-if="uncertain.length" class="uncertain-notice" role="status">
      <AppIcon name="warning" :size="17" />{{ uncertain.length }}
      {{ uncertain.length === 1 ? "criação precisa" : "criações precisam" }} de
      conferência. Confira antes de reenviar.
    </p>
    <label v-if="items.length" class="compact-task-select"
      >Tarefa em revisão<select
        :value="openId ?? ''"
        @change="openId = ($event.target as HTMLSelectElement).value"
      >
        <option v-for="item in items" :key="item.id" :value="item.id">
          {{ presentationLabel(item) }}: {{ item.title || "(sem título)" }}
        </option>
      </select></label
    >
    <div v-if="items.length > 0" class="panes">
      <nav class="list" aria-label="Tarefas">
        <h3>A revisar ({{ pending.length }})</h3>
        <button
          v-for="t in pending"
          :key="t.id"
          type="button"
          class="row"
          :class="{ open: t.id === openId }"
          :aria-current="t.id === openId ? 'true' : undefined"
          :data-row-id="t.id"
          @click="openId = t.id"
        >
          <span class="kind">
            {{ KIND_LABELS[t.kind] ?? t.kind }}
            <span v-if="t.create_state === 'failed'" class="tag bad"
              >falhou</span
            >
          </span>
          <span class="rtitle">{{ t.title || "(sem título)" }}</span>
        </button>

        <template v-for="group in otherGroups" :key="group.label">
          <h3 v-if="group.items.length">
            {{ group.label }} ({{ group.items.length }})
          </h3>
          <button
            v-for="t in group.items"
            :key="t.id"
            type="button"
            class="row"
            :class="{ open: t.id === openId }"
            :aria-current="t.id === openId ? 'true' : undefined"
            :data-row-id="t.id"
            @click="openId = t.id"
          >
            <span class="kind"
              >{{ KIND_LABELS[t.kind] ?? t.kind
              }}<span
                v-if="rowState(t)"
                class="tag"
                :class="{
                  ok: t.create_state === 'created',
                  warn: t.create_state === 'uncertain',
                }"
                ><AppSpinner v-if="isBusy(`create:${t.id}`) || t.create_state === 'creating'" :size="10" />{{ rowState(t) }}</span
              ></span
            >
            <span class="rtitle" :class="{ struck: t.discarded }">{{
              t.title || "(sem título)"
            }}</span>
          </button>
        </template>
      </nav>

      <section v-if="open" class="detail">
        <p v-if="allReviewed" class="note">Tudo revisado</p>
        <div class="card-wrap" :class="{ busy: openCreating }">
          <TaskCard
            :key="open.id"
            class="open-card"
            :task="open"
            :options="options"
            :missing="openMissing"
            :error="cardErrors[open.id] ?? null"
            :prints="prints"
            :inert="openCreating ? '' : undefined"
            @update="onUpdate"
            @draft="(id, title) => (liveTitles = { ...liveTitles, [id]: title })"
            @play="(s, e) => emit('play', s, e)"
            @resolve="onResolve"
          />
          <div v-if="openCreating" class="card-overlay" role="status">
            <AppSpinner :size="20" />Criando a issue no MCP de issues…
          </div>
        </div>
        <div v-if="isPending(open)" class="actions">
          <button
            type="button"
            class="btn"
            aria-label="Tarefa anterior"
            title="Anterior"
            :disabled="openIndex <= 0"
            @click="step(-1)"
          >
            ←
          </button>
          <button
            type="button"
            class="btn"
            aria-label="Próxima tarefa"
            title="Próxima"
            :disabled="openIndex < 0 || openIndex >= pending.length - 1"
            @click="step(1)"
          >
            →
          </button>
          <span class="spacer"></span>
          <button type="button" class="btn" @click="onDiscard(open)">
            Descartar
          </button>
          <button
            type="button"
            class="btn pri"
            :disabled="
              !connected ||
              openMissing.length > 0 ||
              isBusy(`create:${open.id}`)
            "
            @click="createInIssue(open)"
          >
            <template v-if="isBusy(`create:${open.id}`)"
              ><AppSpinner :size="14" /> Criando…</template
            >
            <template v-else>Criar issue</template>
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tasks {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 12px var(--page-gutter) 0;
  min-height: 0;
  container: tasks / inline-size;
  overflow-y: auto;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  flex-shrink: 0;
}
.spacer {
  flex: 1;
}
.scan-box {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 14px;
  padding: 14px 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 12px);
  background: var(--surface-raised);
  color: var(--accent);
  flex-shrink: 0;
}
.scan-text {
  color: var(--text);
}
.scan-text small {
  display: block;
  color: var(--muted);
  font-size: var(--font-meta);
}
.scan-bar {
  width: min(260px, 100%);
  height: 4px;
  margin-top: 6px;
  border-radius: 4px;
  background: var(--selected);
  overflow: hidden;
}
.scan-bar i {
  display: block;
  width: 35%;
  height: 100%;
  border-radius: 4px;
  background: var(--accent);
  animation: scan-slide 1.4s ease-in-out infinite;
}
@keyframes scan-slide {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(300%);
  }
}
.skeleton {
  display: grid;
  grid-template-columns: minmax(220px, 25%) minmax(0, 1fr);
  gap: 24px;
  flex-shrink: 0;
}
.sk-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sk-list .sk {
  height: 38px;
}
.sk-card {
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-raised);
}
.sk {
  border-radius: 6px;
  background: linear-gradient(90deg, var(--selected) 0%, var(--surface) 50%, var(--selected) 100%);
  background-size: 200% 100%;
  animation: sk-shimmer 1.3s linear infinite;
}
.sk-title {
  width: 60%;
  height: 18px;
  margin-bottom: 12px;
}
.sk-body {
  height: 90px;
  margin-bottom: 12px;
}
.sk-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.sk-fields .sk {
  height: 32px;
}
@keyframes sk-shimmer {
  to {
    background-position: -200% 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .scan-bar i,
  .sk {
    animation: none;
  }
}
@container tasks (max-width:920px) {
  .skeleton {
    grid-template-columns: minmax(0, 1fr);
  }
  .sk-list {
    display: none;
  }
}
.scan-context {
  flex-shrink: 0;
  margin-bottom: 14px;
  font-size: var(--font-control);
}
.scan-context summary {
  width: fit-content;
  max-width: 100%;
  padding: 6px 0;
  cursor: pointer;
  color: var(--muted);
}
.scan-context summary:hover {
  color: var(--text);
}
.scan-context label {
  display: block;
  margin: 8px 0 6px;
  font-weight: 500;
}
.scan-context textarea {
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
}
.scan-context textarea:disabled {
  opacity: 0.55;
}
.scan-context p {
  max-width: 75ch;
  margin: 6px 0 0;
  color: var(--muted);
  font-size: var(--font-meta);
}
.panes {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(220px, 25%) minmax(0, 1fr);
  gap: 24px;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
  overflow-y: auto;
}
.list h3 {
  margin: 8px 0 2px;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
}
.list h3:first-child {
  margin-top: 0;
}
.row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  text-align: left;
  background: transparent;
  border: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--text);
  cursor: pointer;
}
.row:hover {
  background: var(--hover);
}
.row.open {
  background: var(--surface);
  border-color: var(--selected);
}
.kind {
  display: flex;
  gap: 6px;
  font-size: 0.7rem;
  color: var(--muted);
}
.tag:has(.spinner) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.tag.ok {
  color: var(--accent);
  font-weight: 600;
}
.tag.warn {
  color: var(--warn);
}
.tag.bad {
  color: var(--danger);
  font-weight: 600;
}
.rtitle {
  font-size: 0.85rem;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.rtitle.struck {
  text-decoration: line-through;
  color: var(--muted);
}
.detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  overflow-y: auto;
}
/* O card aberto cresce até o fim da coluna (a descrição fica com a sobra); em janela baixa, a coluna rola. */
.open-card {
  flex: 1 0 auto;
}
.card-wrap {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1 0 auto;
}
.card-wrap.busy .open-card {
  opacity: 0.45;
}
.card-overlay {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--accent);
  font-weight: 600;
  font-size: 0.9rem;
}
.note {
  margin: 0;
  color: var(--muted);
  font-size: 0.85rem;
}
.actions {
  position: sticky;
  bottom: 0;
  background: var(--surface);
  border-top: 1px solid var(--border);
  padding: 12px 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 6px;
}
.muted {
  color: var(--muted);
}
.error {
  color: var(--danger);
  margin: 0 0 10px;
}
.warn {
  color: var(--warn);
  margin: 0 0 10px;
  font-size: 0.85rem;
}
.compact-task-select {
  display: none;
  flex-shrink: 0;
  font-size: var(--font-meta);
  color: var(--muted);
  margin-bottom: 16px;
}
.compact-task-select select {
  display: block;
  flex: 1;
  min-width: 0;
  width: 100%;
  min-height: 40px;
  padding: 8px 12px;
  margin-top: 8px;
  border: 1px solid var(--border-control);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font: inherit;
}
.uncertain-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--warning-surface);
  color: var(--warn);
  border-radius: var(--radius-md);
  font-size: var(--font-meta);
  margin: 0 0 14px;
}
@container tasks (max-width:920px) {
  .panes {
    grid-template-columns: minmax(0, 1fr);
  }
  .list {
    display: none;
  }
  .compact-task-select {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .compact-task-select select {
    margin: 0;
  }
}
@container tasks (max-width:380px) {
  .actions {
    flex-wrap: wrap;
  }
  .toolbar {
    gap: 4px;
  }
  .toolbar .btn {
    font-size: 0.75rem;
  }
}
</style>
