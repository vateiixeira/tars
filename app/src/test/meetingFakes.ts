// Apoio dos testes dos componentes de reunião: eventos falsos e store com cliente falso.
import { nextTick, ref } from "vue";
import { vi } from "vitest";
import type { ApiClient } from "../api/client";
import type { ConnectionState, EventConnection } from "../api/events";
import type { ServerEvent, Status } from "../api/types";
import { createStatusStore, setStatusStore } from "../composables/useStatus";

export function fakeEvents() {
  const listeners = new Map<string, ((e: ServerEvent) => void)[]>();
  const events: EventConnection = {
    on: (type, cb) => {
      listeners.set(type, [...(listeners.get(type) ?? []), cb as (e: ServerEvent) => void]);
      return () => {
        listeners.set(type, (listeners.get(type) ?? []).filter((l) => l !== cb));
      };
    },
    state: ref<ConnectionState>("open"),
    error: ref<string | null>(null),
    close: () => {},
  };
  const emit = (e: ServerEvent) => listeners.get(e.type)?.forEach((cb) => cb(e));
  return { events, emit };
}

export const IDLE: Status = {
  dictation: { enabled: true, active: false, key: "ctrl+super" },
  meeting: { state: "idle", meeting_id: null, started_at: null, followed_app: null, alerts: [], print_mode: "off" },
};

/** Simula a queda e a volta do backend: fecha, reabre e manda o `status` da nova conexão. */
export async function reconnect(events: EventConnection, emit: (e: ServerEvent) => void, status: Status = IDLE) {
  events.state.value = "closed";
  await nextTick();
  events.state.value = "open";
  emit({ type: "status", ...status });
  await nextTick();
}

/** Instala o store singleton com o cliente falso (métodos não informados viram `vi.fn()`). */
export function installStore(overrides: Record<string, unknown> = {}, status: Status = IDLE) {
  const client = {
    status: vi.fn(async () => status),
    listMeetings: vi.fn(async () => []),
    archivedCount: vi.fn(async () => ({ count: 0 })),
    ...overrides,
  } as unknown as ApiClient;
  const { events, emit } = fakeEvents();
  setStatusStore(createStatusStore({ client, events }));
  emit({ type: "status", ...status });
  return { client, events, emit };
}
