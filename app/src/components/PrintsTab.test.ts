import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setStatusStore } from "../composables/useStatus";
import type { MeetingPrint, Segment, Speaker } from "../api/types";
import { installStore } from "../test/meetingFakes";

import PrintsTab from "./PrintsTab.vue";

const speakers: Speaker[] = [
  { id: "s1", label: "Você", voice_id: null, is_me: true, color_index: 0 },
  { id: "s2", label: "João", voice_id: null, is_me: false, color_index: 1 },
];
const segments: Segment[] = [
  { id: 1, speaker_id: "s1", source: "mic", start_s: 700, end_s: 710, text: "Vamos ver a tela", dictated: false },
  { id: 2, speaker_id: "s2", source: "pc", start_s: 720, end_s: 740, text: "Aqui o erro aparece", dictated: false },
  { id: 3, speaker_id: "s1", source: "mic", start_s: 800, end_s: 810, text: "Depois disso", dictated: false },
];
const print = (extra: Partial<MeetingPrint> = {}): MeetingPrint => ({
  id: "p1", label: "P1", at_s: 725, source: "flameshot", created_at: "2026-10-19T14:12:05", sent_to_claude: true, ...extra,
});

const createObjectURL = vi.fn((b: Blob) => `blob:${(b as Blob & { tag?: string }).tag ?? "x"}`);
const revokeObjectURL = vi.fn();
const blobOf = (tag: string) => Object.assign(new Blob([tag]), { tag });

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(URL, { createObjectURL, revokeObjectURL });
});
afterEach(() => setStatusStore(null));

function mountTab(prints: MeetingPrint[], overrides: Record<string, unknown> = {}, focusId: string | null = null) {
  const printBlob = vi.fn(async (_m: string, id: string, size: string) => blobOf(`${id}-${size}`));
  const deletePrint = vi.fn(async () => ({ ok: true }));
  installStore({ printBlob, deletePrint, ...overrides });
  const w = mount(PrintsTab, { props: { meetingId: "m1", prints, segments, speakers, focusId }, attachTo: document.body });
  return { w, printBlob, deletePrint };
}

describe("PrintsTab", () => {
  it("mostra cada print com horário e o trecho falado naquele momento", async () => {
    const { w, printBlob } = mountTab([print(), print({ id: "p2", label: "P2", at_s: 805 })]);
    await flushPromises();
    const cards = w.findAll(".print");
    expect(cards).toHaveLength(2);
    expect(cards[0].text()).toContain("P1");
    expect(cards[0].text()).toContain("12:05");
    expect(cards[0].text()).toContain("João");
    expect(cards[0].text()).toContain("Aqui o erro aparece");
    expect(cards[1].text()).toContain("13:25");
    expect(cards[1].text()).toContain("Depois disso");
    expect(printBlob).toHaveBeenCalledWith("m1", "p1", "thumb");
    expect(cards[0].get("img").attributes("src")).toBe("blob:p1-thumb");
    w.unmount();
  });

  it("emite play com o início da fala", async () => {
    const { w } = mountTab([print()]);
    await flushPromises();
    await w.get(".print .play").trigger("click");
    expect(w.emitted("play")?.[0]).toEqual([720]);
    w.unmount();
  });

  it("apaga só depois de confirmar", async () => {
    const { w, deletePrint } = mountTab([print()]);
    await flushPromises();
    const apagar = () => w.findAll("button").find((b) => b.text() === "Apagar")!;
    await apagar().trigger("click");
    expect(w.find('[role="dialog"]').exists()).toBe(true);
    expect(deletePrint).not.toHaveBeenCalled();
    await w.get("[data-dialog-cancel]").trigger("click");
    expect(w.find('[role="dialog"]').exists()).toBe(false);
    expect(deletePrint).not.toHaveBeenCalled();
    expect(w.emitted("deleted")).toBeUndefined();

    await apagar().trigger("click");
    await w.get(".btn-danger").trigger("click");
    await flushPromises();
    expect(deletePrint).toHaveBeenCalledWith("m1", "p1");
    expect(w.emitted("deleted")?.[0]).toEqual(["p1"]);
    expect(w.find('[role="dialog"]').exists()).toBe(false);
    w.unmount();
  });

  it("mostra o erro e mantém o diálogo se apagar falhar", async () => {
    const { w } = mountTab([print()], { deletePrint: vi.fn(async () => { throw new Error("Sem conexão"); }) });
    await flushPromises();
    await w.findAll("button").find((b) => b.text() === "Apagar")!.trigger("click");
    await w.get(".btn-danger").trigger("click");
    await flushPromises();
    expect(w.get('[role="dialog"]').text()).toContain("Sem conexão");
    expect(w.emitted("deleted")).toBeUndefined();
    w.unmount();
  });

  it("avisa quais prints não foram enviados ao Claude", async () => {
    const { w } = mountTab([print(), print({ id: "p2", label: "P2", sent_to_claude: false })]);
    await flushPromises();
    const cards = w.findAll(".print");
    expect(cards[0].text()).not.toContain("Não enviado ao Claude");
    expect(cards[1].text()).toContain("Não enviado ao Claude (limite de 20)");
    w.unmount();
  });

  it("amplia ao clicar na miniatura", async () => {
    const { w, printBlob } = mountTab([print()]);
    await flushPromises();
    await w.get(".print .thumb").trigger("click");
    await flushPromises();
    expect(printBlob).toHaveBeenCalledWith("m1", "p1", "full");
    const dialog = w.get('[role="dialog"]');
    expect(dialog.get("img").attributes("src")).toBe("blob:p1-full");
    await w.get('[aria-label="Fechar diálogo"]').trigger("click");
    expect(w.find('[role="dialog"]').exists()).toBe(false);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p1-full");
    w.unmount();
  });

  it("revoga as miniaturas ao desmontar e quando o print some", async () => {
    const { w } = mountTab([print(), print({ id: "p2", label: "P2" })]);
    await flushPromises();
    await w.setProps({ prints: [print()] });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p2-thumb");
    expect(revokeObjectURL).not.toHaveBeenCalledWith("blob:p1-thumb");
    w.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:p1-thumb");
  });

  it("destaca o print do focusId", async () => {
    const { w } = mountTab([print(), print({ id: "p2", label: "P2" })], {}, "p2");
    await flushPromises();
    const cards = w.findAll(".print");
    expect(cards[0].classes()).not.toContain("focused");
    expect(cards[1].classes()).toContain("focused");
    w.unmount();
  });

  it("mostra aviso quando a miniatura não carrega", async () => {
    const { w } = mountTab([print()], { printBlob: vi.fn(async () => { throw new Error("404"); }) });
    await flushPromises();
    expect(w.get(".print").text()).toContain("Imagem indisponível");
    expect(w.find(".print img").exists()).toBe(false);
    w.unmount();
  });
});
