import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ApiError } from "../api/client";
import { meetingsRevision } from "../composables/useMeetingsRevision";
import { setStatusStore } from "../composables/useStatus";
import type { MeetingDetail as Detail, MeetingPrint } from "../api/types";
import { installStore, reconnect } from "../test/meetingFakes";

const writeText = vi.fn(async (_t: string) => {});
const save = vi.fn(async (_o?: unknown): Promise<string | null> => "/tmp/x.md");
const writeTextFile = vi.fn(async (_p: string, _t: string) => {});
vi.mock("@tauri-apps/plugin-clipboard-manager", () => ({ writeText: (t: string) => writeText(t) }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ save: (o?: unknown) => save(o) }));
const writeFile = vi.fn(async (_p: string, _d: Uint8Array) => {});
vi.mock("@tauri-apps/plugin-fs", () => ({
  writeTextFile: (p: string, t: string) => writeTextFile(p, t),
  writeFile: (p: string, d: Uint8Array) => writeFile(p, d),
}));

vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn(async () => {}) }));

import MeetingDetail from "./MeetingDetail.vue";

const detail = (extra: Partial<Detail> = {}): Detail => ({
  id: "m1",
  title: "Alinhamento NFSe",
  started_at: "2026-10-19T14:00:00",
  ended_at: "2026-10-19T14:42:00",
  status: "ready",
  error: null,
  followed_app: null,
  speakers: [
    { id: "s1", label: "Você", voice_id: null, is_me: true, color_index: 0 },
    { id: "s2", label: "Pessoa 2", voice_id: null, is_me: false, color_index: 1 },
  ],
  segments: [
    { id: 1, speaker_id: "s1", source: "mic", start_s: 12, end_s: 20, text: "Bom dia", dictated: false },
    { id: 2, speaker_id: "s2", source: "pc", start_s: 31, end_s: 40, text: "Oi", dictated: true },
  ],
  summary: null,
  alerts: [],
  prints: [],
  summary_job: null,
  ...extra,
});

const aPrint = (extra: Partial<MeetingPrint> = {}): MeetingPrint => ({
  id: "p1", label: "P1", at_s: 33, source: "flameshot", created_at: "2026-10-19T14:00:33", sent_to_claude: true, ...extra,
});

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
  // jsdom não toca áudio
  HTMLMediaElement.prototype.play = vi.fn(async () => {});
  HTMLMediaElement.prototype.pause = vi.fn();
});
afterEach(() => setStatusStore(null));

async function mountDetail(d: Detail, overrides: Record<string, unknown> = {}) {
  const getMeeting = vi.fn(async (_id: string) => d);
  const ctx = installStore({ getMeeting, ...overrides });
  const w = mount(MeetingDetail, { props: { meetingId: d.id } });
  await flushPromises();
  return { w, getMeeting, ...ctx };
}

describe("MeetingDetail", () => {
  it("MeetingDetail shows progress and failed state", async () => {
    const { w, emit, getMeeting } = await mountDetail(detail({ status: "processing", segments: [] }));
    expect(w.text()).toContain("Processando");
    emit({ type: "progress", meeting_id: "m1", stage: "separando vozes" });
    await flushPromises();
    expect(w.text()).toContain("separando vozes");
    emit({ type: "progress", meeting_id: "outra", stage: "finalizando" });
    await flushPromises();
    expect(w.text()).not.toContain("finalizando");

    // falhou: mensagem + Reprocessar
    getMeeting.mockResolvedValue(detail({ status: "failed", error: "Whisper caiu", segments: [] }));
    emit({ type: "meeting_updated", meeting_id: "m1", status: "failed" });
    await flushPromises();
    expect(w.text()).toContain("Whisper caiu");
    expect(w.findAll("button").some((b) => b.text() === "Reprocessar")).toBe(true);
  });

  it("failed state reprocess calls the API", async () => {
    const reprocess = vi.fn(async () => ({ meeting_id: "m1" }));
    const { w } = await mountDetail(detail({ status: "failed", error: "falhou", segments: [], speakers: [] }), { reprocess });
    await w.findAll("button").find((b) => b.text() === "Reprocessar")!.trigger("click");
    await flushPromises();
    expect(reprocess).toHaveBeenCalledWith("m1");
  });

  it("warns before replacing existing speaker names and supports cancelling", async () => {
    const reprocess = vi.fn(async () => ({ meeting_id: "m1" }));
    const { w } = await mountDetail(detail({ status: "failed", error: "falhou" }), { reprocess });
    const click = async (text: string) => {
      await w.findAll("button").find((b) => b.text() === text)!.trigger("click");
      await flushPromises();
    };
    await click("Reprocessar");
    expect(reprocess).not.toHaveBeenCalled();
    expect(w.get('[aria-label="Confirmar reprocessamento"]').text()).toContain("Os nomes editados e as mesclagens de falantes serão removidos");
    await click("Cancelar");
    expect(reprocess).not.toHaveBeenCalled();
    expect(w.find('[aria-label="Confirmar reprocessamento"]').exists()).toBe(false);
    await click("Reprocessar");
    await click("Confirmar reprocessamento");
    expect(reprocess).toHaveBeenCalledTimes(1);
    expect(reprocess).toHaveBeenCalledWith("m1");
  });

  it("does not carry reprocessing confirmation into another meeting", async () => {
    const { w } = await mountDetail(detail({ status: "failed" }));
    await w.findAll("button").find((b) => b.text() === "Reprocessar")!.trigger("click");
    expect(w.find('[aria-label="Confirmar reprocessamento"]').exists()).toBe(true);
    await w.setProps({ meetingId: "m2" });
    await flushPromises();
    expect(w.find('[aria-label="Confirmar reprocessamento"]').exists()).toBe(false);
  });

  it("title edit saves on Enter", async () => {
    const renameMeeting = vi.fn(async (_id: string, title: string) => ({ id: "m1", title }));
    const { w, getMeeting } = await mountDetail(detail(), { renameMeeting });
    await w.get(".title").trigger("click");
    const input = w.get("input.title-input");
    await input.setValue("  Novo título ");
    await input.trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(renameMeeting).toHaveBeenCalledTimes(1);
    expect(renameMeeting).toHaveBeenCalledWith("m1", "Novo título");
    expect(getMeeting.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the title draft when saving fails and allows retry", async () => {
    const renameMeeting = vi.fn().mockRejectedValueOnce(new Error("Sem conexão")).mockResolvedValueOnce({ id:"m1", title:"Novo nome" });
    const { w } = await mountDetail(detail(), { renameMeeting });
    await w.get('[aria-label^="Renomear reunião:"]').trigger("click");
    await w.get("input.title-input").setValue("Novo nome");
    await w.get("input.title-input").trigger("keydown", { key:"Enter" });
    await flushPromises();
    expect((w.get("input.title-input").element as HTMLInputElement).value).toBe("Novo nome");
    expect(w.text()).toContain("Sem conexão");
    await w.get("input.title-input").trigger("keydown", { key:"Enter" });
    await flushPromises();
    expect(renameMeeting).toHaveBeenCalledTimes(2);
    expect(w.find("input.title-input").exists()).toBe(false);
  });

  it("exposes tab selection and supports arrow navigation", async () => {
    const { w } = await mountDetail(detail());
    const transcript = w.get('#tab-transcript');
    expect(transcript.attributes("aria-selected")).toBe("true");
    expect(w.get('#panel-transcript').attributes("aria-labelledby")).toBe("tab-transcript");
    await transcript.trigger("keydown", { key:"ArrowLeft" });
    expect(w.get('#tab-summary').attributes("aria-selected")).toBe("true");
    expect(w.get('#tab-transcript').attributes("tabindex")).toBe("-1");
    expect(w.find('#panel-summary').exists()).toBe(true);
  });

  it("copy uses transcript format", async () => {
    const { w } = await mountDetail(detail());
    await w.findAll("button").find((b) => b.text().includes("Copiar transcrição"))!.trigger("click");
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith("[00:12] Você: Bom dia\n[00:31] Pessoa 2: Oi (ditado)");
  });

  it("export asks where to save and writes the markdown", async () => {
    const exportMarkdown = vi.fn(async () => "# Reunião");
    const { w } = await mountDetail(detail(), { exportMarkdown });
    await w.findAll("button").find((b) => b.text().includes("Exportar .md"))!.trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ defaultPath: "reuniao-20261019-1400.md" }));
    expect(writeTextFile).toHaveBeenCalledWith("/tmp/x.md", "# Reunião");
  });

  it("mostra a aba Prints (N) entre Transcrição e Tarefas só quando há prints", async () => {
    const { w } = await mountDetail(detail());
    expect(w.findAll('[role="tab"]').map((t) => t.text())).toEqual(["Resumo", "Transcrição"]);

    const printBlob = vi.fn(async () => new Blob(["i"]));
    const { w: w2 } = await mountDetail(detail({ prints: [aPrint(), aPrint({ id: "p2", label: "P2", at_s: 40 })] }), { printBlob });
    expect(w2.findAll('[role="tab"]').map((t) => t.text())).toEqual(["Resumo", "Transcrição", "Prints (2)"]);
    await w2.get("#tab-prints").trigger("click");
    await flushPromises();
    expect(w2.findAll(".print")).toHaveLength(2);
    expect(printBlob).toHaveBeenCalledWith("m1", "p1", "thumb");
  });

  it("o selo da transcrição abre a aba Prints no print certo", async () => {
    const printBlob = vi.fn(async () => new Blob(["i"]));
    const { w } = await mountDetail(detail({ prints: [aPrint({ id: "p9", label: "P9", at_s: 33 })] }), { printBlob });
    await w.get(".print-badge").trigger("click");
    await flushPromises();
    expect(w.get("#tab-prints").attributes("aria-selected")).toBe("true");
    expect(w.get(".print.focused").text()).toContain("P9");
  });

  it("trocar de aba à mão apaga o destaque do print vindo do selo", async () => {
    const printBlob = vi.fn(async () => new Blob(["i"]));
    const { w } = await mountDetail(detail({ prints: [aPrint({ id: "p9", label: "P9", at_s: 33 })] }), { printBlob });
    await w.get(".print-badge").trigger("click");
    await flushPromises();
    expect(w.find(".print.focused").exists()).toBe(true);
    await w.get("#tab-transcript").trigger("click");
    await w.get("#tab-prints").trigger("click");
    await flushPromises();
    expect(w.find(".print").exists()).toBe(true);
    expect(w.find(".print.focused").exists()).toBe(false);

    await w.get("#tab-transcript").trigger("click");
    await flushPromises();
    await w.get(".print-badge").trigger("click");
    await flushPromises();
    expect(w.find(".print.focused").exists()).toBe(true);
    await w.get("#tab-prints").trigger("keydown", { key: "ArrowLeft" }); // para a Transcrição
    await w.get("#tab-transcript").trigger("keydown", { key: "ArrowRight" }); // e de volta
    await flushPromises();
    expect(w.get("#tab-prints").attributes("aria-selected")).toBe("true");
    expect(w.find(".print.focused").exists()).toBe(false);
  });

  it("ir às Tarefas e voltar também apaga o destaque do print", async () => {
    const printBlob = vi.fn(async () => new Blob(["i"]));
    const listTasks = vi.fn(async () => ({ enabled: false, scan: null, items: [] }));
    const issueOptions = vi.fn(async () => {
      throw new Error("desligado");
    });
    const { w } = await mountDetail(detail({ prints: [aPrint({ id: "p9", label: "P9", at_s: 33 })] }),
      { printBlob, listTasks, issueOptions });
    await w.get(".print-badge").trigger("click");
    await flushPromises();
    expect(w.find(".print.focused").exists()).toBe(true);
    await w.get("#open-tasks").trigger("click");
    await flushPromises();
    await w.get(".tasks-head button").trigger("click");
    await flushPromises();
    expect(w.get("#tab-prints").attributes("aria-selected")).toBe("true");
    expect(w.find(".print").exists()).toBe(true);
    expect(w.find(".print.focused").exists()).toBe(false);
  });

  it("ouvir o momento de um print toca só a fala dele", async () => {
    const audioBlob = vi.fn(async () => new Blob(["x"], { type: "audio/ogg" }));
    const printBlob = vi.fn(async () => new Blob(["i"]));
    const { w } = await mountDetail(detail({ prints: [aPrint()] }), { audioBlob, printBlob });
    await w.get("#tab-prints").trigger("click");
    await flushPromises();
    await w.get(".print .play").trigger("click");
    await flushPromises();
    expect(audioBlob).toHaveBeenCalledWith("m1", 31, 40);
  });

  it("apagar um print recarrega a reunião e volta à transcrição quando acabam os prints", async () => {
    const printBlob = vi.fn(async () => new Blob(["i"]));
    const deletePrint = vi.fn(async () => ({ ok: true }));
    const { w, getMeeting } = await mountDetail(detail({ prints: [aPrint()] }), { printBlob, deletePrint });
    await w.get("#tab-prints").trigger("click");
    await flushPromises();
    getMeeting.mockResolvedValue(detail());
    const before = getMeeting.mock.calls.length;
    await w.findAll("button").find((b) => b.text() === "Apagar")!.trigger("click");
    await w.get(".btn-danger").trigger("click");
    await flushPromises();
    expect(deletePrint).toHaveBeenCalledWith("m1", "p1");
    expect(getMeeting.mock.calls.length).toBeGreaterThan(before);
    expect(w.find("#tab-prints").exists()).toBe(false);
    expect(w.get("#tab-transcript").attributes("aria-selected")).toBe("true");
  });

  it("exporta .zip quando há prints e .md quando não há", async () => {
    const bytes = new Uint8Array([80, 75]);
    const exportZip = vi.fn(async () => bytes);
    const exportMarkdown = vi.fn(async () => "# Reunião");
    const printBlob = vi.fn(async () => new Blob(["i"]));
    const { w } = await mountDetail(detail({ prints: [aPrint()] }), { exportZip, exportMarkdown, printBlob });
    save.mockResolvedValueOnce("/tmp/x.zip");
    await w.findAll("button").find((b) => b.text().includes("Exportar .zip"))!.trigger("click");
    await flushPromises();
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      defaultPath: "reuniao-20261019-1400.zip",
      filters: [{ name: "Zip", extensions: ["zip"] }],
    }));
    expect(exportZip).toHaveBeenCalledWith("m1");
    expect(writeFile).toHaveBeenCalledWith("/tmp/x.zip", bytes);
    expect(exportMarkdown).not.toHaveBeenCalled();
    expect(writeTextFile).not.toHaveBeenCalled();

    vi.clearAllMocks();
    const { w: w2 } = await mountDetail(detail(), { exportZip, exportMarkdown });
    await w2.findAll("button").find((b) => b.text().includes("Exportar .md"))!.trigger("click");
    await flushPromises();
    expect(writeTextFile).toHaveBeenCalledWith("/tmp/x.md", "# Reunião");
    expect(exportZip).not.toHaveBeenCalled();
    expect(writeFile).not.toHaveBeenCalled();
  });

  it("lists recording alerts with time of day", async () => {
    const { w } = await mountDetail(detail({ alerts: [{ kind: "mic_silent", start_s: 600, end_s: 2400 }] }));
    expect(w.text()).toContain("Microfone sem sinal de 14:10 a 14:40");
  });

  it("clicking a speaker chip opens the speaker menu, except for Você", async () => {
    const listVoices = vi.fn(async () => []);
    const { w } = await mountDetail(detail(), { listVoices });
    await w.findAll(".chip")[0].trigger("click"); // Você
    expect(w.find(".speaker-menu").exists()).toBe(false);
    await w.findAll(".chip")[1].trigger("click");
    expect(w.find(".speaker-menu").exists()).toBe(true);
    await w.get(".speaker-menu .backdrop").trigger("click");
    expect(w.find(".speaker-menu").exists()).toBe(false);
  });

  it("merging a speaker reloads the detail and notifies the list", async () => {
    const mergeSpeaker = vi.fn(async () => ({ id: "s1" }));
    const { w, getMeeting } = await mountDetail(detail(), { mergeSpeaker, listVoices: vi.fn(async () => []) });
    const before = meetingsRevision.value;
    await w.findAll(".chip")[1].trigger("click");
    await w.findAll("button").find((b) => b.text().includes("Juntar com"))!.trigger("click");
    await w.findAll(".speaker-menu .option").find((b) => b.text().includes("Você"))!.trigger("click");
    await flushPromises();
    expect(mergeSpeaker).toHaveBeenCalledWith("s2", "s1");
    expect(getMeeting).toHaveBeenCalledTimes(2);
    expect(meetingsRevision.value).toBe(before + 1);
    expect(w.emitted("changed")?.[0]).toEqual(["m1"]);
    expect(w.find(".speaker-menu").exists()).toBe(false);
  });

  it("Sugerir nomes only calls Claude after the click and opens the dialog", async () => {
    const nameSuggestions = vi.fn(async () => [
      { speaker_id: "s2", label: "Pessoa 2", name: "João", evidence: "aqui é o João" },
    ]);
    const renameSpeaker = vi.fn(async () => ({ id: "s2", label: "João" }));
    const { w, getMeeting } = await mountDetail(detail(), { nameSuggestions, renameSpeaker });
    expect(nameSuggestions).not.toHaveBeenCalled();
    await w.findAll("button").find((b) => b.text().includes("Sugerir nomes"))!.trigger("click");
    await flushPromises();
    expect(nameSuggestions).toHaveBeenCalledWith("m1");
    await w.findAll("button").find((b) => b.text() === "Aplicar")!.trigger("click");
    await flushPromises();
    expect(renameSpeaker).toHaveBeenCalledWith("s2", "João");
    expect(getMeeting).toHaveBeenCalledTimes(2); // recarregou o detalhe
    await w.findAll("button").find((b) => b.text() === "Fechar")!.trigger("click");
    expect(w.find(".name-dialog").exists()).toBe(false);
  });

  it("Resumir shows the summary tab, then the rendered summary", async () => {
    let finish!: () => void;
    const summarize = vi.fn(() => new Promise<{ summary: string }>((r) => (finish = () => r({ summary: "## Pontos" }))));
    const { w, getMeeting } = await mountDetail(detail(), { summarize });
    await w.findAll("button").find((b) => b.text().includes("Resumir"))!.trigger("click");
    expect(w.text()).toContain("O Claude está resumindo…");
    getMeeting.mockResolvedValue(detail({ summary: "## Pontos" }));
    finish();
    await flushPromises();
    expect(w.get(".md h2").text()).toBe("Pontos");
    expect(w.emitted("changed")?.[0]).toEqual(["m1"]);
  });

  it("Summary shows backend error message", async () => {
    const summarize = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(502, "O Claude não está logado. Rode `claude` no terminal."))
      .mockResolvedValue({ summary: "ok" });
    const { w } = await mountDetail(detail(), { summarize });
    const resumir = () => w.findAll("button").find((b) => b.text().includes("Resumir"))!;
    await resumir().trigger("click");
    await flushPromises();
    expect(w.text()).toContain("O Claude não está logado");
    expect(w.findAll(".banner.error")).toHaveLength(0); // mostrado só na aba, sem duplicar
    expect(resumir().attributes("disabled")).toBeUndefined();
    await resumir().trigger("click"); // o botão voltou a funcionar
    await flushPromises();
    expect(summarize).toHaveBeenCalledTimes(2);
    expect(w.text()).not.toContain("O Claude não está logado");
  });

  it("ignores a summary that finishes after switching meetings", async () => {
    let finish!: () => void;
    const summarize = vi.fn(() => new Promise<{ summary: string }>((r) => (finish = () => r({ summary: "x" }))));
    const { w, getMeeting } = await mountDetail(detail(), { summarize });
    await w.findAll("button").find((b) => b.text().includes("Resumir"))!.trigger("click");
    getMeeting.mockImplementation(async (id: string) => detail({ id, title: "Outra" }));
    await w.setProps({ meetingId: "m2" });
    await flushPromises();
    const loads = getMeeting.mock.calls.length;
    finish();
    await flushPromises();
    expect(getMeeting.mock.calls.length).toBe(loads); // não recarregou a reunião nova
    expect(w.emitted("changed")).toBeUndefined();
    // a ação em andamento era da reunião antiga: a nova pode resumir
    expect(w.findAll("button").find((b) => b.text().includes("Resumir"))!.attributes("disabled")).toBeUndefined();
    expect(w.text()).not.toContain("O Claude está resumindo…");
  });

  it("does not repeat the same error message key when two actions fail with the same text", async () => {
    const exportMarkdown = vi.fn(async () => { throw new Error("igual"); });
    const audioBlob = vi.fn(async () => { throw new Error("igual"); });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { w } = await mountDetail(detail(), { exportMarkdown, audioBlob });
    const btn = (label: string) => w.findAll("button").find((b) => b.text().includes(label))!;
    await btn("Exportar .md").trigger("click");
    await btn("Ouvir áudio").trigger("click");
    await flushPromises();
    expect(w.findAll(".banner.error")).toHaveLength(2);
    expect(warn.mock.calls.flat().join(" ")).not.toContain("Duplicate keys");
    warn.mockRestore();
  });

  it("delete asks for confirmation", async () => {
    const deleteMeeting = vi.fn(async () => ({ ok: true as const }));
    const { w } = await mountDetail(detail(), { deleteMeeting });
    await w.get(".more").trigger("click");
    await w.findAll("button").find((b) => b.text() === "Excluir reunião")!.trigger("click");
    expect(deleteMeeting).not.toHaveBeenCalled();
    await w.findAll("button").find((b) => b.text() === "Excluir")!.trigger("click");
    await flushPromises();
    expect(deleteMeeting).toHaveBeenCalledWith("m1");
    expect(w.emitted("deleted")?.[0]).toEqual(["m1"]);
  });

  it("plays a segment through the audio player", async () => {
    const audioBlob = vi.fn(async () => new Blob(["x"], { type: "audio/ogg" }));
    const createObjectURL = vi.fn(() => "blob:fake");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const { w } = await mountDetail(detail(), { audioBlob });
    await w.get(".row .play").trigger("click");
    await flushPromises();
    expect(audioBlob).toHaveBeenCalledWith("m1", 12, 20);
    expect(w.get("audio").attributes("src")).toBe("blob:fake");
  });

  it("permite ouvir uma pessoa no menu e interrompe o áudio da reunião", async () => {
    const audioBlob = vi.fn(async () => new Blob(["audio"], { type: "audio/ogg" }));
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:sample"), revokeObjectURL: vi.fn() });
    const { w } = await mountDetail(detail(), { audioBlob });
    await w.findAll("button").find((b) => b.text().includes("Ouvir áudio"))!.trigger("click");
    await flushPromises();
    expect(w.find(".player").exists()).toBe(true);
    await w.get('button[data-speaker="s2"]').trigger("click");
    await w.get('.speaker-menu button[aria-label^="Ouvir trecho"]').trigger("click");
    await flushPromises();
    expect(audioBlob).toHaveBeenLastCalledWith("m1", 31, 40, "pc");
    expect(w.find(".player").exists()).toBe(false);
    expect(w.find(".speaker-menu audio").exists()).toBe(true);
    expect(w.text()).toContain("Identificar como");
    await w.get(".speaker-menu .backdrop").trigger("click");
    expect(w.find("audio").exists()).toBe(false);
    w.unmount();
  });

  it("another action finishing does not re-enable Resumir while it runs", async () => {
    let finishSummary!: () => void;
    const summarize = vi.fn(() => new Promise<{ summary: string }>((r) => (finishSummary = () => r({ summary: "x" }))));
    const { w } = await mountDetail(detail(), { summarize });
    const btn = (label: string) => w.findAll("button").find((b) => b.text().includes(label))!;
    await btn("Resumir").trigger("click");
    await flushPromises();
    expect(btn("Resumindo").attributes("disabled")).toBeDefined();

    await w.findAll('[role="tab"]').find(t => t.text() === "Transcrição")!.trigger("click");
    await btn("Copiar transcrição").trigger("click"); // outra ação começa e termina
    await flushPromises();
    expect(writeText).toHaveBeenCalled();
    expect(btn("Resumindo").attributes("disabled")).toBeDefined();
    await btn("Resumindo").trigger("click"); // segundo clique não faz segunda chamada
    expect(summarize).toHaveBeenCalledTimes(1);

    finishSummary();
    await flushPromises();
    expect(btn("Resumir").attributes("disabled")).toBeUndefined();
  });

  it("keeps an action error when another action starts", async () => {
    const exportMarkdown = vi.fn(async () => { throw new Error("Falha ao exportar"); });
    const { w } = await mountDetail(detail(), { exportMarkdown });
    const btn = (label: string) => w.findAll("button").find((b) => b.text().includes(label))!;
    await btn("Exportar .md").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Falha ao exportar");
    await btn("Copiar transcrição").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Falha ao exportar");
  });

  it("discards audio that arrives after switching meetings", async () => {
    let resolveAudio!: (b: Blob) => void;
    const audioBlob = vi.fn(() => new Promise<Blob>((r) => (resolveAudio = r)));
    const createObjectURL = vi.fn(() => "blob:old");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const { w, getMeeting } = await mountDetail(detail(), { audioBlob });
    getMeeting.mockImplementation(async () => detail({ id: "m2" }));
    await w.findAll("button").find((b) => b.text().includes("Ouvir áudio"))!.trigger("click");
    await w.setProps({ meetingId: "m2" });
    await flushPromises();
    resolveAudio(new Blob(["x"]));
    await flushPromises();
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(w.find("audio").exists()).toBe(false);
  });

  it("reloads the open meeting after reconnecting", async () => {
    const { w, events, emit, getMeeting } = await mountDetail(detail({ status: "processing", segments: [] }));
    emit({ type: "progress", meeting_id: "m1", stage: "separando vozes" });
    await flushPromises();
    expect(w.text()).toContain("separando vozes");

    // durante a queda a reunião terminou; o `meeting_updated` se perdeu
    getMeeting.mockResolvedValue(detail());
    await reconnect(events, emit);
    await flushPromises();
    expect(w.text()).toContain("Bom dia");
    expect(w.text()).not.toContain("separando vozes");
  });

  it("opens tasks as a full page and returns to the previous meeting tab", async () => {
    const listTasks = vi.fn(async () => ({ enabled: false, scan: null, items: [] }));
    const issueOptions = vi.fn(async () => {
      throw new Error("desligado");
    });
    const { w } = await mountDetail(detail(), { listTasks, issueOptions });
    expect(listTasks).not.toHaveBeenCalled();
    const tabs = w.findAll('[role="tab"]').map((t) => t.text());
    expect(tabs).toEqual(["Resumo", "Transcrição"]);
    await w.get('#tab-summary').trigger("click");
    await w.get('#open-tasks').trigger("click");
    await flushPromises();
    expect(listTasks).toHaveBeenCalledWith("m1");
    expect(w.text()).toContain("O criador de issues está desligado");
    expect(w.get('.tasks-head').isVisible()).toBe(true);
    expect(w.get('.head').isVisible()).toBe(false);
    expect(w.get('.tabs-row').isVisible()).toBe(false);
    expect(w.emitted('tasksView')?.slice(-1)[0]).toEqual([true]);
    await w.get('.tasks-head button').trigger("click");
    await flushPromises();
    expect(w.get('#tab-summary').attributes('aria-selected')).toBe('true');
    expect(w.get('.head').attributes('style')).not.toContain('display: none');
    expect(w.emitted('tasksView')?.slice(-1)[0]).toEqual([false]);
  });

  it("preserves tasks and context when going back to the meeting and reopening tasks", async () => {
    const item = {
      id: "t1", meeting_id: "m1", origin: "claude" as const, position: 0, kind: "feature" as const,
      title: "Emitir NFSe em lote", description: "", project: null, product_id: null, assignee_id: null,
      team_id: null, stage_id: null, priority: "medium" as const, suggested: null, evidence: [],
      edited: false, discarded: false, create_state: "none" as const, create_error: null,
      issue_number: null, issue_url: null, repeats_number: null,
      print_ids: [], attach_state: "none", attach_error: null,
    };
    const listTasks = vi.fn(async () => ({
      enabled: true,
      scan: { state: "done" as const, error: null, updated_at: "x" },
      items: [item],
    }));
    const updateTask = vi.fn(async () => item);
    const issueOptions = vi.fn(async () => {
      throw new Error("desligado");
    });
    const { w } = await mountDetail(detail(), { listTasks, updateTask, issueOptions });
    await w.get('#open-tasks').trigger("click");
    await flushPromises();
    await w.get('.scan-context textarea').setValue('Priorize o checkout.');
    await w.findAll("button").find((b) => b.text() === "Descartar")!.trigger("click");
    await flushPromises();
    await w.get('.tasks-head button').trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Bom dia");
    await w.get('#open-tasks').trigger("click");
    await flushPromises();
    expect(w.findAll("button").some((b) => b.text() === "Desfazer")).toBe(true);
    expect(listTasks).toHaveBeenCalledTimes(1);
    expect((w.get('.scan-context textarea').element as HTMLTextAreaElement).value).toBe('Priorize o checkout.');
  });

  it("switching to another meeting resets the Tarefas tab", async () => {
    const listTasks = vi.fn(async () => ({ enabled: false, scan: null, items: [] }));
    const issueOptions = vi.fn(async () => {
      throw new Error("desligado");
    });
    const getMeeting = vi.fn(async (id: string) => detail({ id }));
    const ctx = installStore({ getMeeting, listTasks, issueOptions });
    void ctx;
    const w = mount(MeetingDetail, { props: { meetingId: "m1" } });
    await flushPromises();
    await w.get('#open-tasks').trigger("click");
    await flushPromises();
    expect(listTasks).toHaveBeenCalledWith("m1");
    await w.setProps({ meetingId: "m2" });
    await flushPromises();
    expect(listTasks).toHaveBeenCalledTimes(1); // aba volta à Transcrição: a nova reunião não carrega tarefas
    expect(w.emitted('tasksView')?.slice(-1)[0]).toEqual([false]);
    await w.get('#open-tasks').trigger("click");
    await flushPromises();
    expect(listTasks).toHaveBeenLastCalledWith("m2");
  });

  it("meeting_job of the summary of the open meeting reloads the detail", async () => {
    const { getMeeting, emit } = await mountDetail(detail());
    expect(getMeeting).toHaveBeenCalledTimes(1);
    emit({ type: "meeting_job", meeting_id: "outra", kind: "summary", state: "done" });
    emit({ type: "meeting_job", meeting_id: "m1", kind: "tasks", state: "done" });
    await flushPromises();
    expect(getMeeting).toHaveBeenCalledTimes(1);
    emit({ type: "meeting_job", meeting_id: "m1", kind: "summary", state: "done" });
    await flushPromises();
    expect(getMeeting).toHaveBeenCalledTimes(2);
  });

  it("passes the summary job to the Resumo tab", async () => {
    const running = { state: "running" as const, error: null, updated_at: "2026-10-20T10:00:00" };
    const { w } = await mountDetail(detail({ summary_job: running }));
    await w.findAll('[role="tab"]').find(t => t.text() === "Resumo")!.trigger("click");
    expect(w.text()).toContain("O Claude está resumindo…");
  });

  it("play from the TasksTab plays the excerpt", async () => {
    const audioBlob = vi.fn(async () => new Blob(["x"], { type: "audio/ogg" }));
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:fake"), revokeObjectURL: vi.fn() });
    const item = {
      id: "t1", meeting_id: "m1", origin: "claude", position: 0, kind: "tarefa", title: "Algo", description: "",
      project: null, product_id: null, assignee_id: null, team_id: null, stage_id: null, priority: "medium",
      suggested: null, evidence: [{ start_s: 31, end_s: 40, speaker: "Pessoa 2", text: "Oi" }],
      edited: false, discarded: false, create_state: "none", create_error: null,
      issue_number: null, issue_url: null, repeats_number: null,
      print_ids: [], attach_state: "none", attach_error: null,
    };
    const listTasks = vi.fn(async () => ({ enabled: true, scan: null, items: [item] }));
    const issueOptions = vi.fn(async () => ({
      username: "vi", people: [], teams: [], products: [], stages: [], fetched_at: "2026-10-20T10:00:00",
    }));
    const { w } = await mountDetail(detail(), { audioBlob, listTasks, issueOptions });
    await w.get('#open-tasks').trigger("click");
    await flushPromises();
    await w.get(".evidence .play").trigger("click");
    await flushPromises();
    expect(audioBlob).toHaveBeenCalledWith("m1", 31, 40);
  });
});
