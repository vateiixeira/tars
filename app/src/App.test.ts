import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import App from "./App.vue";
import MeetingsView from "./views/MeetingsView.vue";
import { installStore } from "./test/meetingFakes";
import { setStatusStore } from "./composables/useStatus";

let wrapper: ReturnType<typeof mount> | undefined;
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  setStatusStore(null);
  document.body.innerHTML = "";
});
function openApp() {
  const ctx = installStore();
  wrapper = mount(App, {
    attachTo: document.body,
    global: {
      stubs: {
        SettingsView: { template: "<div>Preferências</div>" },
        MeetingsView: { template: "<div>Reunião</div>" },
      },
    },
  });
  return ctx;
}
describe("Ametista shell", () => {
  it("keeps a single capture control in the top bar when going to settings", async () => {
    openApp();
    await flushPromises();
    expect(document.querySelectorAll(".rec")).toHaveLength(1);
    const control = document.querySelector(".rec");
    expect(document.querySelector(".appbar .rec")).toBe(control);
    await wrapper!.get(".settings-link").trigger("click");
    await flushPromises();
    expect(document.querySelector(".appbar .rec")).toBe(control);
    expect(document.querySelectorAll(".rec")).toHaveLength(1);
    expect(wrapper!.get(".appbar").isVisible()).toBe(true);
    expect(wrapper!.get("aside").isVisible()).toBe(false);
    expect(wrapper!.text()).toContain("Voltar à reunião");
    await wrapper!.get(".appbar .back").trigger("click");
    await flushPromises();
    expect(wrapper!.get("aside").isVisible()).toBe(true);
    expect(document.querySelector(".rec")).toBe(control);
  });
  it("keeps an in-flight start disabled after changing navigation", async () => {
    let finish!: (value: { meeting_id: string }) => void;
    const ctx = openApp();
    ctx.client.startMeeting = vi.fn(
      () =>
        new Promise<{ meeting_id: string }>((resolve) => {
          finish = resolve;
        }),
    );
    await flushPromises();
    await wrapper!.get(".rec").trigger("click");
    await wrapper!.get(".settings-link").trigger("click");
    await flushPromises();
    expect(document.querySelector<HTMLButtonElement>(".appbar .rec")?.disabled).toBe(true);
    expect(ctx.client.startMeeting).toHaveBeenCalledTimes(1);
    finish({ meeting_id: "m1" });
    await flushPromises();
  });
  it("collapses the library for tasks, keeping the top bar and capture control", async () => {
    openApp();
    await flushPromises();
    const control = document.querySelector(".rec");
    const meetings = wrapper!.findComponent(MeetingsView);
    meetings.vm.$emit("tasksView", true);
    await flushPromises();
    expect(wrapper!.classes()).not.toContain("tasks-route");
    expect(wrapper!.get("aside").isVisible()).toBe(false);
    expect(wrapper!.get(".appbar").isVisible()).toBe(true);
    expect(wrapper!.get(".rec").isVisible()).toBe(true);
    expect(document.querySelectorAll(".rec")).toHaveLength(1);
    // recolhida, a biblioteca abre por cima pelo botão "Reuniões"
    await wrapper!.get(".library-toggle").trigger("click");
    expect(wrapper!.get("aside").isVisible()).toBe(true);
    await wrapper!.get("aside").trigger("keydown", { key: "Escape" });
    expect(wrapper!.get("aside").isVisible()).toBe(false);
    meetings.vm.$emit("tasksView", false);
    await flushPromises();
    expect(wrapper!.get("aside").isVisible()).toBe(true);
    expect(wrapper!.find(".library-toggle").exists()).toBe(false);
    expect(document.querySelector(".rec")).toBe(control);
  });
  it("restores the library after leaving tasks for settings", async () => {
    openApp();
    await flushPromises();
    wrapper!.findComponent(MeetingsView).vm.$emit("tasksView", true);
    await flushPromises();
    await wrapper!.get(".settings-link").trigger("click");
    await wrapper!.get(".appbar .back").trigger("click");
    await flushPromises();
    expect(wrapper!.get("aside").isVisible()).toBe(true);
  });
  it("focuses the library search from the top bar, also from settings", async () => {
    openApp();
    await flushPromises();
    await wrapper!.get(".settings-link").trigger("click");
    await flushPromises();
    await wrapper!.get(".appbar [aria-label=Buscar]").trigger("click");
    await flushPromises();
    expect(wrapper!.get("aside").isVisible()).toBe(true);
    expect(document.activeElement).toBe(wrapper!.get("aside input.search").element);
  });
  it("opens the audio check in settings", async () => {
    openApp();
    await flushPromises();
    await wrapper!.get(".appbar [aria-label='Conferir áudio']").trigger("click");
    await flushPromises();
    expect(wrapper!.text()).toContain("Preferências");
    expect(wrapper!.get("aside").isVisible()).toBe(false);
  });
  it("moves the dictation state to the library footer and the connection to a status dot", async () => {
    const ctx = openApp();
    await flushPromises();
    expect(wrapper!.get(".library-footer .dictation").text()).toBe("Ditado ativo · Ctrl + Super");
    const dot = wrapper!.get(".appbar .conn-dot");
    expect(dot.attributes("aria-label")).toBe("Serviço conectado");
    ctx.events.state.value = "closed";
    ctx.events.error.value = "Serviço do ditado não está rodando";
    await flushPromises();
    expect(dot.attributes("aria-label")).toBe("Serviço desconectado");
    expect(dot.attributes("title")).toBe("Serviço desconectado: Serviço do ditado não está rodando");
    expect(wrapper!.find(".connection-banner").exists()).toBe(true);
  });
  it("switches the appearance from the top bar menu", async () => {
    openApp();
    await flushPromises();
    const button = wrapper!.get(".appbar [aria-label=Aparência]");
    await button.trigger("click");
    const options = wrapper!.findAll("[role=menuitemradio]");
    expect(options.map((o) => o.text())).toEqual(["Tema do sistema", "Noite", "Dia"]);
    await options[2].trigger("click");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(wrapper!.find("[role=menu]").exists()).toBe(false);
    expect(document.activeElement).toBe(button.element);
    await button.trigger("click");
    expect(wrapper!.get("[role=menuitemradio][aria-checked=true]").text()).toBe("Dia");
    await wrapper!.findAll("[role=menuitemradio]")[0].trigger("click"); // volta ao padrão
  });
});
