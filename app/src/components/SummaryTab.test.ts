import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import SummaryTab from "./SummaryTab.vue";

const props = (extra: Record<string, unknown> = {}) => ({ summary: null, loading: false, error: null, job: null, ...extra });
const job = (state: "running" | "done" | "error", error: string | null = null) => ({ state, error, updated_at: "2026-10-20T10:00:00" });

describe("SummaryTab", () => {
  it("without a summary offers Resumir", async () => {
    const w = mount(SummaryTab, { props: props() });
    const btn = w.findAll("button").find((b) => b.text().includes("Resumir"))!;
    await btn.trigger("click");
    expect(w.emitted("summarize")).toHaveLength(1);
  });

  it("summary markdown is rendered without raw html", () => {
    const md = "## Decisões\n\n- **Usar** NFSe\n- item <b>x</b>\n\n<script>window.pwned = 1</script>\n\n<img src=x onerror=\"window.pwned = 2\">\n\n[clique](javascript:alert(1)) ![i](http://x/y.png)";
    const w = mount(SummaryTab, { props: props({ summary: md }) });
    const html = w.get(".md").html();
    expect(w.get(".md h2").text()).toBe("Decisões");
    expect(w.get(".md li strong").text()).toBe("Usar");
    // HTML bruto vira texto
    expect(w.find(".md script").exists()).toBe(false);
    expect(w.find(".md img").exists()).toBe(false);
    expect(w.find(".md b").exists()).toBe(false);
    expect(w.get(".md").text()).toContain("<script>window.pwned = 1</script>");
    expect(w.get(".md").text()).toContain("<img src=x onerror=");
    // sem links nem imagens clicáveis/remotas
    expect(w.find(".md a").exists()).toBe(false);
    expect(html).not.toContain("javascript:alert(1)\"");
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it("with a summary shows only the summary (Copiar and Refazer are in the detail actions)", () => {
    const w = mount(SummaryTab, { props: props({ summary: "# Resumo\n\nTexto" }) });
    expect(w.findAll("button")).toHaveLength(0);
    expect(w.get(".md h1").text()).toBe("Resumo");
  });

  it("shows loading with the existing summary and no buttons", () => {
    const w = mount(SummaryTab, { props: props({ summary: "x", loading: true }) });
    expect(w.get('[role="status"]').text()).toContain("Gerando resumo…");
    expect(w.get(".md").text()).toBe("x");
    expect(w.findAll("button")).toHaveLength(0);
  });

  it("shows the error message and keeps the button usable", async () => {
    const w = mount(SummaryTab, { props: props({ error: "O Claude não está logado" }) });
    expect(w.text()).toContain("O Claude não está logado");
    expect(w.get('.error[role="alert"]').text()).toContain("O Claude não está logado");
    const btn = w.findAll("button").find((b) => b.text().includes("Resumir"))!;
    expect(btn.attributes("disabled")).toBeUndefined();
  });

  it("with the automatic job running and no summary shows 'Gerando resumo…'", () => {
    const w = mount(SummaryTab, { props: props({ job: job("running") }) });
    expect(w.text()).toContain("Gerando resumo…");
    expect(w.text()).not.toContain("Nenhum resumo ainda.");
    expect(w.findAll("button")).toHaveLength(0);
  });

  it("with the automatic job in error and no summary shows the failure and keeps Resumir", async () => {
    const w = mount(SummaryTab, { props: props({ job: job("error", "x") }) });
    expect(w.text()).toContain("O resumo automático falhou: x");
    const btn = w.findAll("button").find((b) => b.text().includes("Resumir"))!;
    expect(btn.attributes("disabled")).toBeUndefined();
    await btn.trigger("click");
    expect(w.emitted("summarize")).toHaveLength(1);
  });

  it("without job and without summary shows 'Nenhum resumo ainda.'", () => {
    const w = mount(SummaryTab, { props: props() });
    expect(w.text()).toContain("Nenhum resumo ainda.");
    expect(w.text()).not.toContain("O resumo automático falhou");
  });

  it("a finished job with a summary shows no failure", () => {
    const w = mount(SummaryTab, { props: props({ summary: "texto", job: job("error", "x") }) });
    expect(w.text()).not.toContain("O resumo automático falhou");
  });
});
