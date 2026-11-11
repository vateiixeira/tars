import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { ApiError } from "../api/client";
import type { ImportPreview } from "../api/types";
import { setStatusStore } from "../composables/useStatus";
import { installStore } from "../test/meetingFakes";
import ImportDialog from "./ImportDialog.vue";

const TXT = "/home/u/Downloads/Awake e Angular _ Transcrição.txt";
const MP4 = "/home/u/Vídeos/reuniao-awake.mp4";

const transcript = (extra: Partial<ImportPreview> = {}): ImportPreview => ({
  kind: "transcript",
  title: "Awake e Angular - mudança de mecanismo de créditos",
  date: "2026-11-11",
  time: null,
  duration_s: 2100,
  people: ["Alessandro Reichert", "Felipe Iaropoli"],
  my_name_match: "Alessandro Reichert",
  ...extra,
});

const media = (extra: Partial<ImportPreview> = {}): ImportPreview => ({
  kind: "media",
  title: "reuniao-awake",
  date: "2026-11-11",
  time: "16:30",
  duration_s: 2100,
  people: null,
  my_name_match: null,
  ...extra,
});

let mounted: VueWrapper | undefined;
afterEach(() => {
  mounted?.unmount();
  mounted = undefined;
  setStatusStore(null);
});

async function mountDialog(path: string, preview: ImportPreview | Error, overrides: Record<string, unknown> = {}) {
  const inspectImport = vi.fn(async (_p: string) => {
    if (preview instanceof Error) throw preview;
    return preview;
  });
  const importFile = vi.fn(async (_b: unknown) => ({ meeting_id: "m9" }));
  const ctx = installStore({ inspectImport, importFile, ...overrides });
  // no documento: o clique no botão de envio só envia o form conectado
  const w = mount(ImportDialog, { props: { path }, attachTo: document.body });
  mounted = w;
  await flushPromises();
  const btn = (label: string) => w.findAll("button").find((b) => b.text() === label);
  const radios = () =>
    w.findAll('input[type="radio"]').map((r) => ({
      label: r.element.closest("label")!.textContent!.trim(),
      checked: (r.element as HTMLInputElement).checked,
    }));
  return { w, btn, radios, inspectImport, importFile, ...ctx };
}

describe("ImportDialog", () => {
  it("transcrição: lista as pessoas mais Nenhuma delas e marca o nome lembrado", async () => {
    const { w, radios, inspectImport } = await mountDialog(TXT, transcript());
    expect(inspectImport).toHaveBeenCalledWith(TXT);
    expect(w.get('[role="dialog"]').attributes("aria-label")).toBe("Transcrição do Read AI");
    expect(w.text()).toContain("Awake e Angular _ Transcrição.txt");
    expect(w.text()).toContain("2 pessoas · 35 min");
    expect(w.text()).toContain("Quem é você?");
    expect(radios()).toEqual([
      { label: "Alessandro Reichert", checked: true },
      { label: "Felipe Iaropoli", checked: false },
      { label: "Nenhuma delas", checked: false },
    ]);
    expect(w.text()).toContain(
      "Em seguida o TARS gera o resumo e as tarefas. Sem áudio: sem player e sem aprender vozes.",
    );
    expect((w.get<HTMLInputElement>("#import-title").element).value).toBe(
      "Awake e Angular - mudança de mecanismo de créditos",
    );
    expect((w.get<HTMLInputElement>("#import-date").element).value).toBe("11/11/2026");
  });

  it("transcrição sem hora: Importar fica desabilitado até preencher e envia data + hora e quem é você", async () => {
    const { w, btn, importFile } = await mountDialog(TXT, transcript());
    expect(btn("Importar")!.attributes("disabled")).toBeDefined();
    await w.get("#import-time").setValue("25:00");
    expect(btn("Importar")!.attributes("disabled")).toBeDefined();
    await w.get("#import-time").setValue("16:30");
    expect(btn("Importar")!.attributes("disabled")).toBeUndefined();
    await w.findAll('input[type="radio"]')[1].setValue(true);
    await btn("Importar")!.trigger("click");
    await flushPromises();
    expect(importFile).toHaveBeenCalledWith({
      path: TXT,
      title: "Awake e Angular - mudança de mecanismo de créditos",
      started_at: "2026-11-11T16:30",
      me: "Felipe Iaropoli",
    });
    expect(w.emitted("imported")).toEqual([["m9"]]);
  });

  it("Nenhuma delas envia me null, com título e data editados", async () => {
    const { w, btn, importFile } = await mountDialog(TXT, transcript({ time: "09:05" }));
    await w.get("#import-title").setValue("  Reunião editada ");
    await w.get("#import-date").setValue("10/11/2026");
    await w.findAll('input[type="radio"]')[2].setValue(true);
    await btn("Importar")!.trigger("click");
    await flushPromises();
    expect(importFile).toHaveBeenCalledWith({
      path: TXT, title: "Reunião editada", started_at: "2026-11-10T09:05", me: null,
    });
  });

  it("título vazio ou data inválida desabilitam Importar", async () => {
    const { w, btn } = await mountDialog(TXT, transcript({ time: "09:05" }));
    expect(btn("Importar")!.attributes("disabled")).toBeUndefined();
    await w.get("#import-title").setValue("   ");
    expect(btn("Importar")!.attributes("disabled")).toBeDefined();
    await w.get("#import-title").setValue("Ok");
    await w.get("#import-date").setValue("31/02/2026");
    expect(btn("Importar")!.attributes("disabled")).toBeDefined();
    expect(w.get("#import-date").attributes("aria-invalid")).toBe("true");
  });

  it("mídia: mostra Importar e transcrever e a nota do Sou eu, sem Quem é você?", async () => {
    const { w, btn, importFile } = await mountDialog(MP4, media());
    expect(w.get('[role="dialog"]').attributes("aria-label")).toBe("Vídeo ou áudio");
    expect(w.text()).toContain("Vídeo · 35 min");
    expect(w.text()).not.toContain("Quem é você?");
    expect(w.find('input[type="radio"]').exists()).toBe(false);
    expect(w.text()).toContain(
      "As pessoas só aparecem depois da transcrição. Para marcar a sua voz, clique na etiqueta da pessoa e escolha “Sou eu”.",
    );
    expect(w.text()).toContain("Transcreve e separa falantes como numa reunião gravada (usa a GPU, alguns minutos).");
    expect(btn("Importar")).toBeUndefined();
    await btn("Importar e transcrever")!.trigger("click");
    await flushPromises();
    expect(importFile).toHaveBeenCalledWith({
      path: MP4, title: "reuniao-awake", started_at: "2026-11-11T16:30", me: null,
    });
    expect(w.emitted("imported")).toEqual([["m9"]]);
  });

  it("áudio aparece como Áudio no resumo do arquivo", async () => {
    const { w } = await mountDialog("/tmp/gravacao.m4a", media({ duration_s: 4500 }));
    expect(w.text()).toContain("Áudio · 1 h 15 min");
  });

  it("erro 400 da prévia mostra a mensagem do backend", async () => {
    const message = "Formato de transcrição não reconhecido; hoje só a do Read AI é aceita";
    const { w, btn } = await mountDialog(TXT, new ApiError(400, message));
    expect(w.get('[role="alert"]').text()).toContain(message);
    expect(btn("Importar")).toBeUndefined();
    await btn("Fechar")!.trigger("click");
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("erro ao importar fica na tela e permite tentar de novo", async () => {
    const importFile = vi.fn(async () => {
      throw new ApiError(409, "Já há um processamento em andamento");
    });
    const { w, btn } = await mountDialog(MP4, media(), { importFile });
    await btn("Importar e transcrever")!.trigger("click");
    await flushPromises();
    expect(w.get('[role="alert"]').text()).toContain("Já há um processamento em andamento");
    expect(w.emitted("imported")).toBeUndefined();
    expect(btn("Importar e transcrever")!.attributes("disabled")).toBeUndefined();
  });

  it("Cancelar fecha sem importar", async () => {
    const { btn, w, importFile } = await mountDialog(MP4, media());
    await btn("Cancelar")!.trigger("click");
    expect(w.emitted("close")).toHaveLength(1);
    expect(importFile).not.toHaveBeenCalled();
  });
});
