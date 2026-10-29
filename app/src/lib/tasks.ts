// Funções puras das tarefas sugeridas pela reunião (criador de issues): rótulos, validação dos
// campos obrigatórios do MCP de issues e exportação em Markdown.
import type {
  IssueOption,
  IssueOptions,
  TaskFields,
  TaskKind,
  TaskPriority,
  TaskSuggestion,
} from "../api/types";

const pad = (n: number) => String(n).padStart(2, "0");

/** Links de issues de qualquer provedor, sempre HTTPS e sem credenciais. */
export function issueUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.username || url.password || url.protocol !== "https:") return null;
    return url.href;
  } catch {
    return null;
  }
}

export const KIND_LABELS: Record<TaskKind, string> = {
  feature: "Feature",
  tarefa: "Tarefa",
  bugfix: "Bug fix",
};

export const PRIORITY_LABELS: Record<TaskPriority, string> = {
  low: "Baixa",
  medium: "Média",
  high: "Alta",
  urgent: "Urgente",
};

/** Campos que o MCP de issues exige para criar a tarefa, na ordem em que são cobrados. */
export const REQUIRED: [keyof TaskFields, string][] = [
  ["assignee_id", "responsável"],
  ["team_id", "equipe"],
  ["product_id", "produto"],
  ["stage_id", "etapa"],
];

/** Nomes (pt-BR) dos campos obrigatórios que ainda estão vazios (o título em branco conta). */
export function missingFields(t: TaskFields): string[] {
  const missing = REQUIRED.filter(([key]) => t[key] === null || t[key] === undefined).map(([, label]) => label);
  return t.title.trim() === "" ? ["título", ...missing] : missing;
}

/** Itens em criação, criados ou incertos não podem mais ser editados (o backend devolve 409). */
export function isLocked(t: TaskSuggestion): boolean {
  return t.create_state === "creating" || t.create_state === "created" || t.create_state === "uncertain";
}

export function taskFields(t: TaskSuggestion): TaskFields {
  return {
    kind: t.kind,
    title: t.title,
    description: t.description,
    product_id: t.product_id,
    assignee_id: t.assignee_id,
    team_id: t.team_id,
    stage_id: t.stage_id,
    priority: t.priority,
  };
}

/** "—" sem valor; `#id` quando a lista não existe (opções não carregadas) ou não tem o id. */
export function optionName(list: IssueOption[] | undefined, id: number | null): string {
  if (id === null || id === undefined) return "—";
  return list?.find((o) => o.id === id)?.name ?? `#${id}`;
}

/** Markdown das tarefas não descartadas, numeradas em ordem. */
export function tasksMarkdown(items: TaskSuggestion[], options: IssueOptions | null): string {
  const kept = items.filter((t) => !t.discarded);
  if (kept.length === 0) return "# Tarefas da reunião\n\nNenhuma tarefa.\n";
  const blocks = kept.map((t, i) => {
    const lines = [
      `- **Tipo:** ${KIND_LABELS[t.kind] ?? t.kind}`,
      `- **Produto:** ${optionName(options?.products, t.product_id)}`,
      `- **Responsável:** ${optionName(options?.people, t.assignee_id)}`,
      `- **Equipe:** ${optionName(options?.teams, t.team_id)}`,
      `- **Prioridade:** ${PRIORITY_LABELS[t.priority] ?? t.priority}`,
      `- **Etapa:** ${optionName(options?.stages, t.stage_id)}`,
    ];
    if (t.issue_number !== null) {
      lines.push(`- **Issue:** #${t.issue_number}${t.issue_url ? ` ${t.issue_url}` : ""}`);
    }
    const description = t.description.trim();
    return (
      `## ${i + 1}. ${t.title}\n\n${lines.join("\n")}\n` + (description ? `\n${description}\n` : "")
    );
  });
  return `# Tarefas da reunião\n\n${blocks.join("\n")}`;
}

/** `tarefas-AAAAMMDD-HHMM.md` a partir do início da reunião (ISO local, sem fuso). */
export function tasksExportFileName(startedAt: string): string {
  const d = new Date(startedAt);
  return `tarefas-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.md`;
}
