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
import AppBanner from "./ui/AppBanner.vue";
import AppIcon from "./ui/AppIcon.vue";
import AppLoader from "./ui/AppLoader.vue";
import StatusPill from "./ui/StatusPill.vue";
import TaskCard from "./TaskCard.vue";

const props = defineProps<{ meetingId: string; startedAt: string; prints: MeetingPrint[] }>();
const emit = defineEmits<{
  play: [start: number, end: number];
  /** Tarefas não descartadas, depois de carregar (o contador da aba). */
  count: [n: number];
}>();

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
const activeCount = computed(() => items.value.filter((t) => !t.discarded).length);
watch([loaded, activeCount], ([ready, n]) => {
  if (ready) emit("count", n);
});
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
const otherGroups = computed(() =>
  [
    { label: "Em criação", items: creating.value },
    { label: "Verificar", items: uncertain.value },
    {
      label: "Criadas",
      items: items.value.filter(
        (t) => !t.discarded && t.create_state === "created",
      ),
    },
    { label: "Descartadas", items: items.value.filter((t) => t.discarded) },
  ].filter((g) => g.items.length > 0),
);
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

/** Pílula de estado da linha da lista. Resultado incerto continua "Verificar": nada de falso sucesso. */
interface Pill {
  tone: "neutral" | "ok" | "accent" | "warn" | "danger";
  label: string;
  icon?: string;
  busy?: boolean;
}
function pillFor(t: TaskSuggestion): Pill {
  if (t.discarded) return { tone: "neutral", label: "Descartada" };
  if (t.create_state === "creating" || isBusy(`create:${t.id}`))
    return { tone: "accent", label: "Criando…", busy: true };
  if (t.create_state === "created")
    return { tone: "ok", label: `Criada #${t.issue_number}`, icon: "check" };
  if (t.create_state === "uncertain")
    return { tone: "warn", label: "Verificar", icon: "warning" };
  if (t.create_state === "failed")
    return { tone: "danger", label: "Falhou", icon: "warning" };
  return { tone: "warn", label: "A revisar" };
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

/** Aplica na tela na hora (o "Criar" seguinte já leva o valor) e salva no servidor. */
async function onUpdate(id: string, changes: Changes): Promise<void> {
  loadSeq++; // uma carga em voo traria o valor antigo por cima desta edição
  const touchesFields = Object.keys(changes).some((k) => k !== "discarded");
  items.value = items.value.map((i) =>
    i.id === id ? { ...i, ...changes, edited: i.edited || touchesFields } : i,
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
        class="btn btn-sm"
        :disabled="!connected || !loaded || !enabled || running || isBusy('scan') || isBusy('cancel-scan')"
        :title="enabled ? undefined : 'O criador de issues está desligado'"
        @click="rescan"
      >
        <AppIcon name="refresh" :size="16" />
        {{ scan === null ? "Levantar tarefas" : "Refazer sugestões" }}
      </button>
      <button
        type="button"
        class="btn btn-sm"
        :disabled="!connected || isBusy('add')"
        @click="addManual"
      >
        <AppIcon name="plus" :size="16" /> Adicionar tarefa
      </button>
      <button type="button" class="btn btn-sm" @click="copy">
        <AppIcon name="copy" :size="16" /> {{ copied ? "Copiado" : "Copiar" }}
      </button>
      <button type="button" class="btn btn-sm" @click="exportMarkdown">
        <AppIcon name="export" :size="16" /> Exportar .md
      </button>
    </div>

    <details v-if="enabled || items.length" class="scan-context">
      <summary>Contexto para o levantamento{{ scanContext.trim() ? " (preenchido)" : " (opcional)" }}</summary>
      <label :for="contextId">Como você quer orientar as sugestões?</label>
      <textarea
        :id="contextId"
        v-model="scanContext"
        :aria-describedby="`${contextId}-hint`"
        :disabled="!connected || !enabled || running || isBusy('scan')"
        rows="3"
        maxlength="10000"
        placeholder="Ex.: Foque nas pendências do checkout e agrupe os ajustes de pagamento em uma tarefa."
      ></textarea>
      <p :id="`${contextId}-hint`">
        O texto será usado junto com a transcrição e os prints ao levantar as tarefas.
        Tarefas editadas, descartadas ou já enviadas são preservadas.
      </p>
    </details>

    <AppBanner v-if="loadError" class="error" role="alert">{{ loadError }}</AppBanner>
    <AppBanner v-if="actionError" class="error" role="alert">{{ actionError }}</AppBanner>
    <AppBanner v-if="showOptionsError" tone="warn" class="warn">
      Não foi possível carregar as opções do MCP de issues: {{ optionsError }}
      <template #action>
        <button type="button" class="btn btn-sm" @click="loadOptions(true)">
          Tentar de novo
        </button>
      </template>
    </AppBanner>

    <div v-if="running" class="scan-status">
      <AppLoader size="sm" />
      <span class="scan-text" role="status">{{ isBusy('cancel-scan') ? "Cancelando levantamento…" : "Levantando tarefas…" }}</span>
      <button
        type="button"
        class="btn btn-sm"
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
    <AppBanner v-else-if="scan?.state === 'error'" class="error">
      O levantamento de tarefas falhou: {{ scan.error ?? "erro desconhecido" }}
      <template #action>
        <button
          type="button"
          class="btn btn-sm"
          :disabled="!connected || !enabled || running || isBusy('scan')"
          @click="rescan"
        >
          Tentar de novo
        </button>
      </template>
    </AppBanner>

    <template v-if="items.length === 0 && !running">
      <p v-if="!loaded" class="muted">Carregando…</p>
      <p v-else-if="!enabled" class="muted">
        O criador de issues está desligado. Ative em Configurações → Criador de
        issues.
      </p>
      <p v-else-if="scan?.state === 'done'" class="muted">
        Nenhuma tarefa identificada nesta reunião
      </p>
      <p v-else-if="scan === null" class="muted">
        Revise os prints e clique em Levantar tarefas.
      </p>
    </template>

    <AppBanner v-if="uncertain.length" tone="warn" class="uncertain-notice" role="status">
      {{ uncertain.length }}
      {{ uncertain.length === 1 ? "criação precisa" : "criações precisam" }} de
      conferência. Confira antes de reenviar.
    </AppBanner>
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
        <div class="group">
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
            <span class="top">
              <span class="kind">{{ KIND_LABELS[t.kind] ?? t.kind }}</span>
              <StatusPill class="pill" :tone="pillFor(t).tone" :icon="pillFor(t).icon" :busy="pillFor(t).busy">{{ pillFor(t).label }}</StatusPill>
            </span>
            <span class="rtitle">{{ t.title || "(sem título)" }}</span>
          </button>
        </div>

        <div v-for="group in otherGroups" :key="group.label" class="group">
          <h3>{{ group.label }} ({{ group.items.length }})</h3>
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
            <span class="top">
              <span class="kind">{{ KIND_LABELS[t.kind] ?? t.kind }}</span>
              <StatusPill class="pill" :tone="pillFor(t).tone" :icon="pillFor(t).icon" :busy="pillFor(t).busy">{{ pillFor(t).label }}</StatusPill>
            </span>
            <span class="rtitle" :class="{ struck: t.discarded }">{{
              t.title || "(sem título)"
            }}</span>
          </button>
        </div>
      </nav>

      <section v-if="open" class="detail">
        <p v-if="allReviewed" class="note">Tudo revisado</p>
        <TaskCard
          :key="open.id"
          class="open-card"
          :task="open"
          :options="options"
          :missing="openMissing"
          :error="cardErrors[open.id] ?? null"
          :prints="prints"
          @update="onUpdate"
          @draft="(id, title) => (liveTitles = { ...liveTitles, [id]: title })"
          @play="(s, e) => emit('play', s, e)"
          @resolve="onResolve"
        />
        <div v-if="isPending(open)" class="actions">
          <button
            type="button"
            class="icon-button"
            aria-label="Tarefa anterior"
            title="Anterior"
            :disabled="openIndex <= 0"
            @click="step(-1)"
          >
            <AppIcon name="arrow" :size="18" />
          </button>
          <button
            type="button"
            class="icon-button next"
            aria-label="Próxima tarefa"
            title="Próxima"
            :disabled="openIndex < 0 || openIndex >= pending.length - 1"
            @click="step(1)"
          >
            <AppIcon name="arrow" :size="18" />
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
            {{ isBusy(`create:${open.id}`) ? "Criando…" : "Criar issue" }}
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
  gap: var(--space-3);
  padding: var(--space-3) var(--page-gutter) 0;
  min-height: 0;
  container: tasks / inline-size;
  overflow-y: auto;
}
.tasks > * {
  flex-shrink: 0;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}
.spacer {
  flex: 1;
}
.scan-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
  padding: var(--space-3) var(--space-5);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  background: var(--glass);
}
.scan-text {
  color: var(--text-accent);
  font-size: 14px;
  font-weight: 700;
}
.scan-status .btn {
  margin-left: auto;
}
.scan-context {
  font-size: 14px;
}
.scan-context summary {
  width: fit-content;
  max-width: 100%;
  padding: var(--space-2) 0;
  cursor: pointer;
  color: var(--text-muted);
  font-weight: 700;
}
.scan-context summary:hover {
  color: var(--text);
}
.scan-context label {
  display: block;
  margin: var(--space-2) 0;
  font-size: 13px;
  font-weight: 700;
}
.scan-context textarea {
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
.scan-context textarea::placeholder {
  color: var(--text-muted);
}
.scan-context textarea:focus {
  outline: none;
  border-color: var(--focus-ring);
  box-shadow:
    0 0 0 1px var(--focus-ring),
    0 0 0 5px var(--selected);
}
.scan-context textarea:disabled {
  opacity: 0.6;
}
.scan-context p {
  max-width: 75ch;
  margin: var(--space-2) 0 0;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
}
.panes {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(240px, 340px) minmax(0, 1fr);
  gap: var(--space-6);
}
.list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-height: 0;
  overflow-y: auto;
}
.group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.group h3 {
  margin: 0 var(--space-1);
  font-size: 11px;
  line-height: 14px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-muted);
}
/* Cartão da lista (am-task). */
.row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  padding: 14px;
  text-align: left;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  background: var(--glass);
  color: var(--text);
  cursor: pointer;
  transition: background var(--duration-fast);
}
.row:hover {
  background: var(--selected);
}
.row.open {
  background: var(--selected);
  border-color: var(--selected-border);
}
.top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.kind {
  font-size: 12px;
  line-height: 16px;
  font-weight: 700;
  color: var(--text-muted);
}
.row .pill {
  height: 22px;
  padding: 0 8px;
  font-size: 11px;
  margin-left: auto;
}
.rtitle {
  font-size: 14px;
  line-height: 20px;
  font-weight: 700;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.rtitle.struck {
  text-decoration: line-through;
  color: var(--text-muted);
}
.detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-height: 0;
  overflow-y: auto;
}
/* O card aberto cresce até o fim da coluna (a descrição fica com a sobra); em janela baixa, a coluna rola. */
.open-card {
  flex: 1 0 auto;
}
.note {
  margin: 0;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 600;
}
.actions {
  position: sticky;
  bottom: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-2);
  background: var(--surface-solid);
  border-top: 1px solid var(--glass-border);
}
.icon-button.next svg {
  transform: rotate(180deg);
}
.muted {
  margin: 0;
  color: var(--text-muted);
  font-weight: 600;
}
.error,
.warn,
.uncertain-notice {
  max-width: var(--read-max);
}
.compact-task-select {
  display: none;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-muted);
}
.compact-task-select select {
  display: block;
  width: 100%;
  min-height: var(--control-lg);
  padding: 0 44px 0 var(--space-5);
  margin-top: var(--space-2);
  border: 1px solid var(--border-control);
  border-radius: var(--radius-pill);
  background-color: var(--glass-strong);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  appearance: none;
  background-image:
    linear-gradient(45deg, transparent 50%, currentColor 50%),
    linear-gradient(135deg, currentColor 50%, transparent 50%);
  background-position:
    calc(100% - 24px) 50%,
    calc(100% - 19px) 50%;
  background-size: 5px 5px;
  background-repeat: no-repeat;
}
.compact-task-select select option {
  background: var(--surface-solid);
  color: var(--text);
}
@container tasks (max-width:920px) {
  .panes {
    grid-template-columns: minmax(0, 1fr);
  }
  .list {
    display: none;
  }
  .compact-task-select {
    display: block;
  }
  .compact-task-select select {
    margin-top: var(--space-2);
  }
}
@container tasks (max-width:380px) {
  .actions {
    flex-wrap: wrap;
  }
}
</style>
