<script setup lang="ts">
// Seção Claude: modelo e effort do `claude` CLI para dois usos (resumo/títulos/nomes e o
// levantamento de tarefas). O modelo é gravado pelo apelido (opus, sonnet…), então uma versão nova
// passa a valer sozinha; a lista de apelidos vem do backend e "Atualizar modelos" a renova.
import { computed, onMounted, ref } from "vue";
import type { ClaudeModels, Effort } from "../../api/types";
import { takeAutoRefresh } from "./claudeAutoRefresh";
import SettingField from "./SettingField.vue";
import { messageOf, type SettingsForm } from "./useSettingsForm";

const props = defineProps<{ form: SettingsForm }>();

type ModelKey = "claude_model" | "tasks_model";
type EffortKey = "claude_effort" | "tasks_effort";

const catalog = ref<ClaudeModels | null>(null);
const refreshing = ref(false);
const error = ref<string | null>(null);

const EFFORTS: { value: Effort; label: string }[] = [
  { value: "low", label: "Baixo (low)" },
  { value: "medium", label: "Médio (medium)" },
  { value: "high", label: "Alto (high)" },
  { value: "xhigh", label: "Muito alto (xhigh)" },
  { value: "max", label: "Máximo (max)" },
];

const groups: { title: string; model: ModelKey; effort: EffortKey; prefix: string }[] = [
  { title: "Resumo, títulos e nomes", model: "claude_model", effort: "claude_effort", prefix: "claude" },
  { title: "Levantamento de tarefas e criação no MCP de issues", model: "tasks_model", effort: "tasks_effort", prefix: "tasks" },
];

async function load(): Promise<void> {
  try {
    catalog.value = await props.form.client.claudeModels();
  } catch (e) {
    error.value = messageOf(e);
    return;
  }
  // Cache vazio e nunca consultado: busca sozinha, no máximo uma vez por sessão do app.
  if (catalog.value.models.length === 0 && catalog.value.updated_at === null && takeAutoRefresh()) await refresh();
}

async function refresh(): Promise<void> {
  if (refreshing.value) return;
  refreshing.value = true;
  error.value = null;
  try {
    catalog.value = await props.form.client.refreshClaudeModels();
  } catch (e) {
    error.value = messageOf(e);
  } finally {
    refreshing.value = false;
  }
}

onMounted(load);

const capitalise = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

const defaultLabel = computed(() => {
  const label = catalog.value?.default_label;
  return label ? `Padrão do Claude Code (hoje ${label})` : "Padrão do Claude Code";
});

/**
 * Apelidos da lista; o salvo sempre aparece: sem a lista (carregando ou indisponível) só o valor,
 * com a lista e fora dela, marcado.
 */
function modelOptions(saved: string | null): { value: string; text: string }[] {
  const models = catalog.value?.models ?? [];
  const out = models.map((m) => ({
    value: m.alias,
    text: `${capitalise(m.alias)} (hoje ${m.label})${m.is_new ? " · novo" : ""}`,
  }));
  if (saved !== null && !models.some((m) => m.alias === saved)) {
    out.push({ value: saved, text: catalog.value ? `${saved} (não está na lista)` : saved });
  }
  return out;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** `DD/MM HH:MM` no horário local. */
const updatedText = computed(() => {
  const iso = catalog.value?.updated_at;
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "Lista ainda não consultada";
  return `Lista atualizada em ${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
});

/** A opção vazia ("Padrão") vai como `null`; se o backend recusar, o select volta ao valor salvo. */
async function saveModel(key: ModelKey, e: Event): Promise<void> {
  const select = e.target as HTMLSelectElement;
  const ok = await props.form.save(key, select.value === "" ? null : select.value);
  if (!ok) select.value = props.form.settings.value?.[key] ?? "";
}

async function saveEffort(key: EffortKey, e: Event): Promise<void> {
  const select = e.target as HTMLSelectElement;
  const ok = await props.form.save(key, select.value === "" ? null : (select.value as Effort));
  if (!ok) select.value = props.form.settings.value?.[key] ?? "";
}
</script>

<template>
  <section v-if="form.settings.value" class="s-section">
    <p class="s-note">
      O modelo fica gravado pelo apelido (opus, sonnet…): quando sair uma versão nova, ela passa a ser usada sozinha.
    </p>

    <div v-for="g in groups" :key="g.prefix" class="s-card group" :data-group="g.prefix">
      <h3>{{ g.title }}</h3>

      <div class="s-grid">
        <SettingField label="Modelo" :for="`${g.prefix}-model`" :error="form.errors[g.model]">
          <select :id="`${g.prefix}-model`" class="s-select" @change="saveModel(g.model, $event)">
            <option value="" :selected="form.settings.value[g.model] === null">{{ defaultLabel }}</option>
            <option
              v-for="o in modelOptions(form.settings.value[g.model])"
              :key="o.value"
              :value="o.value"
              :selected="o.value === form.settings.value[g.model]"
            >
              {{ o.text }}
            </option>
          </select>
        </SettingField>

        <SettingField label="Effort" :for="`${g.prefix}-effort`" :error="form.errors[g.effort]">
          <select :id="`${g.prefix}-effort`" class="s-select" @change="saveEffort(g.effort, $event)">
            <option value="" :selected="form.settings.value[g.effort] === null">Padrão</option>
            <option
              v-for="o in EFFORTS"
              :key="o.value"
              :value="o.value"
              :selected="o.value === form.settings.value[g.effort]"
            >
              {{ o.label }}
            </option>
          </select>
        </SettingField>
      </div>
    </div>

    <div class="s-row">
      <button class="s-btn" :disabled="refreshing" data-test="claude-refresh" @click="refresh">
        {{ refreshing ? "Atualizando…" : "Atualizar modelos" }}
      </button>
      <span class="s-note" data-test="claude-updated">{{ updatedText }}</span>
    </div>
    <p v-if="error" class="s-error" role="alert" data-test="claude-error">{{ error }}</p>
  </section>
</template>
