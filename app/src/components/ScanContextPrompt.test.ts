import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import ScanContextPrompt from "./ScanContextPrompt.vue";

const mountPrompt = (modelValue = "", disabled = false) =>
  mount(ScanContextPrompt, { props: { modelValue, disabled } });
const btn = (w: ReturnType<typeof mountPrompt>, label: string) =>
  w.findAll("button").find((b) => b.text().includes(label))!;

describe("ScanContextPrompt", () => {
  it("mostra título, texto, nota e o campo com limite", () => {
    const w = mountPrompt();
    expect(w.text()).toContain("Quer orientar as sugestões de tarefas?");
    expect(w.text()).toContain(
      "Diga ao Claude no que focar, o que agrupar ou ignorar. O texto vai junto com a transcrição e os prints. É opcional: dá para gerar sem escrever nada.",
    );
    expect(w.text()).toContain("Tarefas editadas, descartadas ou já enviadas são preservadas.");
    const area = w.get("textarea");
    expect(area.attributes("maxlength")).toBe("10000");
    expect(area.attributes("rows")).toBe("3");
  });

  it("acrescenta o chip numa nova linha quando o campo já tem texto", async () => {
    const w = mountPrompt("Foque no checkout.");
    await btn(w, "Ignore assuntos de infraestrutura").trigger("click");
    expect(w.emitted("update:modelValue")![0]).toEqual([
      "Foque no checkout.\nIgnore assuntos de infraestrutura.",
    ]);
  });

  it("com o campo vazio o chip entra sozinho, sem quebra de linha", async () => {
    const w = mountPrompt("");
    await btn(w, "Foque só nas pendências de um produto").trigger("click");
    await btn(w, "Junte os ajustes relacionados numa tarefa").trigger("click");
    expect(w.emitted("update:modelValue")).toEqual([
      ["Foque só nas pendências do produto: "],
      ["Junte os ajustes relacionados numa tarefa só."],
    ]);
  });

  it("digitar emite update:modelValue", async () => {
    const w = mountPrompt();
    await w.get("textarea").setValue("Algo");
    expect(w.emitted("update:modelValue")![0]).toEqual(["Algo"]);
  });

  it("Gerar tarefas emite generate e Agora não emite dismiss", async () => {
    const w = mountPrompt();
    await btn(w, "Gerar tarefas").trigger("click");
    await btn(w, "Agora não").trigger("click");
    expect(w.emitted("generate")).toHaveLength(1);
    expect(w.emitted("dismiss")).toHaveLength(1);
  });

  it("desabilitado trava campo, chips e Gerar tarefas, mas não Agora não", async () => {
    const w = mountPrompt("x", true);
    expect(w.get("textarea").attributes("disabled")).toBeDefined();
    const buttons = w.findAll("button");
    expect(buttons).toHaveLength(5);
    for (const b of buttons.filter((b) => !b.text().includes("Agora não")))
      expect(b.attributes("disabled")).toBeDefined();
    expect(btn(w, "Agora não").attributes("disabled")).toBeUndefined();
    await btn(w, "Agora não").trigger("click");
    expect(w.emitted("dismiss")).toHaveLength(1);
  });
});
