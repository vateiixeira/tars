<script setup lang="ts">
// Aba "Prints": miniaturas dos prints de tela tirados durante a reunião, cada um com o instante
// (mm:ss desde o início), a fala daquele momento, e as ações Ampliar / Ouvir / Apagar. As imagens vêm
// da API com o token (não dá para usar `<img src>` direto): as miniaturas vêm de `usePrintThumbs` e a
// imagem inteira do visualizador vira um object URL próprio, revogado ao fechar e ao desmontar.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { MeetingPrint, Segment, Speaker } from "../api/types";
import { usePrintThumbs } from "../composables/usePrintThumbs";
import { useStatus } from "../composables/useStatus";
import { mmss } from "../lib/format";
import { segmentAt, speakerName } from "../lib/meeting";
import AppDialog from "./ui/AppDialog.vue";
import AppIcon from "./ui/AppIcon.vue";

const props = defineProps<{
  meetingId: string;
  prints: MeetingPrint[];
  segments: Segment[];
  speakers: Speaker[];
  focusId: string | null;
}>();
const emit = defineEmits<{ deleted: [id: string]; play: [start: number, end: number] }>();

const { client } = useStatus();

const { thumbs, failed } = usePrintThumbs(
  () => props.meetingId,
  () => props.prints.map((p) => p.id),
);
// Outra reunião: nada do que foi aberto vale mais (as miniaturas o composable já zera).
watch(
  () => props.meetingId,
  () => {
    closeViewer();
    confirming.value = null;
  },
);

const speakerById = computed(() => new Map(props.speakers.map((s) => [s.id, s])));
const items = computed(() =>
  props.prints.map((print) => {
    const segment = segmentAt(props.segments, print.at_s);
    return {
      print,
      time: mmss(print.at_s),
      segment,
      who: segment ? speakerName(segment.speaker_id ? speakerById.value.get(segment.speaker_id) : undefined) : "",
    };
  }),
);

// -- destaque do print vindo do selo da transcrição --------------------------------------------

const list = ref<HTMLElement | null>(null);
function revealFocused(): void {
  if (!props.focusId) return;
  void nextTick(() => {
    const el = list.value?.querySelector<HTMLElement>(`[data-print-id="${CSS.escape(props.focusId ?? "")}"]`);
    el?.scrollIntoView?.({ block: "center" });
  });
}
onMounted(revealFocused);
watch(() => props.focusId, revealFocused);

// -- visualizador ------------------------------------------------------------------------------

const viewing = ref<MeetingPrint | null>(null);
const viewUrl = ref<string | null>(null);
const viewError = ref<string | null>(null);
let viewSeq = 0;

function closeViewer(): void {
  ++viewSeq;
  if (viewUrl.value) URL.revokeObjectURL(viewUrl.value);
  viewUrl.value = null;
  viewError.value = null;
  viewing.value = null;
}

async function openViewer(print: MeetingPrint): Promise<void> {
  closeViewer();
  const seq = viewSeq;
  viewing.value = print;
  try {
    const blob = await client.printBlob(props.meetingId, print.id, "full");
    if (seq !== viewSeq) return;
    viewUrl.value = URL.createObjectURL(blob);
  } catch (e) {
    if (seq === viewSeq) viewError.value = e instanceof Error ? e.message : String(e);
  }
}

// -- apagar ------------------------------------------------------------------------------------

const confirming = ref<MeetingPrint | null>(null);
const deleting = ref(false);
const deleteError = ref<string | null>(null);

function askDelete(print: MeetingPrint): void {
  deleteError.value = null;
  confirming.value = print;
}
function cancelDelete(): void {
  if (deleting.value) return;
  confirming.value = null;
  deleteError.value = null;
}
async function confirmDelete(): Promise<void> {
  const print = confirming.value;
  if (!print || deleting.value) return;
  deleting.value = true;
  deleteError.value = null;
  try {
    await client.deletePrint(props.meetingId, print.id);
    confirming.value = null;
    if (viewing.value?.id === print.id) closeViewer();
    emit("deleted", print.id);
  } catch (e) {
    deleteError.value = e instanceof Error ? e.message : String(e);
  } finally {
    deleting.value = false;
  }
}

onBeforeUnmount(() => {
  closeViewer();
});
</script>

<template>
  <div class="prints" aria-label="Prints da reunião">
    <ul ref="list" class="list">
      <li
        v-for="it in items"
        :key="it.print.id"
        class="print"
        :class="{ focused: it.print.id === focusId }"
        :data-print-id="it.print.id"
      >
        <button
          type="button"
          class="thumb"
          :aria-label="`Ampliar print ${it.print.label}`"
          :disabled="!thumbs[it.print.id]"
          @click="openViewer(it.print)"
        >
          <img v-if="thumbs[it.print.id]" :src="thumbs[it.print.id]" :alt="`Print ${it.print.label} da reunião`" />
          <span v-else class="placeholder">{{ failed.has(it.print.id) ? "Imagem indisponível" : "Carregando…" }}</span>
        </button>
        <div class="info">
          <div class="line">
            <strong>{{ it.print.label }}</strong>
            <span class="time">{{ it.time }}</span>
            <button
              v-if="it.segment"
              type="button"
              class="icon play"
              title="Ouvir este momento"
              :aria-label="`Ouvir a fala do print ${it.print.label}`"
              @click="emit('play', it.segment.start_s, it.segment.end_s)"
            >
              <AppIcon name="play" :size="14" />
            </button>
          </div>
          <p v-if="it.segment" class="said"><span class="who">{{ it.who }}:</span> {{ it.segment.text }}</p>
          <p v-if="!it.print.sent_to_claude" class="warn">Não enviado ao Claude (limite de 20)</p>
          <div class="actions">
            <button type="button" class="btn ghost danger" @click="askDelete(it.print)">Apagar</button>
          </div>
        </div>
      </li>
    </ul>

    <AppDialog v-if="viewing" class="viewer" :title="`Print ${viewing.label} · ${mmss(viewing.at_s)}`" @close="closeViewer">
      <img v-if="viewUrl" class="full" :src="viewUrl" :alt="`Print ${viewing.label} em tamanho real`" />
      <p v-else-if="viewError" class="error" role="alert">{{ viewError }}</p>
      <p v-else class="muted" role="status">Carregando imagem…</p>
    </AppDialog>

    <AppDialog v-if="confirming" title="Apagar print?" @close="cancelDelete">
      <p>
        Apagar o print {{ confirming.label }} ({{ mmss(confirming.at_s) }}) da reunião? Esta ação não pode ser desfeita.
      </p>
      <p v-if="deleteError" class="error" role="alert">{{ deleteError }}</p>
      <div class="dialog-actions">
        <button type="button" class="btn" data-dialog-cancel @click="cancelDelete">Cancelar</button>
        <button type="button" class="btn btn-danger" :disabled="deleting" @click="confirmDelete">Apagar</button>
      </div>
    </AppDialog>
  </div>
</template>

<style scoped>
.prints { height: 100%; overflow-y: auto; padding: 16px var(--page-gutter) 32px; }
.list { list-style: none; margin: 0; padding: 0; max-width: var(--read-max); display: flex; flex-direction: column; gap: 14px; }
.print { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 16px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface-raised); }
.print.focused { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent); }
.thumb { display: grid; place-items: center; min-height: 112px; padding: 0; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--hover); overflow: hidden; cursor: zoom-in; }
.thumb:disabled { cursor: default; }
.thumb img { display: block; width: 100%; height: auto; }
.placeholder { padding: 8px; color: var(--muted); font-size: var(--font-meta); text-align: center; }
.info { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.line { display: flex; align-items: center; gap: 10px; }
.time { color: var(--muted); font-variant-numeric: tabular-nums; font-size: var(--font-meta); }
.icon { display: inline-flex; align-items: center; justify-content: center; border: 0; background: transparent; color: var(--muted); min-width: 32px; min-height: 32px; border-radius: var(--radius-sm); cursor: pointer; }
.icon:hover { color: var(--accent); background: var(--hover); }
.said { margin: 0; line-height: 1.6; overflow-wrap: anywhere; }
.who { font-weight: 600; }
.warn { margin: 0; color: var(--muted); font-size: var(--font-meta); }
.actions { margin-top: auto; display: flex; gap: 8px; }
.danger { color: var(--danger); }
.viewer :deep(.app-dialog) { width: min(1100px, 100%); }
.full { display: block; max-width: 100%; max-height: 70dvh; margin: 0 auto; }
.error { color: var(--danger); margin: 0 0 10px; }
.muted { color: var(--muted); }
.dialog-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
@media (max-width: 640px) { .print { grid-template-columns: minmax(0, 1fr); } }
</style>
