import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setStatusStore } from "../composables/useStatus";
import type { IssueOptions, MeetingPrint, TasksPayload, TaskSuggestion } from "../api/types";
import { ApiError } from "../api/client";
import { installStore, reconnect } from "../test/meetingFakes";

const writeText = vi.fn(async (_t: string) => {});
const save = vi.fn(async (_o?: unknown): Promise<string | null> => "/tmp/tarefas.md");
const writeTextFile = vi.fn(async (_p: string, _t: string) => {});
const openUrl = vi.fn(async (_u: string) => {});
vi.mock("@tauri-apps/plugin-clipboard-manager", () => ({ writeText: (t: string) => writeText(t) }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ save: (o?: unknown) => save(o) }));
vi.mock("@tauri-apps/plugin-fs", () => ({ writeTextFile: (p: string, t: string) => writeTextFile(p, t) }));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: (u: string) => openUrl(u) }));

import TasksTab from "./TasksTab.vue";

const OPTIONS: IssueOptions = {
  username: "vi",
  people: [
    { id: 1, name: "Vini" },
    { id: 2, name: "Miguel" },
  ],
  teams: [
    { id: 10, name: "Backend" },
    { id: 11, name: "Frontend" },
  ],
  products: [{ id: 20, name: "Ecommerce" }],
  stages: [
    { id: 30, name: "To Do" },
    { id: 31, name: "Doing" },
  ],
  fetched_at: "2026-10-20T10:00:00",
};

const task = (extra: Partial<TaskSuggestion> = {}): TaskSuggestion => ({
  id: "t1",
  meeting_id: "m1",
  origin: "claude",
  position: 0,
  kind: "feature",
  title: "Emitir NFSe em lote",
  description: "## Problema\n\nHoje é um por um.",
  project: "ecommerce",
  product_id: 20,
  assignee_id: 1,
  team_id: 10,
  stage_id: 30,
  priority: "medium",
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
  ...extra,
});

const payload = (items: TaskSuggestion[], extra: Partial<TasksPayload> = {}): TasksPayload => ({
  enabled: true,
  scan: { state: "done", error: null, updated_at: "2026-10-20T10:00:00" },
  items,
  ...extra,
});

beforeEach(() => vi.clearAllMocks());
afterEach(() => setStatusStore(null));

async function mountTab(initial: TasksPayload, overrides: Record<string, unknown> = {}, prints: MeetingPrint[] = []) {
  const listTasks = vi.fn(async (_id: string) => initial);
  const issueOptions = vi.fn(async (_refresh?: boolean) => OPTIONS);
  const ctx = installStore({ listTasks, issueOptions, ...overrides });
  const w = mount(TasksTab, {
    props: { meetingId: "m1", startedAt: "2026-10-19T14:00:00", prints },
    attachTo: document.body,
  });
  await flushPromises();
  return { w, listTasks, issueOptions, ...ctx };
}

const btn = (w: ReturnType<typeof mount>, label: string) =>
  w.findAll("button").find((b) => b.text().includes(label))!;
const field = (w: ReturnType<typeof mount>, label: string, id = "t1") =>
  w.get(`[data-task-id="${id}"] [aria-label="${label}"]`);
const row = (w: ReturnType<typeof mount>, id: string) => w.get(`[data-row-id="${id}"]`);
const openId = (w: ReturnType<typeof mount>) => w.get(".detail [data-task-id]").attributes("data-task-id");

describe("TasksTab", () => {
  it("antes do primeiro levantamento mostra a caixa de contexto em destaque e gera com o texto", async () => {
    const scanTasks = vi.fn(async () => ({ state: "running" as const }));
    const { w } = await mountTab(payload([], { scan: null }), { scanTasks });
    expect(w.text()).toContain("Quer orientar as sugestões de tarefas?");
    expect(w.find(".scan-context").exists()).toBe(false);
    expect(w.text()).not.toContain("Revise os prints e clique em Levantar tarefas.");
    expect(w.text()).not.toContain("Refazer sugestões");
    await w.get("textarea").setValue("  Foque no checkout.  ");
    await btn(w, "Gerar tarefas").trigger("click");
    await flushPromises();
    expect(scanTasks).toHaveBeenCalledWith("m1", "Foque no checkout.");
  });

  it("'Agora não' esconde a caixa e deixa o contexto recolhido, preservando o texto", async () => {
    const { w } = await mountTab(payload([], { scan: null }));
    await w.get("textarea").setValue("Foque no checkout.");
    await btn(w, "Agora não").trigger("click");
    expect(w.text()).not.toContain("Quer orientar as sugestões de tarefas?");
    expect(w.get(".scan-context summary").text()).toBe("Orientar as sugestões (preenchido)");
    expect((w.get(".scan-context textarea").element as HTMLTextAreaElement).value).toBe("Foque no checkout.");
    expect(w.text()).toContain("Revise os prints e clique em Levantar tarefas.");
  });

  it("'Agora não' sem texto deixa o resumo como opcional", async () => {
    const { w } = await mountTab(payload([], { scan: null }));
    await btn(w, "Agora não").trigger("click");
    expect(w.get(".scan-context summary").text()).toBe("Orientar as sugestões (opcional)");
  });

  it("depois do levantamento a caixa não aparece e o contexto fica recolhido", async () => {
    const { w } = await mountTab(payload([task()]));
    expect(w.text()).not.toContain("Quer orientar as sugestões de tarefas?");
    expect(w.get(".scan-context summary").text()).toBe("Orientar as sugestões (opcional)");
    expect(w.get(".scan-context").text()).toContain(
      "O texto será usado junto com a transcrição e os prints ao levantar as tarefas. Tarefas editadas, descartadas ou já enviadas são preservadas.",
    );
  });

  it("com o fluxo desligado a caixa não aparece", async () => {
    const { w } = await mountTab(payload([], { enabled: false, scan: null }));
    expect(w.text()).not.toContain("Quer orientar as sugestões de tarefas?");
  });

  it("com o levantamento rodando a caixa não aparece", async () => {
    const { w } = await mountTab(payload([], { scan: { state: "running", error: null, updated_at: "x" } }));
    expect(w.text()).not.toContain("Quer orientar as sugestões de tarefas?");
  });

  it("deixa o botão de levantamento desabilitado até a primeira carga terminar", async () => {
    let finish!: (p: TasksPayload) => void;
    const listTasks = vi.fn(() => new Promise<TasksPayload>((resolve) => { finish = resolve; }));
    installStore({ listTasks, issueOptions: vi.fn(async () => OPTIONS) });
    const w = mount(TasksTab, {
      props: { meetingId: "m1", startedAt: "2026-10-19T14:00:00", prints: [] },
      attachTo: document.body,
    });
    await flushPromises();
    expect(w.findAll(".toolbar button")[0].attributes("disabled")).toBeDefined();
    finish(payload([task()]));
    await flushPromises();
    expect(btn(w, "Refazer sugestões").attributes("disabled")).toBeUndefined();
  });

  it("repassa os prints da reunião ao cartão da tarefa", async () => {
    const printBlob = vi.fn(async () => new Blob(["x"]));
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
    const { w } = await mountTab(payload([task({ print_ids: ["p1"] })]), { printBlob }, [
      { id: "p1", label: "P1", at_s: 65, source: "native", created_at: "2026-10-19T14:01:05", sent_to_claude: true },
    ]);
    expect(w.get(".task-prints").text()).toContain("P1");
    expect(w.find('[aria-label="Tirar P1 desta tarefa"]').exists()).toBe(true);
    expect(printBlob).toHaveBeenCalledWith("m1", "p1", "thumb");
  });

  it("depois de um levantamento o botão vira Refazer sugestões", async () => {
    const { w } = await mountTab(payload([task()]));
    expect(btn(w, "Refazer sugestões")).toBeDefined();
    expect(w.findAll("button").some((b) => b.text().includes("Levantar tarefas"))).toBe(false);
  });

  it("mostra 'Levantando tarefas…' com o levantamento rodando", async () => {
    const { w } = await mountTab(payload([], { scan: { state: "running", error: null, updated_at: "x" } }));
    expect(w.text()).toContain("Levantando tarefas…");
  });

  it("cancela o levantamento em andamento e mantém tarefas e contexto", async () => {
    let finish!: (value: unknown) => void;
    const cancelTasksScan = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
    const { w, listTasks, emit } = await mountTab(payload([task()]), { cancelTasksScan });
    const context = w.get(".scan-context textarea");
    await context.setValue("Foque no checkout.");
    listTasks.mockResolvedValue(payload([task()], { scan: { state: "running", error: null, updated_at: "x" } }));
    emit({ type: "meeting_job", meeting_id: "m1", kind: "tasks", state: "running" });
    await flushPromises();
    expect(context.attributes("disabled")).toBeDefined();
    await btn(w, "Cancelar levantamento").trigger("click");
    expect(cancelTasksScan).toHaveBeenCalledWith("m1");
    expect(btn(w, "Cancelando…").attributes("disabled")).toBeDefined();
    expect(w.text()).toContain("Cancelando levantamento…");
    await btn(w, "Cancelando…").trigger("click");
    expect(cancelTasksScan).toHaveBeenCalledTimes(1);
    const scan = { state: "cancelled" as const, error: null, updated_at: "y" };
    listTasks.mockResolvedValue(payload([task()], { scan }));
    finish({ scan });
    await flushPromises();
    expect(w.text()).toContain("Levantamento cancelado.");
    expect(w.text()).toContain(task().title);
    expect(w.find(".scan-box").exists()).toBe(false);
    expect(btn(w, "Refazer sugestões").attributes("disabled")).toBeUndefined();
    expect(context.attributes("disabled")).toBeUndefined();
    expect((context.element as HTMLTextAreaElement).value).toBe("Foque no checkout.");
  });

  it("mantém o levantamento e permite tentar cancelar novamente após falha", async () => {
    const cancelTasksScan = vi.fn().mockRejectedValue(new Error("Não foi possível cancelar"));
    const { w } = await mountTab(payload([], {
      scan: { state: "running", error: null, updated_at: "x" },
    }), { cancelTasksScan });
    await btn(w, "Cancelar levantamento").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Não foi possível cancelar");
    expect(btn(w, "Cancelar levantamento").attributes("disabled")).toBeUndefined();
    expect(w.text()).toContain("Levantando tarefas…");
  });

  it("mostra 'Nenhuma tarefa identificada nesta reunião' com scan done e sem itens visíveis", async () => {
    const { w } = await mountTab(payload([]));
    expect(w.text()).toContain("Nenhuma tarefa identificada nesta reunião");
  });

  it("erro do levantamento com 'Tentar de novo' chama scanTasks", async () => {
    const scanTasks = vi.fn(async () => ({ state: "running" as const }));
    const { w } = await mountTab(
      payload([], { scan: { state: "error", error: "O Claude não está logado", updated_at: "x" } }),
      { scanTasks },
    );
    expect(w.text()).toContain("O Claude não está logado");
    await btn(w, "Tentar de novo").trigger("click");
    await flushPromises();
    expect(scanTasks).toHaveBeenCalledWith("m1");
  });

  it("fluxo desligado e sem itens: 'O criador de issues está desligado. Ative em Configurações → Criador de issues.'", async () => {
    const { w } = await mountTab(payload([], { enabled: false, scan: null }));
    expect(w.text()).toContain("O criador de issues está desligado. Ative em Configurações → Criador de issues.");
    expect(w.text()).not.toContain("Nenhuma tarefa identificada");
  });

  it("'Refazer sugestões' chama scanTasks e fica desabilitado enquanto roda", async () => {
    const scanTasks = vi.fn(async () => ({ state: "running" as const }));
    const { w, listTasks } = await mountTab(payload([task()]), { scanTasks });
    expect(btn(w, "Refazer sugestões").attributes("disabled")).toBeUndefined();
    listTasks.mockResolvedValue(payload([task()], { scan: { state: "running", error: null, updated_at: "x" } }));
    await btn(w, "Refazer sugestões").trigger("click");
    await flushPromises();
    expect(scanTasks).toHaveBeenCalledWith("m1");
    expect(btn(w, "Refazer sugestões").attributes("disabled")).toBeDefined();
    expect(w.text()).toContain("Levantando tarefas…");
  });

  it("editar título salva com updateTask no change", async () => {
    const updateTask = vi.fn(async (_id: string, _c: unknown) => task());
    const { w } = await mountTab(payload([task()]), { updateTask });
    await field(w, "Título").setValue("Emitir NFSe por lote");
    await flushPromises();
    expect(updateTask).toHaveBeenCalledWith("t1", { title: "Emitir NFSe por lote" });
  });

  it("refaz com o contexto informado e preserva o texto se o pedido falhar", async () => {
    const scanTasks = vi.fn().mockRejectedValueOnce(new Error("Serviço indisponível"))
      .mockResolvedValueOnce({ state: "running" });
    const { w, listTasks } = await mountTab(payload([task()]), { scanTasks });
    const context = w.get(".scan-context textarea");
    const contextId = context.attributes("id");
    expect(w.get(`label[for="${contextId}"]`).text()).toContain("orientar as sugestões");
    await context.setValue("  Foque no checkout.\nAgrupe pagamentos.  ");
    await btn(w, "Refazer sugestões").trigger("click");
    await flushPromises();
    expect(scanTasks).toHaveBeenCalledWith("m1", "Foque no checkout.\nAgrupe pagamentos.");
    expect(w.text()).toContain("Serviço indisponível");
    expect((context.element as HTMLTextAreaElement).value).toBe("  Foque no checkout.\nAgrupe pagamentos.  ");
    listTasks.mockResolvedValue(payload([task()], { scan: { state: "running", error: null, updated_at: "x" } }));
    await btn(w, "Refazer sugestões").trigger("click");
    await flushPromises();
    expect(scanTasks).toHaveBeenCalledTimes(2);
    expect(context.attributes("disabled")).toBeDefined();
  });

  it("seletor de responsável lista as pessoas e marca a sugestão do Claude com '(sugestão)'", async () => {
    const updateTask = vi.fn(async (_id: string, _c: unknown) => task());
    const t = task({
      assignee_id: null,
      suggested: { product_id: null, assignee_id: 2, team_id: null, assignee_hint: "Miguel", team_hint: null },
    });
    const { w } = await mountTab(payload([t]), { updateTask });
    const options = field(w, "Responsável").findAll("option").map((o) => o.text());
    expect(options).toEqual(["—", "Vini", "Miguel (sugestão)"]);
    await field(w, "Responsável").setValue("2");
    await flushPromises();
    expect(updateTask).toHaveBeenCalledWith("t1", { assignee_id: 2 });
    await field(w, "Responsável").setValue("");
    await flushPromises();
    expect(updateTask).toHaveBeenLastCalledWith("t1", { assignee_id: null });
  });

  it.each([
    ["Responsável", "assignee_id", "2", "Miguel"],
    ["Equipe", "team_id", "11", "Frontend"],
  ] as const)("%s trocado à mão perde o '(último usado)' antes de recarregar", async (label, key, value, name) => {
    const updateTask = vi.fn(() => new Promise<never>(() => {})); // o servidor ainda não respondeu
    const t = task({ suggested: { fill: { [key]: "last" } } });
    const { w } = await mountTab(payload([t]), { updateTask });
    await field(w, label).setValue(value);
    await flushPromises();
    const select = field(w, label).element as HTMLSelectElement;
    expect(select.selectedOptions[0].text).toBe(name);
  });

  it("dica sem id: 'Sugestão do Claude: Miguel — não encontrado no MCP de issues'", async () => {
    const t = task({
      assignee_id: null,
      team_id: null,
      suggested: { product_id: null, assignee_id: null, team_id: null, assignee_hint: "Miguel", team_hint: "Fiscal" },
    });
    const { w } = await mountTab(payload([t]));
    expect(w.text()).toContain("Sugestão do Claude: Miguel — não encontrado no MCP de issues");
    expect(w.text()).toContain("Sugestão do Claude: Fiscal — não encontrado no MCP de issues");
  });

  it("'Adicionar tarefa' chama addTask e abre o item novo à direita, em edição", async () => {
    const added = task({ id: "t2", origin: "manual", title: "", position: 1 });
    const addTask = vi.fn(async (_id: string, _f: unknown) => added);
    const { w, listTasks } = await mountTab(payload([task()]), { addTask });
    expect(openId(w)).toBe("t1");
    listTasks.mockResolvedValue(payload([task(), added]));
    await btn(w, "Adicionar tarefa").trigger("click");
    await flushPromises();
    expect(addTask).toHaveBeenCalledWith("m1", {});
    expect(w.findAll(".task")).toHaveLength(1);
    expect(openId(w)).toBe("t2");
    expect(document.activeElement).toBe(field(w, "Título", "t2").element);
  });

  it("keeps uncertain and in-flight creations out of the reviewed success state", async () => {
    const { w } = await mountTab(payload([task({create_state:"uncertain"}), task({id:"t2",create_state:"creating"})]));
    expect(w.text()).toContain("Verificar (1)");
    expect(w.text()).toContain("Em criação (1)");
    expect(w.text()).not.toContain("Tudo revisado");
    expect(w.findAll("button").some(button => button.text() === "Criar issue")).toBe(false);
  });

  it("item com falha (failed) fica em Pendentes com o create_error e pode ser criado de novo", async () => {
    const createTasks = vi.fn(async (_id: string, _items: unknown) => ({ accepted: ["t1"], rejected: [] }));
    const t = task({ create_state: "failed", create_error: "Equipe inválida no MCP de issues" });
    const { w } = await mountTab(payload([t]), { createTasks });
    expect(w.text()).toContain("A revisar (1)");
    expect(row(w, "t1").text()).toContain("falhou");
    expect(w.get('[data-task-id="t1"]').text()).toContain("Equipe inválida no MCP de issues");
    expect(btn(w, "Criar issue").attributes("disabled")).toBeUndefined();
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(createTasks).toHaveBeenCalledTimes(1);
  });

  it("Criar issue fica desabilitado com campo faltando e o 'Falta: equipe' some ao preencher", async () => {
    const createTasks = vi.fn(async (_id: string, _items: unknown) => ({ accepted: [], rejected: [] }));
    const updateTask = vi.fn(async (_id: string, _c: unknown) => task());
    const { w } = await mountTab(payload([task({ team_id: null })]), { createTasks, updateTask });
    expect(w.get('[data-task-id="t1"]').text()).toContain("Falta: equipe");
    expect(btn(w, "Criar issue").attributes("disabled")).toBeDefined();
    await btn(w, "Criar issue").trigger("click");
    expect(createTasks).not.toHaveBeenCalled();
    await field(w, "Equipe").setValue("11");
    await flushPromises();
    expect(updateTask).toHaveBeenCalledWith("t1", { team_id: 11 });
    expect(w.text()).not.toContain("Falta:");
    expect(btn(w, "Criar issue").attributes("disabled")).toBeUndefined();
  });

  it("Criar issue envia só o item aberto, com os campos da tela, e abre o próximo pendente", async () => {
    const createTasks = vi.fn(async (_id: string, _items: unknown) => ({ accepted: ["t1"], rejected: [] }));
    const items = [
      task(),
      task({ id: "t2", title: "Segunda", position: 1 }),
      task({ id: "t3", title: "Terceira", position: 2 }),
    ];
    const { w, listTasks } = await mountTab(payload(items), { createTasks, updateTask: vi.fn(async () => task()) });
    // edição ainda não salva no servidor: o pedido leva o que está na tela
    await field(w, "Título").setValue("Título novo");
    listTasks.mockResolvedValue(payload([task({ create_state: "creating" }), items[1], items[2]]));
    const before = listTasks.mock.calls.length;
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(createTasks).toHaveBeenCalledTimes(1);
    const [meetingId, sent] = createTasks.mock.calls[0] as [string, Record<string, unknown>[]];
    expect(meetingId).toBe("m1");
    expect(sent).toEqual([
      {
        id: "t1",
        kind: "feature",
        title: "Título novo",
        description: "## Problema\n\nHoje é um por um.",
        product_id: 20,
        assignee_id: 1,
        team_id: 10,
        stage_id: 30,
        priority: "medium",
      },
    ]);
    expect(listTasks.mock.calls.length).toBeGreaterThan(before);
    expect(openId(w)).toBe("t2");
    expect(w.text()).toContain("A revisar (2)");
    expect(row(w, "t1").text()).toContain("criando…");
  });

  it("Criar no último pendente abre o anterior", async () => {
    const createTasks = vi.fn(async (_id: string, _items: unknown) => ({ accepted: ["t3"], rejected: [] }));
    const items = [task(), task({ id: "t2", position: 1 }), task({ id: "t3", position: 2 })];
    const { w, listTasks } = await mountTab(payload(items), { createTasks });
    await row(w, "t3").trigger("click");
    listTasks.mockResolvedValue(payload([items[0], items[1], task({ id: "t3", position: 2, create_state: "creating" })]));
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(openId(w)).toBe("t2");
  });

  it("várias criações podem ficar em voo ao mesmo tempo (ocupado por item)", async () => {
    const createTasks = vi.fn((_id: string, _items: unknown) => new Promise<never>(() => {}));
    const items = [task(), task({ id: "t2", position: 1 })];
    const { w } = await mountTab(payload(items), { createTasks });
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(openId(w)).toBe("t2");
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(createTasks).toHaveBeenCalledTimes(2);
    expect((createTasks.mock.calls[1][1] as { id: string }[])[0].id).toBe("t2");
  });

  it("item recusado na criação mostra o motivo e continua em Pendentes", async () => {
    const createTasks = vi.fn(async (_id: string, _items: unknown) => ({
      accepted: [],
      rejected: [{ id: "t1", reason: "Já foi criada por outra janela" }],
    }));
    const items = [task(), task({ id: "t2", position: 1 })];
    const { w } = await mountTab(payload(items), { createTasks });
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("A revisar (2)");
    expect(openId(w)).toBe("t1");
    expect(w.get('[data-task-id="t1"]').text()).toContain("Já foi criada por outra janela");
  });

  it("item criado mostra '#217', link que abre no navegador e campos desabilitados", async () => {
    const t = task({
      create_state: "created",
      issue_number: 217,
      issue_url: "https://issues.example.test/issues/217",
    });
    const { w } = await mountTab(payload([t]));
    const link = w.get('[data-task-id="t1"] a');
    expect(link.text()).toBe("#217");
    await link.trigger("click");
    expect(openUrl).toHaveBeenCalledWith("https://issues.example.test/issues/217");
    for (const label of ["Título", "Descrição (Markdown)", "Responsável", "Equipe", "Produto", "Etapa", "Prioridade", "Tipo"]) {
      expect(field(w, label).attributes("disabled"), label).toBeDefined();
    }
    expect(row(w, "t1").text()).toContain("#217");
    expect(w.text()).toContain("Criadas (1)");
    expect(w.find(".actions").exists()).toBe(false);
  });

  it("não abre links com protocolo inválido", async () => {
    const t = task({ create_state: "created", issue_number: 9, issue_url: "javascript:alert(1)" });
    const { w } = await mountTab(payload([t]));
    expect(w.find('[data-task-id="t1"] a').exists()).toBe(false);
    expect(openUrl).not.toHaveBeenCalled();
  });

  it("abre o link real do dashboard devolvido pelo MCP de issues", async () => {
    const url = "https://tracker.example.test/issues/235/mobile-app-app-code/";
    const { w } = await mountTab(payload([task({ create_state: "created", issue_number: 235, issue_url: url })]));
    const link = w.get('[data-task-id="t1"] a');
    expect(link.attributes("href")).toBe(url);
    await link.trigger("click");
    await flushPromises();
    expect(openUrl).toHaveBeenCalledWith(url);
  });

  it("exibe a falha ao abrir o navegador e permite tentar novamente", async () => {
    const url = "https://tracker.example.test/issues/235/mobile-app-app-code/";
    openUrl.mockRejectedValueOnce(new Error("Falha no portal do desktop"));
    const { w } = await mountTab(payload([task({ create_state: "created", issue_number: 235, issue_url: url })]));
    const link = w.get('[data-task-id="t1"] a');
    await link.trigger("click");
    await flushPromises();
    expect(w.get('[role="alert"]').text()).toContain("Não foi possível abrir o navegador: Falha no portal do desktop");
    await link.trigger("click");
    await flushPromises();
    expect(w.find('[role="alert"]').exists()).toBe(false);
  });

  it("não apresenta link clicável quando só o número da issue está disponível", async () => {
    const { w } = await mountTab(payload([task({ create_state: "created", issue_number: 235, issue_url: null })]));
    expect(w.get('[data-task-id="t1"]').text()).toContain("#235");
    expect(w.find('[data-task-id="t1"] a').exists()).toBe(false);
  });

  it("item incerto mostra 'incerto — confira no MCP de issues' e os botões de resolver", async () => {
    const resolveTask = vi.fn(async (_id: string, _b: unknown) => task());
    const { w } = await mountTab(payload([task({ create_state: "uncertain" })]), { resolveTask });
    expect(w.text()).toContain("incerto — confira no MCP de issues");
    await btn(w, "Não foi criada, tentar de novo").trigger("click");
    await flushPromises();
    expect(resolveTask).toHaveBeenCalledWith("t1", { action: "retry" });

    const informar = btn(w, "Informar número");
    expect(informar.attributes("disabled")).toBeDefined();
    await w.get('[aria-label="Número da issue no MCP de issues"]').setValue("217");
    await informar.trigger("click");
    await flushPromises();
    expect(resolveTask).toHaveBeenLastCalledWith("t1", { action: "number", number: 217 });
  });

  it("'parece repetir #217' aparece quando repeats_number existe", async () => {
    const { w } = await mountTab(payload([task({ repeats_number: 217 })]));
    expect(w.text()).toContain("parece repetir #217");
  });

  it("▶ do trecho emite play(start_s, end_s)", async () => {
    const t = task({ evidence: [{ start_s: 61, end_s: 80, speaker: "Miguel", text: "<b>Eu pego</b> isso" }] });
    const { w } = await mountTab(payload([t]));
    expect(w.get(".evidence").text()).toContain("01:01");
    expect(w.get(".evidence").text()).toContain("Miguel");
    // texto do Claude/da conversa é texto puro
    expect(w.get(".evidence").text()).toContain("<b>Eu pego</b> isso");
    expect(w.find(".evidence b").exists()).toBe(false);
    await w.get(".evidence .play").trigger("click");
    expect(w.emitted("play")).toEqual([[61, 80]]);
  });

  it("Copiar escreve tasksMarkdown no clipboard; Exportar .md grava o arquivo escolhido", async () => {
    const items = [task(), task({ id: "t2", title: "Descartada", discarded: true, position: 1 })];
    const { w } = await mountTab(payload(items));
    await btn(w, "Copiar").trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledTimes(1);
    const md = writeText.mock.calls[0][0];
    expect(md).toContain("## 1. Emitir NFSe em lote");
    expect(md).toContain("- **Responsável:** Vini");
    expect(md).not.toContain("Descartada");
    expect(btn(w, "Copiado")).toBeDefined();

    await btn(w, "Exportar .md").trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ defaultPath: "tarefas-20261019-1400.md" }));
    expect(writeTextFile).toHaveBeenCalledWith("/tmp/tarefas.md", md);
  });

  it("recarrega em tasks_changed e meeting_job(tasks) da própria reunião e ao reconectar", async () => {
    const { listTasks, emit, events } = await mountTab(payload([task()]));
    expect(listTasks).toHaveBeenCalledTimes(1);
    emit({ type: "tasks_changed", meeting_id: "outra" });
    emit({ type: "meeting_job", meeting_id: "outra", kind: "tasks", state: "done" });
    emit({ type: "meeting_job", meeting_id: "m1", kind: "summary", state: "done" });
    await flushPromises();
    expect(listTasks).toHaveBeenCalledTimes(1);
    emit({ type: "tasks_changed", meeting_id: "m1" });
    await flushPromises();
    expect(listTasks).toHaveBeenCalledTimes(2);
    emit({ type: "meeting_job", meeting_id: "m1", kind: "tasks", state: "done" });
    await flushPromises();
    expect(listTasks).toHaveBeenCalledTimes(3);
    await reconnect(events, emit);
    await flushPromises();
    expect(listTasks).toHaveBeenCalledTimes(4);
  });

  it("erro das opções do MCP de issues aparece como aviso com 'Tentar de novo' (issueOptions(true))", async () => {
    const issueOptions = vi.fn(async (refresh?: boolean) => {
      if (!refresh) throw new Error("MCP de issues sem login");
      return OPTIONS;
    });
    const { w } = await mountTab(payload([task()]), { issueOptions });
    expect(w.text()).toContain("MCP de issues sem login");
    await btn(w, "Tentar de novo").trigger("click");
    await flushPromises();
    expect(issueOptions).toHaveBeenLastCalledWith(true);
    expect(w.text()).not.toContain("MCP de issues sem login");
    expect(field(w, "Responsável").findAll("option").map((o) => o.text())).toContain("Miguel");
  });
  it("item criado com create_error mostra o aviso ao lado de '#217'", async () => {
    const t = task({
      create_state: "created",
      issue_number: 217,
      issue_url: "https://issues.example.test/issues/217",
      create_error: "Confira no MCP de issues: o conteúdo criado pode diferir do aprovado",
    });
    const { w } = await mountTab(payload([t]));
    const card = w.get('[data-task-id="t1"]');
    expect(card.text()).toContain("#217");
    const warn = card.get(".msg.warn");
    expect(warn.text()).toBe("Confira no MCP de issues: o conteúdo criado pode diferir do aprovado");
  });

  it("item criado sem create_error não mostra aviso", async () => {
    const t = task({ create_state: "created", issue_number: 217, issue_url: "https://issues.example.test/issues/217" });
    const { w } = await mountTab(payload([t]));
    expect(w.find('[data-task-id="t1"] .msg').exists()).toBe(false);
  });

  it("item incerto mostra o motivo (create_error) dentro da caixa de resolver", async () => {
    const t = task({ create_state: "uncertain", create_error: "O serviço reiniciou durante a criação — confira no MCP de issues" });
    const { w } = await mountTab(payload([t]));
    const box = w.get('[data-task-id="t1"] .resolve');
    expect(box.text()).toContain("O serviço reiniciou durante a criação — confira no MCP de issues");
    expect(box.text()).toContain("Não deu para saber se a tarefa foi criada");
  });

  it("um carregamento em voo não desfaz uma edição feita depois dele", async () => {
    const updateTask = vi.fn(async (_id: string, _c: unknown) => task());
    const { w, listTasks, emit } = await mountTab(payload([task()]), { updateTask });
    let finish!: (p: TasksPayload) => void;
    listTasks.mockImplementationOnce(() => new Promise<TasksPayload>((r) => (finish = r)));
    emit({ type: "tasks_changed", meeting_id: "m1" }); // carga em voo (eco do PATCH anterior)
    await field(w, "Responsável").setValue("2");
    await flushPromises();
    finish(payload([task({ assignee_id: 1 })])); // resposta velha chega depois
    await flushPromises();
    expect((field(w, "Responsável").element as HTMLSelectElement).value).toBe("2");
  });

  it("criação aceita limpa o motivo antigo de recusa do cartão", async () => {
    const createTasks = vi
      .fn()
      .mockResolvedValueOnce({ accepted: [], rejected: [{ id: "t1", reason: "Equipe inválida no MCP de issues" }] })
      .mockResolvedValueOnce({ accepted: ["t1"], rejected: [] });
    const { w } = await mountTab(payload([task()]), { createTasks });
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Equipe inválida no MCP de issues");
    await btn(w, "Criar issue").trigger("click");
    await flushPromises();
    expect(createTasks).toHaveBeenCalledTimes(2);
    expect(w.text()).not.toContain("Equipe inválida no MCP de issues");
  });

  it("título vazio vira 'Falta: título' e o Criar fica desabilitado", async () => {
    const createTasks = vi.fn(async (_id: string, _items: unknown) => ({ accepted: ["t1"], rejected: [] }));
    const { w } = await mountTab(payload([task({ title: "  " })]), { createTasks });
    expect(w.get('[data-task-id="t1"] .msg.bad').text()).toBe("Falta: título");
    expect(btn(w, "Criar issue").attributes("disabled")).toBeDefined();
    expect(createTasks).not.toHaveBeenCalled();
  });

  describe("lista e item aberto", () => {
    const created = (extra: Partial<TaskSuggestion> = {}) =>
      task({ create_state: "created", issue_number: 217, issue_url: "https://issues.example.test/issues/217", ...extra });

    it("abre o primeiro pendente", async () => {
      const { w } = await mountTab(
        payload([created({ id: "c1" }), task({ id: "t2", position: 1 }), task({ id: "t3", position: 2 })]),
      );
      expect(openId(w)).toBe("t2");
      expect(row(w, "t2").classes()).toContain("open");
      expect(w.findAll(".task")).toHaveLength(1);
    });

    it("sem pendentes, abre a primeira concluída; sem itens, nada à direita", async () => {
      const first = await mountTab(payload([created({ id: "c1" }), task({ id: "t2", discarded: true })]));
      expect(openId(first.w)).toBe("c1");
      const empty = await mountTab(payload([]));
      expect(empty.w.find(".detail").exists()).toBe(false);
      expect(empty.w.find(".panes").exists()).toBe(false);
    });

    it("renderiza as listas Pendentes e Concluídas com contagens e marcas", async () => {
      const items = [
        task({ id: "p1", title: "Primeira", kind: "bugfix" }),
        task({ id: "p2", title: "", position: 1, create_state: "failed", create_error: "boom" }),
        created({ id: "c1", position: 2 }),
        task({ id: "c2", position: 3, create_state: "creating" }),
        task({ id: "c3", position: 4, create_state: "uncertain" }),
        task({ id: "c4", position: 5, title: "Riscada", discarded: true }),
      ];
      const { w } = await mountTab(payload(items));
      expect(w.text()).toContain("A revisar (2)");
      expect(w.text()).toContain("Em criação (1)");
      expect(w.text()).toContain("Verificar (1)");
      expect(w.text()).toContain("Criadas (1)");
      expect(w.text()).toContain("Descartadas (1)");
      expect(w.text()).not.toContain("Tudo revisado");
      expect(row(w, "p1").text()).toContain("Bug fix");
      expect(row(w, "p1").text()).toContain("Primeira");
      expect(row(w, "p2").text()).toContain("(sem título)");
      expect(row(w, "p2").text()).toContain("falhou");
      expect(row(w, "c1").text()).toContain("#217");
      expect(row(w, "c2").text()).toContain("criando…");
      expect(row(w, "c3").text()).toContain("conferir no MCP de issues");
      expect(row(w, "c4").get(".rtitle").classes()).toContain("struck");
    });

    it("clicar numa linha abre o item à direita (inclusive concluído)", async () => {
      const items = [task(), task({ id: "t2", title: "Segunda", position: 1 }), created({ id: "c1", position: 2 })];
      const { w } = await mountTab(payload(items));
      await row(w, "t2").trigger("click");
      expect(openId(w)).toBe("t2");
      expect(row(w, "t2").classes()).toContain("open");
      await row(w, "c1").trigger("click");
      expect(openId(w)).toBe("c1");
      expect(w.find(".actions").exists()).toBe(false);
    });

    it("← → andam só entre os pendentes e desabilitam nas pontas", async () => {
      const items = [
        task(),
        created({ id: "c1", position: 1 }),
        task({ id: "t2", position: 2 }),
        task({ id: "t3", position: 3 }),
      ];
      const { w } = await mountTab(payload(items));
      const prev = () => w.get('[aria-label="Tarefa anterior"]');
      const next = () => w.get('[aria-label="Próxima tarefa"]');
      expect(prev().attributes("disabled")).toBeDefined();
      await next().trigger("click");
      expect(openId(w)).toBe("t2");
      expect(prev().attributes("disabled")).toBeUndefined();
      await next().trigger("click");
      expect(openId(w)).toBe("t3");
      expect(next().attributes("disabled")).toBeDefined();
      await prev().trigger("click");
      await prev().trigger("click");
      expect(openId(w)).toBe("t1");
      expect(prev().attributes("disabled")).toBeDefined();
    });

    it("Descartar abre o próximo pendente; o descartado vai para Concluídas com Desfazer", async () => {
      const updateTask = vi.fn(async (_id: string, _c: unknown) => task());
      const items = [task(), task({ id: "t2", title: "Segunda", position: 1 })];
      const { w, listTasks } = await mountTab(payload(items), { updateTask });
      listTasks.mockResolvedValue(payload([task({ discarded: true }), items[1]]));
      await btn(w, "Descartar").trigger("click");
      await flushPromises();
      expect(updateTask).toHaveBeenCalledWith("t1", { discarded: true });
      expect(openId(w)).toBe("t2");
      expect(w.text()).toContain("A revisar (1)");
      expect(w.text()).toContain("Descartadas (1)");
      expect(row(w, "t1").get(".rtitle").classes()).toContain("struck");
      await row(w, "t1").trigger("click");
      expect(w.get('[data-task-id="t1"]').text()).toContain("Tarefa descartada");
      await btn(w, "Desfazer").trigger("click");
      await flushPromises();
      expect(updateTask).toHaveBeenLastCalledWith("t1", { discarded: false });
      expect(w.text()).toContain("A revisar (2)");
      expect(w.find('[data-task-id="t1"] [aria-label="Título"]').exists()).toBe(true);
    });

    it("mantém o item aberto (por id) numa recarga e abre o primeiro pendente se ele sumir", async () => {
      const items = [task(), task({ id: "t2", position: 1 }), task({ id: "t3", position: 2 })];
      const { w, listTasks, emit } = await mountTab(payload(items));
      await row(w, "t2").trigger("click");
      emit({ type: "tasks_changed", meeting_id: "m1" });
      await flushPromises();
      expect(openId(w)).toBe("t2");
      // o levantamento foi refeito: t2 foi substituído
      listTasks.mockResolvedValue(payload([task({ id: "n1" }), task({ id: "n2", position: 1 })]));
      emit({ type: "tasks_changed", meeting_id: "m1" });
      await flushPromises();
      expect(openId(w)).toBe("n1");
    });

    it("mostra 'Tudo revisado' quando não há pendentes e mantém o item aberto", async () => {
      const items = [created({ id: "c1" }), task({ id: "t2", position: 1, discarded: true })];
      const { w } = await mountTab(payload(items));
      expect(w.text()).toContain("Tudo revisado");
      expect(w.text()).toContain("A revisar (0)");
      expect(openId(w)).toBe("c1");
      const pending = await mountTab(payload([task()]));
      expect(pending.w.text()).not.toContain("Tudo revisado");
    });
  });

  it("não carrega as opções do MCP de issues com o fluxo desligado e sem itens", async () => {
    const { issueOptions } = await mountTab(payload([], { enabled: false, scan: null }));
    expect(issueOptions).not.toHaveBeenCalled();
  });

  it("carrega as opções do MCP de issues uma vez, depois do primeiro carregamento, com o fluxo ligado", async () => {
    const { issueOptions, listTasks, emit } = await mountTab(payload([]));
    expect(issueOptions).toHaveBeenCalledTimes(1);
    emit({ type: "tasks_changed", meeting_id: "m1" });
    await flushPromises();
    expect(listTasks).toHaveBeenCalledTimes(2);
    expect(issueOptions).toHaveBeenCalledTimes(1);
  });

  it("com o fluxo desligado mas com itens, ainda carrega as opções (para mostrar nomes)", async () => {
    const { issueOptions } = await mountTab(payload([task()], { enabled: false }));
    expect(issueOptions).toHaveBeenCalledTimes(1);
  });

  it("carrega as opções quando o fluxo é ligado depois, numa recarga", async () => {
    const { issueOptions, listTasks, emit } = await mountTab(payload([], { enabled: false, scan: null }));
    expect(issueOptions).not.toHaveBeenCalled();
    listTasks.mockResolvedValue(payload([]));
    emit({ type: "tasks_changed", meeting_id: "m1" });
    await flushPromises();
    expect(issueOptions).toHaveBeenCalledTimes(1);
  });

  describe("criação, avanço e avisos", () => {
    function deferred<T>() {
      let resolve!: (v: T) => void;
      let reject!: (e: unknown) => void;
      const promise = new Promise<T>((res, rej) => {
        resolve = res;
        reject = rej;
      });
      return { promise, resolve, reject };
    }

    it("409 do salvamento enquanto a criação do item está em voo não vira erro no cartão", async () => {
      const patch = deferred<TaskSuggestion>();
      const create = deferred<{ accepted: string[]; rejected: never[] }>();
      const { w } = await mountTab(payload([task()]), {
        updateTask: vi.fn(() => patch.promise),
        createTasks: vi.fn(() => create.promise),
      });
      await field(w, "Título").setValue("Novo título"); // PATCH em voo
      await btn(w, "Criar issue").trigger("click"); // POST em voo
      patch.reject(new ApiError(409, "Esta tarefa está sendo criada"));
      await flushPromises();
      expect(w.text()).not.toContain("Esta tarefa está sendo criada");
    });

    it("erro de salvamento que chega depois da criação não fica grudado no item concluído", async () => {
      const patch = deferred<TaskSuggestion>();
      const { w, listTasks } = await mountTab(payload([task()]), {
        updateTask: vi.fn(() => patch.promise),
        createTasks: vi.fn(async () => ({ accepted: ["t1"], rejected: [] })),
      });
      await field(w, "Título").setValue("Novo título");
      listTasks.mockResolvedValue(payload([task({ create_state: "creating" })]));
      await btn(w, "Criar issue").trigger("click");
      await flushPromises();
      patch.reject(new Error("falha ao salvar"));
      await flushPromises();
      expect(row(w, "t1").text()).toContain("criando…");
      expect(w.text()).not.toContain("falha ao salvar");
    });

    it("o evento do cartão vale para a tarefa do próprio cartão, mesmo com outro item já aberto", async () => {
      const updateTask = vi.fn(async (_id: string, _c: unknown) => task());
      const items = [task(), task({ id: "t2", position: 1 })];
      const { w } = await mountTab(payload(items), { updateTask });
      // ao desmontar um campo focado o navegador dispara o change; simulamos na remoção do cartão
      const removeChild = Node.prototype.removeChild;
      const spy = vi.spyOn(Node.prototype, "removeChild").mockImplementation(function (this: Node, node: Node) {
        const el = node as HTMLElement;
        if (el.getAttribute?.("data-task-id") === "t1") {
          const input = el.querySelector<HTMLInputElement>('[aria-label="Título"]')!;
          input.value = "Editado ao sair";
          input.dispatchEvent(new Event("input"));
          input.dispatchEvent(new Event("change"));
        }
        return removeChild.call(this, node) as typeof node;
      } as typeof removeChild);
      await row(w, "t2").trigger("click");
      await flushPromises();
      spy.mockRestore();
      expect(updateTask).toHaveBeenCalledTimes(1);
      expect(updateTask).toHaveBeenCalledWith("t1", { title: "Editado ao sair" });
    });

    it("não avança nem navega para um pendente que já está sendo criado", async () => {
      const createTasks = vi.fn((_id: string, _items: unknown) => new Promise<never>(() => {}));
      const items = [task(), task({ id: "t2", position: 1 })];
      const { w } = await mountTab(payload(items), { createTasks });
      await btn(w, "Criar issue").trigger("click"); // t1 em voo, abre t2
      await flushPromises();
      expect(openId(w)).toBe("t2");
      expect(w.text()).toContain("A revisar (1)");
      expect(row(w, "t1").text()).toContain("criando…");
      await btn(w, "Criar issue").trigger("click"); // t2 em voo: não volta para t1
      await flushPromises();
      expect(createTasks).toHaveBeenCalledTimes(2);
      expect(openId(w)).toBe("t2");
    });

    it("'Falta: título' e o Criar acompanham o que está sendo digitado, antes do change", async () => {
      const updateTask = vi.fn(async (_id: string, _c: unknown) => task());
      const { w } = await mountTab(payload([task({ title: "" })]), { updateTask });
      expect(w.get('[data-task-id="t1"]').text()).toContain("Falta: título");
      expect(btn(w, "Criar issue").attributes("disabled")).toBeDefined();
      const input = field(w, "Título");
      (input.element as HTMLInputElement).value = "Digitando";
      await input.trigger("input");
      expect(w.text()).not.toContain("Falta:");
      expect(btn(w, "Criar issue").attributes("disabled")).toBeUndefined();
      expect(updateTask).not.toHaveBeenCalled();
      // apagar de volta traz o aviso de novo
      (input.element as HTMLInputElement).value = " ";
      await input.trigger("input");
      expect(w.get('[data-task-id="t1"]').text()).toContain("Falta: título");
    });
  });
  describe("carregamento", () => {
    const RUNNING = { state: "running" as const, error: null, updated_at: "x" };

    it("levantando: caixa com spinner, subtítulo, barra e esqueleto sem itens", async () => {
      const { w } = await mountTab(payload([], { scan: RUNNING }));
      const box = w.get(".scan-box");
      expect(box.text()).toContain("Levantando tarefas…");
      expect(box.text()).toContain("O Claude está lendo a transcrição e os prints. Isso pode levar alguns minutos.");
      expect(box.find('[role="status"]').exists()).toBe(true);
      expect(box.find(".scan-bar").exists()).toBe(true);
      expect(box.text()).toContain("Cancelar levantamento");
      expect(w.get(".skeleton").attributes("aria-hidden")).toBe("true");
    });

    it("levantando com itens: sem esqueleto, com a lista", async () => {
      const { w } = await mountTab(payload([task()], { scan: RUNNING }));
      expect(w.find(".scan-box").exists()).toBe(true);
      expect(w.find(".skeleton").exists()).toBe(false);
      expect(w.find(".panes").exists()).toBe(true);
    });

    it("sem levantamento rodando não há caixa nem esqueleto", async () => {
      const { w } = await mountTab(payload([]));
      expect(w.find(".scan-box").exists()).toBe(false);
      expect(w.find(".skeleton").exists()).toBe(false);
    });

    it("cancelando: a caixa diz 'Cancelando levantamento…'", async () => {
      let finish!: (v: unknown) => void;
      const cancelTasksScan = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
      const { w } = await mountTab(payload([], { scan: RUNNING }), { cancelTasksScan });
      await btn(w, "Cancelar levantamento").trigger("click");
      expect(w.get(".scan-box").text()).toContain("Cancelando levantamento…");
      finish({ scan: RUNNING });
      await flushPromises();
    });

    it("criação em andamento no servidor: cartão esmaecido com a mensagem por cima", async () => {
      const { w } = await mountTab(payload([task({ create_state: "creating" })]));
      expect(w.find(".card-wrap.busy").exists()).toBe(true);
      expect(w.get(".card-overlay").text()).toContain("Criando a issue no MCP de issues…");
      expect(w.get(".card-overlay").attributes("role")).toBe("status");
      expect(w.find(".detail [data-task-id]").exists()).toBe(true);
    });

    it("criando no clique: cartão esmaecido e tag da lista com spinner", async () => {
      let finish!: (v: unknown) => void;
      const createTasks = vi.fn(() => new Promise((resolve) => { finish = resolve; }));
      const { w } = await mountTab(payload([task()]), { createTasks });
      expect(w.find(".card-wrap.busy").exists()).toBe(false);
      expect(w.find(".card-overlay").exists()).toBe(false);
      await btn(w, "Criar issue").trigger("click");
      expect(w.find(".card-wrap.busy").exists()).toBe(true);
      expect(w.get(".card-overlay").text()).toContain("Criando a issue no MCP de issues…");
      expect(row(w, "t1").get(".tag").find(".spinner").exists()).toBe(true);
      finish({ accepted: ["t1"], rejected: [] });
      await flushPromises();
    });

    it("sem criação não há overlay", async () => {
      const { w } = await mountTab(payload([task()]));
      expect(w.find(".card-wrap").exists()).toBe(true);
      expect(w.find(".card-wrap.busy").exists()).toBe(false);
      expect(w.find(".card-overlay").exists()).toBe(false);
    });
  });
});
