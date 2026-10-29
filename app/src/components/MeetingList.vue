<script setup lang="ts">
// Busca (debounce 300 ms) + lista de reuniões agrupada por dia. O link do rodapé alterna para
// as reuniões arquivadas (a busca vale para a lista que estiver na tela). Recarrega em `meeting_updated`,
// quando outro componente avisa por `notifyMeetingsChanged()` e a cada (re)conexão com o backend
// (no login o app sobe antes do serviço, e eventos se perdem enquanto ele reinicia).
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { MeetingListItem } from "../api/types";
import { meetingsRevision } from "../composables/useMeetingsRevision";
import { useStatus } from "../composables/useStatus";
import { groupByDay, timeOfDay } from "../lib/format";
import { formatDuration, peopleLabel } from "../lib/meeting";

defineProps<{ selectedId: string | null }>();
const emit = defineEmits<{ select: [meetingId: string] }>();

const SEARCH_DEBOUNCE_MS = 300;
const { client, events, meetingState, connected } = useStatus();

const query = ref("");
const showArchived = ref(false);
const archivedCount = ref(0);
const items = ref<MeetingListItem[]>([]);
const error = ref<string | null>(null);
const loaded = ref(false);
let debounce: ReturnType<typeof setTimeout> | undefined;
let requestSeq = 0; // descarta respostas de buscas antigas

const groups = computed(() => groupByDay(items.value));

async function load(): Promise<void> {
  const seq = ++requestSeq;
  const q = query.value.trim();
  try {
    const archived = showArchived.value;
    const [result, count] = await Promise.all([
      client.listMeetings(q || undefined, archived || undefined),
      // a contagem é só do link do rodapé: se falhar, a lista ainda aparece
      archived ? null : client.archivedCount().catch(() => null),
    ]);
    if (seq !== requestSeq) return;
    items.value = result;
    if (count) archivedCount.value = count.count;
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

function toggleArchived(): void {
  clearTimeout(debounce);
  showArchived.value = !showArchived.value;
  loaded.value = false;
  items.value = [];
  void load();
}

const emptyText = computed(() => {
  const q = query.value.trim();
  if (q) return `Nada encontrado para “${q}”.`;
  return showArchived.value ? "Nenhuma reunião arquivada." : "Nenhuma reunião ainda.";
});

function meta(m: MeetingListItem): string {
  const time = timeOfDay(m.started_at);
  if (m.status === "recording") return `${time} · gravando…`;
  if (m.status === "processing") return `${time} · processando…`;
  if (m.status === "failed") return `${time} · falhou`;
  return `${time} · ${formatDuration(m.duration_s)} · ${peopleLabel(m.speakers_count)}`;
}

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
</script>

<template>
  <div class="meeting-list">
    <input
      v-model="query"
      class="search"
      type="search"
      :placeholder="showArchived ? 'Buscar nas arquivadas…' : 'Buscar nas reuniões…'"
      :aria-label="showArchived ? 'Buscar nas reuniões arquivadas' : 'Buscar nas reuniões'"
      @input="onSearch"
    />
    <button v-if="showArchived" type="button" class="archive-toggle back" @click="toggleArchived">
      ← Voltar às reuniões
    </button>
    <div class="list" :aria-label="showArchived ? 'Reuniões arquivadas' : 'Reuniões'">
      <p v-if="!loaded" class="empty" role="status">Carregando reuniões…</p>
      <p v-if="error" class="empty error">{{ error }}</p>
      <p v-else-if="loaded && items.length === 0" class="empty">{{ emptyText }}</p>
      <template v-for="g in groups" :key="g.label">
        <div class="day">{{ g.label }}</div>
        <button
          v-for="m in g.items"
          :key="m.id"
          type="button"
          class="item"
          :class="{ on: m.id === selectedId }" :aria-current="m.id === selectedId ? 'page' : undefined"
          @click="emit('select', m.id)"
        >
          <b>{{ m.title || "Reunião sem título" }}</b>
          <span>{{ meta(m) }}</span>
        </button>
      </template>
    </div>
    <button
      v-if="!showArchived && archivedCount > 0"
      type="button"
      class="archive-toggle"
      @click="toggleArchived"
    >
      Arquivadas ({{ archivedCount }})
    </button>
  </div>
</template>

<style scoped>
.meeting-list { display:flex; flex-direction:column; min-height:0; flex:1; }
.search { min-height:40px; padding:10px 8px; border:0; border-bottom:1px solid var(--border-control); border-radius:0; background:transparent; color:var(--text); font:inherit; font-size:var(--font-meta); }
.search::placeholder { color:var(--muted); }
.list { flex:1; min-height:0; overflow-y:auto; padding-top:8px; }
.day { padding:18px 10px 6px; font-size:.75rem; font-weight:550; color:var(--muted); }
.item { display:block; width:100%; padding:12px 10px; margin-bottom:4px; border:1px solid transparent; border-radius:var(--radius-md); background:transparent; text-align:left; cursor:pointer; }
.item:hover { background:var(--hover); }
.item.on { background:var(--selected); }
.item b { display:block; font-size:var(--font-meta); font-weight:600; overflow-wrap:anywhere; line-height:1.5; }
.item span { display:block; font-size:.6875rem; color:var(--muted); margin-top:6px; }
.empty { margin:16px 10px; color:var(--muted); font-size:var(--font-meta); }
.empty.error { color:var(--danger); }
.archive-toggle { align-self:flex-start; margin:6px 2px; padding:6px 8px; border:0; border-radius:var(--radius-md); background:transparent; color:var(--muted); font:inherit; font-size:.75rem; cursor:pointer; }
.archive-toggle:hover { background:var(--hover); color:var(--text); }
.archive-toggle.back { margin-top:8px; }
</style>
