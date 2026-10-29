import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive, ref } from "vue";
import ToggleField from "./ToggleField.vue";
import type { SettingsForm } from "./useSettingsForm";

function form(save = vi.fn().mockResolvedValue(true)): SettingsForm {
  return {
    settings: ref({ dictation_enabled: true }),
    errors: reactive({}),
    saving: reactive({}),
    save,
  } as unknown as SettingsForm;
}

describe("ToggleField", () => {
  it("é um switch nomeado só pelo título, com a dica como descrição", () => {
    const w = mount(ToggleField, {
      props: { form: form(), field: "dictation_enabled", label: "Ditado ligado", hint: "Desligado, os atalhos ficam pausados." },
    });
    const input = w.get("#toggle-dictation_enabled");
    expect(input.attributes("type")).toBe("checkbox");
    expect(input.attributes("role")).toBe("switch");
    expect(w.get(`#${input.attributes("aria-labelledby")}`).text()).toBe("Ditado ligado");
    expect(w.get(`#${input.attributes("aria-describedby")}`).text()).toBe("Desligado, os atalhos ficam pausados.");
    expect((input.element as HTMLInputElement).checked).toBe(true);
    // a linha inteira é o rótulo do controle
    expect(w.get("label").attributes("for")).toBe("toggle-dictation_enabled");
  });

  it("volta ao valor confirmado quando o backend recusa", async () => {
    const save = vi.fn().mockResolvedValue(false);
    const w = mount(ToggleField, { props: { form: form(save), field: "dictation_enabled", label: "Ditado ligado" } });
    const input = w.get("#toggle-dictation_enabled");
    await input.setValue(false);
    await Promise.resolve();
    expect(save).toHaveBeenCalledWith("dictation_enabled", false);
    expect((input.element as HTMLInputElement).checked).toBe(true);
    expect(input.attributes("aria-describedby")).toBeUndefined();
  });
});
