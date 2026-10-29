import { afterEach, describe, expect, it } from "vitest";
import { mount, type VueWrapper } from "@vue/test-utils";
import AppLoader from "./AppLoader.vue";
import AppMenu from "./AppMenu.vue";
import BrandOrb from "./BrandOrb.vue";
import GlowOrb from "./GlowOrb.vue";
import SkeletonLines from "./SkeletonLines.vue";
import StatusPill from "./StatusPill.vue";
import StepProgress from "./StepProgress.vue";
import TypingDots from "./TypingDots.vue";

const mounted: VueWrapper[] = [];
afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount());
});
function track<T extends VueWrapper>(w: T): T {
  mounted.push(w);
  return w;
}

describe("AppLoader", () => {
  it("is large and decorative by default", () => {
    const w = mount(AppLoader);
    expect(w.classes()).toContain("size-lg");
    expect(w.attributes("aria-hidden")).toBe("true");
    expect(w.attributes("role")).toBeUndefined();
    expect(w.findAll("i")).toHaveLength(3);
  });

  it("supports the small size and the on-accent tone", () => {
    const w = mount(AppLoader, { props: { size: "sm", tone: "on-accent" } });
    expect(w.classes()).toEqual(expect.arrayContaining(["size-sm", "tone-on-accent"]));
  });

  it("becomes a status with screen reader text when labelled", () => {
    const w = mount(AppLoader, { props: { label: "Transcrevendo…" } });
    expect(w.attributes("role")).toBe("status");
    expect(w.attributes("aria-hidden")).toBeUndefined();
    expect(w.get(".sr-only").text()).toBe("Transcrevendo…");
  });
});

describe("GlowOrb", () => {
  it("renders the slot, hidden from assistive tech, with the size", () => {
    const w = mount(GlowOrb, { props: { size: 80 }, slots: { default: "<svg data-test='ico' />" } });
    expect(w.attributes("aria-hidden")).toBe("true");
    expect(w.attributes("style")).toContain("--orb-size: 80px");
    expect(w.find("[data-test=ico]").exists()).toBe(true);
  });

  it("defaults to 56px", () => {
    expect(mount(GlowOrb).attributes("style")).toContain("--orb-size: 56px");
  });
});

describe("BrandOrb", () => {
  it("is decorative and sized", () => {
    expect(mount(BrandOrb).attributes("style")).toContain("width: 36px");
    const w = mount(BrandOrb, { props: { size: 72 } });
    expect(w.attributes("aria-hidden")).toBe("true");
    expect(w.attributes("style")).toContain("height: 72px");
  });
});

describe("TypingDots", () => {
  it("renders three decorative dots", () => {
    const w = mount(TypingDots);
    expect(w.findAll("i")).toHaveLength(3);
    expect(w.attributes("aria-hidden")).toBe("true");
  });

  it("exposes the label as a status", () => {
    const w = mount(TypingDots, { props: { label: "Gerando resumo…" } });
    expect(w.attributes("role")).toBe("status");
    expect(w.get(".sr-only").text()).toBe("Gerando resumo…");
  });
});

describe("SkeletonLines", () => {
  it("renders 3 lines by default, the last one shorter", () => {
    const w = mount(SkeletonLines);
    const lines = w.findAll(".skel");
    expect(lines).toHaveLength(3);
    expect(lines[2].attributes("style")).toContain("width: 60%");
    expect(w.attributes("aria-hidden")).toBe("true");
  });

  it("respects the lines prop", () => {
    expect(mount(SkeletonLines, { props: { lines: 5 } }).findAll(".skel")).toHaveLength(5);
  });

  it("announces the label as a status", () => {
    const w = mount(SkeletonLines, { props: { label: "Carregando reuniões…" } });
    expect(w.attributes("role")).toBe("status");
    expect(w.attributes("aria-hidden")).toBeUndefined();
    expect(w.get(".sr-only").text()).toBe("Carregando reuniões…");
  });
});

describe("StatusPill", () => {
  it("is neutral by default and renders the slot", () => {
    const w = mount(StatusPill, { slots: { default: "Arquivada" } });
    expect(w.classes()).toContain("tone-neutral");
    expect(w.text()).toBe("Arquivada");
    expect(w.find("svg").exists()).toBe(false);
  });

  it("applies the tone and the icon", () => {
    const w = mount(StatusPill, { props: { tone: "ok", icon: "check" }, slots: { default: "Pronta" } });
    expect(w.classes()).toContain("tone-ok");
    expect(w.find("svg").exists()).toBe(true);
  });

  it("shows the loader instead of the icon when busy", () => {
    const w = mount(StatusPill, { props: { tone: "accent", icon: "check", busy: true }, slots: { default: "Transcrevendo" } });
    expect(w.findComponent(AppLoader).props("size")).toBe("sm");
    expect(w.find("svg").exists()).toBe(false);
  });
});

describe("StepProgress", () => {
  const steps = [
    { key: "a", label: "Transcrevendo" },
    { key: "b", label: "Separando vozes" },
    { key: "c", label: "Finalizando" },
  ];
  const states = (w: ReturnType<typeof mount>) => w.findAll("li").map((li) => li.attributes("data-state"));

  it("marks steps before the current as done and after as pending", () => {
    const w = mount(StepProgress, { props: { steps, current: "b" } });
    expect(w.element.querySelector("ol")).not.toBeNull();
    expect(states(w)).toEqual(["done", "run", "todo"]);
    expect(w.findAll("li")[0].find("svg").exists()).toBe(true);
    expect(w.findAll("li")[1].findComponent(AppLoader).exists()).toBe(true);
  });

  it("describes each state in screen reader text", () => {
    const w = mount(StepProgress, { props: { steps, current: "b" } });
    const texts = w.findAll("li").map((li) => li.get(".sr-only").text());
    expect(texts).toEqual([": concluída", ": em andamento", ": pendente"]);
  });

  it("has every step pending without a current step", () => {
    expect(states(mount(StepProgress, { props: { steps, current: null } }))).toEqual(["todo", "todo", "todo"]);
  });

  it("has every step done when done is set", () => {
    const w = mount(StepProgress, { props: { steps, current: null, done: true } });
    expect(states(w)).toEqual(["done", "done", "done"]);
    expect(w.get("[aria-live=polite]").text()).toBe("Todas as etapas concluídas");
  });

  it("announces the running step politely", () => {
    const w = mount(StepProgress, { props: { steps, current: "c" } });
    expect(w.get("[aria-live=polite]").text()).toBe("Em andamento: Finalizando");
  });
});

describe("AppMenu", () => {
  const items = [
    { key: "export", label: "Exportar", icon: "export" },
    { key: "delete", label: "Excluir reunião", icon: "trash", danger: true },
    { key: "locked", label: "Bloqueado", disabled: true },
    { key: "rename", label: "Renomear" },
  ];
  const mountMenu = (props: Record<string, unknown> = {}) =>
    track(mount(AppMenu, { props: { items, label: "Mais opções", ...props }, attachTo: document.body }));
  const labels = (w: ReturnType<typeof mount>) => w.findAll("[role=menuitem]").map((b) => b.text());

  it("exposes a labelled menu with menuitem buttons", () => {
    const w = mountMenu();
    expect(w.attributes("role")).toBe("menu");
    expect(w.attributes("aria-label")).toBe("Mais opções");
    w.findAll("[role=menuitem]").forEach((b) => expect(b.element.tagName).toBe("BUTTON"));
  });

  it("renders destructive items last, after a separator, regardless of input order", () => {
    const w = mountMenu();
    expect(labels(w)).toEqual(["Exportar", "Bloqueado", "Renomear", "Excluir reunião"]);
    const kids = Array.from((w.element as HTMLElement).children);
    const sep = kids.findIndex((el) => el.getAttribute("role") === "separator");
    expect(sep).toBe(kids.length - 2);
    expect(kids[kids.length - 1].classList.contains("danger")).toBe(true);
  });

  it("omits the separator when there is no destructive item", () => {
    const w = mountMenu({ items: items.filter((i) => !i.danger) });
    expect(w.find("[role=separator]").exists()).toBe(false);
  });

  it("focuses the first enabled item on mount", () => {
    mountMenu();
    expect((document.activeElement as HTMLElement).textContent).toContain("Exportar");
  });

  it("emits select with the item key and ignores disabled items", async () => {
    const w = mountMenu();
    await w.findAll("[role=menuitem]")[2].trigger("click");
    expect(w.emitted("select")).toEqual([["rename"]]);
    expect(w.findAll("[role=menuitem]")[1].attributes("disabled")).toBeDefined();
  });

  it("moves focus with the arrows, wrapping around and skipping disabled items", async () => {
    const w = mountMenu();
    const focused = () => (document.activeElement as HTMLElement).textContent?.trim();
    await w.trigger("keydown", { key: "ArrowDown" });
    expect(focused()).toBe("Renomear");
    await w.trigger("keydown", { key: "ArrowDown" });
    expect(focused()).toBe("Excluir reunião");
    await w.trigger("keydown", { key: "ArrowDown" });
    expect(focused()).toBe("Exportar");
    await w.trigger("keydown", { key: "ArrowUp" });
    expect(focused()).toBe("Excluir reunião");
  });

  it("jumps with Home and End", async () => {
    const w = mountMenu();
    const focused = () => (document.activeElement as HTMLElement).textContent?.trim();
    await w.trigger("keydown", { key: "End" });
    expect(focused()).toBe("Excluir reunião");
    await w.trigger("keydown", { key: "Home" });
    expect(focused()).toBe("Exportar");
  });

  it("emits close on Escape", async () => {
    const w = mountMenu();
    await w.trigger("keydown", { key: "Escape" });
    expect(w.emitted("close")).toHaveLength(1);
  });
});
