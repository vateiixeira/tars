<script setup lang="ts">
// Reunião aberta: título editável, etiquetas dos participantes, ações, alertas e abas
// Transcrição/Resumo. Cobre também os estados "processando" (com o estágio do evento `progress`)
// e "falhou" (com Reprocessar).
//
// Pessoas e resumo: clique numa etiqueta abre o `SpeakerMenu` (renomear/identificar/juntar), "Sugerir
// nomes" abre o `NameSuggestionsDialog`, a aba "Resumo" é o `SummaryTab` e a aba "Tarefas" é o
// `TasksTab` (sugestões do criador de issues). O resumo também pode vir automático ao fim do
// processamento (evento `meeting_job`); "Resumir"/"Refazer"/"Sugerir nomes" chamam o Claude por
// clique. Toda mudança recarrega o detalhe e avisa a lista.
import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";
import { save } from "@tauri-apps/plugin-dialog";
import { writeFile, writeTextFile } from "@tauri-apps/plugin-fs";
import type {
  MeetingDetail,
  ProgressStage,
  Segment,
  Speaker,
} from "../api/types";
import { notifyMeetingsChanged } from "../composables/useMeetingsRevision";
import { useStatus } from "../composables/useStatus";
import { groupByDay, timeOfDay } from "../lib/format";
import { alertLines, exportFileName, transcriptText } from "../lib/meeting";
import AppIcon from "./ui/AppIcon.vue";
import AppDialog from "./ui/AppDialog.vue";
import NameSuggestionsDialog from "./NameSuggestionsDialog.vue";
import SpeakerChips from "./SpeakerChips.vue";
import SpeakerMenu from "./SpeakerMenu.vue";
import PrintsTab from "./PrintsTab.vue";
import SummaryTab from "./SummaryTab.vue";
import TasksTab from "./TasksTab.vue";
import TranscriptView from "./TranscriptView.vue";

const props = defineProps<{ meetingId: string }>();
const emit = defineEmits<{
  /** Reunião excluída (o pai deve deixar de mostrá-la). */
  deleted: [meetingId: string];
  /** Mudou algo na reunião (resumo, nomes, falantes...): o pai pode recarregar. */
  changed: [meetingId: string];
  tasksView: [active: boolean];
}>();

const { client, events, connected, alerts: liveAlerts, levels } = useStatus();

const meeting = ref<MeetingDetail | null>(null);
const levelTime = ref(Date.now());
let levelTimer: ReturnType<typeof setInterval> | undefined;
watch(
  () => meeting.value?.status,
  (value) => {
    clearInterval(levelTimer);
    levelTimer = undefined;
    if (value === "recording") {
      levelTime.value = Date.now();
      levelTimer = setInterval(() => (levelTime.value = Date.now()), 500);
    }
  },
);
onBeforeUnmount(() => clearInterval(levelTimer));
const captureLevels = computed(() =>
  connected.value && levels.value && levelTime.value - levels.value.at < 3000
    ? levels.value
    : null,
);
const loadError = ref<string | null>(null);
// Erro por ação: o de uma não some quando outra começa.
const actionErrors = ref<Record<string, string>>({});
// Banners por ação (chave = ação, única). O erro do resumo aparece na própria aba quando ela está aberta.
const errorBanners = computed(() =>
  Object.entries(actionErrors.value)
    .filter(([action]) => !(action === "summarize" && tab.value === "summary"))
    .map(([action, message]) => ({ action, message })),
);

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
const stage = ref<ProgressStage | null>(null);
type MeetingTab = "transcript" | "summary" | "prints" | "tasks";
const tab = ref<MeetingTab>("transcript");
const previousTab = ref<"transcript" | "summary" | "prints">("transcript");
const tasksActive = computed(() => tab.value === "tasks" && meeting.value?.status === "ready");
const tasksBack = ref<HTMLButtonElement | null>(null);
function openTasks(): void {
  if (tab.value !== "tasks") previousTab.value = tab.value;
  menuOpen.value = false;
  speakerMenu.value = null;
  tab.value = "tasks";
}
function closeTasks(): void {
  tab.value = previousTab.value;
}
watch(tasksActive, (active, previous) => {
  emit("tasksView", active);
  void nextTick(() => {
    if (active) tasksBack.value?.focus();
    else if (previous) document.getElementById("open-tasks")?.focus();
  });
});
const tabMemory = inject<Map<string, string>>("meeting-tabs", new Map());
let didChooseTab = false;
const detailTabs = computed<{ id: MeetingTab; label: string }[]>(() => {
  const tabs: { id: MeetingTab; label: string }[] = [
    { id: "summary", label: "Resumo" },
    { id: "transcript", label: "Transcrição" },
  ];
  const count = meeting.value?.prints.length ?? 0;
  if (count) tabs.push({ id: "prints", label: `Prints (${count})` });
  return tabs;
});
// Sem prints (ex.: o último foi apagado) a aba some: volta para a transcrição.
watch(detailTabs, (tabs) => {
  if (!tabs.some((t) => t.id === tab.value) && tab.value !== "tasks") tab.value = "transcript";
  if (previousTab.value === "prints" && !tabs.some((t) => t.id === "prints")) previousTab.value = "transcript";
});
const printFocus = ref<string | null>(null);
function openPrint(id: string): void {
  printFocus.value = id;
  tab.value = "prints";
  void nextTick(() => document.getElementById("tab-prints")?.focus());
}
function onTabKey(event: KeyboardEvent, index: number) {
  let next = index;
  const tabs = detailTabs.value;
  if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
  else if (event.key === "ArrowLeft")
    next = (index + tabs.length - 1) % tabs.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = tabs.length - 1;
  else return;
  event.preventDefault();
  tab.value = tabs[next]!.id;
  void nextTick(() => document.getElementById(`tab-${tab.value}`)?.focus());
}
// Reunião cuja aba Tarefas já foi aberta: a aba fica montada daí em diante (até trocar de reunião).
const tasksOpenedFor = ref<string | null>(null);
watch(tab, (t) => {
  if (didChooseTab) tabMemory.set(props.meetingId, t);
  if (t === "tasks")
    tasksOpenedFor.value = meeting.value?.id ?? props.meetingId;
});

let loadSeq = 0;
async function load(): Promise<void> {
  const seq = ++loadSeq;
  try {
    const result = await client.getMeeting(props.meetingId);
    if (seq !== loadSeq) return;
    meeting.value = result;
    if (!didChooseTab) {
      didChooseTab = true;
      previousTab.value = result.summary ? "summary" : "transcript";
      const remembered = tabMemory.get(props.meetingId) as MeetingTab | undefined;
      tab.value =
        remembered && (remembered !== "prints" || result.prints.length)
          ? remembered
          : result.summary ? "summary" : "transcript";
      if (tab.value === "tasks") tasksOpenedFor.value = props.meetingId;
    }
    loadError.value = null;
    if (result.status !== "processing") stage.value = null; // estágio de um `progress` antigo
  } catch (e) {
    if (seq !== loadSeq) return;
    loadError.value = e instanceof Error ? e.message : String(e);
  }
}

const offUpdated = events.on("meeting_updated", (e) => {
  if (e.meeting_id !== props.meetingId) return;
  if (e.status !== "processing") stage.value = null;
  void load();
});
const offProgress = events.on("progress", (e) => {
  if (e.meeting_id === props.meetingId) stage.value = e.stage;
});
// Resumo automático (ou refeito em outra janela) mudou de estado: recarrega o detalhe.
const offJob = events.on("meeting_job", (e) => {
  if (e.meeting_id === props.meetingId && e.kind === "summary") void load();
});
// (Re)conexão: um `meeting_updated` pode ter se perdido durante a queda do backend.
watch(connected, (on) => {
  if (on) void load();
});

// -- cabeçalho ------------------------------------------------------------------------------

const title = computed(() => meeting.value?.title || "Reunião sem título");
const when = computed(() => {
  const m = meeting.value;
  if (!m) return "";
  const day = groupByDay([m])[0].label;
  const end = m.ended_at ? `–${timeOfDay(m.ended_at)}` : "";
  return `${day}, ${timeOfDay(m.started_at)}${end}`;
});
const alerts = computed(() => (meeting.value ? alertLines(meeting.value) : []));

const editing = ref(false);
const draft = ref("");
const titleInput = ref<HTMLInputElement | null>(null);
const savingTitle = ref(false);
const renameButton = ref<HTMLButtonElement | null>(null);
const moreButton = ref<HTMLButtonElement | null>(null);
const moreWrap = ref<HTMLElement | null>(null);
function toggleMore() {
  menuOpen.value = !menuOpen.value;
  confirmingDelete.value = false;
  if (menuOpen.value)
    void nextTick(() =>
      moreWrap.value?.querySelector<HTMLButtonElement>(".menu button")?.focus(),
    );
}
function closeOutside(event: PointerEvent) {
  if (menuOpen.value && !moreWrap.value?.contains(event.target as Node))
    menuOpen.value = false;
}
onMounted(() => document.addEventListener("pointerdown", closeOutside));
onBeforeUnmount(() =>
  document.removeEventListener("pointerdown", closeOutside),
);
function cancelDelete() {
  confirmingDelete.value = false;
  void nextTick(() => moreButton.value?.focus());
}
function cancelTitle() {
  editing.value = false;
  void nextTick(() => renameButton.value?.focus());
}

function startEdit(): void {
  draft.value = meeting.value?.title ?? "";
  editing.value = true;
  void nextTick(() => titleInput.value?.select());
}

async function commitTitle(): Promise<void> {
  if (!editing.value || savingTitle.value) return;
  const next = draft.value.trim();
  if (!next || next === (meeting.value?.title ?? "")) {
    cancelTitle();
    return;
  }
  savingTitle.value = true;
  const id = props.meetingId;
  const { title: _oldError, ...rest } = actionErrors.value;
  actionErrors.value = rest;
  try {
    await client.renameMeeting(id, next);
    await refreshAfterChange(id);
    if (id === props.meetingId) cancelTitle();
  } catch (e) {
    if (id === props.meetingId) {
      actionErrors.value = { ...actionErrors.value, title: errorText(e) };
      void nextTick(() => titleInput.value?.focus());
    }
  } finally {
    savingTitle.value = false;
  }
}

// -- ações ----------------------------------------------------------------------------------

const copied = ref(false);
let copiedTimer: ReturnType<typeof setTimeout> | undefined;
// Ações em andamento (várias podem rodar juntas; cada uma só habilita o próprio botão ao terminar).
// A chave leva o id da reunião: uma ação que ficou para trás ao trocar de reunião não trava os
// botões da reunião nova nem os libera antes da hora.
const busy = ref<ReadonlySet<string>>(new Set());
const busyKey = (name: string, id: string = props.meetingId) => `${id}:${name}`;
const isBusy = (name: string) => busy.value.has(busyKey(name));

function setBusy(key: string, on: boolean): void {
  const next = new Set(busy.value);
  if (on) next.add(key);
  else next.delete(key);
  busy.value = next;
}

/** Recarrega o detalhe e avisa a lista/pai, a menos que o usuário já tenha trocado de reunião. */
async function refreshAfterChange(id: string): Promise<void> {
  if (id !== props.meetingId) return;
  await load();
  if (id !== props.meetingId) return;
  notifyMeetingsChanged();
  emit("changed", id);
}

async function run(name: string, fn: () => Promise<void>): Promise<void> {
  const forMeeting = props.meetingId;
  const key = busyKey(name, forMeeting);
  if (busy.value.has(key)) return; // já em andamento: segundo clique não dispara outra chamada
  setBusy(key, true);
  const { [name]: _cleared, ...rest } = actionErrors.value;
  actionErrors.value = rest;
  try {
    await fn();
  } catch (e) {
    // erro de uma reunião que já não está aberta não deve aparecer na outra
    if (forMeeting === props.meetingId)
      actionErrors.value = { ...actionErrors.value, [name]: errorText(e) };
  } finally {
    setBusy(key, false);
  }
}

function copyTranscript(): Promise<void> {
  return run("copy", async () => {
    const m = meeting.value;
    if (!m) return;
    await writeText(transcriptText(m.segments, m.speakers));
    copied.value = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (copied.value = false), 2000);
  });
}

/** Com prints, exporta o .zip (markdown + imagens); sem prints, só o .md. */
function exportMeeting(): Promise<void> {
  return run("export", async () => {
    const m = meeting.value;
    if (!m) return;
    if (m.prints.length) {
      const path = await save({
        defaultPath: exportFileName(m.started_at, "zip"),
        filters: [{ name: "Zip", extensions: ["zip"] }],
      });
      if (!path) return; // diálogo cancelado
      await writeFile(path, await client.exportZip(m.id));
      return;
    }
    const path = await save({
      defaultPath: exportFileName(m.started_at),
      filters: [{ name: "Markdown", extensions: ["md"] }],
    });
    if (!path) return; // diálogo cancelado
    await writeTextFile(path, await client.exportMarkdown(m.id));
  });
}
const exportExtension = computed(() => (meeting.value?.prints.length ? ".zip" : ".md"));

// Resumir/Refazer: chama o Claude só no clique. Abre a aba Resumo já no clique (mostra "resumindo…");
// se o usuário trocar de reunião no meio, o resultado não mexe na reunião nova.
function onSummarize(): Promise<void> {
  const id = props.meetingId;
  if (isBusy("summarize")) return Promise.resolve();
  tab.value = "summary";
  return run("summarize", async () => {
    await client.summarize(id);
    await refreshAfterChange(id);
  });
}

// Pessoas: menu da etiqueta e diálogo de sugestões (o Claude é chamado só ao abrir o diálogo, por clique).
const speakerMenu = ref<{ speaker: Speaker; x: number; y: number } | null>(
  null,
);
const suggesting = ref(false);

function onSpeakerSelect(speaker: Speaker, ev: MouseEvent): void {
  if (speaker.is_me) return; // "Você" não tem menu
  const rect = (
    ev.currentTarget as HTMLElement | null
  )?.getBoundingClientRect();
  speakerMenu.value = {
    speaker,
    x: rect ? rect.left : ev.clientX,
    y: rect ? rect.bottom + 4 : ev.clientY,
  };
}

const onPrintDeleted = () => refreshAfterChange(props.meetingId);
const onPeopleChanged = () => refreshAfterChange(props.meetingId);

const confirmingReprocess = ref(false);

function requestReprocess(): void {
  if (meeting.value?.speakers.length) confirmingReprocess.value = true;
  else void reprocess();
}

function reprocess(): Promise<void> {
  const id = props.meetingId;
  confirmingReprocess.value = false;
  return run("reprocess", async () => {
    await client.reprocess(id);
    if (id !== props.meetingId) return;
    stage.value = null;
    await load();
    if (id !== props.meetingId) return;
    notifyMeetingsChanged();
  });
}

const menuOpen = ref(false);
const peopleExpanded = ref(false);
const confirmingDelete = ref(false);

function remove(): Promise<void> {
  const id = props.meetingId;
  return run("delete", async () => {
    await client.deleteMeeting(id);
    notifyMeetingsChanged();
    emit("deleted", id);
    if (id !== props.meetingId) return;
    menuOpen.value = false;
    confirmingDelete.value = false;
  });
}

// -- áudio ----------------------------------------------------------------------------------

const audioUrl = ref<string | null>(null);
const audioEl = ref<HTMLAudioElement | null>(null);
let audioSeq = 0;

function stopAudio(): void {
  ++audioSeq;
  audioEl.value?.pause?.();
  if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
  audioUrl.value = null;
}

/** Baixa o áudio (reunião inteira ou só o trecho) e toca no player. */
function playAudio(start?: number, end?: number): Promise<void> {
  return run("audio", async () => {
    const id = props.meetingId;
    const seq = ++audioSeq;
    const blob = await client.audioBlob(id, start, end);
    if (id !== props.meetingId || seq !== audioSeq) return;
    const previous = audioUrl.value;
    audioUrl.value = URL.createObjectURL(blob);
    if (previous) URL.revokeObjectURL(previous);
    await nextTick();
    // jsdom não implementa `play`; no app, um autoplay barrado não deve virar erro na tela
    await audioEl.value?.play?.()?.catch(() => {});
  });
}

function playSegment(seg: Segment): void {
  void playAudio(seg.start_s, seg.end_s);
}

// Trocar de reunião zera o estado local (declarado por último: usa refs definidos acima).
watch(
  () => props.meetingId,
  () => {
    didChooseTab = false;
    meeting.value = null;
    stage.value = null;
    actionErrors.value = {};
    editing.value = false;
    confirmingDelete.value = false;
    confirmingReprocess.value = false;
    menuOpen.value = false;
    peopleExpanded.value = false;
    speakerMenu.value = null;
    suggesting.value = false;
    tab.value = "transcript";
    printFocus.value = null;
    tasksOpenedFor.value = null;
    previousTab.value = "transcript";
    stopAudio();
    void load();
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  emit("tasksView", false);
  offUpdated();
  offProgress();
  offJob();
  clearTimeout(copiedTimer);
  stopAudio();
});
</script>

<template>
  <section class="detail" :class="{ 'tasks-page': tasksActive }">
    <p v-if="loadError && !meeting" class="state error" role="alert">
      {{ loadError }}
      <button class="btn" type="button" @click="load">Tentar de novo</button>
    </p>
    <div v-else-if="!meeting" class="state" role="status">
      <div class="skeleton-line" />
      <div class="skeleton-line short" />
      Carregando reunião…
    </div>
    <template v-else>
      <header v-show="!tasksActive" class="head">
        <div class="context-label">
          Reunião
          <span v-if="meeting.status === 'ready'" class="ready"
            ><AppIcon name="check" :size="14" />Pronta para revisar</span
          >
        </div>
        <div class="title-row">
          <input
            v-if="editing"
            ref="titleInput"
            v-model="draft"
            class="title-input"
            maxlength="200"
            aria-label="Título da reunião"
            :aria-invalid="actionErrors.title ? true : undefined"
            :disabled="savingTitle"
            @keydown.enter.prevent="commitTitle"
            @keydown.esc.prevent="cancelTitle"
            @blur="commitTitle"
          />
          <h1 v-else>
            <button
              ref="renameButton"
              type="button"
              class="title"
              title="Editar título da reunião"
              :aria-label="`Renomear reunião: ${title}`"
              :disabled="!connected"
              @click="startEdit"
            >
              {{ title }}<AppIcon name="edit" :size="17" />
            </button>
          </h1>
          <div
            ref="moreWrap"
            class="more-wrap"
            @keydown.esc.stop.prevent="
              menuOpen = false;
              moreButton?.focus();
            "
          >
            <button
              ref="moreButton"
              type="button"
              class="more icon-button"
              aria-label="Mais opções"
              :aria-expanded="menuOpen"
              @click="toggleMore"
            >
              <AppIcon name="more" />
            </button>
            <div
              v-if="menuOpen"
              class="menu"
              @keydown.esc.stop.prevent="
                menuOpen = false;
                moreButton?.focus();
              "
            >
              <button
                type="button"
                :disabled="isBusy('export') || !connected"
                @click="
                  exportMeeting();
                  menuOpen = false;
                "
              >
                {{ meeting.prints.length ? "Exportar reunião com prints (.zip)" : "Exportar reunião em Markdown" }}
              </button>
              <button
                type="button"
                :disabled="!connected"
                @click="
                  suggesting = true;
                  menuOpen = false;
                "
              >
                Sugerir nomes
              </button>
              <button
                type="button"
                @click="
                  peopleExpanded = !peopleExpanded;
                  menuOpen = false;
                "
              >
                {{ peopleExpanded ? "Ocultar participantes" : "Participantes" }}
              </button>
              <button
                type="button"
                class="danger"
                :disabled="!connected"
                @click="
                  confirmingDelete = true;
                  menuOpen = false;
                "
              >
                Excluir reunião
              </button>
            </div>
          </div>
        </div>
        <div class="meta">
          <AppIcon name="clock" :size="14" />{{ when
          }}<span v-if="savingTitle" role="status">Salvando título…</span>
        </div>
        <div class="head-bottom" :class="{ expanded: peopleExpanded }">
          <SpeakerChips
            :speakers="meeting.speakers"
            @select="onSpeakerSelect"
          /><button
            type="button"
            class="btn export"
            :disabled="isBusy('export') || !connected"
            @click="exportMeeting"
          >
            <AppIcon name="export" :size="16" />Exportar {{ exportExtension }}
          </button>
        </div>
      </header>
      <p
        v-for="b in errorBanners"
        :key="b.action"
        class="banner error"
        role="alert"
      >
        {{ b.message }}
      </p>
      <div v-if="meeting.status === 'recording'" class="capture-state">
        <span class="capture-status"
          ><span class="capture-dot" />Gravação em andamento</span
        >
        <h2>Registrando a reunião</h2>
        <p>
          Microfone e áudio do computador. A transcrição completa aparece após o
          processamento.
        </p>
        <div
          v-for="alert in liveAlerts"
          :key="alert.kind"
          class="capture-alert"
          role="status"
        >
          <AppIcon name="warning" :size="18" />{{ alert.message }}
        </div>
        <div v-if="captureLevels" class="capture-levels">
          <label
            >Microfone<meter
              min="-90"
              max="0"
              :value="captureLevels.mic_db"
            />{{
              captureLevels.mic_db > -60
                ? "Sinal recebido"
                : "Sem sinal recente"
            }}</label
          ><label
            >Áudio do PC<meter
              min="-90"
              max="0"
              :value="captureLevels.pc_db"
            />{{
              captureLevels.pc_db > -60 ? "Sinal recebido" : "Sem sinal recente"
            }}</label
          >
        </div>
        <p v-if="!captureLevels" class="capture-hint">
          Aguardando níveis de áudio recentes.
        </p>
        <p class="capture-hint">
          Use Parar no controle de captura para encerrar a reunião.
        </p>
      </div>
      <div
        v-else-if="meeting.status === 'processing'"
        class="state progress"
        role="status"
      >
        <AppIcon name="refresh" :size="28" />
        <h2>Preparando sua reunião</h2>
        <p>Processando: {{ stage ?? "aguardando" }}…</p>
        <p class="muted">Você já pode iniciar outra reunião.</p>
      </div>
      <div v-else-if="meeting.status === 'failed'" class="state failed">
        <p role="alert">
          {{ meeting.error || "O processamento da reunião falhou." }}
        </p>
        <div
          v-if="confirmingReprocess"
          class="confirm"
          role="group"
          aria-label="Confirmar reprocessamento"
        >
          <p>
            Reprocessar recria a transcrição e os falantes. Os nomes editados e
            as mesclagens de falantes serão removidos. As vozes cadastradas
            continuam salvas.
          </p>
          <div>
            <button
              type="button"
              class="btn"
              @click="confirmingReprocess = false"
            >
              Cancelar</button
            ><button
              type="button"
              class="btn pri"
              :disabled="isBusy('reprocess') || !connected"
              @click="reprocess"
            >
              Confirmar reprocessamento
            </button>
          </div>
        </div>
        <button
          v-else
          type="button"
          class="btn pri"
          :disabled="isBusy('reprocess') || !connected"
          @click="requestReprocess"
        >
          Reprocessar
        </button>
      </div>
      <template v-else>
        <header v-show="tasksActive" class="tasks-head">
          <button ref="tasksBack" type="button" class="btn ghost" @click="closeTasks">
            <AppIcon name="arrow" :size="18" />Voltar à reunião
          </button>
          <div class="tasks-heading">
            <h1 id="tasks-heading">Tarefas</h1>
            <p>{{ title }}</p>
          </div>
        </header>
        <div v-show="!tasksActive" class="tabs-row">
          <div class="tabs" role="tablist" aria-label="Conteúdo da reunião">
            <button
              v-for="(item, index) in detailTabs"
              :id="`tab-${item.id}`"
              :key="item.id"
              type="button"
              role="tab"
              class="tab"
              :class="{ on: tab === item.id }"
              :aria-selected="tab === item.id"
              :aria-controls="`panel-${item.id}`"
              :tabindex="tab === item.id ? 0 : -1"
              @click="tab = item.id"
              @keydown="onTabKey($event, index)"
            >
              {{ item.label }}
            </button>
          </div>
          <button id="open-tasks" type="button" class="btn ghost tasks-link" @click="openTasks">
            <AppIcon name="file" :size="16" />Tarefas<AppIcon name="chevron" :size="16" />
          </button>
        </div>
        <div v-if="tab !== 'tasks'" class="actions">
          <template v-if="tab === 'transcript'"
            ><button
              type="button"
              class="btn ghost"
              :disabled="isBusy('copy')"
              @click="copyTranscript"
            >
              <AppIcon name="copy" :size="16" />{{
                copied ? "Copiado" : "Copiar transcrição"
              }}</button
            ><button
              type="button"
              class="btn ghost"
              :disabled="!connected"
              @click="suggesting = true"
            >
              <AppIcon name="people" :size="16" />Sugerir nomes</button
            ><button
              type="button"
              class="btn ghost"
              :disabled="isBusy('summarize') || !connected"
              @click="onSummarize"
            >
              <AppIcon name="file" :size="16" />{{
                isBusy("summarize") ? "Resumindo…" : "Resumir"
              }}
            </button></template
          >
          <span v-else-if="tab === 'summary'" class="section-caption"
            >Resumo da reunião</span
          ><span v-else-if="tab === 'prints'" class="section-caption"
            >Prints de tela tirados durante a reunião.</span
          ><span v-else class="section-caption"
            >Revise antes de criar no MCP de issues.</span
          >
          <button
            type="button"
            class="btn ghost audio-action"
            :disabled="isBusy('audio') || !connected"
            @click="playAudio()"
          >
            <AppIcon name="play" :size="16" />Ouvir áudio
          </button>
        </div>
        <ul v-if="alerts.length && tab === 'transcript'" class="alerts">
          <li v-for="(line, i) in alerts" :key="i">{{ line }}</li>
        </ul>
        <div class="body">
          <div
            v-show="tab === 'transcript'"
            id="panel-transcript"
            role="tabpanel"
            aria-labelledby="tab-transcript"
            class="tab-panel"
            tabindex="0"
          >
            <TranscriptView
              v-if="tab === 'transcript'"
              :key="meeting.id"
              :segments="meeting.segments"
              :speakers="meeting.speakers"
              :prints="meeting.prints"
              @play="playSegment"
              @open-print="openPrint"
            />
          </div>
          <div
            v-show="tab === 'summary'"
            id="panel-summary"
            role="tabpanel"
            aria-labelledby="tab-summary"
            class="tab-panel"
            tabindex="0"
          >
            <SummaryTab
              v-if="tab === 'summary'"
              :key="meeting.id"
              :summary="meeting.summary"
              :loading="isBusy('summarize')"
              :error="actionErrors.summarize ?? null"
              :job="meeting.summary_job"
              @summarize="onSummarize"
            />
          </div>
          <div
            v-show="tab === 'prints'"
            id="panel-prints"
            role="tabpanel"
            aria-labelledby="tab-prints"
            class="tab-panel"
            tabindex="0"
          >
            <PrintsTab
              v-if="tab === 'prints'"
              :key="meeting.id"
              :meeting-id="meeting.id"
              :prints="meeting.prints"
              :segments="meeting.segments"
              :speakers="meeting.speakers"
              :focus-id="printFocus"
              @play="(start: number) => playAudio(start)"
              @deleted="onPrintDeleted"
            />
          </div>
          <div
            v-show="tab === 'tasks'"
            id="panel-tasks"
            role="region"
            aria-labelledby="tasks-heading"
            class="tab-panel"
            tabindex="0"
          >
            <TasksTab
              v-if="tasksOpenedFor === meeting.id"
              :key="meeting.id"
              :meeting-id="meeting.id"
              :started-at="meeting.started_at"
              @play="playAudio"
            />
          </div>
        </div>
        <audio
          v-if="audioUrl"
          ref="audioEl"
          class="player"
          :src="audioUrl"
          aria-label="Áudio da reunião"
          controls
        />
        <span class="sr-only" role="status">{{
          copied ? "Transcrição copiada." : ""
        }}</span>
      </template>
      <SpeakerMenu
        v-if="speakerMenu"
        :key="speakerMenu.speaker.id"
        :meeting-id="meeting.id"
        :segments="meeting.segments"
        :speaker="speakerMenu.speaker"
        :speakers="meeting.speakers"
        :x="speakerMenu.x"
        :y="speakerMenu.y"
        @preview="stopAudio"
        @changed="onPeopleChanged"
        @close="speakerMenu = null"
      />
      <NameSuggestionsDialog
        v-if="suggesting"
        :key="meeting.id"
        :meeting-id="meeting.id"
        @changed="onPeopleChanged"
        @close="suggesting = false"
      />
      <AppDialog
        v-if="confirmingDelete"
        title="Excluir reunião?"
        @close="cancelDelete"
        ><p>
          Excluir “{{ title }}”, sua transcrição e o áudio? Esta ação não pode
          ser desfeita.
        </p>
        <div class="dialog-actions">
          <button
            type="button"
            class="btn"
            data-dialog-cancel
            @click="cancelDelete"
          >
            Cancelar</button
          ><button
            type="button"
            class="btn btn-danger"
            :disabled="isBusy('delete') || !connected"
            @click="remove"
          >
            Excluir
          </button>
        </div></AppDialog
      >
    </template>
  </section>
</template>
<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  container: detail / inline-size;
}
.head {
  padding: 28px var(--page-gutter) 18px;
}
.tasks-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px 24px;
  padding: 16px var(--page-gutter);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.tasks-heading {
  min-width: 0;
  flex: 1;
}
.tasks-heading h1 {
  font-size: var(--font-h2);
}
.tasks-heading p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: var(--font-meta);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tabs-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 var(--page-gutter);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.tasks-link {
  margin-left: auto;
}
.context-label {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  font-size: 0.75rem;
  color: var(--muted);
  margin-bottom: 14px;
}
.ready {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--audio-color);
  font-size: 0.6875rem;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
h1 {
  flex: 1;
  min-width: 0;
  margin: 0;
}
.title {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: inherit;
  font-weight: inherit;
  letter-spacing: inherit;
  text-align: left;
  overflow-wrap: anywhere;
  max-width: 100%;
}
.title svg {
  color: var(--muted);
  flex-shrink: 0;
}
.title-input {
  flex: 1;
  min-width: 0;
  font: inherit;
  font-size: var(--font-h1);
  padding: 4px 8px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  color: var(--text);
  background: var(--surface);
}
.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: var(--font-meta);
  margin-top: 12px;
}
.head-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}
.export {
  font-size: var(--font-meta);
  flex-shrink: 0;
}
.more-wrap {
  position: relative;
  margin-left: auto;
}
.menu {
  position: absolute;
  right: 0;
  top: 100%;
  z-index: 20;
  min-width: 220px;
  padding: 6px;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-popover);
}
.menu button {
  padding: 12px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  text-align: left;
  width: 100%;
  font-size: var(--font-control);
}
.menu button:hover {
  background: var(--hover);
}
.danger,
.error {
  color: var(--danger);
}
.confirm {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: var(--font-control);
}
.confirm div,
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  padding: 8px var(--page-gutter);
}
.actions .btn {
  font-size: 0.75rem;
  min-height: 36px;
  padding: 6px 4px;
}
.audio-action {
  margin-left: auto;
}
.section-caption {
  color: var(--muted);
  font-size: var(--font-meta);
}
.player {
  width: calc(100% - 2 * var(--page-gutter));
  margin: 8px var(--page-gutter) 16px;
  height: 42px;
  flex-shrink: 0;
}
.tabs {
  display: flex;
  flex-shrink: 0;
  gap: 28px;
}
.tab {
  padding: 14px 0;
  min-height: 48px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  font-size: var(--font-control);
}
.tab.on {
  color: var(--text);
  font-weight: 600;
  border-bottom-color: var(--accent);
}
.alerts {
  margin: 0;
  padding: 8px var(--page-gutter) 8px calc(var(--page-gutter) + 18px);
  color: var(--warn);
  font-size: var(--font-meta);
}
.body {
  flex: 1;
  min-height: 0;
}
.tab-panel {
  height: 100%;
  min-height: 0;
}
.state {
  padding: var(--space-8) var(--page-gutter);
  color: var(--muted);
}
.progress {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}
.progress svg {
  color: var(--accent);
}
.progress h2 {
  color: var(--text);
  margin-top: 20px;
}
.state.failed p {
  color: var(--danger);
}
.banner {
  margin: 0;
  padding: 8px var(--page-gutter);
  font-size: var(--font-meta);
}
.capture-state {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 32px;
  text-align: center;
}
.capture-status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--danger);
  font-size: var(--font-control);
}
.capture-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--danger);
}
.capture-state h2 {
  font-size: 1.5rem;
  margin-top: 24px;
}
.capture-state > p {
  max-width: 55ch;
  margin: 16px auto;
  font-size: var(--font-control);
  color: var(--muted);
}
.capture-alert {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: center;
  color: var(--warn);
  background: var(--warning-surface);
  padding: 12px;
  margin: 12px auto;
  max-width: 600px;
}
.capture-levels {
  max-width: 500px;
  margin: 24px auto;
}
.capture-levels label {
  display: grid;
  grid-template-columns: 100px 1fr 120px;
  align-items: center;
  gap: 12px;
  margin: 14px 0;
  font-size: 0.75rem;
  color: var(--muted);
}
meter {
  width: 100%;
  accent-color: var(--audio-color);
}
.skeleton-line {
  height: 24px;
  margin-bottom: 16px;
  width: 70%;
  background: var(--panel);
  border-radius: var(--radius-sm);
}
.skeleton-line.short {
  width: 40%;
}
@container detail (max-width:560px) {
  .head {
    padding: 20px;
  }
  .ready {
    display: none;
  }
  .head-bottom {
    flex-wrap: wrap;
  }
  .title {
    font-size: 1.25rem;
  }
  .tabs {
    padding: 0 20px;
  }
  .actions {
    padding: 8px 20px;
  }
  .actions .audio-action {
    margin-left: 0;
  }
  .capture-levels label {
    grid-template-columns: 1fr;
    text-align: left;
  }
}
@container detail (max-width:900px) {
  .head {
    padding: 16px 24px 12px;
  }
  .context-label {
    display: none;
  }
  .title {
    font-size: 1.375rem;
  }
  .meta {
    margin-top: 8px;
    font-size: 0.75rem;
  }
  .head-bottom {
    margin-top: 8px;
  }
  .export {
    min-height: 34px;
  }
  .tabs {
    padding: 0 24px;
  }
  .tab {
    padding: 11px 0;
    min-height: 44px;
  }
  .actions {
    padding: 4px 24px;
  }
}
@media (max-height: 600px) {
  .head {
    padding: 12px 24px;
  }
  .context-label,
  .meta,
  .head-bottom {
    display: none;
  }
  .head-bottom.expanded {
    display: flex;
  }
  .title {
    font-size: 1.25rem;
  }
}
</style>
