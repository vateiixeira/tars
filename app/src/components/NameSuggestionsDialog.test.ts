import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ApiError } from "../api/client";
import type { NameSuggestion } from "../api/types";
import { setStatusStore } from "../composables/useStatus";
import { installStore } from "../test/meetingFakes";
import NameSuggestionsDialog from "./NameSuggestionsDialog.vue";

const suggestions: NameSuggestion[] = [
  { speaker_id: "s2", label: "Pessoa 2", name: "João", evidence: "Oi, aqui é o João da contabilidade" },
  { speaker_id: "s3", label: "Pessoa 3", name: "Maria", evidence: "A Maria vai mandar a nota" },
];

afterEach(() => setStatusStore(null));

async function mountDialog(overrides: Record<string, unknown> = {}) {
  const ctx = installStore({
    nameSuggestions: vi.fn(async () => suggestions),
    renameSpeaker: vi.fn(async () => ({ id: "s2", label: "João" })),
    enrollSpeaker: vi.fn(async () => ({ id: "v1", name: "João", n_samples: 1, created_at: null, updated_at: null })),
    ...overrides,
  });
  const w = mount(NameSuggestionsDialog, { props: { meetingId: "m1" } });
  await flushPromises();
  const apply = (i: number) => w.findAll("button").filter((b) => b.text() === "Aplicar")[i];
  return { w, apply, ...ctx };
}

describe("NameSuggestionsDialog", () => {
  it("lists suggestions with their evidence", async () => {
    const { w, client } = await mountDialog();
    expect(client.nameSuggestions).toHaveBeenCalledWith("m1");
    expect(w.text()).toContain("Pessoa 2");
    expect(w.text()).toContain("João");
    expect(w.text()).toContain("aqui é o João da contabilidade");
    const boxes = w.findAll("input[type=checkbox]");
    expect(boxes).toHaveLength(2);
    expect((boxes[0].element as HTMLInputElement).checked).toBe(false);
  });

  it("applying a suggestion only renames", async () => {
    const { w, apply, client } = await mountDialog();
    await apply(0).trigger("click");
    await flushPromises();
    expect(client.renameSpeaker).toHaveBeenCalledWith("s2", "João");
    expect(client.enrollSpeaker).not.toHaveBeenCalled();
    expect(w.emitted("changed")).toHaveLength(1);
    expect(w.findAll("button").filter((b) => b.text() === "Aplicar")).toHaveLength(1); // a outra segue pendente
    expect(w.text()).toContain("Aplicado");
  });

  it("checking save voice also enrolls", async () => {
    const { w, apply, client } = await mountDialog();
    await w.findAll("input[type=checkbox]")[0].setValue(true);
    await apply(0).trigger("click");
    await flushPromises();
    expect(client.renameSpeaker).toHaveBeenCalledWith("s2", "João");
    expect(client.enrollSpeaker).toHaveBeenCalledWith("s2", { name: "João" });
    expect(w.emitted("changed")).toHaveLength(1);
  });

  it("keeps the rename when saving the voice fails", async () => {
    const enrollSpeaker = vi.fn(async () => {
      throw new ApiError(400, "Este falante não tem uma amostra de voz para cadastrar");
    });
    const { w, apply, client } = await mountDialog({ enrollSpeaker });
    await w.findAll("input[type=checkbox]")[0].setValue(true);
    await apply(0).trigger("click");
    await flushPromises();
    expect(client.renameSpeaker).toHaveBeenCalledTimes(1);
    expect(w.text()).toContain("não tem uma amostra de voz");
    expect(w.text()).toContain("Aplicado");
    expect(w.emitted("changed")).toHaveLength(1);
  });

  it("shows a message when there are no suggestions", async () => {
    const { w } = await mountDialog({ nameSuggestions: vi.fn(async () => []) });
    expect(w.text()).toContain("O Claude não encontrou nomes no contexto");
  });

  it("shows loading, then the backend error and lets the user retry", async () => {
    let fail = true;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const nameSuggestions = vi.fn(async () => {
      await gate;
      if (fail) throw new ApiError(502, "O Claude não está logado. Rode `claude` no terminal.");
      return suggestions;
    });
    const ctx = installStore({ nameSuggestions });
    const w = mount(NameSuggestionsDialog, { props: { meetingId: "m1" } });
    await flushPromises();
    expect(w.text()).toContain("O Claude está lendo a transcrição");
    release();
    await flushPromises();
    expect(w.text()).toContain("O Claude não está logado");
    fail = false;
    await w.findAll("button").find((b) => b.text() === "Tentar de novo")!.trigger("click");
    await flushPromises();
    expect(w.text()).toContain("João");
    expect(ctx.client.nameSuggestions).toHaveBeenCalledTimes(2);
  });

  it("emits close from the close button", async () => {
    const { w } = await mountDialog();
    await w.get('[aria-label="Fechar diálogo"]').trigger("click");
    expect(w.emitted("close")).toHaveLength(1);
  });
});
