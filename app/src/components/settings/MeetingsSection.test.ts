import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { ApiClient } from "../../api/client";
import type { PrintMonitor, Settings } from "../../api/types";
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
  print_monitor: null,
} as unknown as Settings;

const MONITORS: PrintMonitor[] = [
  { id: "SAM|LU28R55|HX5X901044|HDMI-1", label: 'Samsung Electric Company 29" (HDMI-1)', width: 3840, height: 2160, primary: true },
  { id: "GSM|LG FULL HD|0x01010101|DP-3", label: 'LG Electronics 22" (DP-3)', width: 1920, height: 1080, primary: false },
];

async function mountSection(
  saved: Partial<Settings> = {},
  putError?: Error,
  monitors: () => Promise<{ monitors: PrintMonitor[] }> = async () => ({ monitors: MONITORS }),
) {
  let current = { ...base, ...saved };
  const putSettings = vi.fn(async (partial: Record<string, unknown>) => {
    if (putError) throw putError;
    current = { ...current, ...partial } as Settings;
    return current;
  });
  const monitorsFn = vi.fn(monitors);
  const form = createSettingsForm({ getSettings: async () => current, putSettings, monitors: monitorsFn } as unknown as ApiClient);
  await form.load();
  const w = mount(MeetingsSection, { props: { form }, attachTo: document.body });
  await flushPromises();
  return { w, putSettings, monitorsFn };
}

describe("MeetingsSection prints", () => {
  it("mostra pasta só no modo Flameshot e atalho só no modo Nativo", async () => {
    const off = await mountSection();
    expect(off.w.find("#meet-print-folder").exists()).toBe(false);
    expect(off.w.find("#meet-print-key").exists()).toBe(false);
    expect(off.w.get("#meet-print-mode").findAll("option").map((o) => o.text())).toEqual([
      "Desligado", "Flameshot", "Nativo",
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
    expect(nat.w.text()).toContain("Funciona só durante uma reunião. Captura o monitor escolhido abaixo e toca um som curto.");
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

describe("MeetingsSection monitor do print", () => {
  it("lista os monitores só no modo Nativo e salva a escolha", async () => {
    const off = await mountSection({ print_mode: "flameshot" });
    expect(off.w.find("#meet-print-monitor").exists()).toBe(false);
    expect(off.monitorsFn).not.toHaveBeenCalled();

    const { w, putSettings } = await mountSection({ print_mode: "native" });
    const select = w.get<HTMLSelectElement>("#meet-print-monitor");
    expect(select.findAll("option").map((o) => o.text())).toEqual([
      "Todos os monitores", 'Samsung Electric Company 29" (HDMI-1)', 'LG Electronics 22" (DP-3)',
    ]);
    expect(select.element.value).toBe("");
    await select.setValue("GSM|LG FULL HD|0x01010101|DP-3");
    await flushPromises();
    expect(putSettings).toHaveBeenLastCalledWith({ print_monitor: "GSM|LG FULL HD|0x01010101|DP-3" });
    await select.setValue("");
    await flushPromises();
    expect(putSettings).toHaveBeenLastCalledWith({ print_monitor: null });
  });

  it("carrega a lista ao trocar para o modo Nativo", async () => {
    const { w, monitorsFn } = await mountSection();
    expect(monitorsFn).not.toHaveBeenCalled();
    await w.get("#meet-print-mode").setValue("native");
    await flushPromises();
    expect(monitorsFn).toHaveBeenCalledTimes(1);
    expect(w.get("#meet-print-monitor").findAll("option")).toHaveLength(3);
  });

  it("mostra o monitor salvo como desconectado quando ele não está na lista", async () => {
    const { w } = await mountSection({ print_mode: "native", print_monitor: "DEL|U2720Q|ABC123|DP-1" });
    const select = w.get<HTMLSelectElement>("#meet-print-monitor");
    expect(select.element.value).toBe("DEL|U2720Q|ABC123|DP-1");
    expect(select.findAll("option").map((o) => o.text())).toContain("DEL U2720Q (desconectado)");
  });

  it("mantém selecionado o monitor salvo que está ligado", async () => {
    const { w } = await mountSection({ print_mode: "native", print_monitor: "GSM|LG FULL HD|0x01010101|DP-3" });
    const select = w.get<HTMLSelectElement>("#meet-print-monitor");
    expect(select.element.value).toBe("GSM|LG FULL HD|0x01010101|DP-3");
    expect(select.findAll("option")).toHaveLength(3);
  });

  it("reconhece o monitor salvo que trocou de porta, como o backend", async () => {
    const { w } = await mountSection({ print_mode: "native", print_monitor: "GSM|LG FULL HD|0x01010101|HDMI-2" });
    const select = w.get<HTMLSelectElement>("#meet-print-monitor");
    expect(select.element.value).toBe("GSM|LG FULL HD|0x01010101|DP-3");
    expect(select.text()).not.toContain("desconectado");
  });

  it("sem lista de monitores, ainda deixa escolher Todos e avisa o erro", async () => {
    const fail = async () => {
      throw new Error("backend fora");
    };
    const { w } = await mountSection({ print_mode: "native" }, undefined, fail);
    const select = w.get<HTMLSelectElement>("#meet-print-monitor");
    expect(select.findAll("option").map((o) => o.text())).toEqual(["Todos os monitores"]);
    expect(w.text()).toContain("Não foi possível listar os monitores");

    // com um monitor salvo, ele continua escolhido, mas sem afirmar que está desconectado
    const saved = await mountSection({ print_mode: "native", print_monitor: "GSM|LG FULL HD|0x01010101|DP-3" }, undefined, fail);
    const sel = saved.w.get<HTMLSelectElement>("#meet-print-monitor");
    expect(sel.element.value).toBe("GSM|LG FULL HD|0x01010101|DP-3");
    expect(sel.findAll("option").map((o) => o.text())).toEqual(["Todos os monitores", "GSM LG FULL HD (salvo)"]);
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
