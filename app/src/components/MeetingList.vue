<script setup lang="ts">
// Busca (debounce 300 ms), chips de filtro e lista de reuniões agrupada por dia. "Esta semana"
// filtra a lista já carregada; "Arquivadas" troca para as reuniões arquivadas (a busca vale para
// a lista que estiver na tela). Recarrega em `meeting_updated`, quando outro componente avisa por
// `notifyMeetingsChanged()` e a cada (re)conexão com o backend (no login o app sobe antes do
// serviço, e eventos se perdem enquanto ele reinicia).
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { MeetingListItem } from "../api/types";
import { meetingsRevision } from "../composables/useMeetingsRevision";
import { useStatus } from "../composables/useStatus";
import { groupByDay, startOfWeek } from "../lib/format";
import MeetingCard from "./MeetingCard.vue";
import FilterChips from "./ui/FilterChips.vue";

defineProps<{ selectedId: string | null }>();
const emit = defineEmits<{
  select: [meetingId: string];
  /** Quantas reuniões existem (arquivadas inclusive), a cada carga sem busca nem arquivadas. */
  count: [n: number];
}>();

const SEARCH_DEBOUNCE_MS = 300;
const { client, events, meetingState, connected } = useStatus();

type Filter = "all" | "week" | "archived";
const query = ref("");
const filter = ref<Filter>("all");
const showArchived = computed(() => filter.value === "archived");
const archivedCount = ref(0);
const items = ref<MeetingListItem[]>([]);
const error = ref<string | null>(null);
const loaded = ref(false);
const search = ref<HTMLInputElement | null>(null);
let debounce: ReturnType<typeof setTimeout> | undefined;
let requestSeq = 0; // descarta respostas de buscas antigas

const visible = computed(() => {
  if (filter.value !== "week") return items.value;
  const since = startOfWeek().getTime();
  return items.value.filter((m) => new Date(m.started_at).getTime() >= since);
});
const groups = computed(() => groupByDay(visible.value));

const chips = computed(() => [
  { key: "all", label: "Todas" },
  { key: "week", label: "Esta semana" },
  // o chip some sem arquivadas, a não ser que seja o filtro atual
  ...(archivedCount.value > 0 || showArchived.value
    ? [{ key: "archived", label: `Arquivadas · ${archivedCount.value}`, class: "archive-toggle" }]
    : []),
]);

async function load(): Promise<void> {
  const seq = ++requestSeq;
  const q = query.value.trim();
  try {
    const archived = showArchived.value;
    const [result, count] = await Promise.all([
      client.listMeetings(q || undefined, archived || undefined),
      // a contagem é só do chip: se falhar, a lista ainda aparece
      archived ? null : client.archivedCount().catch(() => null),
    ]);
    if (seq !== requestSeq) return;
    items.value = result;
    if (count) archivedCount.value = count.count;
    if (!q && !archived) emit("count", result.length + archivedCount.value);
    error.value = null;
  } catch (e) {
    if (seq !== requestSeq) return;
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (seq === requestSeq) loaded.value = true;
  }
}

function onSearch(): void {
  clearTimeout(debounce);
  debounce = setTimeout(load, SEARCH_DEBOUNCE_MS);
}

// Entrar ou sair das arquivadas troca a fonte da lista; "Todas" ↔ "Esta semana" só filtra.
watch(showArchived, () => {
  clearTimeout(debounce);
  loaded.value = false;
  items.value = [];
  void load();
});

const emptyText = computed(() => {
  const q = query.value.trim();
  if (q) return `Nada encontrado para “${q}”.`;
  if (filter.value === "week") return "Nenhuma reunião nesta semana.";
  return showArchived.value ? "Nenhuma reunião arquivada." : "Nenhuma reunião ainda.";
});

const offUpdated = events.on("meeting_updated", () => void load());
watch([meetingsRevision, meetingState], () => void load());
watch(connected, (on) => {
  if (on) void load();
});
void load();

onBeforeUnmount(() => {
  clearTimeout(debounce);
  offUpdated();
});

/** Botão Buscar da barra superior. */
function focusSearch(): void {
  search.value?.focus();
}
defineExpose({ focusSearch });
</script>

<template>
  <div class="meeting-list">
    <input
      ref="search"
      v-model="query"
      class="search"
      type="search"
      :placeholder="showArchived ? 'Buscar nas arquivadas…' : 'Buscar nas reuniões…'"
      :aria-label="showArchived ? 'Buscar nas reuniões arquivadas' : 'Buscar nas reuniões'"
      @input="onSearch"
    />
    <FilterChips v-model="filter" class="filters" :options="chips" label="Filtrar reuniões" />
    <div class="list" :aria-label="showArchived ? 'Reuniões arquivadas' : 'Reuniões'">
      <p v-if="!loaded" class="empty" role="status">Carregando reuniões…</p>
      <p v-if="error" class="empty error">{{ error }}</p>
      <p v-else-if="loaded && visible.length === 0" class="empty">{{ emptyText }}</p>
      <template v-for="g in groups" :key="g.label">
        <div class="day">{{ g.label }}</div>
        <MeetingCard
          v-for="m in g.items"
          :key="m.id"
          :meeting="m"
          :selected="m.id === selectedId"
          @click="emit('select', m.id)"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.meeting-list { display:flex; flex-direction:column; gap:var(--space-3); min-height:0; flex:1; }
.search { height:var(--control-md); flex-shrink:0; padding:0 var(--space-4); border:1px solid var(--border-control); border-radius:var(--radius-pill); background:var(--glass-strong); color:var(--text); font:inherit; font-size:14px; font-weight:600; }
.search::placeholder { color:var(--text-muted); }
.filters { flex-shrink:0; }
.list { flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:6px; margin:0 calc(-1 * var(--space-2)); padding:0 var(--space-2) var(--space-2); }
.day { padding:var(--space-3) var(--space-2) 2px; font-size:11px; line-height:16px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:var(--text-muted); }
.day:first-child { padding-top:0; }
.empty { margin:var(--space-4) var(--space-2); color:var(--text-muted); font-size:13px; font-weight:600; }
.empty.error { color:var(--danger); }
</style>
