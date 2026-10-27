import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount, type VueWrapper } from "@vue/test-utils";
import { nextTick, ref } from "vue";
import type { ApiClient } from "../api/client";
import type { ConnectionState, EventConnection } from "../api/events";
import type { ServerEvent, Status } from "../api/types";
import { createStatusStore, setStatusStore } from "../composables/useStatus";

const win = vi.hoisted(() => ({ show: vi.fn(async (): Promise<void> => {}), hide: vi.fn(async () => {}) }));
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({ hide: win.hide }) }));
// mostrar passa pelo comando do Rust (reaplica posição e clique atravessando)
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string) => {
    if (cmd === "show_overlay") return win.show();
    throw new Error(`comando inesperado: ${cmd}`);
  },
}));

import Overlay from "./Overlay.vue";

function fakeEvents() {
  const listeners = new Map<string, ((e: ServerEvent) => void)[]>();
  const events: EventConnection = {
    on: (type, cb) => {
      listeners.set(type, [...(listeners.get(type) ?? []), cb as (e: ServerEvent) => void]);
      return () => {};
    },
    state: ref<ConnectionState>("connecting"),
    error: ref<string | null>(null),
    close: () => {},
  };
  const emit = async (e: ServerEvent) => {
    listeners.get(e.type)?.forEach((cb) => cb(e));
    await nextTick();
  };
  return { events, emit };
}

const idle: Status = {
  dictation: { enabled: true, active: false, key: "ctrl+super" },
  meeting: { state: "idle", meeting_id: null, started_at: null, followed_app: null, alerts: [], print_mode: "off" },
};

// 12:00:00 local; a gravação começou 12:34 antes
const NOW = new Date(2026, 9, 20, 12, 0, 0);
const STARTED = "2026-10-20T11:47:25.900123";

function recording(alerts: Status["meeting"]["alerts"] = []): Status {
  return { ...idle, meeting: { ...idle.meeting, state: "recording", meeting_id: "m1", started_at: STARTED, alerts } };
}

let wrapper: VueWrapper | null = null;

function setup() {
  // WebSocket já aberto; o `GET /status` da abertura nunca responde (o estado vem só dos eventos)
  const client = { status: vi.fn(() => new Promise<Status>(() => {})) } as unknown as ApiClient;
  const fake = fakeEvents();
  fake.events.state.value = "open";
  setStatusStore(createStatusStore({ client, events: fake.events }));
  wrapper = mount(Overlay);
  return fake;
}

const pill = () => wrapper!.find("[data-test=pill]");

describe("Overlay", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    win.show.mockReset();
    win.show.mockImplementation(async () => {});
    win.hide.mockClear();
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    setStatusStore(null);
    vi.useRealTimers();
  });

  it("identifies computer audio dictation", async () => {
    const { emit } = setup();
    await emit({ type: "status", ...idle, dictation: { ...idle.dictation, active: true, source: "pc" } });
    expect(pill().text()).toContain("Transcrevendo áudio do PC");
  });

  it("hidden when idle", async () => {
    const { emit } = setup();
    await nextTick();
    expect(pill().exists()).toBe(false);
    expect(win.hide).toHaveBeenCalled();

    await emit({ type: "status", ...idle });
    expect(pill().exists()).toBe(false);
    expect(win.show).not.toHaveBeenCalled();
  });

  it("shows timer and meters while recording", async () => {
    const { emit } = setup();
    await emit({ type: "status", ...recording() });
    expect(win.show).toHaveBeenCalled();
    expect(pill().exists()).toBe(true);
    expect(pill().classes()).not.toContain("alert");
    expect(pill().attributes("style")).toContain("opacity: 1");
    expect(wrapper!.find("[data-test=timer]").text()).toBe("12:34");

    // o relógio anda sozinho
    vi.advanceTimersByTime(2000);
    await nextTick();
    expect(wrapper!.find("[data-test=timer]").text()).toBe("12:36");

    // −60 dBFS ou menos = vazio; −10 ou mais = cheio
    await emit({ type: "levels", mic_db: -10, pc_db: -90 });
    const mic = wrapper!.find("[data-test=meter-mic]");
    const pc = wrapper!.find("[data-test=meter-pc]");
    expect(mic.findAll("i")).toHaveLength(3);
    expect(pc.findAll("i")).toHaveLength(3);
    expect(mic.find(".bars").classes()).not.toContain("off");
    expect(pc.find(".bars").classes()).toContain("off");
    expect(mic.findAll("i")[1]!.attributes("style")).toContain("height: 10px");

    await emit({ type: "levels", mic_db: -35, pc_db: -60 });
    expect(mic.findAll("i")[1]!.attributes("style")).toContain("height: 5px");
    expect(pc.find(".bars").classes()).toContain("off");

    await emit({ type: "status", ...idle });
    expect(pill().exists()).toBe(false);
    expect(win.hide).toHaveBeenCalled();
  });

  it("shows Ditando while dictating", async () => {
    const { emit } = setup();
    await emit({ type: "status", ...idle });
    await emit({ type: "dictation", active: true });
    expect(win.show).toHaveBeenCalled();
    expect(pill().text()).toContain("Ditando");
    expect(wrapper!.find("[data-test=timer]").exists()).toBe(false);
    expect(wrapper!.find(".dot").classes()).not.toContain("blink");

    // gravando e ditando: mostra a gravação com o ponto piscando
    await emit({ type: "status", ...recording(), dictation: { ...idle.dictation, active: true } });
    expect(pill().text()).not.toContain("Ditando");
    expect(wrapper!.find("[data-test=timer]").text()).toBe("12:34");
    expect(wrapper!.find(".dot").classes()).toContain("blink");

    await emit({ type: "dictation", active: false });
    expect(wrapper!.find(".dot").classes()).not.toContain("blink");
  });

  it("alert makes pill opaque with message", async () => {
    const { emit } = setup();
    await emit({ type: "status", ...recording() });
    await emit({ type: "alert", kind: "mic_silent", active: true, message: "Microfone sem sinal" });
    expect(pill().classes()).toContain("alert");
    expect(pill().attributes("style")).toContain("opacity: 1");
    expect(wrapper!.find("[data-test=alert]").text()).toBe("Microfone sem sinal");
    // só o medidor da fonte com problema
    expect(wrapper!.find("[data-test=meter-mic]").exists()).toBe(true);
    expect(wrapper!.find("[data-test=meter-pc]").exists()).toBe(false);

    // o mais recente ativo vence
    await emit({ type: "alert", kind: "pc_wrong_output", active: true, message: "Áudio da chamada não está sendo captado" });
    expect(wrapper!.find("[data-test=alert]").text()).toBe("Áudio da chamada não está sendo captado");
    expect(wrapper!.find("[data-test=meter-pc]").exists()).toBe(true);
    expect(wrapper!.find("[data-test=meter-mic]").exists()).toBe(false);
  });

  it("alert clears when inactive event arrives", async () => {
    const { emit } = setup();
    await emit({ type: "status", ...recording(["mic_silent"]) });
    expect(wrapper!.find("[data-test=alert]").text()).toBe("Microfone sem sinal");

    await emit({ type: "alert", kind: "pc_dead", active: true, message: "Captura do áudio do PC parou" });
    await emit({ type: "alert", kind: "pc_dead", active: false, message: "Captura do áudio do PC parou" });
    expect(wrapper!.find("[data-test=alert]").text()).toBe("Microfone sem sinal");

    await emit({ type: "alert", kind: "mic_silent", active: false, message: "Microfone sem sinal" });
    expect(pill().classes()).not.toContain("alert");
    expect(pill().attributes("style")).toContain("opacity: 1");
    expect(wrapper!.find("[data-test=alert]").exists()).toBe(false);
    expect(wrapper!.find("[data-test=meter-mic]").exists()).toBe(true);
    expect(wrapper!.find("[data-test=meter-pc]").exists()).toBe(true);
  });

  it("warns when the connection to the backend is lost", async () => {
    const { events, emit } = setup();
    await emit({ type: "status", ...recording() });
    await emit({ type: "levels", mic_db: -20, pc_db: -20 });
    expect(pill().classes()).not.toContain("alert");

    // o último status ainda diz "gravando", mas ninguém garante mais: alerta, sem medidores
    events.state.value = "closed";
    await nextTick();
    expect(pill().classes()).toContain("alert");
    expect(wrapper!.find("[data-test=alert]").text()).toBe("Sem conexão com o serviço do ditado");
    expect(wrapper!.find("[data-test=meter-mic]").exists()).toBe(false);
    expect(wrapper!.find("[data-test=meter-pc]").exists()).toBe(false);

    events.state.value = "open";
    await nextTick();
    expect(pill().classes()).not.toContain("alert");
  });

  it("shows the interrupted alert after the backend comes back idle", async () => {
    const { events, emit } = setup();
    await emit({ type: "status", ...recording() });
    events.state.value = "closed";
    await nextTick();
    events.state.value = "open";
    await nextTick();
    await emit({ type: "status", ...idle }); // o serviço reiniciou e não está mais gravando

    // a pílula não some calada: fica laranja avisando que a gravação parou
    expect(pill().exists()).toBe(true);
    expect(pill().classes()).toContain("alert");
    expect(wrapper!.find("[data-test=alert]").text()).toBe("Gravação interrompida: o serviço reiniciou");
    expect(wrapper!.find("[data-test=timer]").exists()).toBe(false);
    expect(wrapper!.find(".dot").exists()).toBe(false); // nada de "gravando"

    await vi.advanceTimersByTimeAsync(60_000);
    expect(pill().exists()).toBe(false);
  });

  it("meters go empty when levels stop arriving", async () => {
    const { emit } = setup();
    await emit({ type: "status", ...recording() });
    await emit({ type: "levels", mic_db: -20, pc_db: -20 });
    expect(wrapper!.find("[data-test=meter-mic] .bars").classes()).not.toContain("off");

    vi.advanceTimersByTime(4000);
    await nextTick();
    expect(wrapper!.find("[data-test=meter-mic] .bars").classes()).toContain("off");
    expect(wrapper!.find("[data-test=meter-pc] .bars").classes()).toContain("off");
  });

  it("dictation pill goes away when the connection drops", async () => {
    const { events, emit } = setup();
    await emit({ type: "status", ...idle });
    await emit({ type: "dictation", active: true });
    expect(pill().text()).toContain("Ditando");
    win.hide.mockClear();

    // sem conexão não há como saber quando o ditado acabou: não fica preso em "Ditando"
    events.state.value = "closed";
    await nextTick();
    expect(pill().exists()).toBe(false);
    expect(win.hide).toHaveBeenCalled();
  });

  it("retries showing the window when show_overlay fails", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    win.show.mockRejectedValueOnce(new Error("posicionar: falhou")).mockRejectedValueOnce(new Error("de novo"));
    const { emit } = setup();
    await emit({ type: "status", ...recording() });
    await vi.advanceTimersByTimeAsync(0);
    expect(win.show).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(win.show).toHaveBeenCalledTimes(2);
    expect(error).toHaveBeenCalledTimes(2);
    // a espera dobra a cada falha
    await vi.advanceTimersByTimeAsync(1999);
    expect(win.show).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(win.show).toHaveBeenCalledTimes(3);

    // deu certo: para de tentar
    await vi.advanceTimersByTimeAsync(20000);
    expect(win.show).toHaveBeenCalledTimes(3);
    error.mockRestore();
  });

  it("stops retrying once the pill is hidden", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    win.show.mockRejectedValue(new Error("falhou"));
    const { emit } = setup();
    await emit({ type: "status", ...recording() });
    await vi.advanceTimersByTimeAsync(0);
    await emit({ type: "status", ...idle });
    await vi.advanceTimersByTimeAsync(20000);
    expect(win.show).toHaveBeenCalledTimes(1);
    error.mockRestore();
  });
});
