<script setup lang="ts">
// Aba "Resumo": estados sem resumo / carregando / erro / resumo em Markdown. Só apresenta: quem
// chama o Claude por clique é o `MeetingDetail`, que recebe `summarize` (Copiar e Refazer ficam
// nas ações da aba, no detalhe). O resumo também pode vir automático ao fim do processamento
// (`job`: rodando, ou falhou sem deixar resumo). O backend não informa etapas do resumo, então a espera
// é a órbita com texto (e não o StepProgress, que mostraria um avanço inventado).
import { computed } from "vue";
import AppBanner from "./ui/AppBanner.vue";
import AppIcon from "./ui/AppIcon.vue";
import AppLoader from "./ui/AppLoader.vue";
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
    <AppBanner v-if="error" class="error" role="alert">{{ error }}</AppBanner>
    <AppBanner v-if="autoError" class="error" role="alert">{{ autoError }}</AppBanner>

    <div v-if="busy" class="busy" role="status">
      <AppLoader size="sm" />
      <span>Gerando resumo…</span>
    </div>

    <div v-if="summary" class="md" :class="{ dim: busy }" v-html="rendered"></div>
    <div v-else-if="!busy" class="empty">
      <p>Nenhum resumo ainda.</p>
      <button type="button" class="btn pri" @click="emit('summarize')"><AppIcon name="spark" :size="16" /> Resumir</button>
    </div>
  </div>
</template>

<style scoped>
.summary {
  height: 100%;
  overflow-y: auto;
  padding: var(--space-4) var(--page-gutter) var(--space-8);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.error {
  max-width: var(--read-max);
}
.busy {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: var(--text-accent);
  font-size: 14px;
  font-weight: 700;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-12) var(--space-6);
  text-align: center;
}
.empty p {
  margin: 0;
  color: var(--text-muted);
}
/* Prosa (am-prose): 16/26, marcadores em accent-glow, `code` em pílula. */
.md {
  max-width: var(--read-max);
  font-size: 16px;
  line-height: 26px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.md.dim {
  opacity: 0.55;
}
.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  margin: 18px 0 6px;
  font-size: 16px;
  line-height: 22px;
  font-weight: 700;
}
.md :deep(h1) {
  font-size: 20px;
  line-height: 26px;
}
.md :deep(h2) {
  font-size: 18px;
  line-height: 24px;
}
.md > :deep(:first-child) {
  margin-top: 0;
}
.md :deep(p) {
  margin: 0 0 var(--space-3);
}
.md :deep(ul),
.md :deep(ol) {
  margin: 0 0 var(--space-3);
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.md :deep(li::marker) {
  color: var(--accent-glow);
}
.md :deep(strong) {
  font-weight: 700;
}
.md :deep(code) {
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 1px 7px;
  border-radius: 8px;
  background: var(--glass-strong);
}
.md :deep(pre) {
  margin: 0 0 var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--glass-strong);
  overflow-x: auto;
}
.md :deep(pre code) {
  padding: 0;
  background: transparent;
}
.md :deep(blockquote) {
  margin: 0 0 var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  color: var(--text-muted);
}
</style>
