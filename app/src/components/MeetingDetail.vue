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
import { alertLines, exportFileName, transcriptText } from "../lib/meeting";
import AppButton from "./ui/AppButton.vue";
import AppIcon from "./ui/AppIcon.vue";
import AppDialog from "./ui/AppDialog.vue";
import SkeletonLines from "./ui/SkeletonLines.vue";
import StepProgress from "./ui/StepProgress.vue";
import MeetingRecording from "./MeetingRecording.vue";
import MeetingHeader, { type MeetingMenuKey } from "./MeetingHeader.vue";
import NameSuggestionsDialog from "./NameSuggestionsDialog.vue";
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

const { client, events, connected } = useStatus();

const meeting = ref<MeetingDetail | null>(null);
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
// Etapas reais do processamento (evento `progress`), na ordem em que o backend as percorre.
const PROCESSING_STEPS: { key: ProgressStage; label: string }[] = [
  { key: "transcrevendo", label: "Transcrição" },
  { key: "separando vozes", label: "Falantes" },
  { key: "finalizando", label: "Finalização" },
];
type MeetingTab = "transcript" | "summary" | "prints" | "tasks";
const tab = ref<MeetingTab>("transcript");
// Aba Tarefas aberta: o App recolhe a biblioteca para dar largura às duas colunas das tarefas.
const tasksActive = computed(() => tab.value === "tasks" && meeting.value?.status === "ready");
watch(tasksActive, (active) => emit("tasksView", active));
const tabMemory = inject<Map<string, string>>("meeting-tabs", new Map());
let didChooseTab = false;
// Tarefas da reunião (não descartadas), informadas pela aba depois que ela carrega.
const tasksCount = ref<number | null>(null);
const detailTabs = computed<{ id: MeetingTab; label: string; count?: number }[]>(() => {
  const tabs: { id: MeetingTab; label: string; count?: number }[] = [
    { id: "summary", label: "Resumo" },
    { id: "transcript", label: "Transcrição" },
  ];
  const prints = meeting.value?.prints.length ?? 0;
  if (prints) tabs.push({ id: "prints", label: "Prints", count: prints });
  tabs.push({ id: "tasks", label: "Tarefas", count: tasksCount.value || undefined });
  return tabs;
});
// Sem prints (ex.: o último foi apagado) a aba some: volta para a transcrição.
watch(detailTabs, (tabs) => {
  if (!tabs.some((t) => t.id === tab.value)) tab.value = "transcript";
});
const printFocus = ref<string | null>(null);
function openPrint(id: string): void {
  printFocus.value = id;
  tab.value = "prints";
  void nextTick(() => document.getElementById("tab-prints")?.focus());
}
// Troca de aba feita pela pessoa (clique ou teclado): o destaque vindo do selo deixa de valer.
function selectTab(id: MeetingTab): void {
  printFocus.value = null;
  tab.value = id;
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
  selectTab(tabs[next]!.id);
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

const header = ref<InstanceType<typeof MeetingHeader> | null>(null);
const title = computed(() => meeting.value?.title || "Reunião sem título");
const alerts = computed(() => (meeting.value ? alertLines(meeting.value) : []));

/** Salva o título vindo do cabeçalho; o erro vira banner e o cabeçalho continua editando. */
async function renameTitle(next: string): Promise<boolean> {
  const id = props.meetingId;
  const { title: _oldError, ...rest } = actionErrors.value;
  actionErrors.value = rest;
  try {
    await client.renameMeeting(id, next);
    await refreshAfterChange(id);
    return true;
  } catch (e) {
    if (id === props.meetingId) actionErrors.value = { ...actionErrors.value, title: errorText(e) };
    return false;
  }
}

function onMenu(key: MeetingMenuKey): void {
  if (key === "export") void exportMeeting();
  else if (key === "suggest") suggesting.value = true;
  else if (key === "archive") void toggleArchive();
  else confirmingDelete.value = true;
}
function cancelDelete(): void {
  confirmingDelete.value = false;
  void nextTick(() => header.value?.focusMenuButton());
}

// -- ações ----------------------------------------------------------------------------------

// O que acabou de ser copiado (o botão mostra "Copiado" por 2 s).
const copied = ref<"transcript" | "summary" | null>(null);
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

function copyText(what: "transcript" | "summary", text: () => string | null): Promise<void> {
  return run(`copy-${what}`, async () => {
    const value = text();
    if (!value) return;
    await writeText(value);
    copied.value = what;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (copied.value = null), 2000);
  });
}
const copyTranscript = () =>
  copyText("transcript", () => (meeting.value ? transcriptText(meeting.value.segments, meeting.value.speakers) : null));
const copySummary = () => copyText("summary", () => meeting.value?.summary ?? null);
// Resumindo: por clique ou pelo resumo automático (job rodando).
const summarizing = computed(() => isBusy("summarize") || meeting.value?.summary_job?.state === "running");

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

const confirmingDelete = ref(false);
const archived = computed(() => !!meeting.value?.archived_at);

function toggleArchive(): Promise<void> {
  const id = props.meetingId;
  const archive = !archived.value;
  return run("archive", async () => {
    const r = archive ? await client.archiveMeeting(id) : await client.unarchiveMeeting(id);
    if (id === props.meetingId && meeting.value) meeting.value.archived_at = r.archived_at;
    notifyMeetingsChanged();
  });
}

function remove(): Promise<void> {
  const id = props.meetingId;
  return run("delete", async () => {
    await client.deleteMeeting(id);
    notifyMeetingsChanged();
    emit("deleted", id);
    if (id !== props.meetingId) return;
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
    confirmingDelete.value = false;
    confirmingReprocess.value = false;
    speakerMenu.value = null;
    suggesting.value = false;
    tab.value = "transcript";
    printFocus.value = null;
    tasksOpenedFor.value = null;
    tasksCount.value = null;
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
  <section class="detail">
    <div v-if="loadError && !meeting" class="state">
      <p class="banner error" role="alert">
        <AppIcon name="warning" :size="18" /><span>{{ loadError }}</span>
        <button class="btn btn-sm" type="button" @click="load">Tentar de novo</button>
      </p>
    </div>
    <div v-else-if="!meeting" class="state loading">
      <SkeletonLines :lines="1" class="skel-title" />
      <SkeletonLines :lines="4" label="Carregando reunião…" />
    </div>
    <template v-else>
      <MeetingHeader
        ref="header"
        :meeting="meeting"
        :connected="connected"
        :rename="renameTitle"
        :title-invalid="!!actionErrors.title"
        :export-busy="isBusy('export')"
        :archive-busy="isBusy('archive')"
        @menu="onMenu"
        @select-speaker="onSpeakerSelect"
      />
      <p
        v-for="b in errorBanners"
        :key="b.action"
        class="banner error"
        role="alert"
      >
        <AppIcon name="warning" :size="18" />{{ b.message }}
      </p>
      <MeetingRecording v-if="meeting.status === 'recording'" :started-at="meeting.started_at" />
      <div v-else-if="meeting.status === 'processing'" class="processing">
        <div>
          <h2>Preparando sua reunião</h2>
          <p class="hint">Você já pode iniciar outra reunião.</p>
        </div>
        <!-- montado durante todo o processamento: a região viva dele anuncia cada etapa -->
        <StepProgress class="steps" :steps="PROCESSING_STEPS" :current="stage" />
      </div>
      <div v-else-if="meeting.status === 'failed'" class="state failed">
        <div class="banner failed-banner" role="alert">
          <AppIcon name="warning" :size="18" />
          <div>
            <strong>O processamento falhou</strong>
            {{ meeting.error || "Não foi possível transcrever esta reunião." }}
          </div>
          <AppButton
            v-if="!confirmingReprocess"
            size="sm"
            :busy="isBusy('reprocess')"
            :disabled="!connected"
            @click="requestReprocess"
          >
            <AppIcon name="refresh" :size="15" />Reprocessar
          </AppButton>
        </div>
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
      </div>
      <template v-else>
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
            @click="selectTab(item.id)"
            @keydown="onTabKey($event, index)"
          >
            {{ item.label }}<span v-if="item.count" class="count">{{ item.count }}</span>
          </button>
        </div>
        <div v-if="tab !== 'tasks'" class="actions">
          <template v-if="tab === 'transcript'">
            <AppButton size="sm" :disabled="isBusy('copy-transcript')" @click="copyTranscript">
              <AppIcon name="copy" :size="15" />{{ copied === "transcript" ? "Copiado" : "Copiar transcrição" }}
            </AppButton>
            <AppButton size="sm" :disabled="!connected" @click="suggesting = true">
              <AppIcon name="user-plus" :size="15" />Sugerir nomes
            </AppButton>
            <AppButton size="sm" :busy="isBusy('summarize')" :disabled="!connected" @click="onSummarize">
              <AppIcon name="spark" :size="15" />{{ isBusy("summarize") ? "Resumindo…" : "Resumir" }}
            </AppButton>
          </template>
          <template v-else-if="tab === 'summary' && meeting.summary">
            <AppButton size="sm" :disabled="summarizing || isBusy('copy-summary')" @click="copySummary">
              <AppIcon name="copy" :size="15" />{{ copied === "summary" ? "Copiado" : "Copiar" }}
            </AppButton>
            <AppButton size="sm" :disabled="summarizing || !connected" @click="onSummarize">
              <AppIcon name="refresh" :size="15" />Refazer
            </AppButton>
          </template>
          <span v-else-if="tab === 'prints'" class="section-caption">Prints de tela tirados durante a reunião.</span>
          <AppButton size="sm" :busy="isBusy('export')" :disabled="!connected" @click="exportMeeting">
            <AppIcon name="export" :size="15" />Exportar {{ exportExtension }}
          </AppButton>
          <button
            type="button"
            class="btn ghost btn-sm audio-action"
            :disabled="isBusy('audio') || !connected"
            @click="playAudio()"
          >
            <AppIcon name="play" :size="15" />Ouvir áudio
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
              @play="playAudio"
              @deleted="onPrintDeleted"
            />
          </div>
          <div
            v-show="tab === 'tasks'"
            id="panel-tasks"
            role="tabpanel"
            aria-labelledby="tab-tasks"
            class="tab-panel"
            tabindex="0"
          >
            <TasksTab
              v-if="tasksOpenedFor === meeting.id"
              :key="meeting.id"
              :meeting-id="meeting.id"
              :started-at="meeting.started_at"
              :prints="meeting.prints"
              @play="playAudio"
              @count="tasksCount = $event"
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
          copied === "transcript" ? "Transcrição copiada." : copied === "summary" ? "Resumo copiado." : ""
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
.confirm {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-width: var(--read-max);
  padding: var(--space-4) var(--space-5);
  border-radius: var(--radius-lg);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  color: var(--text);
  font-size: 14px;
}
.confirm p {
  margin: 0;
}
.confirm div,
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--page-gutter) var(--space-2);
}
.audio-action {
  margin-left: auto;
}
.section-caption {
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
}
.player {
  width: calc(100% - 2 * var(--page-gutter));
  margin: 8px var(--page-gutter) 16px;
  height: 42px;
  flex-shrink: 0;
}
/* Abas (am-tab): sublinhado em gradiente na selecionada e contador em pílula. */
.tabs {
  display: flex;
  flex-shrink: 0;
  gap: 22px;
  margin: 0 var(--page-gutter);
  border-bottom: 1px solid var(--glass-border);
  overflow-x: auto;
  scrollbar-width: none;
}
.tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  min-height: 44px;
  padding: 10px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
}
.tab:hover {
  color: var(--text);
}
.tab.on {
  color: var(--text);
}
.tab.on::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--text-accent), var(--accent));
}
.tab:focus-visible {
  outline: none;
  box-shadow: inset 0 -3px 0 var(--focus-ring);
}
.count {
  padding: 0 6px;
  border-radius: var(--radius-pill);
  background: var(--selected);
  color: var(--text-accent);
  font-size: 11px;
  line-height: 16px;
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
.processing {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-6);
  padding: var(--space-8) var(--page-gutter);
  text-align: center;
}
.processing h2 {
  margin: 0;
  font-size: 22px;
  line-height: 28px;
  font-weight: 700;
}
.hint {
  margin: var(--space-2) 0 0;
  color: var(--text-muted);
  font-size: 14px;
  font-weight: 600;
}
.steps {
  width: min(440px, 100%);
  padding: var(--space-5);
  border-radius: var(--radius-lg);
  background: var(--glass);
  border: 1px solid var(--glass-border);
  text-align: left;
}
.state {
  padding: var(--space-8) var(--page-gutter);
  color: var(--text-muted);
}
.state.loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: var(--read-max);
}
.skel-title {
  width: 55%;
}
.skel-title :deep(.skel) {
  height: 22px;
}
.state.failed {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding-top: var(--space-2);
}
.banner strong {
  display: block;
  font-size: 14px;
  font-weight: 700;
}
/* Erro de ação (am-banner--danger): ícone, mensagem e, quando houver, a ação de refazer. */
.banner {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0 var(--page-gutter) var(--space-2);
  padding: 14px var(--space-5);
  border-radius: var(--radius-lg);
  background: var(--danger-surface);
  color: var(--danger);
  font-size: 13px;
  line-height: 19px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.banner svg {
  flex-shrink: 0;
}
.banner .btn {
  margin-left: auto;
  flex-shrink: 0;
}
.state .banner {
  margin: 0;
}
/* do mais largo para o mais estreito: a regra mais estreita vence */
@container detail (max-width:900px) {
  .tabs {
    margin: 0 var(--space-6);
  }
  .actions {
    padding: var(--space-2) var(--space-6);
  }
}
@container detail (max-width:560px) {
  .tabs {
    margin: 0 var(--space-5);
  }
  .actions {
    padding: var(--space-2) var(--space-5);
  }
  .actions .audio-action {
    margin-left: 0;
  }
}
</style>
