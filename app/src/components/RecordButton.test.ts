import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setStatusStore } from "../composables/useStatus";
import type { Status } from "../api/types";
import { IDLE, installStore, reconnect } from "../test/meetingFakes";
import RecordButton from "./RecordButton.vue";

afterEach(() => {
  vi.useRealTimers();
  setStatusStore(null);
});

const recording = (followed: string | null, startedAt: string): Status => ({
  dictation: { enabled: true, active: false, key: "ctrl+super" },
  meeting: { state: "recording", meeting_id: "m1", started_at: startedAt, followed_app: followed, alerts: [], print_mode: "off" },
});

describe("RecordButton", () => {
  it("RecordButton toggles start and stop", async () => {
    const startMeeting = vi.fn(async () => ({ meeting_id: "m1" }));
    const stopMeeting = vi.fn(async () => ({ meeting_id: "m1" }));
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(new Date("2026-10-19T14:02:05"));
    const { emit } = installStore({ startMeeting, stopMeeting });
    const w = mount(RecordButton);
    await flushPromises();

    expect(w.text()).toContain("Iniciar reunião");
    expect(w.text()).toContain("microfone + áudio do PC");
    await w.get("button").trigger("click");
    await flushPromises();
    expect(startMeeting).toHaveBeenCalledOnce();
    expect(w.emitted("started")?.[0]).toEqual(["m1"]);

    emit({ type: "status", ...recording("Zoom", "2026-10-19T14:00:00") });
    await flushPromises();
    expect(w.text()).toContain("Parar · 02:05");
    expect(w.text()).toContain("Seguindo: Zoom");
    await w.get("button").trigger("click");
    await flushPromises();
    expect(stopMeeting).toHaveBeenCalledOnce();
    expect(w.emitted("stopped")?.[0]).toEqual(["m1"]);

    emit({ type: "status", ...recording(null, "2026-10-19T14:00:00") });
    await flushPromises();
    expect(w.text()).toContain("Saída padrão do sistema");

  });

  it("starts a new meeting while the previous one is processing", async () => {
    const startMeeting = vi.fn(async () => ({ meeting_id: "m2" }));
    const { emit } = installStore({ startMeeting });
    const w = mount(RecordButton);
    emit({
      type: "status",
      dictation: { enabled: true, active: false, key: "ctrl+super" },
      meeting: { state: "processing", meeting_id: "m1", started_at: null, followed_app: null, alerts: [], print_mode: "off" },
    });
    await flushPromises();
    expect(w.text()).toContain("Iniciar reunião");
    expect(w.text()).toContain("Processando a anterior…");
    expect(w.get("button").attributes("disabled")).toBeUndefined();
    await w.get("button").trigger("click");
    await flushPromises();
    expect(startMeeting).toHaveBeenCalledOnce();
    expect(w.emitted("started")?.[0]).toEqual(["m2"]);
  });

  it("shows the backend error when start fails", async () => {
    installStore({ startMeeting: vi.fn(async () => { throw new Error("Sem microfone"); }) });
    const w = mount(RecordButton);
    await flushPromises();
    await w.get("button").trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Sem microfone");
  });

  it("ignores a second click while start is in flight", async () => {
    let finish!: () => void;
    const startMeeting = vi.fn(() => new Promise<{ meeting_id: string }>((r) => (finish = () => r({ meeting_id: "m1" }))));
    installStore({ startMeeting });
    const w = mount(RecordButton);
    await flushPromises();
    const btn = w.get("button");
    void btn.trigger("click");
    void btn.trigger("click"); // mesmo tick, antes de o botão re-renderizar desabilitado
    await flushPromises();
    expect(startMeeting).toHaveBeenCalledTimes(1);
    finish();
    await flushPromises();
  });

  it("explains that the recording was interrupted by a backend restart", async () => {
    const startMeeting = vi.fn(async () => ({ meeting_id: "m2" }));
    const { events, emit } = installStore({ startMeeting });
    const w = mount(RecordButton);
    emit({ type: "status", ...recording(null, "2026-10-19T14:00:00") });
    await flushPromises();
    await reconnect(events, emit, IDLE);
    await flushPromises();
    expect(w.text()).toContain("Iniciar reunião");
    expect(w.get(".error").text()).toBe("Gravação interrompida: o serviço reiniciou");

    // até a próxima ação
    await w.get("button").trigger("click");
    await flushPromises();
    expect(w.find(".error").exists()).toBe(false);
  });
});
