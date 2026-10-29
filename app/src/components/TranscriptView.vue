<script setup lang="ts">
// Transcrição virtualizada: só as linhas visíveis (mais uma margem) vão para o DOM. As alturas
// começam estimadas e são trocadas pelas medidas depois de renderizadas (linhas quebram em
// larguras diferentes); `padding` em cima/embaixo mantém a barra de rolagem do tamanho certo.
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import type { MeetingPrint, Segment, Speaker } from "../api/types";
import { mmss, speakerColor } from "../lib/format";
import { segmentAt, speakerName } from "../lib/meeting";

const props = defineProps<{ segments: Segment[]; speakers: Speaker[]; prints: MeetingPrint[] }>();
const emit = defineEmits<{ play: [segment: Segment]; openPrint: [id: string] }>();

const ESTIMATED_ROW = 44; // px, antes de medir
const OVERSCAN = 8; // linhas extras acima e abaixo
const FALLBACK_VIEWPORT = 600; // sem layout (testes)

const scroller = ref<HTMLElement | null>(null);
const scrollTop = ref(0);
const viewport = ref(FALLBACK_VIEWPORT);
const measured = new Map<number, number>(); // id do segmento -> altura medida
const measureRev = ref(0);
let observer: ResizeObserver | null = null;
let lastWidth = 0;

const speakerById = computed(() => new Map(props.speakers.map((s) => [s.id, s])));

/** Prints por fala: o selo vai na fala que estava rolando no instante do print. */
const printsBySegment = computed(() => {
  const map = new Map<number, MeetingPrint[]>();
  for (const p of props.prints) {
    const seg = segmentAt(props.segments, p.at_s);
    if (seg) map.set(seg.id, [...(map.get(seg.id) ?? []), p]);
  }
  return map;
});

/** `offsets[i]` = topo da linha i; o último item é a altura total. */
const offsets = computed(() => {
  measureRev.value; // dependência: recalcula depois de medir
  const out = new Float64Array(props.segments.length + 1);
  props.segments.forEach((seg, i) => {
    out[i + 1] = out[i] + (measured.get(seg.id) ?? ESTIMATED_ROW);
  });
  return out;
});

/** Primeira linha cujo fim passa de `y` (busca binária). */
function indexAt(y: number): number {
  const o = offsets.value;
  let lo = 0;
  let hi = props.segments.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (o[mid + 1] <= y) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

const firstVisible = computed(() => indexAt(scrollTop.value));
const range = computed(() => {
  const n = props.segments.length;
  const start = Math.max(0, firstVisible.value - OVERSCAN);
  const end = Math.min(n, indexAt(scrollTop.value + viewport.value) + 1 + OVERSCAN);
  return { start, end };
});

const rows = computed(() =>
  props.segments.slice(range.value.start, range.value.end).map((seg) => {
    const sp = seg.speaker_id ? speakerById.value.get(seg.speaker_id) : undefined;
    return {
      seg,
      time: mmss(seg.start_s),
      name: speakerName(sp),
      color: sp ? speakerColor(sp.color_index) : "var(--muted)",
      prints: printsBySegment.value.get(seg.id) ?? [],
    };
  }),
);

const padTop = computed(() => offsets.value[range.value.start]);
const padBottom = computed(() => offsets.value[offsets.value.length - 1] - offsets.value[range.value.end]);

function onScroll(): void {
  scrollTop.value = scroller.value?.scrollTop ?? 0;
}

/** Troca as alturas estimadas pelas reais; compensa a rolagem se mudou algo acima da tela. */
function measure(): void {
  const el = scroller.value;
  if (!el) return;
  const firstId = props.segments[firstVisible.value]?.id;
  let changed = false;
  let deltaAbove = 0;
  for (const rowEl of el.querySelectorAll<HTMLElement>(".row")) {
    const id = Number(rowEl.dataset.id);
    const h = rowEl.offsetHeight;
    if (!h) continue; // sem layout
    const before = measured.get(id) ?? ESTIMATED_ROW;
    if (Math.abs(before - h) < 0.5) continue;
    measured.set(id, h);
    changed = true;
    const index = props.segments.findIndex((s) => s.id === id);
    if (index >= 0 && index < firstVisible.value && id !== firstId) deltaAbove += h - before;
  }
  if (!changed) return;
  measureRev.value++;
  if (deltaAbove) {
    nextTick(() => {
      el.scrollTop += deltaAbove;
      onScroll();
    });
  }
}

onMounted(() => {
  const el = scroller.value;
  if (!el) return;
  viewport.value = el.clientHeight || FALLBACK_VIEWPORT;
  lastWidth = el.clientWidth;
  if (typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(() => {
      viewport.value = el.clientHeight || FALLBACK_VIEWPORT;
      if (el.clientWidth !== lastWidth) {
        // a quebra de linha mudou: as medidas antigas não valem mais
        lastWidth = el.clientWidth;
        measured.clear();
        measureRev.value++;
      }
    });
    observer.observe(el);
  }
  measure();
});
onUpdated(measure);
onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div ref="scroller" class="scroller" aria-label="Transcrição da reunião" tabindex="0" @scroll.passive="onScroll">
    <div class="rows" :style="{ paddingTop: padTop + 'px', paddingBottom: padBottom + 'px' }">
      <div v-for="r in rows" :key="r.seg.id" class="row" :data-id="r.seg.id">
        <span class="ts">{{ r.time }}</span>
        <span class="who" :style="{ color: r.color }">{{ r.name }}</span>
        <span class="text">
          {{ r.seg.text }}<em v-if="r.seg.dictated" class="dictated">(ditado)</em>
          <button v-for="p in r.prints" :key="p.id" type="button" class="print-badge" :title="`Ver print ${p.label}`" :aria-label="`Ver print ${p.label}`" @click="emit('openPrint', p.id)">📷 {{ p.label }}</button>
          <button type="button" class="play" title="Ouvir este trecho" :aria-label="`Ouvir ${r.name} a partir de ${r.time}`" @click="emit('play', r.seg)"><AppIcon name="play" :size="14" /></button>
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scroller { height:100%; overflow-y:auto; overflow-anchor:none; padding:0 var(--page-gutter); }
.rows { max-width:var(--read-max); }
.row { display:grid; grid-template-columns:58px minmax(0,1fr); gap:6px 16px; padding:18px 0; border-bottom:1px solid var(--border); }
.ts { grid-row:1 / 3; color:var(--muted); font-size:.75rem; padding-top:3px; font-variant-numeric:tabular-nums; }
.who { font-weight:600; font-size:var(--font-control); overflow-wrap:anywhere; }
.text { grid-column:2; min-width:0; overflow-wrap:anywhere; line-height:1.7; }
.dictated { margin-left:6px; color:var(--muted); font-size:var(--font-meta); }
.play { display:inline-flex; align-items:center; justify-content:center; vertical-align:middle; border:0; background:transparent; color:var(--muted); min-width:32px; min-height:32px; padding:4px; border-radius:var(--radius-sm); cursor:pointer; }
.play:hover { color:var(--text-accent); background:var(--hover); }
.print-badge { display:inline-flex; align-items:center; gap:4px; vertical-align:middle; margin-left:6px; border:1px solid var(--border); background:var(--surface-raised); color:var(--muted); font:inherit; font-size:var(--font-meta); min-height:28px; padding:2px 8px; border-radius:var(--radius-sm); cursor:pointer; }
.print-badge:hover { color:var(--text-accent); background:var(--hover); }
@media(max-width:640px) { .row { grid-template-columns:42px minmax(0,1fr); gap:6px 10px; } }
</style>
