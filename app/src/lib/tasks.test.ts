import { describe, expect, it } from "vitest";
import type { IssueOptions, TaskFields, TaskSuggestion } from "../api/types";
import {
  KIND_LABELS,
  PRIORITY_LABELS,
  REQUIRED,
  isLocked,
  issueUrl,
  missingFields,
  optionName,
  taskFields,
  tasksExportFileName,
  tasksMarkdown,
} from "./tasks";

const FIELDS: TaskFields = {
  kind: "bugfix",
  title: "Checkout: corrigir frete",
  description: "## Problema\nO frete some.",
  product_id: 1,
  assignee_id: 10,
  team_id: 20,
  stage_id: 30,
  priority: "medium",
};

const ITEM: TaskSuggestion = {
  ...FIELDS,
  project: "web-store",
  id: "t1",
  meeting_id: "m1",
  origin: "claude",
  position: 0,
  suggested: null,
  evidence: [],
  edited: false,
  discarded: false,
  create_state: "none",
  create_error: null,
  issue_number: null,
  issue_url: null,
  repeats_number: null,
  print_ids: [],
  attach_state: "none",
  attach_error: null,
};

const CREATED_ITEM: TaskSuggestion = {
  ...ITEM,
  id: "t2",
  create_state: "created",
  issue_number: 217,
  issue_url: "https://issues.example.test/issues/217",
};

const OPTIONS: IssueOptions = {
  username: "vini",
  people: [{ id: 10, name: "Miguel" }],
  teams: [{ id: 20, name: "Desenvolvimento" }],
  products: [{ id: 1, name: "e-commerce" }],
  stages: [{ id: 30, name: "To Do" }],
  fetched_at: "2026-10-20T10:00:00",
};

describe("tasks", () => {
  it.each([
    "https://issues.example.test/issues/217",
    "https://other-provider.example:8443/tickets/42",
    "https://tracker.example.test/issues/235/mobile-app/",
  ])("aceita links HTTPS de provedores diferentes: %s", (url) => {
    expect(issueUrl(url)).toBe(url);
  });

  it.each([
    null,
    "",
    "não é uma URL",
    "javascript:alert(1)",
    "http://tracker.example.test/issues/235/",
    "https://tracker.example.test@evil.example/issues/235/",
    "https://user:pass@tracker.example.test/issues/235/",
  ])("recusa um link ausente, inválido, com credenciais ou sem HTTPS: %s", (url) => {
    expect(issueUrl(url)).toBeNull();
  });

  it("rótulos de tipo e prioridade", () => {
    expect(KIND_LABELS).toEqual({ feature: "Feature", tarefa: "Tarefa", bugfix: "Bug fix" });
    expect(PRIORITY_LABELS).toEqual({ low: "Baixa", medium: "Média", high: "Alta", urgent: "Urgente" });
    expect(REQUIRED.map(([k]) => k)).toEqual(["assignee_id", "team_id", "product_id", "stage_id"]);
  });

  it("lista o que falta na ordem responsável, equipe, produto, etapa", () => {
    expect(missingFields({ ...FIELDS, assignee_id: null, stage_id: null })).toEqual(["responsável", "etapa"]);
    expect(missingFields({ ...FIELDS, team_id: null, product_id: null })).toEqual(["equipe", "produto"]);
    expect(missingFields(FIELDS)).toEqual([]);
  });

  it("missingFields conta título vazio (só espaços também) como faltando, na frente", () => {
    expect(missingFields({ ...FIELDS, title: "" })).toEqual(["título"]);
    expect(missingFields({ ...FIELDS, title: "   ", team_id: null })).toEqual(["título", "equipe"]);
  });

  it("travado em creating, created e uncertain", () => {
    for (const s of ["creating", "created", "uncertain"] as const) {
      expect(isLocked({ ...ITEM, create_state: s })).toBe(true);
    }
    for (const s of ["none", "failed"] as const) {
      expect(isLocked({ ...ITEM, create_state: s })).toBe(false);
    }
  });

  it("taskFields extrai só os campos editáveis", () => {
    expect(taskFields(CREATED_ITEM)).toEqual(FIELDS);
    expect(taskFields(CREATED_ITEM)).not.toHaveProperty("project");
  });

  it("mostra — em campo vazio e #id quando a opção não existe", () => {
    expect(optionName(undefined, 5)).toBe("#5");
    expect(optionName(OPTIONS.people, 99)).toBe("#99");
    expect(optionName(OPTIONS.people, null)).toBe("—");
    expect(optionName(undefined, null)).toBe("—");
    expect(optionName(OPTIONS.people, 10)).toBe("Miguel");
  });

  it("gera o Markdown só dos itens não descartados", () => {
    expect(tasksMarkdown([CREATED_ITEM, { ...ITEM, discarded: true }], OPTIONS)).toBe(
      "# Tarefas da reunião\n\n## 1. Checkout: corrigir frete\n\n" +
        "- **Tipo:** Bug fix\n- **Produto:** e-commerce\n- **Responsável:** Miguel\n" +
        "- **Equipe:** Desenvolvimento\n- **Prioridade:** Média\n- **Etapa:** To Do\n- **Issue:** #217 https://issues.example.test/issues/217\n\n" +
        "## Problema\nO frete some.\n",
    );
  });

  it("separa itens com linha em branco, numera só os não descartados e usa #id sem opções", () => {
    const md = tasksMarkdown(
      [{ ...ITEM, discarded: true }, { ...ITEM, id: "a", project: null }, { ...ITEM, id: "b", title: "Outra" }],
      null,
    );
    expect(md).toContain("## 1. Checkout: corrigir frete");
    expect(md).toContain("## 2. Outra");
    expect(md).not.toContain("## 3.");
    expect(md).not.toContain("**Projeto:**");
    expect(md).toContain("- **Produto:** #1");
    expect(md).toContain("O frete some.\n\n## 2. Outra");
    expect(md).not.toContain("MCP de issues");
  });

  it("sem itens", () => {
    expect(tasksMarkdown([], OPTIONS)).toBe("# Tarefas da reunião\n\nNenhuma tarefa.\n");
    expect(tasksMarkdown([{ ...ITEM, discarded: true }], OPTIONS)).toBe(
      "# Tarefas da reunião\n\nNenhuma tarefa.\n",
    );
  });

  it("nome do arquivo de exportação", () => {
    expect(tasksExportFileName("2026-10-20T14:05:00")).toBe("tarefas-20261020-1405.md");
  });
});
