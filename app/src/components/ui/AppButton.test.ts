import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import AppButton from "./AppButton.vue";
import AppLoader from "./AppLoader.vue";

describe("AppButton", () => {
  it("mantém as classes de variante e tamanho", () => {
    const w = mount(AppButton, { props: { variant: "primary", size: "sm" }, slots: { default: "Salvar" } });
    expect(w.classes()).toEqual(expect.arrayContaining(["btn", "pri", "btn-sm"]));
    expect(w.findComponent(AppLoader).exists()).toBe(false);
  });

  it("ocupado: desabilita, marca aria-busy e mostra a órbita pequena", () => {
    const w = mount(AppButton, { props: { variant: "primary", busy: true }, slots: { default: "Salvar" } });
    expect(w.attributes("disabled")).toBeDefined();
    expect(w.attributes("aria-busy")).toBe("true");
    const loader = w.findComponent(AppLoader);
    expect(loader.props()).toMatchObject({ size: "sm", tone: "on-accent" });
    expect(w.text()).toContain("Salvar");
  });

  it("ocupado fora do primário usa a cor padrão da órbita", () => {
    const w = mount(AppButton, { props: { busy: true } });
    expect(w.findComponent(AppLoader).props("tone")).toBe("default");
  });
});
