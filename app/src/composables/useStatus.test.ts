import { afterEach, describe, expect, it, vi } from "vitest";
import { nextTick, ref } from "vue";
import type { ApiClient } from "../api/client";
import type { ConnectionState, EventConnection } from "../api/events";
import type { ServerEvent, Status } from "../api/types";
import { createStatusStore, INTERRUPTED_MESSAGE } from "./useStatus";

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
  const emit = (e: ServerEvent) => listeners.get(e.type)?.forEach((cb) => cb(e));
  return { events, emit };
}

const idle: Status = {
  dictation: { enabled: true, active: false, key: "ctrl+super" },
  meeting: { state: "idle", meeting_id: null, started_at: null, followed_app: null, alerts: [], print_mode: "off" },
};

describe("useStatus", () => {
  it("status store applies events and refreshes on open", async () => {
    const recording: Status = {
      ...idle,
      meeting: { ...idle.meeting, state: "recording", meeting_id: "m1", alerts: ["mic_silent"] },
    };
    const client = { status: vi.fn(async () => recording) } as unknown as ApiClient;
    const { events, emit } = fakeEvents();
    const store = createStatusStore({ client, events });
    expect(store.connected.value).toBe(false);
    expect(store.meetingState.value).toBe("idle");

    events.state.value = "open";
    await nextTick();
    await Promise.resolve();
    expect(client.status).toHaveBeenCalledTimes(1);
    expect(store.connected.value).toBe(true);
    expect(store.meetingState.value).toBe("recording");
    expect(store.alerts.value).toEqual([{ kind: "mic_silent", message: "Microfone sem sinal" }]);

    emit({ type: "alert", kind: "pc_dead", active: true, message: "Captura parou" });
    emit({ type: "alert", kind: "mic_silent", active: false, message: "Microfone sem sinal" });
    expect(store.alerts.value).toEqual([{ kind: "pc_dead", message: "Captura parou" }]);
    expect(store.status.value?.meeting.alerts).toEqual(["pc_dead"]);

    emit({ type: "dictation", active: true });
    expect(store.dictationActive.value).toBe(true);
    emit({ type: "levels", mic_db: -12.5, pc_db: -90 });
    expect(store.levels.value).toMatchObject({ mic_db: -12.5, pc_db: -90 });

    emit({ type: "status", ...idle });
    expect(store.meetingState.value).toBe("idle");
    expect(store.alerts.value).toEqual([]);
  });

  describe("recording interrupted by a backend restart", () => {
    afterEach(() => vi.useRealTimers());

    const recordingStatus = (id: string): Status => ({
      ...idle,
      meeting: { ...idle.meeting, state: "recording", meeting_id: id, started_at: "2026-10-20T10:00:00" },
    });

    function openStore() {
      // o `GET /status` da abertura nunca responde: o estado vem só dos eventos
      const client = { status: vi.fn(() => new Promise<Status>(() => {})) } as unknown as ApiClient;
      const fake = fakeEvents();
      const store = createStatusStore({ client, events: fake.events });
      return { store, ...fake };
    }

    it("flags a recording interrupted by a backend restart", async () => {
      vi.useFakeTimers();
      const { store, events, emit } = openStore();
      events.state.value = "open";
      await nextTick();
      emit({ type: "status", ...recordingStatus("m1") });
      expect(store.interrupted.value).toBeNull();

      events.state.value = "closed"; // backend caiu gravando
      await nextTick();
      expect(store.interrupted.value).toBeNull(); // ainda não se sabe: pode voltar gravando
      events.state.value = "open";
      await nextTick();
      emit({ type: "status", ...idle }); // voltou parado: a gravação acabou
      expect(store.interrupted.value).toMatchObject({ meetingId: "m1" });
      expect(INTERRUPTED_MESSAGE).toBe("Gravação interrompida: o serviço reiniciou");

      // some sozinho depois de um tempo
      await vi.advanceTimersByTimeAsync(60_000);
      expect(store.interrupted.value).toBeNull();
    });

    it("clears the flag when a new recording starts", async () => {
      const { store, events, emit } = openStore();
      events.state.value = "open";
      await nextTick();
      emit({ type: "status", ...recordingStatus("m1") });
      events.state.value = "closed";
      await nextTick();
      events.state.value = "open";
      await nextTick();
      emit({ type: "status", ...idle });
      expect(store.interrupted.value).not.toBeNull();
      emit({ type: "status", ...recordingStatus("m2") });
      expect(store.interrupted.value).toBeNull();
    });

    it("a normal stop while connected is not flagged", async () => {
      const { store, events, emit } = openStore();
      events.state.value = "open";
      await nextTick();
      emit({ type: "status", ...recordingStatus("m1") });
      emit({ type: "status", ...idle });
      expect(store.interrupted.value).toBeNull();

      // queda breve e volta gravando a mesma reunião: nada a avisar
      emit({ type: "status", ...recordingStatus("m2") });
      events.state.value = "closed";
      await nextTick();
      events.state.value = "open";
      await nextTick();
      emit({ type: "status", ...recordingStatus("m2") });
      expect(store.interrupted.value).toBeNull();
      emit({ type: "status", ...idle }); // e depois para normalmente
      expect(store.interrupted.value).toBeNull();
    });
  });
});
