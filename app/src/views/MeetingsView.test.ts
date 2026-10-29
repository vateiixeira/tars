import { afterEach, describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setStatusStore } from "../composables/useStatus";
import { installStore } from "../test/meetingFakes";
import MeetingsView from "./MeetingsView.vue";

afterEach(() => setStatusStore(null));

describe("MeetingsView", () => {
  it("sem reunião selecionada mostra o estado vazio com a marca", async () => {
    installStore();
    const w = mount(MeetingsView, { props: { meetingId: null, hasMeetings: true } });
    await flushPromises();
    expect(w.get("h1").text()).toBe("Nenhuma reunião selecionada");
    expect(w.find(".orb").exists()).toBe(true);
    expect(w.find("button").exists()).toBe(false);
  });

  it("sem nenhuma reunião oferece Iniciar reunião", async () => {
    const { events } = installStore();
    const w = mount(MeetingsView, { props: { meetingId: null, hasMeetings: false } });
    await flushPromises();
    expect(w.get("h1").text()).toBe("Nenhuma reunião ainda");
    const start = w.get("button");
    expect(start.text()).toBe("Iniciar reunião");
    await start.trigger("click");
    expect(w.emitted("start")).toHaveLength(1);
    events.state.value = "closed"; // sem conexão não dá para iniciar
    await flushPromises();
    expect(w.get("button").attributes("disabled")).toBeDefined();
  });
});
