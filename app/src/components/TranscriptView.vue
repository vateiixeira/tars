<script setup lang="ts">
// Transcrição virtualizada: só as linhas visíveis (mais uma margem) vão para o DOM. As alturas
// começam estimadas e são trocadas pelas medidas depois de renderizadas (linhas quebram em
// larguras diferentes); `padding` em cima/embaixo mantém a barra de rolagem do tamanho certo.
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import type { MeetingPrint, Segment, Speaker } from "../api/types";
import { mmss, speakerColor } from "../lib/format";
import { segmentAt, speakerName } from "../lib/meeting";

const props = defineProps<{
  segments: Segment[];
  speakers: Speaker[];
  prints: MeetingPrint[];
  /** Segundo da reunião que está tocando agora; `null` = nada tocando. */
  playingAt?: number | null;
}>();
const emit = defineEmits<{ play: [segment: Segment]; openPrint: [id: string] }>();

const ESTIMATED_ROW = 84; // px (balão + espaço entre balões), antes de medir
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

/**
 * Índice da fala tocando: a última que começou até `playingAt` (busca binária, as falas vêm em
 * ordem de início). Depois do fim da última fala, nenhuma. Só muda de valor quando a fala troca,
 * então o `timeupdate` do player não re-renderiza a lista.
 */
const playingIndex = computed(() => {
  const at = props.playingAt;
  const segs = props.segments;
  if (at == null || !Number.isFinite(at) || !segs.length) return -1;
  let lo = 0;
  let hi = segs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].start_s <= at) lo = mid + 1;
    else hi = mid;
  }
  const i = lo - 1;
  if (i < 0) return -1;
  return i === segs.length - 1 && at >= segs[i].end_s ? -1 : i;
});

const rows = computed(() =>
  props.segments.slice(range.value.start, range.value.end).map((seg, k) => {
    const index = range.value.start + k;
    const sp = seg.speaker_id ? speakerById.value.get(seg.speaker_id) : undefined;
    const prev = index > 0 ? props.segments[index - 1] : undefined;
    return {
      seg,
      time: mmss(seg.start_s),
      name: speakerName(sp),
      color: sp ? speakerColor(sp.color_index) : "var(--text-muted)",
      me: !!sp?.is_me,
      // falas seguidas do mesmo falante omitem o cabeçalho
      header: !prev || prev.speaker_id !== seg.speaker_id,
      playing: index === playingIndex.value,
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
      <div v-for="r in rows" :key="r.seg.id" class="row" :class="{ me: r.me }" :data-id="r.seg.id">
        <div class="am-bubble" :class="{ 'am-bubble--me': r.me, 'is-playing': r.playing, 'no-header': !r.header }">
          <div v-if="r.header" class="am-bubble__meta">
            <span class="am-bubble__who" :style="{ color: r.color }">{{ r.name }}</span>
            <span class="ts">· {{ r.time }}</span>
          </div>
          <button type="button" class="play am-bubble__play" title="Ouvir este trecho" :aria-label="`Ouvir ${r.name} a partir de ${r.time}`" @click="emit('play', r.seg)"><AppIcon name="play" :size="13" /></button>
          <span class="text">{{ r.seg.text }}<em v-if="r.seg.dictated" class="dictated">(ditado)</em></span>
          <div v-if="r.prints.length" class="prints">
            <button v-for="p in r.prints" :key="p.id" type="button" class="print-badge" :title="`Ver print ${p.label}`" :aria-label="`Ver print ${p.label}`" @click="emit('openPrint', p.id)"><AppIcon name="file" :size="13" />{{ p.label }}</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scroller { height:100%; overflow-y:auto; overflow-anchor:none; padding:0 var(--page-gutter); }
/* O espaço entre balões é padding da linha medida, para a soma dos offsets incluí-lo. */
.row { display:flex; justify-content:flex-start; padding-bottom:var(--space-3); }
.row.me { justify-content:flex-end; }
.am-bubble { position:relative; box-sizing:border-box; max-width:76%; min-width:0; padding:var(--space-3) calc(var(--space-4) + var(--space-5)) var(--space-3) var(--space-4); border-radius:var(--radius-lg) var(--radius-lg) var(--radius-lg) var(--space-2); background:var(--glass); border:1px solid var(--glass-border); font-size:16px; line-height:1.5; font-weight:500; transition:box-shadow var(--duration-fast) var(--ease-out); }
.am-bubble--me { border-radius:var(--radius-lg) var(--radius-lg) var(--space-2) var(--radius-lg); background:linear-gradient(150deg, var(--selected), var(--glass)); border-color:var(--selected-border); }
.am-bubble.is-playing { box-shadow:0 0 0 2px var(--accent-glow); }
.am-bubble__meta { display:flex; align-items:center; gap:var(--space-1); margin-bottom:var(--space-1); font-size:12px; line-height:16px; font-weight:700; color:var(--text-muted); }
.am-bubble__who { font-weight:700; overflow-wrap:anywhere; }
.ts { font-variant-numeric:tabular-nums; }
.text { display:block; min-width:0; overflow-wrap:anywhere; }
.dictated { margin-left:var(--space-2); padding:1px var(--space-2); border-radius:var(--radius-pill); border:1px solid var(--glass-border); color:var(--text-muted); font-size:11px; font-style:normal; font-weight:600; vertical-align:middle; }
.am-bubble__play { position:absolute; top:var(--space-2); right:var(--space-2); width:28px; height:28px; border-radius:50%; border:0; display:grid; place-items:center; background:transparent; color:var(--text-muted); cursor:pointer; }
.am-bubble__play:hover { background:var(--selected); color:var(--text); }
.prints { display:flex; flex-wrap:wrap; gap:var(--space-2); margin-top:var(--space-2); }
.print-badge { display:inline-flex; align-items:center; gap:var(--space-1); border:1px solid var(--glass-border); background:var(--glass); color:var(--text-muted); font:inherit; font-size:12px; font-weight:700; min-height:28px; padding:2px var(--space-3); border-radius:var(--radius-pill); cursor:pointer; }
.print-badge:hover { color:var(--text-accent); background:var(--selected); }
@media (prefers-reduced-motion:reduce) { .am-bubble { transition:none; } }
@media (max-width:640px) { .am-bubble { max-width:92%; } }
</style>
