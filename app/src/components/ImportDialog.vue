<script setup lang="ts">
// Tela de confirmação da importação de um arquivo (transcrição do Read AI, vídeo ou áudio). Pede
// a prévia ao backend (`inspectImport`) e mostra o arquivo, o título e o início da reunião (data e
// hora, sugeridos e editáveis). Na transcrição pergunta "Quem é você?" entre as pessoas do arquivo;
// em vídeo/áudio explica o "Sou eu", porque as pessoas só aparecem depois da transcrição.
// Erros do backend (arquivo fora do formato, sem áudio, fila ocupada...) aparecem na própria tela.
import { computed, onMounted, ref } from "vue";
import type { ImportPreview } from "../api/types";
import { notifyMeetingsChanged } from "../composables/useMeetingsRevision";
import { useStatus } from "../composables/useStatus";
import AppButton from "./ui/AppButton.vue";
import AppDialog from "./ui/AppDialog.vue";
import AppIcon from "./ui/AppIcon.vue";
import AppLoader from "./ui/AppLoader.vue";

const props = defineProps<{ path: string }>();
const emit = defineEmits<{ close: []; imported: [meetingId: string] }>();

const { client, connected } = useStatus();

const VIDEO_EXTENSIONS = new Set(["mp4", "mkv", "mov", "webm"]);

const preview = ref<ImportPreview | null>(null);
const loadError = ref<string | null>(null);
const title = ref("");
const date = ref("");
const time = ref("");
// Índice da pessoa escolhida em "Quem é você?"; `people.length` = "Nenhuma delas", -1 = nenhuma marcada.
const choice = ref(-1);
const busy = ref(false);
const error = ref<string | null>(null);

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

const fileName = computed(() => props.path.split("/").pop() || props.path);
const isTranscript = computed(() => preview.value?.kind === "transcript");
const people = computed(() => preview.value?.people ?? []);

function durationText(seconds: number): string | null {
  if (!(seconds > 0)) return null;
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return "menos de 1 min";
  if (minutes < 60) return `${minutes} min`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)} h ${String(rest).padStart(2, "0")} min` : `${minutes / 60} h`;
}

const fileMeta = computed(() => {
  const p = preview.value;
  if (!p) return "";
  const parts: string[] = [];
  if (p.kind === "transcript") {
    if (p.people) parts.push(p.people.length === 1 ? "1 pessoa" : `${p.people.length} pessoas`);
  } else {
    const ext = fileName.value.split(".").pop()?.toLowerCase() ?? "";
    parts.push(VIDEO_EXTENSIONS.has(ext) ? "Vídeo" : "Áudio");
  }
  const duration = durationText(p.duration_s);
  if (duration) parts.push(duration);
  return parts.join(" · ");
});

/** "dd/mm/aaaa" -> "aaaa-mm-dd"; `null` se não for uma data válida. */
function parseDate(text: string): string | null {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(text.trim());
  if (!m) return null;
  const [day, month, year] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const d = new Date(year, month - 1, day);
  if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return null;
  return `${m[3]}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** "h:mm" -> "hh:mm"; `null` se não for um horário válido. */
function parseTime(text: string): string | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(text.trim());
  if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return null;
  return `${m[1].padStart(2, "0")}:${m[2]}`;
}

const isoDate = computed(() => parseDate(date.value));
const isoTime = computed(() => parseTime(time.value));
const dateInvalid = computed(() => !!date.value.trim() && !isoDate.value);
const timeInvalid = computed(() => !!time.value.trim() && !isoTime.value);
const canSubmit = computed(
  () => !!preview.value && !!title.value.trim() && !!isoDate.value && !!isoTime.value && connected.value,
);

onMounted(async () => {
  try {
    const p = await client.inspectImport(props.path);
    preview.value = p;
    title.value = p.title;
    date.value = p.date ? p.date.split("-").reverse().join("/") : "";
    time.value = p.time ?? "";
    choice.value = p.my_name_match ? (p.people ?? []).indexOf(p.my_name_match) : -1;
  } catch (e) {
    loadError.value = errorText(e);
  }
});

async function submit(): Promise<void> {
  if (busy.value || !canSubmit.value) return;
  busy.value = true;
  error.value = null;
  try {
    const { meeting_id } = await client.importFile({
      path: props.path,
      title: title.value.trim(),
      started_at: `${isoDate.value}T${isoTime.value}`,
      me: isTranscript.value ? (people.value[choice.value] ?? null) : null,
    });
    notifyMeetingsChanged();
    emit("imported", meeting_id);
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <AppDialog
    :title="preview ? (isTranscript ? 'Transcrição do Read AI' : 'Vídeo ou áudio') : 'Importar arquivo'"
    @close="emit('close')"
  >
    <p v-if="!preview && !loadError" class="loading" role="status">
      <AppLoader size="sm" />Lendo o arquivo…
    </p>
    <template v-else-if="!preview">
      <p class="error" role="alert">{{ loadError }}</p>
      <div class="dialog-actions">
        <AppButton data-dialog-cancel @click="emit('close')">Fechar</AppButton>
      </div>
    </template>
    <form v-else class="import-form" novalidate @submit.prevent="submit">
      <div class="file">
        <span class="file-icon"><AppIcon :name="isTranscript ? 'file' : 'wave'" :size="18" /></span>
        <div class="file-text">
          <strong :title="path">{{ fileName }}</strong>
          <span v-if="fileMeta">{{ fileMeta }}</span>
        </div>
      </div>

      <div class="field">
        <label for="import-title">Título</label>
        <input id="import-title" v-model="title" class="input" maxlength="200" :aria-invalid="!title.trim()" />
      </div>

      <fieldset class="field">
        <legend>Início da reunião</legend>
        <div class="when">
          <input
            id="import-date"
            v-model="date"
            class="input"
            inputmode="numeric"
            placeholder="dd/mm/aaaa"
            aria-label="Data"
            :aria-invalid="dateInvalid || !date.trim()"
          />
          <input
            id="import-time"
            v-model="time"
            class="input time"
            inputmode="numeric"
            placeholder="hh:mm"
            aria-label="Hora"
            :aria-invalid="timeInvalid || !time.trim()"
          />
        </div>
        <p class="hint">
          {{
            preview.time === null
              ? "Não deu para estimar o horário: informe o início da reunião."
              : "Sugerido a partir do arquivo. Ajuste se precisar."
          }}
        </p>
      </fieldset>

      <fieldset v-if="isTranscript" class="field">
        <legend>Quem é você?</legend>
        <label v-for="(name, i) in people" :key="name" class="choice" :class="{ on: choice === i }">
          <input v-model="choice" type="radio" name="import-me" :value="i" />{{ name }}
        </label>
        <label class="choice" :class="{ on: choice === people.length }">
          <input v-model="choice" type="radio" name="import-me" :value="people.length" />Nenhuma delas
        </label>
      </fieldset>
      <p v-else class="note">
        As pessoas só aparecem depois da transcrição. Para marcar a sua voz, clique na etiqueta da pessoa e escolha “Sou eu”.
      </p>

      <p class="hint">
        {{
          isTranscript
            ? "Em seguida o TARS gera o resumo e as tarefas. Sem áudio: sem player e sem aprender vozes."
            : "Transcreve e separa falantes como numa reunião gravada (usa a GPU, alguns minutos)."
        }}
      </p>

      <p v-if="error" class="error" role="alert">{{ error }}</p>

      <div class="dialog-actions">
        <AppButton data-dialog-cancel @click="emit('close')">Cancelar</AppButton>
        <AppButton variant="primary" type="submit" :busy="busy" :disabled="!canSubmit">
          {{ isTranscript ? "Importar" : "Importar e transcrever" }}
        </AppButton>
      </div>
    </form>
  </AppDialog>
</template>

<style scoped>
.import-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.loading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  color: var(--text-muted);
}
.file {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: var(--glass);
}
.file-icon {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  background: var(--selected);
  color: var(--text-accent);
}
.file-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: 13px;
  line-height: 18px;
  color: var(--text-muted);
}
.file-text strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  color: var(--text);
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.field > label,
.field > legend {
  padding: 0;
  margin-bottom: var(--space-2);
  font-size: 13px;
  line-height: 18px;
  font-weight: 700;
}
/* fieldset: a legenda não entra no gap do flex */
fieldset.field > legend + * {
  margin-top: 0;
}
.input {
  width: 100%;
  min-width: 0;
  min-height: var(--control-md);
  padding: 0 var(--space-4);
  border: 1px solid var(--border-control);
  border-radius: var(--radius-pill);
  background-color: var(--glass-strong);
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  transition:
    border-color var(--duration-fast),
    box-shadow var(--duration-fast);
}
.input::placeholder {
  color: var(--text-muted);
}
.input:focus {
  outline: none;
  border-color: var(--focus-ring);
  box-shadow:
    0 0 0 1px var(--focus-ring),
    0 0 0 4px var(--selected);
}
.input[aria-invalid="true"] {
  border-color: var(--danger);
}
.when {
  display: flex;
  gap: var(--space-2);
}
.when .time {
  width: 104px;
  flex-shrink: 0;
}
.choice {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-md);
  padding: 0 var(--space-4);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background var(--duration-fast),
    border-color var(--duration-fast);
}
.choice:hover {
  background: var(--selected);
}
.choice.on {
  border-color: var(--selected-border);
  background: var(--selected);
  color: var(--text-accent);
}
.choice input {
  margin: 0;
}
.choice:has(input:focus-visible) {
  box-shadow: 0 0 0 2px var(--focus-ring);
}
.note {
  margin: 0;
  padding: var(--space-3);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  background: var(--glass);
  font-size: 13px;
  line-height: 19px;
}
.hint {
  margin: 0;
  font-size: 13px;
  line-height: 18px;
  color: var(--text-muted);
}
.error {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--danger);
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
@media (prefers-reduced-motion: reduce) {
  .input,
  .choice {
    transition: none;
  }
}
</style>
