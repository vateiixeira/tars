import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import AppSpinner from "./AppSpinner.vue";

describe("AppSpinner", () => {
  it("sem label fica escondido para leitores de tela e usa 16px", () => {
    const w = mount(AppSpinner);
    expect(w.attributes("aria-hidden")).toBe("true");
    expect(w.attributes("role")).toBeUndefined();
    expect(w.attributes("style")).toContain("16px");
  });

  it("com label vira role=status com aria-label e respeita o tamanho", () => {
    const w = mount(AppSpinner, { props: { size: 24, label: "Carregando" } });
    expect(w.attributes("role")).toBe("status");
    expect(w.attributes("aria-label")).toBe("Carregando");
    expect(w.attributes("aria-hidden")).toBeUndefined();
    expect(w.attributes("style")).toContain("24px");
  });
});
