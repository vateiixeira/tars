import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { ApiClient } from "../../api/client";
import type { Settings } from "../../api/types";
import { createSettingsForm } from "./useSettingsForm";

vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn(async () => {}) }));

import MeetingsSection from "./MeetingsSection.vue";

const base = {
  auto_title: true,
  auto_summary: true,
  summary_prompt: "Resuma.",
  diarizer: "local",
  voice_match_threshold_local: 0.62,
  voice_match_threshold_pyannote: 0.6,
  meeting_model_idle_sec: 120,
  hf_token_set: false,
  print_mode: "off",
  print_folder: null,
  print_key: "ctrl+alt+KEY_P",
} as unknown as Settings;

async function mountSection(saved: Partial<Settings> = {}, putError?: Error) {
  let current = { ...base, ...saved };
  const putSettings = vi.fn(async (partial: Record<string, unknown>) => {
    if (putError) throw putError;
    current = { ...current, ...partial } as Settings;
    return current;
  });
  const form = createSettingsForm({ getSettings: async () => current, putSettings } as unknown as ApiClient);
  await form.load();
  const w = mount(MeetingsSection, { props: { form }, attachTo: document.body });
  await flushPromises();
  return { w, putSettings };
}

describe("MeetingsSection prints", () => {
  it("mostra pasta só no modo Flameshot e atalho só no modo Nativo", async () => {
    const off = await mountSection();
    expect(off.w.find("#meet-print-folder").exists()).toBe(false);
    expect(off.w.find("#meet-print-key").exists()).toBe(false);
    expect(off.w.get("#meet-print-mode").findAll("option").map((o) => o.text())).toEqual([
      "Desligado", "Flameshot", "Nativo (tela inteira)",
    ]);

    const fl = await mountSection({ print_mode: "flameshot" });
    expect(fl.w.find("#meet-print-folder").exists()).toBe(true);
    expect(fl.w.find("#meet-print-key").exists()).toBe(false);
    expect(fl.w.text()).toContain("Pasta dos prints");
    expect(fl.w.text()).toContain(
      "Vazio usa a pasta de Imagens. Os arquivos criados durante a reunião são copiados ao finalizar; os originais não são alterados.",
    );

    const nat = await mountSection({ print_mode: "native" });
    expect(nat.w.find("#meet-print-folder").exists()).toBe(false);
    expect(nat.w.find("#meet-print-key").exists()).toBe(true);
    expect(nat.w.text()).toContain("Atalho do print");
    expect(nat.w.text()).toContain("Funciona só durante uma reunião. Captura todos os monitores e toca um som curto.");
  });

  it("salva print_mode, print_folder e print_key", async () => {
    const { w, putSettings } = await mountSection({ print_mode: "flameshot" });
    await w.get("#meet-print-folder").setValue("/home/vi/Prints");
    await w.get("#meet-print-folder").trigger("change");
    await flushPromises();
    expect(putSettings).toHaveBeenLastCalledWith({ print_folder: "/home/vi/Prints" });
    await w.get("#meet-print-folder").setValue("   ");
    await w.get("#meet-print-folder").trigger("change");
    await flushPromises();
    expect(putSettings).toHaveBeenLastCalledWith({ print_folder: null });

    await w.get("#meet-print-mode").setValue("native");
    await flushPromises();
    expect(putSettings).toHaveBeenLastCalledWith({ print_mode: "native" });
    await w.get("#meet-print-key").setValue("ctrl+alt+KEY_O");
    await w.get("#meet-print-key").trigger("change");
    await flushPromises();
    expect(putSettings).toHaveBeenLastCalledWith({ print_key: "ctrl+alt+KEY_O" });
  });

  it("mostra o erro do backend embaixo do campo", async () => {
    const { w } = await mountSection({ print_mode: "flameshot" }, new Error("A pasta precisa existir"));
    await w.get("#meet-print-folder").setValue("/nada");
    await w.get("#meet-print-folder").trigger("change");
    await flushPromises();
    expect(w.text()).toContain("A pasta precisa existir");
  });
});

describe("MeetingsSection limiar de voz", () => {
  it("usa passo de 0,01 para o padrão calibrado cair num passo do slider", async () => {
    const { w, putSettings } = await mountSection();
    const range = w.get<HTMLInputElement>("#meet-threshold");
    expect(range.attributes("step")).toBe("0.01");
    expect(range.element.value).toBe("0.62");
    expect(w.get('[data-test="threshold-value"]').text()).toBe("0,62");
    await range.setValue("0.63");
    await range.trigger("change");
    await flushPromises();
    expect(putSettings).toHaveBeenLastCalledWith({ voice_match_threshold_local: 0.63 });
  });
});
