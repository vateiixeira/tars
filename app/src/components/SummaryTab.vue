<script setup lang="ts">
// Aba "Resumo": estados sem resumo / carregando / erro / resumo em Markdown. Só apresenta: quem
// chama o Claude por clique é o `MeetingDetail`, que recebe `summarize` (Copiar e Refazer ficam
// nas ações da aba, no detalhe). O resumo também pode vir automático ao fim do processamento
// (`job`: rodando, ou falhou sem deixar resumo).
import { computed } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import MarkdownIt from "markdown-it";
import type { JobState } from "../api/types";

const props = defineProps<{ summary: string | null; loading: boolean; error: string | null; job: JobState | null }>();
const emit = defineEmits<{ summarize: [] }>();

// O resumo vem do Claude, então é tratado como não confiável: HTML bruto vira texto (`html: false`),
// links e imagens ficam desligados (nada a clicar nem a carregar de fora) e `v-html` só recebe a
// saída do markdown-it.
const md = new MarkdownIt({ html: false, linkify: false, typographer: false });
md.disable(["link", "autolink", "image"]);

// Resumindo: por clique (`loading`) ou pelo resumo automático (job rodando).
const busy = computed(() => props.loading || props.job?.state === "running");
const autoError = computed(() =>
  !props.summary && props.job?.state === "error" ? `O resumo automático falhou: ${props.job.error ?? "erro desconhecido"}` : null,
);

const rendered = computed(() => (props.summary ? md.render(props.summary) : ""));
</script>

<template>
  <div class="summary">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="autoError" class="error" role="alert">{{ autoError }}</p>

    <div v-if="busy" class="summary-busy" role="status"><p class="muted">O Claude está resumindo…</p><button type="button" class="btn" disabled aria-busy="true">Resumindo…</button></div>

    <div v-if="summary" class="md" v-html="rendered"></div>
    <template v-else-if="!busy">
      <p class="muted">Nenhum resumo ainda.</p>
      <button type="button" class="btn pri" @click="emit('summarize')"><AppIcon name="file" :size="16" /> Resumir</button>
    </template>
  </div>
</template>

<style scoped>
.summary {
  height: 100%;
  overflow-y: auto;
  padding: 16px var(--page-gutter) 32px;
}
.muted {
  color: var(--muted);
}
.error {
  color: var(--danger);
  margin: 0 0 10px;
}
.md {
  line-height: 1.7;
  max-width:var(--read-max);
  overflow-wrap: anywhere;
}
.md :deep(h1),
.md :deep(h2),
.md :deep(h3) {
  margin: 1em 0 0.4em;
  font-size: 1.05rem;
}
.md :deep(h1) {
  font-size: 1.2rem;
}
.md :deep(p),
.md :deep(ul),
.md :deep(ol) {
  margin: 0 0 0.7em;
}
.md :deep(ul),
.md :deep(ol) {
  padding-left: 1.4em;
}
.md :deep(code) {
  background: var(--surface);
  border-radius: 4px;
  padding: 0 4px;
}
.md :deep(pre) {
  background: var(--surface);
  border-radius: 6px;
  padding: 8px 10px;
  overflow-x: auto;
}
.md :deep(pre code) {
  padding: 0;
  background: transparent;
}
.md :deep(blockquote) {
  margin: 0 0 0.7em;
  padding:12px 16px;
  border:1px solid var(--border);
  border-radius:var(--radius-md);
  color: var(--muted);
}
</style>
